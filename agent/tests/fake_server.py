"""An in-memory stand-in for the PhoneHome server's agent API."""

from __future__ import annotations

import hashlib
import io
import json

import httpx
import numpy as np
import soundfile as sf


class FakeServer:
    def __init__(self, token: str = "secret") -> None:
        self.token = token
        self.messages: dict[str, dict] = {}
        self.heartbeats: list[dict] = []
        self.config: dict = {"version": "srv-1"}
        self.assets: dict[str, bytes] = {}
        self.replay: tuple[str, bytes] | None = None
        self.corrupt_uploads = False
        self.down = False

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    @property
    def etag(self) -> str:
        return '"' + hashlib.sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest()[:16] + '"'

    def handle(self, req: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("server down", request=req)
        if req.headers.get("Authorization") != f"Bearer {self.token}":
            return httpx.Response(401)
        path = req.url.path
        if path == "/api/agent/messages" and req.method == "POST":
            meta, audio = _parse_multipart(req)
            digest = hashlib.sha256(audio).hexdigest()
            if self.corrupt_uploads:
                digest = "0" * 64
            self.messages[meta["id"]] = {"meta": meta, "audio": audio}
            return httpx.Response(201, json={"id": meta["id"], "sha256": digest})
        if path == "/api/agent/heartbeat":
            self.heartbeats.append(json.loads(req.content))
            return httpx.Response(200, json={"config_version": self.config.get("version"), "indicator": "on"})
        if path == "/api/agent/config":
            if req.headers.get("If-None-Match") == self.etag:
                return httpx.Response(304)
            return httpx.Response(200, json=self.config, headers={"ETag": self.etag})
        if path.startswith("/api/agent/assets/"):
            data = self.assets.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, content=data) if data is not None else httpx.Response(404)
        if path == "/api/agent/replay/random":
            if not self.replay:
                return httpx.Response(204)
            msg_id, data = self.replay
            return httpx.Response(200, content=data, headers={
                "Content-Type": "audio/flac", "X-Message-Id": msg_id, "X-Recorded-At": "2026-01-01T10:00:00-05:00"})
        return httpx.Response(404)


def _parse_multipart(req: httpx.Request) -> tuple[dict, bytes]:
    from email.parser import BytesParser
    from email.policy import default

    raw = b"Content-Type: " + req.headers["Content-Type"].encode() + b"\r\n\r\n" + req.content
    msg = BytesParser(policy=default).parsebytes(raw)
    meta, audio = None, None
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name == "meta":
            meta = json.loads(part.get_content())
        elif name == "audio":
            audio = part.get_payload(decode=True)
    return meta, audio


def flac_bytes(seconds: float = 1.0, sr: int = 16000) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, (np.random.default_rng(0).standard_normal(int(sr * seconds)) * 0.1).astype(np.float32),
             sr, format="FLAC")
    return buf.getvalue()
