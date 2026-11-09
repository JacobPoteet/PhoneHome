"""The FastAPI application: agent API, JSON API and the server-rendered web UI.

  /api/agent/*     the phone (Bearer agent_token)       docs/PLAN.md §4.7
  /api/*           JSON for the web app (login session)
  everything else  HTML pages (login session)
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from .archive import Archive, is_message_id
from .auth import COOKIE_NAME, Auth
from .config import Settings
from .db import Database
from .jobs import Worker
from .media import ffmpeg_available
from .messages import IngestError, MessageService
from .phone import AgentControl, BehaviorConfig, PhoneRegistry

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
PAGE_SIZE = 50


class LoginRequired(Exception):
    pass


class Services:
    def __init__(self, settings: Settings, control: AgentControl | None = None) -> None:
        self.settings = settings
        self.archive = Archive(settings.archive_dir)
        self.archive.init()
        self.db = Database(settings.db_path)
        self.db.init()
        self.messages = MessageService(self.archive, self.db)
        self.auth = Auth(self.db, settings.session_days)
        self.behavior = BehaviorConfig(settings.behavior_path, self.archive)
        self.phones = PhoneRegistry(self.db, settings)
        self.control = control or AgentControl(settings)
        self.worker = Worker(settings, self.db, self.messages)


# -- template helpers ---------------------------------------------------------
def fmt_duration(seconds: float | None) -> str:
    s = int(round(seconds or 0))
    return f"{s // 60}:{s % 60:02d}"


def fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    s = int(seconds)
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if s >= size:
            n = s // size
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


def fmt_span(seconds: float | None) -> str:
    s = int(seconds or 0)
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    if d:
        return f"{d} d {h} h"
    if h:
        return f"{h} h {m} min"
    return f"{m} min"


def fmt_date(d: datetime) -> str:
    return f"{d:%A}, {d:%B} {d.day}, {d.year}"


def fmt_time(d: datetime) -> str:
    return f"{d.hour % 12 or 12}:{d:%M} {'am' if d.hour < 12 else 'pm'}"


def fmt_bytes(n: float | None) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


KIND_LABELS = {"guest": "Message", "prompted": "Answered a prompt", "reply": "Reply to a memory"}


def create_app(settings: Settings, *, control: AgentControl | None = None) -> FastAPI:
    svc = Services(settings, control)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.run_worker:
            svc.worker.start()
        yield
        if settings.run_worker:
            svc.worker.stop()

    app = FastAPI(title="PhoneHome", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.state.svc = svc
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters.update(duration=fmt_duration, age=fmt_age, span=fmt_span, date=fmt_date, time=fmt_time,
                                 bytes=fmt_bytes)
    templates.env.globals.update(version=__version__, kind_labels=KIND_LABELS)

    def render(request: Request, name: str, status_code: int = 200, **ctx: Any) -> HTMLResponse:
        ctx.setdefault("user", getattr(request.state, "user", None))
        ctx.setdefault("notice", request.query_params.get("notice"))
        ctx.setdefault("error", request.query_params.get("error"))
        ctx.setdefault("unheard", svc.messages.stats()["unheard"] if ctx["user"] else 0)
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)

    def redirect(url: str, **params: str) -> RedirectResponse:
        if params:
            url += ("&" if "?" in url else "?") + "&".join(f"{k}={quote(v)}" for k, v in params.items())
        return RedirectResponse(url, status_code=303)

    # -- auth dependencies ------------------------------------------------------
    def page_user(request: Request) -> str:
        user = svc.auth.user_for(request.cookies.get(COOKIE_NAME))
        if not user:
            raise LoginRequired()
        request.state.user = user
        return user

    def api_user(request: Request) -> str:
        user = svc.auth.user_for(request.cookies.get(COOKIE_NAME))
        if not user:
            raise HTTPException(401, "login required")
        request.state.user = user
        return user

    def agent_auth(authorization: str | None = Header(default=None)) -> None:
        if not settings.agent_token:
            raise HTTPException(503, "agent_token is not configured on the server")
        expected = f"Bearer {settings.agent_token}"
        if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(401, "bad agent token")

    @app.exception_handler(LoginRequired)
    async def _login_redirect(request: Request, exc: LoginRequired) -> RedirectResponse:
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return redirect("/login", next=target)

    def get_msg(msg_id: str) -> dict:
        msg = svc.messages.get(msg_id)
        if not msg:
            raise HTTPException(404, "message not found")
        return msg

    # ===================================================================
    # Health
    # ===================================================================
    @app.get("/health")
    def health() -> dict:
        with svc.db.connect() as c:
            c.execute("SELECT 1")
        return {"ok": True, "version": __version__, "archive_writable": svc.archive.root.exists()}

    # ===================================================================
    # Agent API (the phone)
    # ===================================================================
    agent = [Depends(agent_auth)]

    @app.post("/api/agent/messages", dependencies=agent, status_code=201)
    def agent_upload(meta: str = Form(...), audio: UploadFile = File(...)) -> JSONResponse:
        try:
            meta_obj = json.loads(meta)
            if not isinstance(meta_obj, dict):
                raise ValueError
        except ValueError:
            raise HTTPException(422, "meta must be a JSON object") from None
        tmp = svc.archive.tmp / f"upload-{secrets.token_hex(8)}.flac"
        try:
            with open(tmp, "wb") as fh:
                while chunk := audio.file.read(1 << 20):
                    fh.write(chunk)
            result = svc.messages.ingest(meta_obj, tmp)
        except IngestError as exc:
            raise HTTPException(exc.status, exc.detail) from None
        finally:
            tmp.unlink(missing_ok=True)
        if result.created:
            svc.worker.wake()
        return JSONResponse({"id": result.id, "sha256": result.sha256, "created": result.created},
                            status_code=201 if result.created else 200)

    @app.post("/api/agent/heartbeat", dependencies=agent)
    async def agent_heartbeat(request: Request,
                              x_phonehome_device: str | None = Header(default=None)) -> dict:
        try:
            status = await request.json()
        except ValueError:
            status = {}
        device = x_phonehome_device or (status.get("device") if isinstance(status, dict) else None) or "phone"
        svc.phones.record_heartbeat(device[:100], status if isinstance(status, dict) else {},
                                    request.client.host if request.client else None)
        _, etag = svc.behavior.current()
        unheard = svc.messages.stats()["unheard"]
        return {"config_version": etag.strip('"'), "indicator": "on" if unheard else "off",
                "server_time": datetime.now().astimezone().isoformat(timespec="seconds")}

    @app.get("/api/agent/config", dependencies=agent)
    def agent_config(if_none_match: str | None = Header(default=None)) -> Response:
        doc, etag = svc.behavior.current()
        if if_none_match == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return JSONResponse(doc, headers={"ETag": etag})

    @app.get("/api/agent/assets/{asset_id}", dependencies=agent)
    def agent_asset(asset_id: str) -> FileResponse:
        path = svc.behavior.asset_by_id(asset_id)
        if not path:
            raise HTTPException(404, "unknown asset")
        return FileResponse(path)

    @app.get("/api/agent/replay/random", dependencies=agent)
    def agent_replay() -> Response:
        msg = svc.messages.random_replayable()
        if not msg:
            return Response(status_code=204)
        # Always the FLAC original: the agent decodes it with libsndfile.
        return FileResponse(svc.messages.audio_path(msg, "flac"), media_type="audio/flac",
                            headers={"X-Message-Id": msg["id"], "X-Recorded-At": msg["recorded_at"]})

    # ===================================================================
    # Login
    # ===================================================================
    def set_session(resp: Response, token: str) -> None:
        resp.set_cookie(COOKIE_NAME, token, max_age=settings.session_days * 86400, httponly=True,
                        samesite="lax", secure=settings.cookie_secure)

    def safe_next(target: str | None) -> str:
        return target if target and target.startswith("/") and not target.startswith("//") else "/"

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/") -> HTMLResponse:
        return render(request, "login.html", user=None, next=safe_next(next),
                      first_run=not svc.auth.has_users())

    @app.post("/login")
    def login(request: Request, username: str = Form(...), password: str = Form(...),
              next: str = Form("/")) -> Response:
        token = svc.auth.login(username, password)
        if not token:
            time.sleep(0.5)
            return render(request, "login.html", status_code=401, user=None, next=safe_next(next),
                          first_run=False, error="Wrong username or password.")
        resp = redirect(safe_next(next))
        set_session(resp, token)
        return resp

    @app.post("/setup")
    def first_account(request: Request, username: str = Form(...), password: str = Form(...)) -> Response:
        # Only while no account exists at all; afterwards use the CLI.
        if svc.auth.has_users():
            raise HTTPException(403, "an account already exists")
        try:
            svc.auth.set_user(username, password)
        except ValueError as exc:
            return render(request, "login.html", status_code=400, user=None, next="/", first_run=True,
                          error=str(exc))
        token = svc.auth.login(username, password)
        resp = redirect("/", notice="Welcome! Your account is ready.")
        set_session(resp, token or "")
        return resp

    @app.post("/logout")
    def logout(request: Request) -> Response:
        svc.auth.logout(request.cookies.get(COOKIE_NAME))
        resp = redirect("/login")
        resp.delete_cookie(COOKIE_NAME)
        return resp

    # ===================================================================
    # Pages
    # ===================================================================
    @app.get("/", response_class=HTMLResponse)
    def timeline(request: Request, before: float | None = None, show: str = "all",
                 user: str = Depends(page_user)) -> HTMLResponse:
        msgs = svc.messages.list(limit=PAGE_SIZE + 1, before_ts=before,
                                 unheard_only=show == "unheard", favorites_only=show == "favorites")
        more = len(msgs) > PAGE_SIZE
        msgs = msgs[:PAGE_SIZE]
        groups: list[tuple[str, list[dict]]] = []
        for m in msgs:
            label = f"{m['recorded']:%B %Y}"
            if not groups or groups[-1][0] != label:
                groups.append((label, []))
            groups[-1][1].append(m)
        return render(request, "timeline.html", groups=groups, show=show, stats=svc.messages.stats(),
                      next_before=msgs[-1]["recorded_ts"] if more else None,
                      phone=svc.phones.primary(), offline_after_s=settings.offline_after_s)

    @app.get("/m/{msg_id}", response_class=HTMLResponse)
    def message_page(request: Request, msg_id: str, user: str = Depends(page_user)) -> HTMLResponse:
        msg = get_msg(msg_id)
        reply_to = svc.messages.get(msg["reply_to"]) if msg.get("reply_to") else None
        trashed = msg["deleted_at"] is not None
        sidecar = svc.archive.read_sidecar(msg_id, trashed=trashed)
        return render(request, "message.html", m=msg, reply_to=reply_to, sidecar=sidecar,
                      files=svc.archive.message_files(msg_id, trashed=trashed))

    @app.post("/m/{msg_id}/edit")
    def message_edit(msg_id: str, title: str = Form(""), notes: str = Form(""), people: str = Form(""),
                     tags: str = Form(""), favorite: str | None = Form(None),
                     replay_eligible: str | None = Form(None), user: str = Depends(page_user)) -> Response:
        get_msg(msg_id)
        svc.messages.update(msg_id, {"title": title, "notes": notes, "people": people, "tags": tags,
                                     "favorite": favorite is not None,
                                     "replay_eligible": replay_eligible is not None})
        return redirect(f"/m/{msg_id}", notice="Saved.")

    @app.post("/m/{msg_id}/favorite")
    def message_favorite(msg_id: str, back: str = Form("/"), user: str = Depends(page_user)) -> Response:
        msg = get_msg(msg_id)
        svc.messages.update(msg_id, {"favorite": not msg["favorite"]})
        return redirect(safe_next(back))

    @app.post("/m/{msg_id}/trash")
    def message_trash(msg_id: str, user: str = Depends(page_user)) -> Response:
        get_msg(msg_id)
        svc.messages.trash(msg_id)
        return redirect("/", notice=f"Moved to the trash. It can be restored for {settings.trash_days} days.")

    @app.post("/m/{msg_id}/restore")
    def message_restore(msg_id: str, user: str = Depends(page_user)) -> Response:
        get_msg(msg_id)
        svc.messages.restore(msg_id)
        return redirect(f"/m/{msg_id}", notice="Restored.")

    @app.post("/heard-all")
    def heard_all(user: str = Depends(page_user)) -> Response:
        svc.messages.mark_all_heard()
        return redirect("/")

    @app.get("/trash", response_class=HTMLResponse)
    def trash_page(request: Request, user: str = Depends(page_user)) -> HTMLResponse:
        return render(request, "trash.html", msgs=svc.messages.list(trashed=True, limit=1000),
                      trash_days=settings.trash_days)

    @app.get("/phone", response_class=HTMLResponse)
    def phone_page(request: Request, user: str = Depends(page_user)) -> HTMLResponse:
        phone = svc.phones.primary()
        return render(request, "phone.html", phone=phone, control=svc.control.configured,
                      logs=svc.control.logs(150) if svc.control.configured and phone and phone["online"] else None,
                      jobs=svc.worker.summary(), stats=svc.messages.stats(),
                      ffmpeg=ffmpeg_available(settings.ffmpeg), settings=settings)

    @app.post("/phone/{action}")
    def phone_action(action: str, mode: str = Form("auto"), route: str = Form("earpiece"),
                     user: str = Depends(page_user)) -> Response:
        body: dict[str, Any] = {}
        if action == "ring":
            body = {"mode": mode, "force": True}
        elif action == "tone":
            body = {"route": route}
        ok, detail = svc.control.call(action, body)
        labels = {"ring": "Ringing the phone.", "tone": "Playing a test tone.", "sync": "Phone is syncing.",
                  "restart": "Agent is restarting.", "reboot": "Phone is rebooting."}
        if ok:
            return redirect("/phone", notice=labels.get(action, "Done."))
        return redirect("/phone", error=detail)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, user: str = Depends(page_user)) -> HTMLResponse:
        doc, etag = svc.behavior.current()
        doc.pop("version")
        return render(request, "settings.html", behavior=json.dumps(doc, indent=2), version=etag.strip('"'),
                      users=svc.auth.users(), assets=_list_assets(svc.archive))

    @app.post("/settings")
    def settings_save(request: Request, behavior: str = Form(...), user: str = Depends(page_user)) -> Response:
        try:
            data = json.loads(behavior or "{}")
            version = svc.behavior.save(data)
        except (ValueError, TypeError) as exc:
            return render(request, "settings.html", status_code=400, behavior=behavior, version="unsaved",
                          users=svc.auth.users(), assets=_list_assets(svc.archive), error=str(exc))
        if svc.control.configured:
            svc.control.call("sync")
        return redirect("/settings", notice=f"Saved as {version}. The phone picks it up within a minute.")

    # -- audio ------------------------------------------------------------------
    @app.get("/media/{msg_id}.{ext}")
    def media(msg_id: str, ext: str, download: bool = False, user: str = Depends(page_user)) -> FileResponse:
        if ext not in ("flac", "mp3") or not is_message_id(msg_id):
            raise HTTPException(404)
        msg = get_msg(msg_id)
        path = svc.messages.audio_path(msg, ext)
        if not path.exists():
            raise HTTPException(404, f"no {ext} for this message yet")
        media_type = "audio/mpeg" if ext == "mp3" else "audio/flac"
        return FileResponse(path, media_type=media_type,
                            filename=path.name if download else None,
                            content_disposition_type="attachment" if download else "inline")

    # ===================================================================
    # JSON API (for the web app and integrations)
    # ===================================================================
    def msg_json(m: dict) -> dict:
        out = {k: v for k, v in m.items() if k != "recorded"}
        audio = "mp3" if m["has_mp3"] else "flac"
        out["audio_url"] = f"/media/{m['id']}.{audio}"
        return out

    @app.get("/api/messages")
    def api_messages(before: float | None = None, limit: int = PAGE_SIZE, trashed: bool = False,
                     user: str = Depends(api_user)) -> dict:
        msgs = svc.messages.list(limit=max(1, min(limit, 500)), before_ts=before, trashed=trashed)
        return {"messages": [msg_json(m) for m in msgs]}

    @app.get("/api/messages/{msg_id}")
    def api_message(msg_id: str, user: str = Depends(api_user)) -> dict:
        return msg_json(get_msg(msg_id))

    @app.patch("/api/messages/{msg_id}")
    async def api_message_patch(msg_id: str, request: Request, user: str = Depends(api_user)) -> dict:
        get_msg(msg_id)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, "expected a JSON object")
        return msg_json(svc.messages.update(msg_id, body))

    @app.delete("/api/messages/{msg_id}")
    def api_message_delete(msg_id: str, user: str = Depends(api_user)) -> dict:
        msg = get_msg(msg_id)
        if msg["deleted_at"]:
            raise HTTPException(409, "already in the trash")
        svc.messages.trash(msg_id)
        return {"ok": True}

    @app.post("/api/messages/{msg_id}/heard")
    def api_message_heard(msg_id: str, user: str = Depends(api_user)) -> dict:
        get_msg(msg_id)
        svc.messages.set_heard(msg_id, True)
        return {"ok": True}

    @app.get("/api/phone")
    def api_phone(user: str = Depends(api_user)) -> dict:
        return {"phones": svc.phones.phones(), "jobs": svc.worker.summary()}

    @app.get("/api/homepage")
    def api_homepage(request: Request, token: str | None = None,
                     authorization: str | None = Header(default=None)) -> dict:
        """Summary for the Homepage dashboard's customapi widget."""
        supplied = token or (authorization or "").removeprefix("Bearer ").strip()
        ok = bool(settings.homepage_token) and hmac.compare_digest(supplied.encode(),
                                                                   settings.homepage_token.encode())
        if not ok:
            api_user(request)
        stats = svc.messages.stats()
        phone = svc.phones.primary()
        last_age = time.time() - stats["last_ts"] if stats["last_ts"] else None
        return {"phone": "online" if phone and phone["online"] else "offline",
                "new": stats["unheard"], "total": stats["total"],
                "last": fmt_age(last_age) if last_age is not None else "never",
                "hours": round(stats["seconds"] / 3600, 1)}

    return app


def _list_assets(archive: Archive) -> list[dict]:
    from .archive import ASSET_DIRS

    out = []
    for d in ASSET_DIRS:
        for p in sorted((archive.root / d).glob("*")):
            if p.is_file() and not p.name.startswith("."):
                out.append({"dir": d, "name": p.name, "size": p.stat().st_size})
    return out
