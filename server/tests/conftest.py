from __future__ import annotations

import hashlib
import io
import json

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from phonehome_server.app import create_app
from phonehome_server.config import Settings

TOKEN = "agent-secret"


def flac_bytes(seconds: float = 2.0, sr: int = 16000, freq: float = 440.0) -> bytes:
    t = np.arange(int(seconds * sr)) / sr
    tone = (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    buf = io.BytesIO()
    sf.write(buf, tone, sr, format="FLAC", subtype="PCM_16")
    return buf.getvalue()


def make_meta(msg_id: str = "2026-11-06_193210_k3f9", audio: bytes | None = None, **extra) -> dict:
    meta = {"schema_version": 1, "id": msg_id, "recorded_at": msg_id[:10] + "T" +
            f"{msg_id[11:13]}:{msg_id[13:15]}:{msg_id[15:17]}-05:00",
            "kind": "guest", "greeting_id": "builtin:chime", "prompt_id": None, "reply_to": None,
            "device": "test-phone", "agent_version": "0.1.0", "clock_synced": True,
            "duration_s": 2.0, "ended_by": "hangup"}
    if audio is not None:
        meta["audio"] = {"format": "flac", "sha256": hashlib.sha256(audio).hexdigest()}
    meta.update(extra)
    return meta


class FakeTranscriber:
    """Stands in for faster-whisper: every message says the same thing."""

    name = "fake-whisper"

    def __init__(self, text: str = "Happy Thanksgiving everyone. We miss you, Grandma!") -> None:
        self.text = text
        self.calls = 0

    def transcribe(self, audio):
        from phonehome_server.transcribe import Segment, Transcript, Word

        self.calls += 1
        words, t = [], 0.0
        for w in self.text.split():
            words.append(Word(round(t, 2), round(t + 0.3, 2), " " + w))
            t += 0.4
        return Transcript(self.name, "en", [Segment(0.0, round(t, 2), self.text, words)], round(t, 2))


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(archive_dir=tmp_path / "archive", data_dir=tmp_path / "data", agent_token=TOKEN,
                    run_worker=False, homepage_token="hp", web_dir=str(tmp_path / "no-web"))


@pytest.fixture
def transcriber() -> FakeTranscriber:
    return FakeTranscriber()


@pytest.fixture
def app(settings, transcriber):
    return create_app(settings, transcriber=transcriber)


@pytest.fixture
def svc(app):
    return app.state.svc


CSRF = {"X-Requested-With": "phonehome"}


@pytest.fixture
def client(app):
    with TestClient(app, headers=CSRF) as c:
        yield c


@pytest.fixture
def agent_headers() -> dict:
    return {"Authorization": f"Bearer {TOKEN}", "X-PhoneHome-Device": "test-phone"}


def upload(client, headers, msg_id="2026-11-06_193210_k3f9", audio=None, meta=None):
    audio = audio if audio is not None else flac_bytes()
    meta = meta if meta is not None else make_meta(msg_id, audio)
    return client.post("/api/agent/messages", headers=headers, data={"meta": json.dumps(meta)},
                       files={"audio": (f"{msg_id}.flac", audio, "audio/flac")})


@pytest.fixture
def logged_in(client, svc):
    svc.auth.set_user("jacob", "correct horse")
    r = client.post("/api/auth/login", json={"username": "jacob", "password": "correct horse"})
    assert r.status_code == 200
    return client
