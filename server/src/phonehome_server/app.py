"""The FastAPI application.

  /api/agent/*   the phone (Bearer agent_token)                 docs/PLAN.md §4.7
  /api/*         JSON for the web app (session cookie)
  /media/*       message audio (session cookie, supports Range for seeking)
  everything else  the SvelteKit web app (web/, built to static files)
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from . import __version__
from .archive import Archive, is_message_id
from .auth import COOKIE_NAME, Auth
from .clips import KINDS, ClipError, ClipStore, active_now, describe_rules
from .config import Settings
from .db import Database
from .jobs import Worker
from .media import MediaError, ffmpeg_available, normalize_clip
from .messages import IngestError, MessageService
from .phone import AgentControl, BehaviorConfig, PhoneRegistry
from .transcribe import FasterWhisperTranscriber, Transcriber, faster_whisper_installed

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
PAGE_SIZE = 30
MAX_CLIP_UPLOAD = 50 * 1024 * 1024
# State-changing API calls must carry this header. Browsers never add custom
# headers to cross-site form posts, so this blocks CSRF on top of SameSite=Lax.
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "phonehome"


def build_transcriber(settings: Settings) -> Transcriber | None:
    if not settings.transcribe:
        return None
    if not faster_whisper_installed():
        log.warning("transcription is on but faster-whisper is not installed; skipping transcripts")
        return None
    return FasterWhisperTranscriber(settings.whisper_model, compute_type=settings.whisper_compute_type,
                                    threads=settings.whisper_threads, beam_size=settings.whisper_beam_size,
                                    download_root=settings.models_dir)


class Services:
    def __init__(self, settings: Settings, control: AgentControl | None = None,
                 transcriber: Transcriber | None | bool = True) -> None:
        self.settings = settings
        self.archive = Archive(settings.archive_dir)
        self.archive.init()
        self.db = Database(settings.db_path)
        self.db.init()
        # transcriber=True: build from settings; None/False: off; an object: use it.
        if transcriber is True:
            transcriber = build_transcriber(settings)
        self.transcriber: Transcriber | None = transcriber or None
        self.messages = MessageService(self.archive, self.db, transcribe=self.transcriber is not None)
        self.clips = ClipStore(self.archive)
        self.clips.init()
        self.auth = Auth(self.db, settings.session_days)
        self.behavior = BehaviorConfig(settings.behavior_path, self.archive, self.clips)
        self.phones = PhoneRegistry(self.db, settings)
        self.control = control or AgentControl(settings)
        self.worker = Worker(settings, self.db, self.messages, self.transcriber)


def find_web_dir(settings: Settings) -> Path | None:
    # An explicit setting is the only place we look; otherwise the copy inside
    # the package (Docker), then the repository's web/build (development).
    candidates = [Path(settings.web_dir)] if settings.web_dir else [HERE / "web", HERE.parents[2] / "web" / "build"]
    for c in candidates:
        if (c / "index.html").exists():
            return c
    return None


def fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    s = int(seconds)
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if s >= size:
            n = s // size
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


NO_WEB_UI = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>PhoneHome</title><body style="font:16px/1.5 system-ui;max-width:560px;margin:10vh auto;padding:0 16px">
<h1>PhoneHome server is running</h1><p>The web app hasn't been built. From the repository:</p>
<pre style="background:#eee;padding:12px;border-radius:8px">cd web
npm ci
npm run build</pre><p>then reload. (The Docker image builds it automatically.)</p>
<p>The API is up: <a href="/health">/health</a> · <a href="/api/docs">/api/docs</a></p></body>"""


def create_app(settings: Settings, *, control: AgentControl | None = None,
               transcriber: Transcriber | None | bool = True) -> FastAPI:
    svc = Services(settings, control, transcriber)

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

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        path = request.url.path
        if (request.method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/api/")
                and not path.startswith("/api/agent/")
                and request.headers.get(CSRF_HEADER) != CSRF_VALUE):
            return JSONResponse({"detail": "missing X-Requested-With header"}, status_code=403)
        return await call_next(request)

    # -- auth ---------------------------------------------------------------------
    def current_user(request: Request) -> str:
        user = svc.auth.user_for(request.cookies.get(COOKIE_NAME))
        if not user:
            raise HTTPException(401, "login required")
        return user

    def agent_auth(authorization: str | None = Header(default=None)) -> None:
        if not settings.agent_token:
            raise HTTPException(503, "agent_token is not configured on the server")
        expected = f"Bearer {settings.agent_token}"
        if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(401, "bad agent token")

    def nudge_phone() -> None:
        """Ask the phone to fetch its settings now, without waiting for it (it may be offline)."""
        if svc.control.configured:
            threading.Thread(target=svc.control.call, args=("sync",), daemon=True).start()

    user_dep = [Depends(current_user)]
    agent_dep = [Depends(agent_auth)]

    def get_msg(msg_id: str) -> dict:
        msg = svc.messages.get(msg_id)
        if not msg:
            raise HTTPException(404, "message not found")
        return msg

    # -- serializers ----------------------------------------------------------------
    def msg_json(m: dict, titles: dict[str, str] | None = None) -> dict:
        titles = titles if titles is not None else svc.clips.titles()
        text = m.get("transcript") or ""
        out = {k: m[k] for k in (
            "id", "recorded_at", "received_at", "duration_s", "kind", "greeting_id", "prompt_id",
            "reply_to", "device", "title", "notes", "people", "tags", "favorite", "replay_eligible",
            "heard", "has_mp3", "deleted_at", "transcript_state")}
        out["greeting_title"] = titles.get(m["greeting_id"] or "")
        out["prompt_title"] = titles.get(m["prompt_id"] or "")
        out["excerpt"] = (text[:240] + "…") if len(text) > 240 else text
        out["flac_url"] = f"/media/{m['id']}.flac"
        out["mp3_url"] = f"/media/{m['id']}.mp3" if m["has_mp3"] else None
        out["audio_url"] = out["mp3_url"] or out["flac_url"]
        if m.get("snippet"):
            out["snippet"] = m["snippet"]
        return out

    def clip_json(c: dict, active: dict[str, float] | None = None, answers: dict[str, int] | None = None) -> dict:
        out = {k: c.get(k) for k in ("id", "kind", "title", "text", "builtin", "enabled", "priority", "weight",
                                     "duration_s", "created_at", "updated_at", "deleted")}
        out["rules"] = c.get("rules") or {}
        out["rules_summary"] = describe_rules(out["rules"])
        out["audio_url"] = f"/api/clips/{c['id']}/audio" if c.get("filename") else None
        out["builtin_sound"] = c.get("asset") if c.get("builtin") else None
        if active is not None:
            out["active_chance"] = active.get(c["id"])
        if answers is not None:
            out["answers"] = answers.get(c["id"], 0)
        return out

    # ===================================================================
    # Health
    # ===================================================================
    @app.get("/health")
    def health() -> dict:
        with svc.db.connect() as c:
            c.execute("SELECT 1")
        return {"ok": True, "version": __version__}

    # ===================================================================
    # Agent API (the phone)
    # ===================================================================
    @app.post("/api/agent/messages", dependencies=agent_dep, status_code=201)
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

    @app.post("/api/agent/heartbeat", dependencies=agent_dep)
    async def agent_heartbeat(request: Request, x_phonehome_device: str | None = Header(default=None)) -> dict:
        try:
            status = await request.json()
        except ValueError:
            status = {}
        if not isinstance(status, dict):
            status = {}
        device = x_phonehome_device or status.get("device") or "phone"
        svc.phones.record_heartbeat(str(device)[:100], status, request.client.host if request.client else None)
        _, etag = svc.behavior.current()
        unheard = svc.messages.stats()["unheard"]
        return {"config_version": etag.strip('"'), "indicator": "on" if unheard else "off",
                "server_time": datetime.now().astimezone().isoformat(timespec="seconds")}

    @app.get("/api/agent/config", dependencies=agent_dep)
    def agent_config(if_none_match: str | None = Header(default=None)) -> Response:
        doc, etag = svc.behavior.current()
        if if_none_match == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return JSONResponse(doc, headers={"ETag": etag})

    @app.get("/api/agent/assets/{asset_id}", dependencies=agent_dep)
    def agent_asset(asset_id: str) -> FileResponse:
        path = svc.behavior.asset_by_id(asset_id)
        if not path:
            raise HTTPException(404, "unknown asset")
        return FileResponse(path)

    @app.get("/api/agent/replay/random", dependencies=agent_dep)
    def agent_replay() -> Response:
        msg = svc.messages.random_replayable()
        if not msg:
            return Response(status_code=204)
        # Always the FLAC original: the agent decodes it with libsndfile.
        return FileResponse(svc.messages.audio_path(msg, "flac"), media_type="audio/flac",
                            headers={"X-Message-Id": msg["id"], "X-Recorded-At": msg["recorded_at"]})

    # ===================================================================
    # Accounts
    # ===================================================================
    def set_session(resp: Response, token: str) -> None:
        resp.set_cookie(COOKIE_NAME, token, max_age=settings.session_days * 86400, httponly=True,
                        samesite="lax", secure=settings.cookie_secure)

    @app.get("/api/auth/me")
    def auth_me(request: Request) -> dict:
        user = svc.auth.user_for(request.cookies.get(COOKIE_NAME))
        return {"user": user, "first_run": not svc.auth.has_users(), "version": __version__}

    @app.post("/api/auth/login")
    async def auth_login(request: Request) -> Response:
        body = await request.json()
        token = await asyncio.to_thread(svc.auth.login, str(body.get("username", "")), str(body.get("password", "")))
        if not token:
            await asyncio.sleep(0.5)  # slow down password guessing
            raise HTTPException(401, "Wrong username or password.")
        resp = JSONResponse({"user": body["username"].strip()})
        set_session(resp, token)
        return resp

    @app.post("/api/auth/setup")
    async def auth_setup(request: Request) -> Response:
        # Only while no account exists at all; afterwards use the CLI.
        if svc.auth.has_users():
            raise HTTPException(403, "an account already exists")
        body = await request.json()
        username, password = str(body.get("username", "")), str(body.get("password", ""))
        try:
            svc.auth.set_user(username, password)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        resp = JSONResponse({"user": username.strip()})
        set_session(resp, svc.auth.login(username, password) or "")
        return resp

    @app.post("/api/auth/logout")
    def auth_logout(request: Request) -> Response:
        svc.auth.logout(request.cookies.get(COOKIE_NAME))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE_NAME)
        return resp

    # ===================================================================
    # Messages
    # ===================================================================
    @app.get("/api/stats", dependencies=user_dep)
    def api_stats() -> dict:
        stats = svc.messages.stats()
        now = datetime.now().astimezone()
        stats["on_this_day"] = len(svc.messages.on_this_day(now.month, now.day, before_year=now.year))
        phone = svc.phones.primary()
        stats["phone"] = {"device": phone["device"], "online": phone["online"], "age_s": phone["age_s"]} if phone else None
        return stats

    @app.get("/api/messages", dependencies=user_dep)
    def api_messages(before: float | None = None, limit: int = PAGE_SIZE, show: str = "all",
                     trashed: bool = False) -> dict:
        limit = max(1, min(limit, 200))
        msgs = svc.messages.list(limit=limit + 1, before_ts=before, trashed=trashed,
                                 unheard_only=show == "unheard", favorites_only=show == "favorites")
        more = len(msgs) > limit
        msgs = msgs[:limit]
        titles = svc.clips.titles()
        return {"messages": [msg_json(m, titles) for m in msgs],
                "next_before": msgs[-1]["recorded_ts"] if more and msgs else None}

    @app.get("/api/messages/{msg_id}", dependencies=user_dep)
    def api_message(msg_id: str) -> dict:
        m = get_msg(msg_id)
        trashed = m["deleted_at"] is not None
        out = msg_json(m)
        sidecar = svc.archive.read_sidecar(msg_id, trashed=trashed)
        out["transcript"] = svc.messages.transcript(m)
        out["flac_sha256"] = m["flac_sha256"]
        out["files"] = [{"name": f.name, "size": f.stat().st_size}
                        for f in svc.archive.message_files(msg_id, trashed=trashed)]
        out["details"] = {k: sidecar.get(k) for k in ("received_at", "device", "agent_version", "ended_by",
                                                     "recovered", "clock_synced", "levels", "edited_at")}
        reply = svc.messages.get(m["reply_to"]) if m.get("reply_to") else None
        out["reply_to_message"] = {"id": reply["id"], "title": reply["title"], "recorded_at": reply["recorded_at"]} \
            if reply else None
        with svc.db.connect() as c:
            replies = c.execute("SELECT id, title, recorded_at FROM messages WHERE reply_to = ? "
                                "AND deleted_at IS NULL ORDER BY recorded_ts", (msg_id,)).fetchall()
        out["replies"] = [dict(r) for r in replies]
        out["transcription_available"] = svc.transcriber is not None
        return out

    @app.patch("/api/messages/{msg_id}", dependencies=user_dep)
    async def api_message_patch(msg_id: str, request: Request) -> dict:
        get_msg(msg_id)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, "expected a JSON object")
        return msg_json(svc.messages.update(msg_id, body))

    @app.delete("/api/messages/{msg_id}", dependencies=user_dep)
    def api_message_delete(msg_id: str) -> dict:
        msg = get_msg(msg_id)
        if msg["deleted_at"]:
            raise HTTPException(409, "already in the trash")
        svc.messages.trash(msg_id)
        return {"ok": True, "trash_days": settings.trash_days}

    @app.post("/api/messages/{msg_id}/restore", dependencies=user_dep)
    def api_message_restore(msg_id: str) -> dict:
        msg = get_msg(msg_id)
        if not msg["deleted_at"]:
            raise HTTPException(409, "not in the trash")
        svc.messages.restore(msg_id)
        return {"ok": True}

    @app.post("/api/messages/{msg_id}/heard", dependencies=user_dep)
    async def api_message_heard(msg_id: str, request: Request) -> dict:
        get_msg(msg_id)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        svc.messages.set_heard(msg_id, bool(body.get("heard", True)) if isinstance(body, dict) else True)
        return {"ok": True}

    @app.post("/api/messages/heard-all", dependencies=user_dep)
    def api_heard_all() -> dict:
        svc.messages.mark_all_heard()
        return {"ok": True}

    @app.put("/api/messages/{msg_id}/transcript", dependencies=user_dep)
    async def api_transcript_edit(msg_id: str, request: Request) -> dict:
        get_msg(msg_id)
        body = await request.json()
        m = svc.messages.edit_transcript(msg_id, str((body or {}).get("text") or ""))
        return svc.messages.transcript(m)

    @app.post("/api/messages/{msg_id}/transcribe", dependencies=user_dep)
    def api_transcribe(msg_id: str) -> dict:
        get_msg(msg_id)
        if svc.transcriber is None:
            raise HTTPException(409, "transcription isn't available on this server")
        svc.messages.queue_transcription(msg_id)
        svc.worker.wake()
        return {"ok": True}

    # ===================================================================
    # Search and memories
    # ===================================================================
    @app.get("/api/search", dependencies=user_dep)
    def api_search(q: str = "", year: int | None = None, tag: str | None = None, person: str | None = None,
                   kind: str | None = None, favorite: bool = False, prompt: str | None = None,
                   greeting: str | None = None, limit: int = PAGE_SIZE, offset: int = 0) -> dict:
        res = svc.messages.search(q, year=year, tag=tag or None, person=person or None, kind=kind or None,
                                  favorite=favorite, prompt_id=prompt or None, greeting_id=greeting or None,
                                  limit=max(1, min(limit, 200)), offset=max(0, offset))
        titles = svc.clips.titles()
        return {"total": res["total"], "results": [msg_json(m, titles) for m in res["results"]],
                "facets": svc.messages.facets()}

    @app.get("/api/on-this-day", dependencies=user_dep)
    def api_on_this_day(date: str | None = None) -> dict:
        """``date`` is MM-DD; default today. Earlier years only when it's today."""
        now = datetime.now().astimezone()
        if date:
            try:
                month, day = (int(x) for x in date.split("-"))
                datetime(2024, month, day)
            except ValueError:
                raise HTTPException(422, "date must be MM-DD") from None
        else:
            month, day = now.month, now.day
        is_today = (month, day) == (now.month, now.day)
        msgs = svc.messages.on_this_day(month, day, before_year=now.year if is_today else None)
        titles = svc.clips.titles()
        years: dict[str, list[dict]] = {}
        for m in msgs:
            years.setdefault(m["recorded_at"][:4], []).append(msg_json(m, titles))
        return {"date": f"{month:02d}-{day:02d}", "today": is_today,
                "years": [{"year": int(y), "years_ago": now.year - int(y), "messages": v}
                          for y, v in years.items()]}

    # ===================================================================
    # Greetings and prompts
    # ===================================================================
    def answers_by_prompt() -> dict[str, int]:
        with svc.db.connect() as c:
            rows = c.execute("SELECT prompt_id, COUNT(*) FROM messages WHERE deleted_at IS NULL "
                             "AND prompt_id IS NOT NULL GROUP BY prompt_id").fetchall()
        return {r[0]: r[1] for r in rows}

    @app.get("/api/clips", dependencies=user_dep)
    def api_clips(kind: str | None = None, at: str | None = None) -> dict:
        when = datetime.now().astimezone()
        if at:
            try:
                when = datetime.fromisoformat(at)
            except ValueError:
                raise HTTPException(422, "at must be an ISO date/time") from None
        out: dict[str, Any] = {"at": when.isoformat(timespec="minutes")}
        answers = answers_by_prompt()
        for k in ([kind] if kind else list(KINDS)):
            if k not in KINDS:
                raise HTTPException(422, "unknown kind")
            clips = svc.clips.list(k)
            active = {a["id"]: a["chance"] for a in active_now(clips, when)}
            out[KINDS[k]] = [clip_json(c, active, answers if k == "prompt" else None) for c in clips]
        return out

    def _save_upload(upload: UploadFile) -> tuple[Path, Path, str, float]:
        """Store the upload, convert it, return (normalized, source, ext, seconds)."""
        name = upload.filename or "audio"
        ext = name.rsplit(".", 1)[-1] if "." in name else (upload.content_type or "audio/x").split("/")[-1]
        ext = ext.split(";")[0]
        src = svc.archive.tmp / f"clip-{secrets.token_hex(8)}.src"
        out = svc.archive.tmp / f"clip-{secrets.token_hex(8)}.flac"
        size = 0
        with open(src, "wb") as fh:
            while chunk := upload.file.read(1 << 20):
                size += len(chunk)
                if size > MAX_CLIP_UPLOAD:
                    fh.close()
                    src.unlink(missing_ok=True)
                    raise HTTPException(413, "audio files are limited to 50 MB")
                fh.write(chunk)
        try:
            seconds = normalize_clip(src, out, ffmpeg=settings.ffmpeg)
        except MediaError as exc:
            src.unlink(missing_ok=True)
            raise HTTPException(422, str(exc)) from None
        return out, src, ext, seconds

    @app.post("/api/clips", dependencies=user_dep, status_code=201)
    def api_clip_create(kind: str = Form(...), title: str = Form(""), text: str = Form(""),
                        audio: UploadFile = File(...)) -> dict:
        if kind not in KINDS:
            raise HTTPException(422, "kind must be greeting or prompt")
        out, src, ext, seconds = _save_upload(audio)
        try:
            clip = svc.clips.create(kind, title, normalized=out, source=src, source_ext=ext,
                                    duration_s=seconds, text=text)
        finally:
            out.unlink(missing_ok=True)
            src.unlink(missing_ok=True)
        return clip_json(clip)

    @app.put("/api/clips/{clip_id}/audio", dependencies=user_dep)
    def api_clip_audio_replace(clip_id: str, audio: UploadFile = File(...)) -> dict:
        if not svc.clips.get(clip_id):
            raise HTTPException(404, "no such greeting or prompt")
        out, src, ext, seconds = _save_upload(audio)
        try:
            clip = svc.clips.replace_audio(clip_id, normalized=out, source=src, source_ext=ext, duration_s=seconds)
        except KeyError:
            raise HTTPException(404, "no such greeting or prompt") from None
        except ClipError as exc:
            raise HTTPException(422, str(exc)) from None
        finally:
            out.unlink(missing_ok=True)
            src.unlink(missing_ok=True)
        return clip_json(clip)

    @app.patch("/api/clips/{clip_id}", dependencies=user_dep)
    async def api_clip_update(clip_id: str, request: Request) -> dict:
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, "expected a JSON object")
        try:
            clip = svc.clips.update(clip_id, body)
        except KeyError:
            raise HTTPException(404, "no such greeting or prompt") from None
        except ClipError as exc:
            raise HTTPException(422, str(exc)) from None
        nudge_phone()
        return clip_json(clip)

    @app.delete("/api/clips/{clip_id}", dependencies=user_dep)
    def api_clip_delete(clip_id: str) -> dict:
        try:
            svc.clips.delete(clip_id)
        except KeyError:
            raise HTTPException(404, "no such greeting or prompt") from None
        except ClipError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"ok": True}

    @app.get("/api/clips/{clip_id}/audio", dependencies=user_dep)
    def api_clip_audio(clip_id: str) -> FileResponse:
        clip = svc.clips.get(clip_id)
        path = svc.clips.audio_path(clip) if clip else None
        if not path:
            raise HTTPException(404, "no audio for this clip")
        return FileResponse(path, media_type="audio/flac")

    # ===================================================================
    # Phone, settings, system
    # ===================================================================
    @app.get("/api/phone", dependencies=user_dep)
    def api_phone() -> dict:
        return {"phones": svc.phones.phones(), "control": svc.control.configured,
                "offline_after_s": settings.offline_after_s}

    @app.get("/api/phone/logs", dependencies=user_dep)
    def api_phone_logs(lines: int = 200) -> dict:
        return {"log": svc.control.logs(max(1, min(lines, 2000)))}

    @app.post("/api/phone/{action}", dependencies=user_dep)
    async def api_phone_action(action: str, request: Request) -> dict:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        payload: dict[str, Any] = {}
        if action == "ring":
            payload = {"mode": str((body or {}).get("mode") or "auto"), "force": True}
        elif action == "tone":
            payload = {"route": str((body or {}).get("route") or "earpiece")}
        ok, detail = await asyncio.to_thread(svc.control.call, action, payload)
        if not ok:
            raise HTTPException(502, detail)
        return {"ok": True}

    @app.get("/api/settings/behavior", dependencies=user_dep)
    def api_behavior_get() -> dict:
        _, etag = svc.behavior.current()
        return {"settings": svc.behavior.load(), "version": etag.strip('"')}

    @app.put("/api/settings/behavior", dependencies=user_dep)
    async def api_behavior_put(request: Request) -> dict:
        body = await request.json()
        try:
            version = svc.behavior.save((body or {}).get("settings"))
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from None
        nudge_phone()
        return {"settings": svc.behavior.load(), "version": version}

    @app.get("/api/system", dependencies=user_dep)
    def api_system() -> dict:
        return {
            "version": __version__,
            "ffmpeg": ffmpeg_available(settings.ffmpeg),
            "transcription": {
                "enabled": svc.transcriber is not None,
                "model": getattr(svc.transcriber, "name", None),
                "installed": faster_whisper_installed(),
                "requested": settings.transcribe,
            },
            "jobs": svc.worker.summary(),
            "users": svc.auth.users(),
            "archive_dir": str(settings.archive_dir),
            "trash_days": settings.trash_days,
            "control": svc.control.configured,
        }

    @app.post("/api/system/transcribe-missing", dependencies=user_dep)
    def api_transcribe_missing() -> dict:
        if svc.transcriber is None:
            raise HTTPException(409, "transcription isn't available on this server")
        n = svc.messages.queue_transcription(None)
        svc.worker.wake()
        return {"queued": n}

    @app.get("/api/homepage")
    def api_homepage(request: Request, token: str | None = None,
                     authorization: str | None = Header(default=None)) -> dict:
        """Summary for the Homepage dashboard's customapi widget."""
        supplied = token or (authorization or "").removeprefix("Bearer ").strip()
        ok = bool(settings.homepage_token) and hmac.compare_digest(supplied.encode(),
                                                                   settings.homepage_token.encode())
        if not ok:
            current_user(request)
        stats = svc.messages.stats()
        phone = svc.phones.primary()
        last_age = time.time() - stats["last_ts"] if stats["last_ts"] else None
        return {"phone": "online" if phone and phone["online"] else "offline",
                "new": stats["unheard"], "total": stats["total"],
                "last": fmt_age(last_age) if last_age is not None else "never",
                "hours": round(stats["seconds"] / 3600, 1)}

    # ===================================================================
    # Audio
    # ===================================================================
    @app.get("/media/{msg_id}.{ext}", dependencies=user_dep)
    def media(msg_id: str, ext: str, download: bool = False) -> FileResponse:
        names = {"flac": "audio/flac", "mp3": "audio/mpeg", "txt": "text/plain; charset=utf-8",
                 "vtt": "text/vtt; charset=utf-8"}
        if ext not in names or not is_message_id(msg_id):
            raise HTTPException(404)
        msg = get_msg(msg_id)
        path = svc.messages.audio_path(msg, ext)
        if not path.exists():
            raise HTTPException(404, f"no {ext} for this message yet")
        return FileResponse(path, media_type=names[ext], filename=path.name if download else None,
                            content_disposition_type="attachment" if download else "inline")

    # ===================================================================
    # The web app (SPA). Last, so it never shadows an API route.
    # ===================================================================
    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def api_not_found(rest: str) -> None:
        raise HTTPException(404, "no such API endpoint")

    @app.get("/{path:path}", include_in_schema=False)
    def web(path: str) -> Response:
        web_dir = find_web_dir(settings)
        if web_dir is None:
            return HTMLResponse(NO_WEB_UI)
        if path:
            candidate = (web_dir / path).resolve()
            if candidate.is_file() and web_dir.resolve() in candidate.parents:
                headers = {"Cache-Control": "public, max-age=31536000, immutable"} \
                    if path.startswith("_app/immutable/") else {"Cache-Control": "no-cache"}
                return FileResponse(candidate, headers=headers)
        # Client-side routes all get the app shell.
        return FileResponse(web_dir / "index.html", headers={"Cache-Control": "no-cache"})

    return app
