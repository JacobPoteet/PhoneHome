"""Greetings and prompts: the audio the phone plays, and when.

Stored in the archive like everything else, one pair of files per clip:

  greetings/<id>.json         title, rules, priority, weight (canonical)
  greetings/<id>.flac         normalized 48 kHz mono audio the phone plays
  greetings/<id>.source.*     the file as uploaded or recorded, untouched
  prompts/...                 the same for prompts (the questions asked when it rings)
  .trash/greetings/...        deleted clips (kept; they're tiny and part of the history)

The built-in chime is a clip too (``builtin-chime``, no audio file), so it
can be weighted into a rotation or switched off like any other greeting.

Selection is the same on the server and the phone (agent/.../behavior.py):
keep the enabled clips whose rules match now, keep the highest priority,
then a weighted random pick. The server uses it to show "playing now".
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from .archive import Archive, move_file_atomic, sha256_file, write_json_atomic
from .db import now_iso

KINDS = {"greeting": "greetings", "prompt": "prompts"}
CLIP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
BUILTIN_CHIME = {
    "schema_version": 1, "id": "builtin-chime", "kind": "greeting", "title": "Chime (default)",
    "text": "", "asset": "builtin:chime", "builtin": True, "enabled": True, "priority": 0,
    "weight": 1.0, "rules": {},
}
# Older agents and phase 3 messages recorded the chime by its asset name.
ALIASES = {"builtin:chime": "builtin-chime"}


class ClipError(ValueError):
    pass


# -- rules --------------------------------------------------------------------
def _md(value: str) -> tuple[int, int]:
    parts = value.split("-")
    return int(parts[-2]), int(parts[-1])


def _hm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def validate_rules(rules: Any) -> dict:
    if rules in (None, ""):
        return {}
    if not isinstance(rules, dict):
        raise ClipError("rules must be an object")
    out: dict[str, Any] = {}
    dates = []
    for d in rules.get("dates") or []:
        try:
            start, end = str(d["start"]).strip(), str(d["end"]).strip()
            for v in (start, end):
                if len(v) == 10:
                    date.fromisoformat(v)
                elif len(v) == 5:
                    m, dd = _md(v)
                    date(2024, m, dd)  # leap year, so 02-29 is allowed
                else:
                    raise ValueError
            if (len(start) == 10) != (len(end) == 10):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ClipError("each date range needs start and end as MM-DD (every year) "
                            "or YYYY-MM-DD (once)") from None
        dates.append({"start": start, "end": end})
    if dates:
        out["dates"] = dates
    weekdays = sorted({int(w) for w in rules.get("weekdays") or []})
    if any(w < 0 or w > 6 for w in weekdays):
        raise ClipError("weekdays are 0 (Monday) to 6 (Sunday)")
    if weekdays and len(weekdays) < 7:
        out["weekdays"] = weekdays
    tf, tt = rules.get("time_from") or None, rules.get("time_to") or None
    if bool(tf) != bool(tt):
        raise ClipError("set both time_from and time_to, or neither")
    if tf and tt:
        try:
            _hm(tf), _hm(tt)
        except ValueError:
            raise ClipError("times are HH:MM") from None
        out["time_from"], out["time_to"] = tf, tt
    return out


def rules_match(rules: dict, now: datetime) -> bool:
    dates = rules.get("dates") or []
    if dates:
        d, ok = now.date(), False
        for r in dates:
            if len(r["start"]) == 10:
                ok = date.fromisoformat(r["start"]) <= d <= date.fromisoformat(r["end"])
            else:
                s, e, cur = _md(r["start"]), _md(r["end"]), (d.month, d.day)
                ok = s <= cur <= e if s <= e else (cur >= s or cur <= e)
            if ok:
                break
        if not ok:
            return False
    weekdays = rules.get("weekdays") or []
    if weekdays and now.weekday() not in weekdays:
        return False
    if rules.get("time_from") and rules.get("time_to"):
        t, a, b = now.time(), _hm(rules["time_from"]), _hm(rules["time_to"])
        if not (a <= t < b if a <= b else (t >= a or t < b)):
            return False
    return True


def active_now(clips: list[dict], now: datetime) -> list[dict]:
    """The clips that could play right now, each with its chance of being picked."""
    cands = [c for c in clips if c.get("enabled", True) and float(c.get("weight", 1)) > 0
             and rules_match(c.get("rules") or {}, now)]
    if not cands:
        return []
    top = max(int(c.get("priority", 0)) for c in cands)
    best = [c for c in cands if int(c.get("priority", 0)) == top]
    total = sum(float(c.get("weight", 1)) for c in best)
    return [{"id": c["id"], "title": c["title"], "chance": float(c.get("weight", 1)) / total} for c in best]


def describe_rules(rules: dict) -> str:
    """A short human summary, e.g. "Dec 1 – Dec 31 · weekends · 17:00–21:00"."""
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    parts = []
    for r in rules.get("dates") or []:
        def fmt(v: str) -> str:
            m, d = _md(v)
            return f"{months[m - 1]} {d}" + (f", {v[:4]}" if len(v) == 10 else "")
        parts.append(f"{fmt(r['start'])} – {fmt(r['end'])}")
    wd = rules.get("weekdays") or []
    if wd:
        names = "Mon Tue Wed Thu Fri Sat Sun".split()
        parts.append("weekends" if wd == [5, 6] else "weekdays" if wd == [0, 1, 2, 3, 4]
                     else ", ".join(names[i] for i in wd))
    if rules.get("time_from"):
        parts.append(f"{rules['time_from']}–{rules['time_to']}")
    return " · ".join(parts) or "Always"


# -- storage ------------------------------------------------------------------
def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    return f"{s or 'clip'}-{secrets.token_hex(2)}"


class ClipStore:
    def __init__(self, archive: Archive) -> None:
        self.archive = archive

    def init(self) -> None:
        path = self._dir("greeting") / "builtin-chime.json"
        if not path.exists() and not (self.archive.trash / "greetings" / "builtin-chime.json").exists():
            write_json_atomic(path, {**BUILTIN_CHIME, "created_at": now_iso()})

    def _dir(self, kind: str, trashed: bool = False) -> Path:
        if kind not in KINDS:
            raise ClipError(f"kind must be one of {', '.join(KINDS)}")
        return (self.archive.trash if trashed else self.archive.root) / KINDS[kind]

    def list(self, kind: str | None = None, *, include_trashed: bool = False) -> list[dict]:
        out = []
        for k in ([kind] if kind else list(KINDS)):
            for trashed in ((False, True) if include_trashed else (False,)):
                for p in sorted(self._dir(k, trashed).glob("*.json")):
                    if not CLIP_ID_RE.match(p.stem):
                        continue
                    try:
                        clip = json.loads(p.read_text())
                    except ValueError:
                        continue
                    clip["id"], clip["kind"] = p.stem, k
                    clip["deleted"] = trashed
                    out.append(clip)
        # Highest priority first, then alphabetical, built-ins last within a priority.
        out.sort(key=lambda c: (-int(c.get("priority", 0)), bool(c.get("builtin")), c.get("title", "").lower()))
        return out

    def get(self, clip_id: str) -> dict | None:
        clip_id = ALIASES.get(clip_id, clip_id)
        if not CLIP_ID_RE.match(clip_id or ""):
            return None
        for c in self.list(include_trashed=True):
            if c["id"] == clip_id:
                return c
        return None

    def titles(self) -> dict[str, str]:
        t = {c["id"]: c.get("title") or c["id"] for c in self.list(include_trashed=True)}
        for alias, real in ALIASES.items():
            if real in t:
                t[alias] = t[real]
        return t

    def audio_path(self, clip: dict) -> Path | None:
        if not clip.get("filename"):
            return None
        p = self._dir(clip["kind"], clip.get("deleted", False)) / clip["filename"]
        return p if p.exists() else None

    def _save(self, clip: dict) -> dict:
        data = {k: v for k, v in clip.items() if k != "deleted"}
        write_json_atomic(self._dir(clip["kind"]) / f"{clip['id']}.json", data)
        return self.get(clip["id"])  # type: ignore[return-value]

    def create(self, kind: str, title: str, *, normalized: Path, source: Path, source_ext: str,
               duration_s: float, text: str = "") -> dict:
        title = title.strip() or ("New greeting" if kind == "greeting" else "New prompt")
        d = self._dir(kind)
        d.mkdir(parents=True, exist_ok=True)
        clip_id = _slug(title)
        while (d / f"{clip_id}.json").exists():
            clip_id = _slug(title)
        flac = d / f"{clip_id}.flac"
        move_file_atomic(normalized, flac)
        ext = re.sub(r"[^a-z0-9]", "", source_ext.lower())[:8] or "bin"
        move_file_atomic(source, d / f"{clip_id}.source.{ext}")
        clip = {
            "schema_version": 1, "id": clip_id, "kind": kind, "title": title, "text": text.strip(),
            "asset": clip_id, "filename": flac.name, "sha256": sha256_file(flac),
            "duration_s": round(duration_s, 2), "builtin": False,
            # New clips start switched off, so nothing changes on the phone until you're ready.
            "enabled": False, "priority": 0, "weight": 1.0, "rules": {},
            "created_at": now_iso(), "updated_at": now_iso(),
        }
        return self._save(clip)

    def replace_audio(self, clip_id: str, *, normalized: Path, source: Path, source_ext: str,
                      duration_s: float) -> dict:
        clip = self._live(clip_id)
        if clip.get("builtin"):
            raise ClipError("the built-in chime has no audio file to replace")
        d = self._dir(clip["kind"])
        for old in d.glob(f"{clip_id}.source.*"):
            old.unlink()
        flac = d / f"{clip_id}.flac"
        move_file_atomic(normalized, flac)
        ext = re.sub(r"[^a-z0-9]", "", source_ext.lower())[:8] or "bin"
        move_file_atomic(source, d / f"{clip_id}.source.{ext}")
        clip.update({"filename": flac.name, "sha256": sha256_file(flac), "duration_s": round(duration_s, 2),
                     "updated_at": now_iso()})
        return self._save(clip)

    def update(self, clip_id: str, changes: dict) -> dict:
        clip = self._live(clip_id)
        if "title" in changes:
            title = str(changes["title"] or "").strip()
            if not title:
                raise ClipError("a title is required")
            clip["title"] = title[:200]
        if "text" in changes:
            clip["text"] = str(changes["text"] or "").strip()[:2000]
        if "enabled" in changes:
            clip["enabled"] = bool(changes["enabled"])
        if "priority" in changes:
            try:
                clip["priority"] = max(-100, min(100, int(changes["priority"])))
            except (TypeError, ValueError):
                raise ClipError("priority must be a whole number") from None
        if "weight" in changes:
            try:
                w = float(changes["weight"])
            except (TypeError, ValueError):
                raise ClipError("weight must be a number") from None
            if not 0 <= w <= 1000:
                raise ClipError("weight must be between 0 and 1000")
            clip["weight"] = w
        if "rules" in changes:
            clip["rules"] = validate_rules(changes["rules"])
        clip["updated_at"] = now_iso()
        return self._save(clip)

    def delete(self, clip_id: str) -> None:
        clip = self._live(clip_id)
        if clip.get("builtin"):
            raise ClipError("the built-in chime can be switched off but not deleted")
        src, dest = self._dir(clip["kind"]), self._dir(clip["kind"], trashed=True)
        files = sorted(src.glob(f"{clip_id}.*"), key=lambda p: p.suffix == ".json")
        for f in files:
            move_file_atomic(f, dest / f.name)

    def _live(self, clip_id: str) -> dict:
        clip = self.get(clip_id)
        if not clip or clip.get("deleted"):
            raise KeyError(clip_id)
        return clip

    # -- what the phone gets ------------------------------------------------------
    def for_agent(self) -> tuple[list[dict], list[dict], dict[str, dict]]:
        """(greetings, prompts, assets) in the agent's behavior.json shape."""
        greetings, prompts, assets = [], [], {}
        for c in self.list():
            if not c.get("builtin") and not c.get("filename"):
                continue
            entry = {"id": c["id"], "asset": c.get("asset") or c["id"], "title": c.get("title", ""),
                     "priority": int(c.get("priority", 0)), "weight": float(c.get("weight", 1)),
                     "enabled": bool(c.get("enabled", True)), "rules": c.get("rules") or {}}
            (greetings if c["kind"] == "greeting" else prompts).append(entry)
            if c.get("filename"):
                assets[c["id"]] = {"filename": c["filename"], "sha256": c.get("sha256", ""),
                                   "title": c.get("title", "")}
        return greetings, prompts, assets
