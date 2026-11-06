"""Logging setup with an in-memory ring buffer the control API can serve."""

from __future__ import annotations

import collections
import logging
import sys
import threading

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


class RingBufferHandler(logging.Handler):
    def __init__(self, capacity: int = 2000) -> None:
        super().__init__()
        self._lines: collections.deque[str] = collections.deque(maxlen=capacity)
        self._lock_ = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:  # pragma: no cover - logging must never raise
            return
        with self._lock_:
            self._lines.append(line)

    def tail(self, n: int = 200) -> list[str]:
        with self._lock_:
            lines = list(self._lines)
        return lines[-n:] if n > 0 else lines


ring_buffer = RingBufferHandler()


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    fmt = logging.Formatter(FORMAT)
    if not any(isinstance(h, logging.StreamHandler) and h.stream is sys.stderr for h in root.handlers):
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        root.addHandler(stream)
    ring_buffer.setFormatter(fmt)
    if ring_buffer not in root.handlers:
        root.addHandler(ring_buffer)
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
