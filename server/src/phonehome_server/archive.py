"""The archive folder: the canonical, long-term copy of everything.

Layout (docs/PLAN.md §4.5):

  /archive/
    README.txt
    messages/YYYY/MM/<id>.flac   original recording, never modified
    messages/YYYY/MM/<id>.mp3    normalized copy for web playback
    messages/YYYY/MM/<id>.json   metadata sidecar (canonical)
    greetings/  prompts/  ring-sounds/
    .trash/messages/YYYY/MM/...  soft-deleted, purged after N days
    SHA256SUMS                   "<sha256>  <relative path>" for audio files

The database is only an index of this folder; ``phonehome-server reindex``
rebuilds it from here. Every write is atomic (temp file + fsync + rename),
so a crash never leaves a half-written file under its real name.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

ID_RE = re.compile(r"^(\d{4})-(\d{2})-\d{2}_\d{6}_[a-z0-9]{4}$")
SIDECAR_SCHEMA_VERSION = 1
ASSET_DIRS = ("greetings", "prompts", "ring-sounds")

README_TEXT = """\
PhoneHome archive
=================

This folder is a family guest book recorded on a telephone. Every message
anyone left is here as ordinary files that any computer can open, with or
without the PhoneHome software.

  messages/YEAR/MONTH/   one set of files per message, named by date and time:
      *.flac   the original recording (lossless audio; plays in VLC, Windows,
               macOS, any web browser)
      *.mp3    the same message, volume-normalized, for easy listening
      *.json   details: when it was recorded, title, notes, people, tags
      *.txt    a written transcript, when one exists (made by a computer;
               *.edited.txt is a person's correction of it)
      *.vtt    the transcript with timings (captions for video players)

  greetings/, prompts/, ring-sounds/   what the phone played to callers
  .trash/      recently deleted messages (removed for good after 30 days)
  SHA256SUMS   checksums to verify no file has been damaged. On Linux/macOS:
               "sha256sum -c SHA256SUMS" from inside this folder.

File names sort by date: 2026-11-06_193210_k3f9 was recorded on
6 November 2026 at 19:32:10 local time.
"""


class ArchiveError(Exception):
    pass


def is_message_id(value: str) -> bool:
    return bool(ID_RE.match(value))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # some network filesystems don't support directory fsync
    finally:
        os.close(fd)


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def write_json_atomic(path: Path, data: dict) -> None:
    write_bytes_atomic(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode())


def move_file_atomic(src: Path, dest: Path) -> None:
    """Move ``src`` into place at ``dest``, durably, across filesystems if needed."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp")
    try:
        os.replace(src, tmp)
    except OSError:  # different filesystem
        shutil.copyfile(src, tmp)
        src.unlink()
    with open(tmp, "rb+") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, dest)
    _fsync_dir(dest.parent)


class Archive:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.messages = root / "messages"
        self.trash = root / ".trash"
        self.sums_path = root / "SHA256SUMS"
        self.tmp = root / ".tmp"

    def init(self) -> None:
        for d in (self.messages, self.tmp, *(self.root / a for a in ASSET_DIRS)):
            d.mkdir(parents=True, exist_ok=True)
        readme = self.root / "README.txt"
        if not readme.exists():
            write_bytes_atomic(readme, README_TEXT.encode())

    # -- paths --------------------------------------------------------------
    @staticmethod
    def rel_dir(msg_id: str) -> str:
        m = ID_RE.match(msg_id)
        if not m:
            raise ArchiveError(f"not a message id: {msg_id!r}")
        return f"messages/{m.group(1)}/{m.group(2)}"

    def path(self, msg_id: str, ext: str, *, trashed: bool = False) -> Path:
        base = self.trash if trashed else self.root
        return base / self.rel_dir(msg_id) / f"{msg_id}.{ext}"

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def message_files(self, msg_id: str, *, trashed: bool = False) -> list[Path]:
        d = (self.trash if trashed else self.root) / self.rel_dir(msg_id)
        return sorted(p for p in d.glob(f"{msg_id}.*") if not p.name.startswith("."))

    # -- sidecars -----------------------------------------------------------
    def read_sidecar(self, msg_id: str, *, trashed: bool = False) -> dict:
        return json.loads(self.path(msg_id, "json", trashed=trashed).read_text())

    def write_sidecar(self, msg_id: str, meta: dict, *, trashed: bool = False) -> None:
        write_json_atomic(self.path(msg_id, "json", trashed=trashed), meta)

    def iter_sidecars(self, *, trashed: bool = False) -> Iterator[tuple[str, Path]]:
        base = (self.trash if trashed else self.root) / "messages"
        for p in sorted(base.glob("*/*/*.json")):
            if is_message_id(p.stem):
                yield p.stem, p

    # -- SHA256SUMS -----------------------------------------------------------
    @contextmanager
    def _sums_lock(self) -> Iterator[None]:
        lock = self.root / ".SHA256SUMS.lock"
        with open(lock, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def read_sums(self) -> dict[str, str]:
        """relative path -> sha256"""
        out: dict[str, str] = {}
        if self.sums_path.exists():
            for line in self.sums_path.read_text().splitlines():
                if "  " in line:
                    digest, rel = line.split("  ", 1)
                    out[rel] = digest
        return out

    def _write_sums(self, sums: dict[str, str]) -> None:
        text = "".join(f"{d}  {r}\n" for r, d in sorted(sums.items()))
        write_bytes_atomic(self.sums_path, text.encode())

    def add_sum(self, path: Path, digest: str | None = None) -> str:
        digest = digest or sha256_file(path)
        with self._sums_lock():
            with open(self.sums_path, "a") as fh:
                fh.write(f"{digest}  {self.rel(path)}\n")
                fh.flush()
                os.fsync(fh.fileno())
        return digest

    def update_sums(self, moves: dict[str, str | None]) -> None:
        """Rename (old rel -> new rel) or drop (old rel -> None) entries."""
        with self._sums_lock():
            sums = self.read_sums()
            for old, new in moves.items():
                digest = sums.pop(old, None)
                if new and digest:
                    sums[new] = digest
            self._write_sums(sums)

    # -- trash --------------------------------------------------------------
    def move_to_trash(self, msg_id: str) -> None:
        self._move_message(msg_id, to_trash=True)

    def restore_from_trash(self, msg_id: str) -> None:
        self._move_message(msg_id, to_trash=False)

    def _move_message(self, msg_id: str, *, to_trash: bool) -> None:
        files = self.message_files(msg_id, trashed=not to_trash)
        if not files:
            raise ArchiveError(f"no files for {msg_id}")
        moves: dict[str, str | None] = {}
        # Sidecar last, so a crash midway leaves the message where its sidecar is.
        dest_dir = (self.trash if to_trash else self.root) / self.rel_dir(msg_id)
        for src in sorted(files, key=lambda p: p.name == f"{msg_id}.json"):
            dest = dest_dir / src.name  # keep full names: <id>.transcript.json is not <id>.json
            move_file_atomic(src, dest)
            moves[self.rel(src)] = self.rel(dest)
        self.update_sums(moves)

    def purge_from_trash(self, msg_id: str) -> None:
        files = self.message_files(msg_id, trashed=True)
        for f in files:
            f.unlink()
        self.update_sums({self.rel(f): None for f in files})
