"""Level analysis used to decide whether a recording contains anything."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass
class LevelStats:
    duration_s: float
    peak_dbfs: float
    rms_dbfs: float
    voiced_s: float  # time spent above the silence threshold

    def to_dict(self) -> dict:
        return {k: round(v, 2) for k, v in asdict(self).items()}


def _db(x: np.ndarray | float) -> np.ndarray | float:
    return 20 * np.log10(np.maximum(x, 1e-10))


def analyze(pcm: np.ndarray, sample_rate: int, silence_threshold_dbfs: float,
            frame_ms: int = 50) -> LevelStats:
    """``pcm`` is int16 or float in [-1, 1]."""
    x = pcm.astype(np.float64)
    if pcm.dtype == np.int16:
        x /= 32768.0
    n = len(x)
    if n == 0:
        return LevelStats(0.0, -100.0, -100.0, 0.0)
    frame = max(1, int(sample_rate * frame_ms / 1000))
    usable = (n // frame) * frame
    voiced_frames = 0
    if usable:
        frames = x[:usable].reshape(-1, frame)
        frame_db = _db(np.sqrt(np.mean(frames**2, axis=1)))
        voiced_frames = int(np.sum(frame_db > silence_threshold_dbfs))
    return LevelStats(
        duration_s=n / sample_rate,
        peak_dbfs=float(_db(np.max(np.abs(x)))),
        rms_dbfs=float(_db(np.sqrt(np.mean(x**2)))),
        voiced_s=voiced_frames * frame / sample_rate,
    )
