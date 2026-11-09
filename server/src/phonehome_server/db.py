"""SQLite index and server state.

The ``messages`` table is an index of the archive's sidecars and can be
rebuilt at any time (``phonehome-server reindex``). The other tables hold
server-only state (users, sessions, jobs, phone heartbeats) that is fine
to lose: a Proxmox backup covers it, and nothing in it is irreplaceable.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id               TEXT PRIMARY KEY,
    recorded_at      TEXT NOT NULL,          -- ISO 8601 with offset, as recorded
    recorded_ts      REAL NOT NULL,          -- unix time, for sorting
    received_at      TEXT,
    duration_s       REAL,
    kind             TEXT NOT NULL DEFAULT 'guest',
    greeting_id      TEXT,
    prompt_id        TEXT,
    reply_to         TEXT,
    device           TEXT,
    title            TEXT NOT NULL DEFAULT '',
    notes            TEXT NOT NULL DEFAULT '',
    people           TEXT NOT NULL DEFAULT '[]',  -- JSON list
    tags             TEXT NOT NULL DEFAULT '[]',  -- JSON list
    favorite         INTEGER NOT NULL DEFAULT 0,
    replay_eligible  INTEGER NOT NULL DEFAULT 1,
    heard            INTEGER NOT NULL DEFAULT 0,  -- UI state only, not in the sidecar
    has_mp3          INTEGER NOT NULL DEFAULT 0,
    flac_sha256      TEXT,
    deleted_at       TEXT,                        -- set while in the trash
    transcript       TEXT NOT NULL DEFAULT '',    -- what's shown: the human edit, else the machine text
    transcript_state TEXT NOT NULL DEFAULT ''     -- '' | pending | done | edited | failed
);
CREATE INDEX IF NOT EXISTS messages_by_time ON messages (deleted_at, recorded_ts DESC);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    message_id  TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed
    priority    INTEGER NOT NULL DEFAULT 0,       -- lower runs first
    attempts    INTEGER NOT NULL DEFAULT 0,
    run_after   REAL NOT NULL DEFAULT 0,
    last_error  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_pending ON jobs (status, run_after);

CREATE TABLE IF NOT EXISTS users (
    username    TEXT PRIMARY KEY,
    pw_hash     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    username    TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    expires_ts  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL                    -- JSON
);

CREATE TABLE IF NOT EXISTS phone (
    device             TEXT PRIMARY KEY,
    last_heartbeat_ts  REAL NOT NULL,
    remote_addr        TEXT,
    status             TEXT NOT NULL  -- JSON, the agent's full status payload
);
"""

# Full-text search over the index. External-content FTS5: the text lives in
# `messages`, triggers keep the search index in step. Porter stemming makes
# "Thanksgiving" match "thanksgivings"; remove_diacritics folds accents.
FTS_SCHEMA = """
CREATE VIRTUAL TABLE messages_fts USING fts5(
    title, notes, people, tags, transcript,
    content='messages', content_rowid='rowid',
    tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TRIGGER messages_fts_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts (rowid, title, notes, people, tags, transcript)
    VALUES (new.rowid, new.title, new.notes, new.people, new.tags, new.transcript);
END;
CREATE TRIGGER messages_fts_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts (messages_fts, rowid, title, notes, people, tags, transcript)
    VALUES ('delete', old.rowid, old.title, old.notes, old.people, old.tags, old.transcript);
END;
CREATE TRIGGER messages_fts_au AFTER UPDATE OF title, notes, people, tags, transcript ON messages BEGIN
    INSERT INTO messages_fts (messages_fts, rowid, title, notes, people, tags, transcript)
    VALUES ('delete', old.rowid, old.title, old.notes, old.people, old.tags, old.transcript);
    INSERT INTO messages_fts (rowid, title, notes, people, tags, transcript)
    VALUES (new.rowid, new.title, new.notes, new.people, new.tags, new.transcript);
END;
"""

# Columns added after the first release: (table, column, definition).
MIGRATIONS = [
    ("messages", "transcript", "TEXT NOT NULL DEFAULT ''"),
    ("messages", "transcript_state", "TEXT NOT NULL DEFAULT ''"),
    ("jobs", "priority", "INTEGER NOT NULL DEFAULT 0"),
]

MESSAGE_FIELDS = (
    "id", "recorded_at", "recorded_ts", "received_at", "duration_s", "kind", "greeting_id",
    "prompt_id", "reply_to", "device", "title", "notes", "people", "tags", "favorite",
    "replay_eligible", "has_mp3", "flac_sha256", "transcript", "transcript_state",
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            for table, column, _ in MIGRATIONS:  # old databases: add columns first
                cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
                if cols and column not in cols:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {_}")
            c.executescript(SCHEMA)
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name = 'messages_fts'").fetchone():
                c.executescript(FTS_SCHEMA)
                c.execute("INSERT INTO messages_fts (messages_fts) VALUES ('rebuild')")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        try:
            yield conn
        finally:
            conn.close()


def row_from_sidecar(meta: dict, *, has_mp3: bool, transcript: str = "",
                     transcript_state: str = "") -> dict:
    recorded_at = meta["recorded_at"]
    ts = datetime.fromisoformat(recorded_at).timestamp()
    return {
        "id": meta["id"],
        "recorded_at": recorded_at,
        "recorded_ts": ts,
        "received_at": meta.get("received_at"),
        "duration_s": meta.get("duration_s"),
        "kind": meta.get("kind") or "guest",
        "greeting_id": meta.get("greeting_id"),
        "prompt_id": meta.get("prompt_id"),
        "reply_to": meta.get("reply_to"),
        "device": meta.get("device"),
        "title": meta.get("title") or "",
        "notes": meta.get("notes") or "",
        "people": json.dumps(meta.get("people") or []),
        "tags": json.dumps(meta.get("tags") or []),
        "favorite": int(bool(meta.get("favorite"))),
        "replay_eligible": int(meta.get("replay_eligible", True) is not False),
        "has_mp3": int(has_mp3),
        "flac_sha256": (meta.get("audio") or {}).get("sha256"),
        "transcript": transcript,
        "transcript_state": transcript_state,
    }


def upsert_message(conn: sqlite3.Connection, row: dict, *, deleted_at: str | None = None) -> None:
    cols = ", ".join(MESSAGE_FIELDS)
    marks = ", ".join(f":{f}" for f in MESSAGE_FIELDS)
    updates = ", ".join(f"{f}=excluded.{f}" for f in MESSAGE_FIELDS if f != "id")
    conn.execute(
        f"INSERT INTO messages ({cols}, deleted_at) VALUES ({marks}, :deleted_at) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}, deleted_at=excluded.deleted_at",
        {**row, "deleted_at": deleted_at},
    )
