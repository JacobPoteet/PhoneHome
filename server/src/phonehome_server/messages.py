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
    return d


def fts_query(text: str) -> str:
    """Turn what someone typed into a safe FTS5 query.

    Every word must match (as a prefix, so "thanks" finds "thanksgiving");
    "quoted phrases" stay phrases. FTS5 syntax characters are neutralized.
    """
    import re

    parts = []
    for phrase, word in re.findall(r'"([^"]+)"|(\S+)', text):
        if phrase:
            tokens = re.findall(r"\w+", phrase)
            if tokens:
                parts.append('"' + " ".join(tokens) + '"')
        else:
            for token in re.findall(r"\w+", word):
                parts.append(f'"{token}"*')
    return " ".join(parts)


class MessageService:
    def __init__(self, archive: Archive, db: Database, *, transcribe: bool = False) -> None:
        self.archive = archive
        self.db = db
        self.transcribe_enabled = transcribe
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
            state = "pending" if self.transcribe_enabled else ""
            with self.db.connect() as c:
                upsert_message(c, row_from_sidecar(sidecar, has_mp3=False, transcript_state=state))
                enqueue(c, "mp3", msg_id)
                if self.transcribe_enabled:
                    enqueue(c, "transcribe", msg_id, priority=10)
                enqueue(c, "viewer", priority=30)
        log.info("stored message %s (%.1fs)", msg_id, sidecar.get("duration_s") or 0)
        return IngestResult(msg_id, digest, created=True)

    def _viewer_dirty(self) -> None:
        """Something changed: rebuild the archive's offline index.html soon."""
        with self.db.connect() as c:
            enqueue(c, "viewer", priority=30)

    # -- queries ------------------------------------------------------------
    def list(self, *, limit: int = 50, before_ts: float | None = None, trashed: bool = False,
             unheard_only: bool = False, favorites_only: bool = False) -> list[dict]:
        """Newest first. ``before_ts`` pages backwards in time."""
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
                upsert_message(c, row_from_sidecar(sidecar, has_mp3=msg["has_mp3"],
                                                   transcript=msg["transcript"],
                                                   transcript_state=msg["transcript_state"]),
                               deleted_at=msg["deleted_at"])
        self._viewer_dirty()
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
        self._viewer_dirty()
        log.info("moved %s to the trash", msg_id)

    def restore(self, msg_id: str) -> None:
        msg = self.get(msg_id)
        if not msg or not msg["deleted_at"]:
            raise KeyError(msg_id)
        with self._lock:
            self.archive.restore_from_trash(msg_id)
            with self.db.connect() as c:
                c.execute("UPDATE messages SET deleted_at = NULL WHERE id = ?", (msg_id,))
        self._viewer_dirty()
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
                enqueue(c, "viewer", priority=30)

    # -- transcripts ------------------------------------------------------------
    def _base(self, msg_id: str, trashed: bool) -> Path:
        return self.archive.path(msg_id, "flac", trashed=trashed).with_suffix("")

    def _transcript_text(self, msg_id: str, trashed: bool) -> tuple[str, str]:
        """(text to show and index, state) from the files on disk."""
        base = self._base(msg_id, trashed)
        edited = base.with_name(base.name + ".edited.txt")
        machine = base.with_name(base.name + ".txt")
        if edited.exists():
            return edited.read_text().strip(), "edited"
        if machine.exists():
            return machine.read_text().strip(), "done"
        return "", ""

    def transcript(self, msg: dict) -> dict:
        """Everything the message page needs about its transcript."""
        from .transcribe import Transcript

        trashed = msg["deleted_at"] is not None
        base = self._base(msg["id"], trashed)
        detail = base.with_name(base.name + ".transcript.json")
        edited = base.with_name(base.name + ".edited.txt")
        machine = None
        if detail.exists():
            try:
                machine = Transcript.from_dict(json.loads(detail.read_text()))
            except (ValueError, KeyError):
                log.warning("unreadable %s", detail)
        return {
            "state": msg["transcript_state"],
            "text": msg["transcript"],
            "edited": edited.read_text().strip() if edited.exists() else None,
            "machine_text": machine.text if machine else None,
            "model": machine.model if machine else None,
            "segments": machine.to_dict()["segments"] if machine else [],
        }

    def save_machine_transcript(self, msg_id: str, t) -> None:
        from .transcribe import write_transcript_files

        msg = self.get(msg_id)
        if not msg:
            return
        trashed = msg["deleted_at"] is not None
        with self._lock:
            write_transcript_files(self._base(msg_id, trashed), t)
            sidecar = self.archive.read_sidecar(msg_id, trashed=trashed)
            prev = sidecar.get("transcript") or {}
            sidecar["transcript"] = {"model": t.model, "language": t.language, "created_at": now_iso(),
                                     "edited": bool(prev.get("edited"))}
            self.archive.write_sidecar(msg_id, sidecar, trashed=trashed)
            text, state = self._transcript_text(msg_id, trashed)
            with self.db.connect() as c:
                c.execute("UPDATE messages SET transcript = ?, transcript_state = ? WHERE id = ?",
                          (text, state, msg_id))
                enqueue(c, "viewer", priority=30)

    def edit_transcript(self, msg_id: str, text: str | None) -> dict:
        """Save a human correction. Empty text drops the correction."""
        msg = self.get(msg_id)
        if not msg:
            raise KeyError(msg_id)
        trashed = msg["deleted_at"] is not None
        base = self._base(msg_id, trashed)
        edited = base.with_name(base.name + ".edited.txt")
        text = (text or "").strip()
        with self._lock:
            if text:
                from .archive import write_bytes_atomic

                write_bytes_atomic(edited, (text + "\n").encode())
            else:
                edited.unlink(missing_ok=True)
            sidecar = self.archive.read_sidecar(msg_id, trashed=trashed)
            info = dict(sidecar.get("transcript") or {})
            info["edited"] = bool(text)
            if text:
                info["edited_at"] = now_iso()
            sidecar["transcript"] = info
            self.archive.write_sidecar(msg_id, sidecar, trashed=trashed)
            shown, state = self._transcript_text(msg_id, trashed)
            if not state and msg["transcript_state"] in ("pending", "failed"):
                state = msg["transcript_state"]
            with self.db.connect() as c:
                c.execute("UPDATE messages SET transcript = ?, transcript_state = ? WHERE id = ?",
                          (shown, state, msg_id))
                enqueue(c, "viewer", priority=30)
        return self.get(msg_id)  # type: ignore[return-value]

    def set_transcript_state(self, msg_id: str, state: str) -> None:
        with self.db.connect() as c:
            c.execute("UPDATE messages SET transcript_state = ? WHERE id = ?", (state, msg_id))

    def queue_transcription(self, msg_id: str | None = None) -> int:
        """Queue one message, or (``None``) every message without a transcript."""
        with self.db.connect() as c:
            if msg_id:
                ids = [msg_id]
            else:
                ids = [r[0] for r in c.execute(
                    "SELECT id FROM messages WHERE deleted_at IS NULL AND transcript_state IN ('', 'failed')")]
            for i in ids:
                enqueue(c, "transcribe", i, priority=10)
                c.execute("UPDATE messages SET transcript_state = CASE WHEN transcript_state = 'edited' "
                          "THEN 'edited' ELSE 'pending' END WHERE id = ?", (i,))
        return len(ids)

    # -- search and memories ------------------------------------------------------
    def search(self, q: str = "", *, year: int | None = None, tag: str | None = None,
               person: str | None = None, kind: str | None = None, favorite: bool = False,
               prompt_id: str | None = None, greeting_id: str | None = None,
               limit: int = 50, offset: int = 0) -> dict:
        where = ["m.deleted_at IS NULL"]
        params: list[Any] = []
        match = fts_query(q)
        if match:
            where.append("m.rowid IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)")
            params.append(match)
        if year:
            where.append("substr(m.recorded_at, 1, 4) = ?")
            params.append(f"{int(year):04d}")
        if tag:
            where.append("EXISTS (SELECT 1 FROM json_each(m.tags) WHERE value = ?)")
            params.append(tag)
        if person:
            where.append("EXISTS (SELECT 1 FROM json_each(m.people) WHERE value = ?)")
            params.append(person)
        if kind:
            where.append("m.kind = ?")
            params.append(kind)
        if favorite:
            where.append("m.favorite = 1")
        if prompt_id:
            where.append("m.prompt_id = ?")
            params.append(prompt_id)
        if greeting_id:
            where.append("m.greeting_id = ?")
            params.append(greeting_id)
        cond = " AND ".join(where)
        with self.db.connect() as c:
            total = c.execute(f"SELECT COUNT(*) FROM messages m WHERE {cond}", params).fetchone()[0]
            if match:
                # Best matches first, with a highlighted snippet of where it matched.
                rows = c.execute(
                    f"SELECT m.*, snippet(messages_fts, -1, '\x02', '\x03', ' … ', 24) AS snippet "
                    f"FROM messages_fts JOIN messages m ON m.rowid = messages_fts.rowid "
                    f"WHERE messages_fts MATCH ? AND {cond} ORDER BY bm25(messages_fts, 5, 2, 3, 3, 1) "
                    f"LIMIT ? OFFSET ?", [match, *params, limit, offset]).fetchall()
            else:
                rows = c.execute(f"SELECT m.*, NULL AS snippet FROM messages m WHERE {cond} "
                                 f"ORDER BY m.recorded_ts DESC LIMIT ? OFFSET ?",
                                 [*params, limit, offset]).fetchall()
        return {"total": total, "results": [message_to_dict(r) for r in rows], "query": match}

    def facets(self) -> dict:
        with self.db.connect() as c:
            alive = "FROM messages WHERE deleted_at IS NULL"
            years = c.execute(f"SELECT substr(recorded_at, 1, 4) AS y, COUNT(*) {alive} "
                              "GROUP BY y ORDER BY y DESC").fetchall()
            tags = c.execute("SELECT j.value, COUNT(*) FROM messages m, json_each(m.tags) j "
                             "WHERE m.deleted_at IS NULL GROUP BY j.value ORDER BY COUNT(*) DESC, j.value").fetchall()
            people = c.execute("SELECT j.value, COUNT(*) FROM messages m, json_each(m.people) j "
                               "WHERE m.deleted_at IS NULL GROUP BY j.value ORDER BY COUNT(*) DESC, j.value").fetchall()
            kinds = c.execute(f"SELECT kind, COUNT(*) {alive} GROUP BY kind ORDER BY COUNT(*) DESC").fetchall()
        pair = lambda rows: [{"value": r[0], "count": r[1]} for r in rows]  # noqa: E731
        return {"years": pair(years), "tags": pair(tags), "people": pair(people), "kinds": pair(kinds)}

    def on_this_day(self, month: int, day: int, *, before_year: int | None = None) -> list[dict]:
        """Messages recorded on this calendar date in earlier years, newest year first."""
        md = f"{month:02d}-{day:02d}"
        params: list[Any] = [md]
        extra = ""
        if before_year:
            extra = " AND substr(recorded_at, 1, 4) < ?"
            params.append(f"{before_year:04d}")
        with self.db.connect() as c:
            rows = c.execute("SELECT * FROM messages WHERE deleted_at IS NULL AND substr(recorded_at, 6, 5) = ?"
                             f"{extra} ORDER BY recorded_ts DESC", params).fetchall()
        return [message_to_dict(r) for r in rows]

    # -- rebuild --------------------------------------------------------------
    def reindex(self) -> dict:
        """Rebuild the message index from the archive's sidecars."""
        seen: set[str] = set()
        counts = {"messages": 0, "trash": 0, "removed": 0, "mp3_queued": 0, "transcribe_queued": 0,
                  "errors": 0}
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
                        text, state = self._transcript_text(msg_id, trashed)
                        row = row_from_sidecar(meta, has_mp3=has_mp3, transcript=text, transcript_state=state)
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
                    if not state and self.transcribe_enabled and not trashed:
                        enqueue(c, "transcribe", msg_id, priority=10)
                        c.execute("UPDATE messages SET transcript_state = 'pending' WHERE id = ?", (msg_id,))
                        counts["transcribe_queued"] += 1
                    seen.add(msg_id)
                    counts["trash" if trashed else "messages"] += 1
            for msg_id in set(heard) - seen:
                c.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
                counts["removed"] += 1
            c.execute("COMMIT")
            # Row ids can change across a VACUUM; rebuilding keeps search exact.
            c.execute("INSERT INTO messages_fts (messages_fts) VALUES ('rebuild')")
        log.info("reindex: %s", counts)
        return counts


# -- jobs -------------------------------------------------------------------
def enqueue(conn: sqlite3.Connection, kind: str, message_id: str | None = None, *, priority: int = 0) -> None:
    exists = conn.execute(
        "SELECT 1 FROM jobs WHERE kind = ? AND message_id IS ? AND status IN ('pending', 'running')",
        (kind, message_id)).fetchone()
    if exists:
        return
    t = now_iso()
    conn.execute("INSERT INTO jobs (kind, message_id, priority, run_after, created_at, updated_at) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (kind, message_id, priority, time.time(), t, t))
