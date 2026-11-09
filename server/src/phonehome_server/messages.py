"""Messages: ingest from the phone, edit, trash, and rebuild the index.

Every change goes to the archive first (the sidecar is canonical) and to
the SQLite index second, so the index can always be rebuilt from files.
"""

from __future__ import annotations

import json
import logging
import random
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import __version__
from .archive import (SIDECAR_SCHEMA_VERSION, Archive, is_message_id,
                      move_file_atomic, sha256_file)
from .db import Database, now_iso, row_from_sidecar, upsert_message

log = logging.getLogger(__name__)

# Fields the web app may edit. Everything else in a sidecar is a record of
# what happened and is never changed after ingest.
EDITABLE = {"title": str, "notes": str, "people": list, "tags": list,
            "favorite": bool, "replay_eligible": bool}


class IngestError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class IngestResult:
    id: str
    sha256: str
    created: bool  # False when the phone re-sent a message we already had


def _clean_list(values: Any) -> list[str]:
    if isinstance(values, str):
        values = values.split(",")
    out: list[str] = []
    for v in values or []:
        v = str(v).strip()
        if v and v not in out:
            out.append(v)
    return out


def message_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["people"] = json.loads(d["people"] or "[]")
    d["tags"] = json.loads(d["tags"] or "[]")
    for k in ("favorite", "replay_eligible", "heard", "has_mp3"):
        d[k] = bool(d[k])
    d["recorded"] = datetime.fromisoformat(d["recorded_at"])
    return d


class MessageService:
    def __init__(self, archive: Archive, db: Database) -> None:
        self.archive = archive
        self.db = db
        self._lock = threading.Lock()

    # -- ingest ---------------------------------------------------------------
    def ingest(self, meta: dict, upload: Path) -> IngestResult:
        """Store an uploaded recording. ``upload`` is a temp file we may move."""
        import soundfile as sf

        msg_id = str(meta.get("id", ""))
        if not is_message_id(msg_id):
            raise IngestError(422, "invalid or missing message id")
        try:
            datetime.fromisoformat(str(meta.get("recorded_at")))
        except (TypeError, ValueError):
            raise IngestError(422, "invalid or missing recorded_at") from None

        digest = sha256_file(upload)
        claimed = (meta.get("audio") or {}).get("sha256")
        if claimed and claimed != digest:
            # Corrupted in transit. Refuse it; the phone keeps it and retries.
            raise IngestError(422, "audio checksum does not match meta.audio.sha256")
        try:
            info = sf.info(str(upload))
        except Exception as exc:
            raise IngestError(422, f"audio is not readable: {exc}") from None
        if info.format != "FLAC":
            raise IngestError(422, f"audio must be FLAC, got {info.format}")

        with self._lock:
            flac = self.archive.path(msg_id, "flac")
            trashed = self.archive.path(msg_id, "flac", trashed=True)
            for existing in (flac, trashed):
                if existing.exists():
                    if sha256_file(existing) == digest:
                        log.info("duplicate upload of %s ignored", msg_id)
                        return IngestResult(msg_id, digest, created=False)
                    raise IngestError(409, "a different recording with this id already exists")

            sidecar = dict(meta)
            sidecar.update({
                "schema_version": SIDECAR_SCHEMA_VERSION,
                "received_at": now_iso(),
                "server_version": __version__,
            })
            sidecar.setdefault("duration_s", round(info.frames / info.samplerate, 2))
            sidecar.setdefault("kind", "guest")
            for key, default in (("title", ""), ("notes", ""), ("people", []), ("tags", []),
                                 ("favorite", False), ("replay_eligible", True), ("transcript", None)):
                sidecar.setdefault(key, default)
            audio = dict(sidecar.get("audio") or {})
            audio.update({"format": "flac", "sample_rate": info.samplerate, "channels": info.channels,
                          "bytes": upload.stat().st_size, "sha256": digest})
            sidecar["audio"] = audio

            # FLAC first, then the checksum line, then the sidecar. The sidecar's
            # presence is what makes the message "exist" for reindex.
            move_file_atomic(upload, flac)
            self.archive.add_sum(flac, digest)
            self.archive.write_sidecar(msg_id, sidecar)
            with self.db.connect() as c:
                upsert_message(c, row_from_sidecar(sidecar, has_mp3=False))
                enqueue(c, "mp3", msg_id)
        log.info("stored message %s (%.1fs)", msg_id, sidecar.get("duration_s") or 0)
        return IngestResult(msg_id, digest, created=True)

    # -- queries ------------------------------------------------------------
    def list(self, *, limit: int = 50, before_ts: float | None = None, trashed: bool = False,
             unheard_only: bool = False, favorites_only: bool = False) -> list[dict]:
        where = ["deleted_at IS NOT NULL" if trashed else "deleted_at IS NULL"]
        params: list[Any] = []
        if before_ts is not None:
            where.append("recorded_ts < ?")
            params.append(before_ts)
        if unheard_only:
            where.append("heard = 0")
        if favorites_only:
            where.append("favorite = 1")
        params.append(limit)
        with self.db.connect() as c:
            rows = c.execute(f"SELECT * FROM messages WHERE {' AND '.join(where)} "
                             "ORDER BY recorded_ts DESC LIMIT ?", params).fetchall()
        return [message_to_dict(r) for r in rows]

    def get(self, msg_id: str) -> dict | None:
        if not is_message_id(msg_id):
            return None
        with self.db.connect() as c:
            row = c.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
        return message_to_dict(row) if row else None

    def stats(self) -> dict:
        with self.db.connect() as c:
            r = c.execute(
                "SELECT COUNT(*) AS total, COALESCE(SUM(heard = 0), 0) AS unheard, "
                "COALESCE(SUM(duration_s), 0) AS seconds, MAX(recorded_ts) AS last_ts "
                "FROM messages WHERE deleted_at IS NULL").fetchone()
            trash = c.execute("SELECT COUNT(*) FROM messages WHERE deleted_at IS NOT NULL").fetchone()[0]
        return {"total": r["total"], "unheard": r["unheard"], "seconds": r["seconds"],
                "last_ts": r["last_ts"], "trash": trash}

    def random_replayable(self, rng: random.Random | None = None) -> dict | None:
        with self.db.connect() as c:
            ids = [r[0] for r in c.execute(
                "SELECT id FROM messages WHERE deleted_at IS NULL AND replay_eligible = 1")]
        if not ids:
            return None
        return self.get((rng or random).choice(ids))

    def audio_path(self, msg: dict, ext: str) -> Path:
        return self.archive.path(msg["id"], ext, trashed=msg["deleted_at"] is not None)

    # -- edits --------------------------------------------------------------
    def update(self, msg_id: str, changes: dict) -> dict:
        msg = self.get(msg_id)
        if not msg:
            raise KeyError(msg_id)
        clean: dict[str, Any] = {}
        for key, value in changes.items():
            if key not in EDITABLE:
                continue
            kind = EDITABLE[key]
            if kind is list:
                clean[key] = _clean_list(value)
            elif kind is bool:
                clean[key] = bool(value)
            else:
                clean[key] = str(value or "").strip()
        if not clean:
            return msg
        trashed = msg["deleted_at"] is not None
        with self._lock:
            sidecar = self.archive.read_sidecar(msg_id, trashed=trashed)
            sidecar.update(clean)
            sidecar["edited_at"] = now_iso()
            self.archive.write_sidecar(msg_id, sidecar, trashed=trashed)
            with self.db.connect() as c:
                upsert_message(c, row_from_sidecar(sidecar, has_mp3=msg["has_mp3"]),
                               deleted_at=msg["deleted_at"])
        return self.get(msg_id)  # type: ignore[return-value]

    def set_heard(self, msg_id: str, heard: bool = True) -> None:
        with self.db.connect() as c:
            c.execute("UPDATE messages SET heard = ? WHERE id = ?", (int(heard), msg_id))

    def mark_all_heard(self) -> None:
        with self.db.connect() as c:
            c.execute("UPDATE messages SET heard = 1 WHERE deleted_at IS NULL")

    # -- trash --------------------------------------------------------------
    def trash(self, msg_id: str) -> None:
        msg = self.get(msg_id)
        if not msg or msg["deleted_at"]:
            raise KeyError(msg_id)
        with self._lock:
            self.archive.move_to_trash(msg_id)
            with self.db.connect() as c:
                c.execute("UPDATE messages SET deleted_at = ? WHERE id = ?", (now_iso(), msg_id))
        log.info("moved %s to the trash", msg_id)

    def restore(self, msg_id: str) -> None:
        msg = self.get(msg_id)
        if not msg or not msg["deleted_at"]:
            raise KeyError(msg_id)
        with self._lock:
            self.archive.restore_from_trash(msg_id)
            with self.db.connect() as c:
                c.execute("UPDATE messages SET deleted_at = NULL WHERE id = ?", (msg_id,))
        log.info("restored %s from the trash", msg_id)

    def purge_trash(self, older_than_days: int) -> list[str]:
        cutoff = datetime.now().astimezone() - timedelta(days=older_than_days)
        purged = []
        for msg in self.list(trashed=True, limit=100000):
            if datetime.fromisoformat(msg["deleted_at"]) < cutoff:
                with self._lock:
                    self.archive.purge_from_trash(msg["id"])
                    with self.db.connect() as c:
                        c.execute("DELETE FROM messages WHERE id = ?", (msg["id"],))
                purged.append(msg["id"])
                log.info("purged %s from the trash", msg["id"])
        return purged

    # -- media --------------------------------------------------------------
    def attach_mp3(self, msg_id: str) -> None:
        msg = self.get(msg_id)
        if not msg:
            return
        trashed = msg["deleted_at"] is not None
        mp3 = self.archive.path(msg_id, "mp3", trashed=trashed)
        with self._lock:
            self.archive.add_sum(mp3)
            sidecar = self.archive.read_sidecar(msg_id, trashed=trashed)
            sidecar["playback"] = {"format": "mp3", "file": mp3.name, "normalized": True}
            self.archive.write_sidecar(msg_id, sidecar, trashed=trashed)
            with self.db.connect() as c:
                c.execute("UPDATE messages SET has_mp3 = 1 WHERE id = ?", (msg_id,))

    # -- rebuild --------------------------------------------------------------
    def reindex(self) -> dict:
        """Rebuild the message index from the archive's sidecars."""
        seen: set[str] = set()
        counts = {"messages": 0, "trash": 0, "removed": 0, "mp3_queued": 0, "errors": 0}
        with self.db.connect() as c:
            heard = {r[0]: r[1] for r in c.execute("SELECT id, heard FROM messages")}
            deleted = {r[0]: r[1] for r in c.execute("SELECT id, deleted_at FROM messages")}
            c.execute("BEGIN")
            for trashed in (False, True):
                for msg_id, path in self.archive.iter_sidecars(trashed=trashed):
                    try:
                        meta = json.loads(path.read_text())
                        meta["id"] = msg_id
                        has_mp3 = self.archive.path(msg_id, "mp3", trashed=trashed).exists()
                        row = row_from_sidecar(meta, has_mp3=has_mp3)
                    except Exception:
                        log.exception("unreadable sidecar %s", path)
                        counts["errors"] += 1
                        continue
                    deleted_at = (deleted.get(msg_id) or now_iso()) if trashed else None
                    upsert_message(c, row, deleted_at=deleted_at)
                    c.execute("UPDATE messages SET heard = ? WHERE id = ?",
                              (heard.get(msg_id, 1), msg_id))
                    if not has_mp3 and self.archive.path(msg_id, "flac", trashed=trashed).exists():
                        enqueue(c, "mp3", msg_id)
                        counts["mp3_queued"] += 1
                    seen.add(msg_id)
                    counts["trash" if trashed else "messages"] += 1
            for msg_id in set(heard) - seen:
                c.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
                counts["removed"] += 1
            c.execute("COMMIT")
        log.info("reindex: %s", counts)
        return counts


# -- jobs -------------------------------------------------------------------
def enqueue(conn: sqlite3.Connection, kind: str, message_id: str | None = None) -> None:
    exists = conn.execute(
        "SELECT 1 FROM jobs WHERE kind = ? AND message_id IS ? AND status IN ('pending', 'running')",
        (kind, message_id)).fetchone()
    if exists:
        return
    t = now_iso()
    conn.execute("INSERT INTO jobs (kind, message_id, run_after, created_at, updated_at) "
                 "VALUES (?, ?, ?, ?, ?)", (kind, message_id, time.time(), t, t))
