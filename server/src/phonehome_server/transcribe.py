"""Local speech-to-text with faster-whisper (English only, on the CPU).

Each transcribed message gets three files next to its audio:

  <id>.txt               the transcript as plain text, one line per sentence-ish segment
  <id>.vtt               WebVTT with segment timings (plays as captions in any player)
  <id>.transcript.json   segments with word timings, for click-a-word-to-jump

A human correction is saved as ``<id>.edited.txt`` and never overwritten, so
re-running a better model years from now keeps your fixes (PLAN §4.5).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    model: str
    language: str
    segments: list[Segment]
    duration_s: float = 0.0

    @property
    def text(self) -> str:
        return "\n".join(s.text.strip() for s in self.segments if s.text.strip())

    def to_dict(self) -> dict:
        return {"schema_version": 1, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict) -> "Transcript":
        segs = [Segment(s["start"], s["end"], s["text"], [Word(**w) for w in s.get("words", [])])
                for s in data.get("segments", [])]
        return cls(data.get("model", ""), data.get("language", "en"), segs, data.get("duration_s", 0.0))


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def to_vtt(t: Transcript) -> str:
    lines = ["WEBVTT", ""]
    for i, s in enumerate(t.segments, 1):
        if not s.text.strip():
            continue
        lines += [str(i), f"{_ts(s.start)} --> {_ts(s.end)}", s.text.strip(), ""]
    return "\n".join(lines)


class Transcriber(Protocol):
    name: str

    def transcribe(self, audio: Path) -> Transcript: ...


class TranscriberUnavailable(Exception):
    pass


def faster_whisper_installed() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


class FasterWhisperTranscriber:
    """Loads the model on first use and keeps it in memory."""

    def __init__(self, model: str = "large-v3-turbo", *, compute_type: str = "int8", threads: int = 0,
                 beam_size: int = 5, download_root: Path | None = None) -> None:
        self.model_name = model
        self.compute_type = compute_type
        self.threads = threads
        self.beam_size = beam_size
        self.download_root = download_root
        self._model = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return f"faster-whisper {self.model_name}"

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TranscriberUnavailable("faster-whisper is not installed") from exc
            log.info("loading whisper model %s (%s); the first time this downloads it", self.model_name,
                     self.compute_type)
            self._model = WhisperModel(self.model_name, device="cpu", compute_type=self.compute_type,
                                       cpu_threads=self.threads,
                                       download_root=str(self.download_root) if self.download_root else None)
        return self._model

    def transcribe(self, audio: Path) -> Transcript:
        with self._lock:  # one at a time; the model already uses every core
            model = self._load()
            segments, info = model.transcribe(
                str(audio), language="en", beam_size=self.beam_size, word_timestamps=True,
                vad_filter=True, condition_on_previous_text=False)
            out = []
            for s in segments:
                words = [Word(round(w.start, 2), round(w.end, 2), w.word) for w in (s.words or [])]
                out.append(Segment(round(s.start, 2), round(s.end, 2), s.text.strip(), words))
        return Transcript(self.name, "en", out, round(info.duration, 2))


def write_transcript_files(base: Path, t: Transcript) -> None:
    """``base`` is the message path without extension."""
    import json

    from .archive import write_bytes_atomic

    write_bytes_atomic(base.with_name(base.name + ".transcript.json"),
                       (json.dumps(t.to_dict(), indent=1, ensure_ascii=False) + "\n").encode())
    write_bytes_atomic(base.with_name(base.name + ".vtt"), to_vtt(t).encode())
    write_bytes_atomic(base.with_name(base.name + ".txt"), (t.text + "\n").encode())
