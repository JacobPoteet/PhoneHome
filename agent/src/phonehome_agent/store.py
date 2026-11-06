"""Local on-device storage.

    <data_dir>/
      incoming/   recordings in progress: <id>.pcm (raw int16) + <id>.json
      outbox/     finished, waiting for upload: <id>.flac + <id>.json
      sent/       uploaded and confirmed by the server; kept N days as a safety net
      discarded/  too short / silent; kept a few days to help tune thresholds
      assets/     greetings, prompts and sounds synced from the server
      config/     cached behavior config from the server
      state.json  small persistent state (last ring time, fired schedules...)

Raw PCM is used while recording because every byte written is valid audio:
after a power cut, whatever reached the disk is recovered on next boot.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


class LocalStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.incoming = root / "incoming"
        self.outbox = root / "outbox"
        self.sent = root / "sent"
        self.discarded = root / "discarded"
        self.assets = root / "assets"
        self.config = root / "config"
        self.replay = root / "replay"
        self.state_file = root / "state.json"
        for d in (self.incoming, self.outbox, self.sent, self.discarded, self.assets,
                  self.config, self.replay):
            d.mkdir(parents=True, exist_ok=True)

    # -- messages ---------------------------------------------------------
    def outbox_ids(self) -> list[str]:
        """Complete outbox entries (JSON is written last, so it marks completeness)."""
        return sorted(p.stem for p in self.outbox.glob("*.json")
                      if (self.outbox / f"{p.stem}.flac").exists())

    def mark_sent(self, msg_id: str) -> None:
        for ext in (".flac", ".json"):
            src = self.outbox / f"{msg_id}{ext}"
            if src.exists():
                os.replace(src, self.sent / src.name)

    def local_messages(self) -> list[tuple[str, Path, dict]]:
        """Messages still on the device (outbox + sent), oldest first."""
        out = []
        for folder in (self.sent, self.outbox):
            for meta_path in folder.glob("*.json"):
                flac = folder / f"{meta_path.stem}.flac"
                if flac.exists():
                    out.append((meta_path.stem, flac, read_json(meta_path, {})))
        return sorted(out, key=lambda t: t[0])

    def counts(self) -> dict[str, int]:
        return {
            "incoming": len(list(self.incoming.glob("*.pcm"))),
            "outbox": len(self.outbox_ids()),
            "sent": len(list(self.sent.glob("*.flac"))),
            "discarded": len(list(self.discarded.glob("*.json"))),
        }

    # -- housekeeping -----------------------------------------------------
    def prune(self, *, sent_days: float, discarded_days: float, now: float | None = None) -> int:
        now = now or time.time()
        removed = 0
        for folder, days in ((self.sent, sent_days), (self.discarded, discarded_days)):
            cutoff = now - days * 86400
            for p in folder.iterdir():
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                    removed += 1
        for p in self.replay.iterdir():  # replay cache is throwaway
            if p.is_file() and p.stat().st_mtime < now - 86400:
                p.unlink(missing_ok=True)
        return removed

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.root).free

    def make_room(self, min_free_bytes: int) -> int:
        """Delete the oldest already-uploaded copies until enough space is free.

        Only ``sent/`` and ``discarded/`` are ever touched: unsent recordings
        are never deleted to make room.
        """
        removed = 0
        candidates = sorted(
            [p for f in (self.discarded, self.sent) for p in f.iterdir() if p.is_file()],
            key=lambda p: p.stat().st_mtime,
        )
        for p in candidates:
            if self.free_bytes() >= min_free_bytes:
                break
            p.unlink(missing_ok=True)
            removed += 1
        return removed

    # -- state ------------------------------------------------------------
    def load_state(self) -> dict:
        return read_json(self.state_file, {}) or {}

    def update_state(self, **changes: Any) -> dict:
        state = self.load_state()
        state.update(changes)
        write_json_atomic(self.state_file, state)
        return state
