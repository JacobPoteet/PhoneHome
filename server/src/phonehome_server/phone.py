"""What the server knows about the phone, and how it talks back to it.

* Behavior config: the JSON the agent syncs (greetings, prompts, ringer,
  recording limits). Its version is a content hash, so any change (a
  greeting switched on, a new limit) reaches the phone on its next heartbeat.
* Heartbeats: the agent's status, stored per device.
* Control: calls to the agent's control API (ring, test tone, sync...).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path

import httpx

from .archive import ASSET_DIRS, Archive, sha256_file, write_json_atomic
from .clips import ClipStore
from .config import Settings
from .db import Database

log = logging.getLogger(__name__)

SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,200}$")


class BehaviorConfig:
    """The phone's behavior: a settings document plus the managed clips.

    ``behavior.json`` (in the data dir) holds recording limits, ringer and
    sound settings, and any extra assets (e.g. ring sounds). Greetings and
    prompts come from the archive's clip files (clips.py) and are merged in
    when the phone asks, so the phone sees one document.
    """

    MANAGED = ("greetings", "prompts")

    def __init__(self, path: Path, archive: Archive, clips: ClipStore) -> None:
        self.path = path
        self.archive = archive
        self.clips = clips

    def load(self) -> dict:
        """The stored settings document, without its version."""
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text())
        data.pop("version", None)
        for key in self.MANAGED:  # from phase 3, before clips were managed
            data.pop(key, None)
        return data

    @staticmethod
    def version_of(data: dict) -> str:
        body = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        return "srv-" + hashlib.sha256(body).hexdigest()[:12]

    def document(self) -> dict:
        data = self.load()
        greetings, prompts, clip_assets = self.clips.for_agent()
        data["greetings"] = greetings
        data["prompts"] = prompts
        data["assets"] = {**(data.get("assets") or {}), **clip_assets}
        return data

    def current(self) -> tuple[dict, str]:
        """(document including "version", etag)"""
        data = self.document()
        version = self.version_of(data)
        return {"version": version, **data}, f'"{version}"'

    def save(self, data: dict) -> str:
        if not isinstance(data, dict):
            raise ValueError("behavior settings must be a JSON object")
        data = dict(data)
        data.pop("version", None)
        for key in self.MANAGED:
            if key in data:
                raise ValueError(f'"{key}" are managed on the Greetings page, not here')
        assets = data.get("assets") or {}
        if not isinstance(assets, dict):
            raise ValueError('"assets" must be an object of {id: {"filename": ...}}')
        for asset_id, info in assets.items():
            if not isinstance(info, dict) or not info.get("filename"):
                raise ValueError(f"asset {asset_id!r} needs a filename")
            path = self.asset_path(info["filename"])
            if path is None:
                raise ValueError(f"asset {asset_id!r}: {info['filename']!r} not found in "
                                 f"{', '.join(ASSET_DIRS)}")
            info["sha256"] = sha256_file(path)
        write_json_atomic(self.path, data)
        return self.current()[0]["version"]

    def asset_path(self, filename: str) -> Path | None:
        if not SAFE_FILENAME.match(filename):
            return None
        for d in ASSET_DIRS:
            p = self.archive.root / d / filename
            if p.is_file():
                return p
        return None

    def asset_by_id(self, asset_id: str) -> Path | None:
        info = (self.document().get("assets") or {}).get(asset_id)
        return self.asset_path(info["filename"]) if info else None


class PhoneRegistry:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def record_heartbeat(self, device: str, status: dict, remote_addr: str | None) -> None:
        with self.db.connect() as c:
            c.execute("INSERT INTO phone (device, last_heartbeat_ts, remote_addr, status) VALUES (?, ?, ?, ?) "
                      "ON CONFLICT(device) DO UPDATE SET last_heartbeat_ts = excluded.last_heartbeat_ts, "
                      "remote_addr = excluded.remote_addr, status = excluded.status",
                      (device, time.time(), remote_addr, json.dumps(status)))

    def phones(self) -> list[dict]:
        with self.db.connect() as c:
            rows = c.execute("SELECT * FROM phone ORDER BY last_heartbeat_ts DESC").fetchall()
        now = time.time()
        out = []
        for r in rows:
            age = now - r["last_heartbeat_ts"]
            out.append({"device": r["device"], "last_heartbeat_ts": r["last_heartbeat_ts"],
                        "age_s": age, "online": age < self.settings.offline_after_s,
                        "remote_addr": r["remote_addr"], "status": json.loads(r["status"])})
        return out

    def primary(self) -> dict | None:
        phones = self.phones()
        return phones[0] if phones else None


class AgentControl:
    """Calls the agent's control API (agent/src/phonehome_agent/api.py)."""

    ACTIONS = {
        "ring": ("POST", "/ring"),
        "tone": ("POST", "/test/tone"),
        "sync": ("POST", "/sync"),
        "restart": ("POST", "/restart"),
        "reboot": ("POST", "/reboot"),
    }

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = settings.agent_control_url.rstrip("/")
        self.token = settings.agent_control_token
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def call(self, action: str, body: dict | None = None) -> tuple[bool, str]:
        if action not in self.ACTIONS:
            return False, f"unknown action {action}"
        if not self.configured:
            return False, "agent_control_url is not set"
        method, path = self.ACTIONS[action]
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            with httpx.Client(base_url=self.base_url, headers=headers, timeout=10,
                              transport=self.transport) as client:
                r = client.request(method, path, json=body or {})
        except httpx.HTTPError as exc:
            return False, f"phone unreachable: {exc}"
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail") or r.text
            except ValueError:
                detail = r.text
            return False, f"The phone said no: {str(detail)[:200]}"
        return True, r.text[:500]

    def logs(self, lines: int = 200) -> str | None:
        if not self.configured:
            return None
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            with httpx.Client(base_url=self.base_url, headers=headers, timeout=5,
                              transport=self.transport) as client:
                r = client.get("/logs", params={"lines": lines})
                r.raise_for_status()
        except httpx.HTTPError as exc:
            return f"(could not fetch logs: {exc})"
        try:
            data = r.json()
        except ValueError:
            return r.text
        if isinstance(data, dict) and "lines" in data:
            return "\n".join(data["lines"])
        return json.dumps(data, indent=2)
