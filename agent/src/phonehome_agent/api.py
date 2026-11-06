"""The agent's small control API, used by the PhoneHome server (not by people).

  GET  /health                      no auth; liveness for monitoring
  GET  /status                      full status
  POST /ring      {"mode": "auto|memory|prompt", "force": false}
  POST /test/tone {"route": "earpiece|ringer"}
  POST /sync                        re-fetch config now, flush the outbox
  GET  /logs?lines=200
  POST /restart                     exit; systemd starts us again
  POST /reboot                      reboot the Pi (if allowed)

Simulation only:
  GET  /sim                         a web panel with a big "handset" button
  POST /sim/hook  {"off_hook": true}
  POST /sim/talk  {"talking": true} (virtual audio: fake voice vs silence)
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import subprocess
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .log import ring_buffer

if TYPE_CHECKING:
    from .app import Agent

log = logging.getLogger(__name__)


class RingBody(BaseModel):
    mode: str = "auto"
    force: bool = False


class ToneBody(BaseModel):
    route: str = "earpiece"


class HookBody(BaseModel):
    off_hook: bool


class TalkBody(BaseModel):
    talking: bool


def create_app(agent: "Agent") -> FastAPI:
    cfg = agent.cfg.control_api
    app = FastAPI(title="PhoneHome agent", version=agent.version, docs_url="/docs" if agent.cfg.simulate else None,
                  redoc_url=None, openapi_url="/openapi.json" if agent.cfg.simulate else None)

    def auth(authorization: str | None = Header(default=None)) -> None:
        if not cfg.token:
            return
        expected = f"Bearer {cfg.token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="bad token")

    secured = [Depends(auth)]

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "audio": agent.engine.healthy, "state": agent.controller.state.value}

    @app.get("/status", dependencies=secured)
    async def status() -> dict:
        return agent.status()

    @app.post("/ring", dependencies=secured)
    async def ring(body: RingBody) -> dict:
        ok, message = agent.controller.request_ring(mode=body.mode, source="manual", force=body.force)
        if not ok:
            raise HTTPException(status_code=409, detail=message)
        return {"ok": True, "message": message}

    @app.post("/test/tone", dependencies=secured)
    async def test_tone(body: ToneBody) -> dict:
        if body.route not in agent.engine.routes:
            raise HTTPException(status_code=400, detail="unknown route")
        if agent.controller.off_hook and body.route == "ringer":
            raise HTTPException(status_code=409, detail="phone is in use")
        samples = agent.assets.get("builtin:test_tone")
        if body.route == "ringer":
            agent.amp.on()
        handle = agent.engine.play(samples, route=body.route, gain_db=-6.0)

        async def finish() -> None:
            await handle.wait()
            if body.route == "ringer" and agent.controller.state.value != "ringing":
                agent.amp.off()

        asyncio.create_task(finish())
        return {"ok": True}

    @app.post("/sync", dependencies=secured)
    async def sync_now() -> dict:
        agent.sync.nudge_config()
        agent.sync.nudge_upload()
        return {"ok": True}

    @app.get("/logs", dependencies=secured)
    async def logs(lines: int = 200) -> dict:
        return {"lines": ring_buffer.tail(max(1, min(lines, 2000)))}

    @app.post("/restart", dependencies=secured)
    async def restart() -> dict:
        log.warning("restart requested via API")
        asyncio.get_running_loop().call_later(0.5, agent.request_stop)
        return {"ok": True}

    @app.post("/reboot", dependencies=secured)
    async def reboot() -> dict:
        if not cfg.allow_reboot or agent.cfg.simulate:
            raise HTTPException(status_code=403, detail="reboot not allowed on this device")
        log.warning("reboot requested via API")
        # The provisioning playbook installs a polkit rule allowing this user to reboot.
        subprocess.Popen(["systemctl", "reboot"], start_new_session=True)
        return {"ok": True}

    if agent.cfg.simulate and agent.cfg.sim.web_panel:
        @app.get("/sim", response_class=HTMLResponse)
        async def sim_panel() -> str:
            return SIM_PANEL

        @app.post("/sim/hook", dependencies=secured)
        async def sim_hook(body: HookBody) -> dict:
            if agent.sim_hook is None:
                raise HTTPException(status_code=400, detail="hook is not simulated")
            agent.sim_hook.set(body.off_hook)
            return {"off_hook": body.off_hook}

        @app.post("/sim/talk", dependencies=secured)
        async def sim_talk(body: TalkBody) -> dict:
            src = getattr(agent.engine, "input_source", None)
            if src is None or not hasattr(src, "talking"):
                raise HTTPException(status_code=400, detail="only available with virtual audio")
            src.talking = body.talking
            return {"talking": body.talking}

    return app


SIM_PANEL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PhoneHome Simulator</title>
<style>
  :root { --bg:#f6f3ee; --fg:#2b2622; --muted:#7a716a; --card:#fffdf9; --accent:#b5452f; --line:#e4ddd4; }
  @media (prefers-color-scheme: dark) { :root { --bg:#1d1a18; --fg:#eee6dd; --muted:#a39a91; --card:#272320; --accent:#e0735a; --line:#3a3430; } }
  * { box-sizing: border-box; }
  body { margin:0; font:16px/1.45 system-ui, sans-serif; background:var(--bg); color:var(--fg); }
  main { max-width:560px; margin:0 auto; padding:24px 16px; }
  h1 { font-size:20px; margin:0 0 4px; } p.sub { color:var(--muted); margin:0 0 20px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px; margin-bottom:14px; }
  #hook { width:100%; padding:22px; font-size:20px; border-radius:12px; border:0; cursor:pointer;
          background:var(--accent); color:#fff; font-weight:600; }
  #hook.off { background:#3d7a4f; }
  .row { display:flex; gap:8px; flex-wrap:wrap; }
  .row button { flex:1; min-width:120px; padding:10px; border-radius:10px; border:1px solid var(--line);
                background:transparent; color:var(--fg); cursor:pointer; font-size:15px; }
  .state { font-size:28px; font-weight:700; text-transform:uppercase; letter-spacing:.04em; }
  .muted { color:var(--muted); font-size:14px; }
  ul { padding-left:18px; margin:6px 0 0; } li { font-size:14px; }
  label { display:flex; gap:8px; align-items:center; font-size:15px; }
</style></head>
<body><main>
  <h1>PhoneHome simulator</h1>
  <p class="sub">The keyboard works too: space = handset, r = ring.</p>
  <div class="card"><div class="muted">State</div><div class="state" id="state">…</div>
    <div class="muted" id="detail"></div></div>
  <div class="card"><button id="hook">Pick up</button></div>
  <div class="card"><div class="row">
    <button data-ring="auto">Ring</button><button data-ring="memory">Ring → memory</button>
    <button data-ring="prompt">Ring → prompt</button></div>
    <p class="muted" id="talkrow" hidden><label><input type="checkbox" id="talk" checked> Fake caller is talking</label></p>
  </div>
  <div class="card"><div class="muted">Recent</div><ul id="history"></ul></div>
<script>
let offHook = false;
async function post(url, body) {
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body||{})});
  if (!r.ok) { const e = await r.json().catch(()=>({})); alert(e.detail || r.statusText); }
}
document.getElementById('hook').onclick = () => post('/sim/hook', {off_hook: !offHook}).then(refresh);
document.querySelectorAll('[data-ring]').forEach(b => b.onclick = () => post('/ring', {mode:b.dataset.ring, force:true}).then(refresh));
document.getElementById('talk').onchange = e => post('/sim/talk', {talking: e.target.checked});
async function refresh() {
  try {
    const s = await (await fetch('/status')).json();
    const c = s.controller; offHook = c.off_hook;
    document.getElementById('state').textContent = c.state.replaceAll('_',' ');
    const rec = c.recording ? `recording ${c.recording.seconds}s` : '';
    document.getElementById('detail').textContent = [rec, `outbox ${s.store.outbox}`, s.sync.server_configured ? (s.sync.server_reachable ? 'server ok' : 'server unreachable') : 'standalone'].filter(Boolean).join(' · ');
    const h = document.getElementById('hook'); h.textContent = offHook ? 'Hang up' : 'Pick up'; h.classList.toggle('off', offHook);
    document.getElementById('talkrow').hidden = s.audio.backend !== 'VirtualAudioEngine';
    document.getElementById('history').innerHTML = c.history.slice(0,10).map(e =>
      `<li>${e.at.slice(11,19)} — ${e.event}${e.id ? ' '+e.id : ''}${e.seconds ? ' ('+e.seconds+'s)' : ''}${e.reason && e.reason!=='ok' ? ' ['+e.reason+']' : ''}</li>`).join('');
  } catch (e) {}
}
refresh(); setInterval(refresh, 500);
</script></main></body></html>
"""
