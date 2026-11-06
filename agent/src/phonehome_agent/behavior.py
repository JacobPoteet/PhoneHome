"""Behavior configuration: what the phone does, set from the web app.

The server owns this config and the agent caches the last copy it received,
so the phone keeps working (with the last known greetings and settings)
when the server is unreachable. Unknown keys are ignored so an older agent
keeps working with a newer server.

Greetings and prompts are chosen by rules:
  1. keep the items whose rules match "now" (date range, weekdays, time)
  2. keep only those with the highest priority
  3. weighted random pick among them
That single mechanism covers a default greeting, holiday greetings, a
birthday-week greeting and random rotations.
"""

from __future__ import annotations

import logging
import random
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Sequence, TypeVar

from .store import read_json, write_json_atomic

log = logging.getLogger(__name__)
T = TypeVar("T")


def _from_dict(cls: type[T], data: dict[str, Any] | None) -> T:
    data = data or {}
    known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    return cls(**{k: v for k, v in data.items() if k in known})


# ---------------------------------------------------------------------------
@dataclass
class DateRange:
    start: str  # "MM-DD" (every year) or "YYYY-MM-DD" (once)
    end: str

    def matches(self, d: date) -> bool:
        if len(self.start) == 10 and len(self.end) == 10:
            return date.fromisoformat(self.start) <= d <= date.fromisoformat(self.end)
        s, e = _md(self.start), _md(self.end)
        cur = (d.month, d.day)
        return s <= cur <= e if s <= e else (cur >= s or cur <= e)  # wraps New Year


def _md(value: str) -> tuple[int, int]:
    parts = value.split("-")
    return int(parts[-2]), int(parts[-1])


def _hm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


@dataclass
class Rules:
    dates: list[DateRange] = field(default_factory=list)
    weekdays: list[int] = field(default_factory=list)  # 0 = Monday
    time_from: str | None = None  # "HH:MM"
    time_to: str | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "Rules":
        data = data or {}
        r = _from_dict(cls, data)
        r.dates = [DateRange(**d) if isinstance(d, dict) else d for d in data.get("dates", [])]
        return r

    def matches(self, now: datetime) -> bool:
        if self.dates and not any(r.matches(now.date()) for r in self.dates):
            return False
        if self.weekdays and now.weekday() not in self.weekdays:
            return False
        if self.time_from and self.time_to:
            t, a, b = now.time(), _hm(self.time_from), _hm(self.time_to)
            if not (a <= t < b if a <= b else (t >= a or t < b)):
                return False
        return True


@dataclass
class Clip:
    """A greeting or a prompt."""

    id: str
    asset: str  # "builtin:chime" or a synced asset id
    title: str = ""
    priority: int = 0
    weight: float = 1.0
    enabled: bool = True
    rules: Rules = field(default_factory=Rules)

    @classmethod
    def from_dict(cls, data: dict) -> "Clip":
        c = _from_dict(cls, data)
        c.rules = Rules.from_dict(data.get("rules"))
        return c


def select(clips: Sequence[Clip], now: datetime, rng: random.Random) -> Clip | None:
    candidates = [c for c in clips if c.enabled and c.weight > 0 and c.rules.matches(now)]
    if not candidates:
        return None
    top = max(c.priority for c in candidates)
    best = [c for c in candidates if c.priority == top]
    return rng.choices(best, weights=[c.weight for c in best], k=1)[0]


# ---------------------------------------------------------------------------
@dataclass
class RecordingSettings:
    max_duration_s: float = 180.0
    warning_before_end_s: float = 30.0
    min_duration_s: float = 2.0
    min_voiced_s: float = 0.5
    silence_threshold_dbfs: float = -50.0
    pickup_settle_ms: int = 300
    hangup_tone_timeout_s: float = 60.0


@dataclass
class Sounds:
    beep: str = "builtin:beep"
    warning: str = "builtin:warning"
    time_up: str = "builtin:time_up"
    hangup: str = "builtin:hangup"
    memory_intro: str = "builtin:chime"
    reply_intro: str = "builtin:chime"


@dataclass
class RandomRing:
    enabled: bool = False
    per_week: float = 2.0
    window_start: str = "10:00"
    window_end: str = "20:00"
    weekdays: list[int] = field(default_factory=lambda: list(range(7)))
    min_gap_hours: float = 24.0


@dataclass
class ScheduledRing:
    id: str
    at: str | None = None      # "YYYY-MM-DDTHH:MM" local, one-shot
    yearly: str | None = None  # "MM-DD HH:MM" local, every year
    mode: str = "auto"         # auto | memory | prompt


@dataclass
class RingerSettings:
    enabled: bool = True
    sound: str = "builtin:ring_electronic"
    volume_db: float = -12.0
    low_switch_offset_db: float = -10.0  # when the phone's ringer switch is on "low"
    ring_timeout_s: float = 30.0
    # How an answered ring plays out: weights for "memory" vs "prompt".
    answer_modes: dict[str, float] = field(default_factory=lambda: {"memory": 0.5, "prompt": 0.5})
    record_after_memory: bool = True
    random: RandomRing = field(default_factory=RandomRing)
    scheduled: list[ScheduledRing] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> "RingerSettings":
        data = data or {}
        r = _from_dict(cls, data)
        r.random = _from_dict(RandomRing, data.get("random"))
        r.scheduled = [_from_dict(ScheduledRing, s) for s in data.get("scheduled", [])]
        return r


@dataclass
class AssetInfo:
    filename: str
    sha256: str = ""
    title: str = ""


@dataclass
class Behavior:
    version: str = "builtin"
    recording: RecordingSettings = field(default_factory=RecordingSettings)
    sounds: Sounds = field(default_factory=Sounds)
    greetings: list[Clip] = field(default_factory=lambda: [
        Clip(id="builtin:chime", asset="builtin:chime", title="Chime (default)")])
    prompts: list[Clip] = field(default_factory=list)
    ringer: RingerSettings = field(default_factory=RingerSettings)
    assets: dict[str, AssetInfo] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Behavior":
        data = data or {}
        b = cls(version=str(data.get("version", "builtin")))
        b.recording = _from_dict(RecordingSettings, data.get("recording"))
        b.sounds = _from_dict(Sounds, data.get("sounds"))
        if "greetings" in data:
            b.greetings = [Clip.from_dict(g) for g in data["greetings"]]
        b.prompts = [Clip.from_dict(p) for p in data.get("prompts", [])]
        b.ringer = RingerSettings.from_dict(data.get("ringer"))
        b.assets = {k: _from_dict(AssetInfo, v) for k, v in (data.get("assets") or {}).items()}
        return b

    def to_dict(self) -> dict:
        return asdict(self)


class BehaviorStore:
    """Holds the current behavior and persists the server's copy to disk."""

    def __init__(self, config_dir: Path) -> None:
        self.path = config_dir / "behavior.json"
        raw = read_json(self.path)
        try:
            self.current = Behavior.from_dict(raw) if raw else Behavior()
        except Exception:
            log.exception("cached behavior config unreadable; using built-in defaults")
            self.current = Behavior()
        self.raw: dict = raw or {}

    def update(self, raw: dict) -> Behavior:
        new = Behavior.from_dict(raw)  # validate before persisting
        write_json_atomic(self.path, raw)
        self.current, self.raw = new, raw
        log.info("behavior config updated to version %s", new.version)
        return new

    def asset_filename(self, asset_id: str) -> str | None:
        info = self.current.assets.get(asset_id)
        return info.filename if info else None
