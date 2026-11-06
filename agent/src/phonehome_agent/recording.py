"""Recording messages crash-safely and turning them into outbox entries.

Flow:
  start()    write incoming/<id>.json (metadata so far), attach to the mic
  feed()     audio thread -> queue -> writer thread appends int16 to <id>.pcm
             (fsync every ~2 s, so a power cut loses at most ~2 s)
  stop()     detach from the mic, drain the queue
  finalize() validate (long enough? anything said?) and encode FLAC into
             outbox/, or move to discarded/
  recover()  on boot: finalize any leftovers in incoming/ (power loss)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from . import __version__, timeutil
from .audio.analysis import analyze
from .audio.engine import AudioEngine, db_to_gain
from .behavior import RecordingSettings
from .ids import new_message_id
from .store import LocalStore, read_json, write_json_atomic

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass
class RecordingResult:
    id: str
    saved: bool
    reason: str  # "ok" or why it was discarded
    duration_s: float
    meta: dict
    flac_path: Path | None


class ActiveRecording:
    def __init__(self, rec_id: str, meta: dict, pcm_path: Path, sample_rate: int,
                 max_frames: int, warn_frames: int, gain: float,
                 loop: asyncio.AbstractEventLoop) -> None:
        self.id = rec_id
        self.meta = meta
        self.pcm_path = pcm_path
        self.sample_rate = sample_rate
        self.max_frames = max_frames
        self.warn_frames = warn_frames
        self.gain = gain
        self.frames = 0
        self.max_reached = asyncio.Event()
        self.warn_reached = asyncio.Event()
        self._warned = False
        self._loop = loop
        self._closed = False
        self._q: queue.SimpleQueue[np.ndarray | None] = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._writer, name=f"rec-{rec_id}", daemon=True)
        self._thread.start()

    @property
    def seconds(self) -> float:
        return self.frames / self.sample_rate

    def feed(self, block: np.ndarray) -> None:
        """Called from the audio thread for every input block."""
        if self._closed:
            return
        remaining = self.max_frames - self.frames
        if remaining <= 0:
            return
        if len(block) > remaining:
            block = block[:remaining]
        pcm = (np.clip(block * self.gain, -1.0, 1.0) * 32767.0).astype("<i2")
        self._q.put(pcm)
        self.frames += len(pcm)
        if not self._warned and self.warn_frames and self.frames >= self.warn_frames:
            self._warned = True
            self._loop.call_soon_threadsafe(self.warn_reached.set)
        if self.frames >= self.max_frames:
            self._loop.call_soon_threadsafe(self.max_reached.set)

    def _writer(self) -> None:
        last_sync = time.monotonic()
        with open(self.pcm_path, "ab") as fh:
            while True:
                item = self._q.get()
                if item is None:
                    break
                fh.write(item.tobytes())
                if time.monotonic() - last_sync > 2.0:
                    fh.flush()
                    os.fsync(fh.fileno())
                    last_sync = time.monotonic()
            fh.flush()
            os.fsync(fh.fileno())

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._q.put(None)
        self._thread.join(timeout=10)


class Recorder:
    def __init__(self, engine: AudioEngine, store: LocalStore, *, device_name: str,
                 mic_gain_db: float = 0.0) -> None:
        self.engine = engine
        self.store = store
        self.device_name = device_name
        self.mic_gain = db_to_gain(mic_gain_db)

    @property
    def sample_rate(self) -> int:
        return self.engine.sample_rate

    def start(self, *, kind: str, settings: RecordingSettings, extra: dict | None = None,
              when: datetime | None = None) -> ActiveRecording:
        when = when or timeutil.now()
        rec_id = new_message_id(when)
        meta = {
            "schema_version": SCHEMA_VERSION,
            "id": rec_id,
            "recorded_at": when.isoformat(timespec="seconds"),
            "kind": kind,
            "greeting_id": None,
            "prompt_id": None,
            "reply_to": None,
            "device": self.device_name,
            "agent_version": __version__,
            "clock_synced": timeutil.clock_synced(),
            "sample_rate": self.sample_rate,
            **(extra or {}),
        }
        write_json_atomic(self.store.incoming / f"{rec_id}.json", meta)
        pcm_path = self.store.incoming / f"{rec_id}.pcm"
        pcm_path.touch()
        max_frames = int(settings.max_duration_s * self.sample_rate)
        warn_s = settings.max_duration_s - settings.warning_before_end_s
        warn_frames = int(warn_s * self.sample_rate) if settings.warning_before_end_s > 0 and warn_s > 0 else 0
        active = ActiveRecording(rec_id, meta, pcm_path, self.sample_rate, max_frames, warn_frames,
                                 self.mic_gain, asyncio.get_running_loop())
        self.engine.add_input_listener(active.feed)
        log.info("recording %s started (%s)", rec_id, kind)
        return active

    def stop(self, active: ActiveRecording) -> None:
        self.engine.remove_input_listener(active.feed)
        active.close()

    def finalize(self, rec_id: str, settings: RecordingSettings, *, ended_by: str,
                 recovered: bool = False) -> RecordingResult:
        """Validate and encode. Blocking; run it in a thread."""
        import soundfile as sf

        meta_in = self.store.incoming / f"{rec_id}.json"
        pcm_path = self.store.incoming / f"{rec_id}.pcm"
        meta = read_json(meta_in, {}) or {"schema_version": SCHEMA_VERSION, "id": rec_id}
        sr = int(meta.get("sample_rate") or self.sample_rate)
        raw = pcm_path.read_bytes() if pcm_path.exists() else b""
        pcm = np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2")
        stats = analyze(pcm, sr, settings.silence_threshold_dbfs)
        meta.update({
            "duration_s": round(stats.duration_s, 2),
            "ended_by": ended_by,
            "recovered": recovered,
            "levels": stats.to_dict(),
        })

        reason = "ok"
        if stats.duration_s < settings.min_duration_s:
            reason = "too_short"
        elif stats.voiced_s < settings.min_voiced_s:
            reason = "silent"

        if reason == "ok":
            dest_dir, saved = self.store.outbox, True
        else:
            dest_dir, saved = self.store.discarded, False
            meta["discard_reason"] = reason

        flac_path = dest_dir / f"{rec_id}.flac"
        tmp = dest_dir / f"{rec_id}.flac.tmp"
        if len(pcm):
            sf.write(str(tmp), pcm, sr, format="FLAC", subtype="PCM_16")
            with open(tmp, "rb+") as fh:
                os.fsync(fh.fileno())
            os.replace(tmp, flac_path)
            digest = hashlib.sha256(flac_path.read_bytes()).hexdigest()
            meta["audio"] = {"format": "flac", "sample_rate": sr, "channels": 1, "bits": 16,
                             "bytes": flac_path.stat().st_size, "sha256": digest}
        else:
            flac_path = None
        # The JSON is written last: its presence marks the entry complete.
        write_json_atomic(dest_dir / f"{rec_id}.json", meta)
        meta_in.unlink(missing_ok=True)
        pcm_path.unlink(missing_ok=True)

        if saved:
            log.info("recording %s saved (%.1fs, %.1fs voiced)", rec_id, stats.duration_s, stats.voiced_s)
        else:
            log.info("recording %s discarded: %s (%.1fs, %.1fs voiced)", rec_id, reason,
                     stats.duration_s, stats.voiced_s)
        return RecordingResult(rec_id, saved, reason, stats.duration_s, meta,
                               flac_path if saved else None)

    def recover(self, settings: RecordingSettings) -> list[RecordingResult]:
        results = []
        ids = {p.stem for p in self.store.incoming.glob("*.pcm")} | \
              {p.stem for p in self.store.incoming.glob("*.json")}
        for rec_id in sorted(ids):
            log.warning("recovering interrupted recording %s", rec_id)
            try:
                results.append(self.finalize(rec_id, settings, ended_by="recovered", recovered=True))
            except Exception:
                log.exception("could not recover %s", rec_id)
        return results
