"""Server settings.

Read from an optional TOML file (``PHONEHOME_CONFIG``, default
``/etc/phonehome/server.toml`` if it exists), then overridden by environment
variables named ``PHONEHOME_<SETTING>`` (upper case), which is what Docker
Compose uses. Example: ``PHONEHOME_AGENT_TOKEN=...``.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("/etc/phonehome/server.toml")


@dataclass
class Settings:
    # Where the canonical files live (the NAS share). Plain files only.
    archive_dir: Path = Path("/archive")
    # Server-private state: the SQLite index, sessions, behavior config.
    data_dir: Path = Path("/data")

    # Shared secret the phone sends as "Authorization: Bearer ...".
    agent_token: str = ""
    # The agent's control API (for "Ring now", test tone, restart...).
    # Empty means the buttons on the Phone page are hidden.
    agent_control_url: str = ""
    agent_control_token: str = ""
    # The phone counts as offline after this long without a heartbeat.
    offline_after_s: int = 180

    # Optional token for /api/homepage. Empty: the endpoint needs a login.
    homepage_token: str = ""

    # Session cookie. Set cookie_secure when served over HTTPS.
    cookie_secure: bool = False
    session_days: int = 365

    # Transcription (faster-whisper, English only, on the CPU). Turned off
    # automatically when faster-whisper isn't installed.
    transcribe: bool = True
    whisper_model: str = "large-v3-turbo"   # or "medium.en", "small.en" for slower machines
    whisper_compute_type: str = "int8"
    whisper_threads: int = 0                # 0 = all cores
    whisper_beam_size: int = 5

    # The built web app (web/ → npm run build). Empty: look next to the package,
    # then in the repository's web/build.
    web_dir: str = ""

    # Media processing.
    ffmpeg: str = "ffmpeg"
    mp3_bitrate: str = "96k"
    # Run the background job worker inside the web process.
    run_worker: bool = True
    trash_days: int = 30

    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "phonehome.sqlite3"

    @property
    def behavior_path(self) -> Path:
        return self.data_dir / "behavior.json"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"


def _coerce(value: str, current: object) -> object:
    if isinstance(current, bool):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        return int(value)
    if isinstance(current, Path):
        return Path(value)
    return value


def load_settings(path: str | Path | None = None, env: dict[str, str] | None = None,
                  **overrides: object) -> Settings:
    env = dict(os.environ) if env is None else env
    s = Settings()
    cfg_path = Path(path or env.get("PHONEHOME_CONFIG") or DEFAULT_CONFIG_PATH)
    raw: dict = {}
    if cfg_path.exists():
        raw = tomllib.loads(cfg_path.read_text())
    for f in fields(Settings):
        current = getattr(s, f.name)
        if f.name in raw:
            val = raw[f.name]
            setattr(s, f.name, Path(val) if isinstance(current, Path) else val)
        env_val = env.get(f"PHONEHOME_{f.name.upper()}")
        if env_val is not None:
            setattr(s, f.name, _coerce(env_val, current))
    for k, v in overrides.items():
        setattr(s, k, v)
    return s
