"""Background jobs, queued in SQLite (no Redis, no broker).

Kinds today:
  mp3    make the normalized playback copy of a message
Later phases add "transcribe". The worker runs as a thread inside the web
process by default; ``phonehome-server worker`` runs it on its own.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .config import Settings
from .db import Database, now_iso
from .media import make_playback_mp3
from .messages import MessageService

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
HOUSEKEEPING_EVERY_S = 3600


class Worker:
    def __init__(self, settings: Settings, db: Database, messages: MessageService) -> None:
        self.settings = settings
        self.db = db
        self.messages = messages
        self.handlers: dict[str, Callable[[str | None], None]] = {"mp3": self._mp3}
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_housekeeping = 0.0

    # -- control ------------------------------------------------------------
    def start(self) -> None:
        with self.db.connect() as c:  # jobs left "running" by a crash
            c.execute("UPDATE jobs SET status = 'pending' WHERE status = 'running'")
        self._thread = threading.Thread(target=self._loop, name="phonehome-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=30)

    def wake(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        log.info("job worker started")
        while not self._stop.is_set():
            try:
                self.housekeeping()
                while self.run_once() and not self._stop.is_set():
                    pass
            except Exception:
                log.exception("worker loop error")
            self._wake.wait(timeout=5)
            self._wake.clear()

    # -- jobs ---------------------------------------------------------------
    def run_once(self) -> bool:
        """Run one due job. Returns False when there was nothing to do."""
        with self.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            job = c.execute("SELECT * FROM jobs WHERE status = 'pending' AND run_after <= ? "
                            "ORDER BY id LIMIT 1", (time.time(),)).fetchone()
            if not job:
                c.execute("COMMIT")
                return False
            c.execute("UPDATE jobs SET status = 'running', attempts = attempts + 1, updated_at = ? "
                      "WHERE id = ?", (now_iso(), job["id"]))
            c.execute("COMMIT")
        handler = self.handlers.get(job["kind"])
        try:
            if handler is None:
                raise RuntimeError(f"unknown job kind {job['kind']!r}")
            handler(job["message_id"])
        except Exception as exc:
            attempts = job["attempts"] + 1
            failed = attempts >= MAX_ATTEMPTS
            log.warning("job %s %s failed (attempt %d): %s", job["kind"], job["message_id"], attempts, exc)
            with self.db.connect() as c:
                c.execute("UPDATE jobs SET status = ?, last_error = ?, run_after = ?, updated_at = ? "
                          "WHERE id = ?", ("failed" if failed else "pending", str(exc)[:1000],
                                           time.time() + 30 * 2 ** attempts, now_iso(), job["id"]))
        else:
            with self.db.connect() as c:
                c.execute("UPDATE jobs SET status = 'done', last_error = NULL, updated_at = ? WHERE id = ?",
                          (now_iso(), job["id"]))
        return True

    def run_all(self) -> int:
        n = 0
        while self.run_once():
            n += 1
        return n

    def _mp3(self, msg_id: str | None) -> None:
        msg = self.messages.get(msg_id or "")
        if not msg:
            return  # deleted meanwhile
        src = self.messages.audio_path(msg, "flac")
        dest = self.messages.audio_path(msg, "mp3")
        if not dest.exists():
            make_playback_mp3(src, dest, ffmpeg=self.settings.ffmpeg, bitrate=self.settings.mp3_bitrate)
        self.messages.attach_mp3(msg["id"])

    # -- periodic -----------------------------------------------------------
    def housekeeping(self, force: bool = False) -> None:
        if not force and time.monotonic() - self._last_housekeeping < HOUSEKEEPING_EVERY_S:
            return
        self._last_housekeeping = time.monotonic()
        self.messages.purge_trash(self.settings.trash_days)
        with self.db.connect() as c:
            c.execute("DELETE FROM jobs WHERE status = 'done' AND updated_at < datetime('now', '-30 days')")
            c.execute("DELETE FROM sessions WHERE expires_ts < ?", (time.time(),))

    def summary(self) -> dict:
        with self.db.connect() as c:
            rows = c.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall()
            failed = c.execute("SELECT kind, message_id, last_error FROM jobs WHERE status = 'failed' "
                               "ORDER BY id DESC LIMIT 5").fetchall()
        return {"counts": {r[0]: r[1] for r in rows}, "recent_failures": [dict(r) for r in failed]}
