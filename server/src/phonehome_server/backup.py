"""Backups and integrity: the "never lose a message" plan (PLAN §5).

* integrity_check(): re-hash every recording against SHA256SUMS, and notice
  recordings that aren't listed at all. Runs weekly in the background.
* export_stream(): the whole archive as one zip, streamed (no temp file),
  with a freshly built offline viewer (index.html) inside.
* archive_stats(): what's in the archive and how big it is.

Results are kept in the small ``kv`` table so the web app can show them.
"""

from __future__ import annotations

import json
import logging
import os
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .archive import Archive, is_message_id, sha256_file
from .db import Database, now_iso
from .viewer import collect, render

log = logging.getLogger(__name__)

AUDIO = (".flac", ".mp3")
SKIP_NAMES = {".SHA256SUMS.lock"}
SKIP_DIRS = {".tmp"}


# -- key/value state -------------------------------------------------------------
def kv_get(db: Database, key: str, default=None):
    with db.connect() as c:
        row = c.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def kv_set(db: Database, key: str, value) -> None:
    with db.connect() as c:
        c.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                  (key, json.dumps(value)))


# -- integrity ---------------------------------------------------------------------
def _recordings(archive: Archive) -> Iterator[Path]:
    for base in (archive.root / "messages", archive.trash / "messages"):
        for p in base.glob("*/*/*"):
            if p.suffix in AUDIO and is_message_id(p.stem) and p.is_file():
                yield p


def integrity_check(archive: Archive) -> dict:
    """Compare every recording with SHA256SUMS. Reads every audio file once."""
    started = time.monotonic()
    sums = archive.read_sums()
    changed, missing, untracked = [], [], []
    checked = 0
    for rel, digest in sorted(sums.items()):
        path = archive.root / rel
        if not path.exists():
            missing.append(rel)
            continue
        checked += 1
        if sha256_file(path) != digest:
            changed.append(rel)
    for p in _recordings(archive):
        rel = archive.rel(p)
        if rel not in sums:
            untracked.append(rel)
    result = {
        "checked_at": now_iso(),
        "files": checked,
        "ok": not (changed or missing),
        "changed": changed[:200],
        "missing": missing[:200],
        "untracked": untracked[:200],
        "counts": {"changed": len(changed), "missing": len(missing), "untracked": len(untracked)},
        "seconds": round(time.monotonic() - started, 1),
    }
    level = logging.INFO if result["ok"] else logging.ERROR
    log.log(level, "integrity check: %d files, %d changed, %d missing, %d not listed",
            checked, len(changed), len(missing), len(untracked))
    return result


# -- stats -------------------------------------------------------------------------
def archive_stats(archive: Archive) -> dict:
    """Bytes and file counts by kind, for the Backup page."""
    kinds = {"recordings": 0, "playback copies": 0, "transcripts and details": 0, "greetings and prompts": 0,
             "trash": 0, "other": 0}
    files = 0
    total = 0
    for dirpath, dirnames, filenames in os.walk(archive.root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel_dir = Path(dirpath).relative_to(archive.root).as_posix()
        for name in filenames:
            if name in SKIP_NAMES:
                continue
            size = (Path(dirpath) / name).stat().st_size
            files += 1
            total += size
            if rel_dir.startswith(".trash"):
                kinds["trash"] += size
            elif rel_dir.startswith("messages"):
                kinds["recordings" if name.endswith(".flac") else "playback copies" if name.endswith(".mp3")
                      else "transcripts and details"] += size
            elif rel_dir.split("/")[0] in ("greetings", "prompts", "ring-sounds"):
                kinds["greetings and prompts"] += size
            else:
                kinds["other"] += size
    return {"files": files, "bytes": total, "by_kind": kinds}


# -- export ------------------------------------------------------------------------
class _Pipe:
    """A write-only file for ZipFile that hands its bytes to a generator."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.pos = 0

    def write(self, b) -> int:
        self.chunks.append(bytes(b))
        self.pos += len(b)
        return len(b)

    def tell(self) -> int:
        return self.pos

    def flush(self) -> None:
        pass

    def drain(self) -> Iterator[bytes]:
        if self.chunks:
            data = b"".join(self.chunks)
            self.chunks.clear()
            yield data


def export_files(archive: Archive, *, include_mp3: bool = True, include_trash: bool = False) -> list[tuple[str, Path]]:
    out = []
    for dirpath, dirnames, filenames in os.walk(archive.root):
        rel_dir = Path(dirpath).relative_to(archive.root)
        top = rel_dir.parts[0] if rel_dir.parts else ""
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and (include_trash or d != ".trash"))
        for name in sorted(filenames):
            if name.startswith("."):
                continue  # locks and half-written temp files
            if name == "index.html" and not rel_dir.parts:
                continue  # a fresh viewer is added instead
            if not include_mp3 and name.endswith(".mp3") and top in ("messages", ".trash"):
                continue
            out.append(((rel_dir / name).as_posix(), Path(dirpath) / name))
    return out


def export_name(now: datetime | None = None) -> str:
    return f"phonehome-archive-{(now or datetime.now()).strftime('%Y-%m-%d')}"


def export_stream(archive: Archive, *, include_mp3: bool = True, include_trash: bool = False,
                  on_done=None) -> Iterator[bytes]:
    """Yield a zip of the archive, piece by piece. Audio is stored as-is (it's
    already compressed); text is deflated. Works for archives over 4 GB (zip64)."""
    root = export_name()
    files = export_files(archive, include_mp3=include_mp3, include_trash=include_trash)
    pipe = _Pipe()
    total = 0
    with zipfile.ZipFile(pipe, "w", allowZip64=True) as zf:
        viewer = render(collect(archive)).encode()
        zf.writestr(zipfile.ZipInfo(f"{root}/index.html", time.localtime()[:6]), viewer, zipfile.ZIP_DEFLATED)
        yield from pipe.drain()
        for rel, path in files:
            info = zipfile.ZipInfo.from_file(path, arcname=f"{root}/{rel}")
            info.compress_type = zipfile.ZIP_STORED if path.suffix in AUDIO + (".png", ".jpg") else zipfile.ZIP_DEFLATED
            with open(path, "rb") as src, zf.open(info, "w", force_zip64=info.file_size > 2 ** 30) as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
                    yield from pipe.drain()
            total += info.file_size
            yield from pipe.drain()
    yield from pipe.drain()
    if on_done:
        on_done({"at": now_iso(), "files": len(files) + 1, "bytes": total + len(viewer),
                 "mp3": include_mp3, "trash": include_trash})
