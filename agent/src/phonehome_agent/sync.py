"""Talking to the PhoneHome server.

Agent → server API (the server implements these; see docs/PLAN.md §4.7):

  POST /api/agent/messages        multipart: "meta" (JSON) + "audio" (FLAC)
                                  → 201 {"id": ..., "sha256": ...}
                                  The agent deletes nothing until the
                                  returned sha256 matches its own.
  POST /api/agent/heartbeat       JSON status → {"config_version": ..., "indicator": "on"|"off"}
  GET  /api/agent/config          behavior JSON (ETag / If-None-Match)
  GET  /api/agent/assets/{id}     audio file bytes
  GET  /api/agent/replay/random   audio bytes + X-Message-Id / X-Recorded-At headers, 204 if none

Every request carries ``Authorization: Bearer <token>`` and ``X-PhoneHome-Device``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from pathlib import Path
from typing import Callable

import httpx

from . import __version__, timeutil
from .audio.assets import AssetLibrary, load_audio_file
from .behavior import Behavior, BehaviorStore
from .controller import Replay
from .store import LocalStore, read_json

log = logging.getLogger(__name__)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class ServerClient:
    def __init__(self, base_url: str, token: str, device_name: str, *, timeout: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        headers = {"X-PhoneHome-Device": device_name, "User-Agent": f"phonehome-agent/{__version__}"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers,
                                       timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def upload_message(self, meta: dict, flac_path: Path) -> bool:
        local_sha = meta.get("audio", {}).get("sha256") or sha256_file(flac_path)
        with open(flac_path, "rb") as fh:
            r = await self._http.post(
                "/api/agent/messages",
                data={"meta": json.dumps(meta)},
                files={"audio": (flac_path.name, fh, "audio/flac")},
            )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"upload rejected: HTTP {r.status_code} {r.text[:200]}")
        remote_sha = r.json().get("sha256")
        if remote_sha != local_sha:
            raise RuntimeError(f"checksum mismatch after upload (local {local_sha[:12]}, server {str(remote_sha)[:12]})")
        return True

    async def heartbeat(self, status: dict) -> dict:
        r = await self._http.post("/api/agent/heartbeat", json=status)
        r.raise_for_status()
        return r.json() if r.content else {}

    async def fetch_config(self, etag: str | None) -> tuple[dict | None, str | None]:
        headers = {"If-None-Match": etag} if etag else {}
        r = await self._http.get("/api/agent/config", headers=headers)
        if r.status_code == 304:
            return None, etag
        r.raise_for_status()
        return r.json(), r.headers.get("ETag")

    async def fetch_asset(self, asset_id: str, dest: Path, sha256: str = "") -> None:
        r = await self._http.get(f"/api/agent/assets/{asset_id}")
        r.raise_for_status()
        if sha256 and hashlib.sha256(r.content).hexdigest() != sha256:
            raise RuntimeError(f"asset {asset_id} checksum mismatch")
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(r.content)
        tmp.replace(dest)

    async def fetch_replay(self, dest_dir: Path) -> tuple[Path, str, str | None] | None:
        r = await self._http.get("/api/agent/replay/random")
        if r.status_code == 204:
            return None
        r.raise_for_status()
        msg_id = r.headers.get("X-Message-Id", "unknown")
        ext = ".mp3" if "mpeg" in r.headers.get("Content-Type", "") else ".flac"
        dest = dest_dir / f"{msg_id}{ext}"
        dest.write_bytes(r.content)
        return dest, msg_id, r.headers.get("X-Recorded-At")


class SyncService:
    """Background loops: upload outbox, heartbeat, config + asset sync."""

    def __init__(self, client: ServerClient | None, store: LocalStore, behavior: BehaviorStore,
                 assets: AssetLibrary, status_provider: Callable[[], dict], *,
                 heartbeat_interval_s: float = 60.0, config_sync_interval_s: float = 300.0,
                 retry_max_s: float = 300.0, on_indicator: Callable[[str], None] | None = None) -> None:
        self.client = client
        self.store = store
        self.behavior = behavior
        self.assets = assets
        self.status_provider = status_provider
        self.heartbeat_interval_s = heartbeat_interval_s
        self.config_sync_interval_s = config_sync_interval_s
        self.retry_max_s = retry_max_s
        self.on_indicator = on_indicator
        self._upload_nudge = asyncio.Event()
        self._config_nudge = asyncio.Event()
        self.server_reachable: bool | None = None
        self.last_upload_error: str | None = None
        self.last_heartbeat_at: str | None = None
        self.uploaded = 0

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def nudge_upload(self) -> None:
        self._upload_nudge.set()

    def nudge_config(self) -> None:
        self._config_nudge.set()

    def status(self) -> dict:
        return {
            "server_configured": self.enabled,
            "server_reachable": self.server_reachable,
            "last_heartbeat_at": self.last_heartbeat_at,
            "last_upload_error": self.last_upload_error,
            "uploaded_this_boot": self.uploaded,
            "config_version": self.behavior.current.version,
        }

    def tasks(self) -> list:
        if not self.client:
            return []
        return [self._uploader(), self._heartbeat(), self._config_loop()]

    # -- uploads ----------------------------------------------------------
    async def upload_pending(self) -> int:
        """Upload everything in the outbox once. Returns the number uploaded."""
        assert self.client
        done = 0
        for msg_id in self.store.outbox_ids():
            meta = read_json(self.store.outbox / f"{msg_id}.json", {})
            await self.client.upload_message(meta, self.store.outbox / f"{msg_id}.flac")
            self.store.mark_sent(msg_id)
            done += 1
            self.uploaded += 1
            log.info("uploaded %s", msg_id)
        return done

    async def _uploader(self) -> None:
        delay = 5.0
        while True:
            try:
                await self.upload_pending()
                self.last_upload_error = None
                delay = 5.0
                wait = None  # nothing to do until nudged (or the periodic re-check)
            except Exception as exc:
                self.last_upload_error = str(exc)
                log.warning("upload failed (retry in %.0fs): %s", delay, exc)
                wait = delay
                delay = min(delay * 2, self.retry_max_s)
            self._upload_nudge.clear()
            try:
                await asyncio.wait_for(self._upload_nudge.wait(), wait or 600)
            except asyncio.TimeoutError:
                pass

    # -- heartbeat + config -------------------------------------------------
    async def _heartbeat(self) -> None:
        assert self.client
        while True:
            try:
                reply = await self.client.heartbeat(self.status_provider())
                self.server_reachable = True
                self.last_heartbeat_at = timeutil.now().isoformat(timespec="seconds")
                remote_version = reply.get("config_version")
                if remote_version and remote_version != self.behavior.current.version:
                    self.nudge_config()
                if self.on_indicator and reply.get("indicator") in ("on", "off"):
                    self.on_indicator(reply["indicator"])
                if self.store.outbox_ids():
                    self.nudge_upload()  # server is back: flush anything waiting
            except Exception as exc:
                if self.server_reachable is not False:
                    log.warning("server unreachable: %s", exc)
                self.server_reachable = False
            await asyncio.sleep(self.heartbeat_interval_s * random.uniform(0.9, 1.1))

    async def sync_config(self) -> bool:
        """Fetch behavior config and any missing assets. True if it changed."""
        assert self.client
        etag = self.store.load_state().get("config_etag")
        raw, new_etag = await self.client.fetch_config(etag)
        if raw is None:
            return False
        incoming = Behavior.from_dict(raw)  # validate before touching anything
        for asset_id, info in incoming.assets.items():
            dest = self.store.assets / info.filename
            if dest.exists() and (not info.sha256 or sha256_file(dest) == info.sha256):
                continue
            log.info("downloading asset %s", asset_id)
            await self.client.fetch_asset(asset_id, dest, info.sha256)
        self.behavior.update(raw)
        self.store.update_state(config_etag=new_etag)
        return True

    async def _config_loop(self) -> None:
        while True:
            try:
                await self.sync_config()
            except Exception as exc:
                log.warning("config sync failed: %s", exc)
            self._config_nudge.clear()
            try:
                await asyncio.wait_for(self._config_nudge.wait(), self.config_sync_interval_s)
            except asyncio.TimeoutError:
                pass


class ReplaySource:
    """Finds an old message for memory lane: from the server, else locally."""

    def __init__(self, client: ServerClient | None, store: LocalStore, sample_rate: int,
                 rng: random.Random | None = None) -> None:
        self.client = client
        self.store = store
        self.sample_rate = sample_rate
        self.rng = rng or random.Random()

    async def __call__(self) -> Replay | None:
        if self.client:
            try:
                got = await asyncio.wait_for(self.client.fetch_replay(self.store.replay), 8.0)
                if got:
                    path, msg_id, recorded_at = got
                    samples = await asyncio.to_thread(load_audio_file, path, self.sample_rate)
                    return Replay(msg_id, recorded_at, samples)
                return None  # the server says there is nothing eligible
            except Exception as exc:
                log.warning("replay from server failed, trying local copies: %s", exc)
        local = [m for m in self.store.local_messages() if m[2].get("replay_eligible", True)]
        if not local:
            return None
        msg_id, flac, meta = self.rng.choice(local)
        samples = await asyncio.to_thread(load_audio_file, flac, self.sample_rate)
        return Replay(msg_id, meta.get("recorded_at"), samples)
