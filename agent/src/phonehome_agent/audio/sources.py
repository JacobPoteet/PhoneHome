"""Fake 'microphone' inputs for the virtual audio engine."""

from __future__ import annotations

import numpy as np


class SilenceSource:
    """Room-tone: very quiet noise (default -75 dBFS)."""

    def __init__(self, level_dbfs: float = -75.0, seed: int = 1) -> None:
        self._amp = 10 ** (level_dbfs / 20)
        self._rng = np.random.default_rng(seed)

    def __call__(self, frames: int) -> np.ndarray:
        return (self._rng.standard_normal(frames) * self._amp).astype(np.float32)


class VoiceLikeSource:
    """Noise shaped into ~4 syllables per second at roughly speech level."""

    def __init__(self, sample_rate: int = 48000, level_dbfs: float = -20.0, seed: int = 2) -> None:
        self._sr = sample_rate
        self._amp = 10 ** (level_dbfs / 20) * 1.6
        self._rng = np.random.default_rng(seed)
        self._pos = 0

    def __call__(self, frames: int) -> np.ndarray:
        t = (np.arange(frames) + self._pos) / self._sr
        self._pos += frames
        envelope = np.clip(np.sin(2 * np.pi * 4 * t), 0, None) ** 0.5
        return (self._rng.standard_normal(frames) * envelope * self._amp).astype(np.float32)


class WavFileSource:
    """Plays a sound file into the 'microphone', then silence (or loops)."""

    def __init__(self, path: str, sample_rate: int, loop: bool = False) -> None:
        from .assets import load_audio_file

        self._data = load_audio_file(path, sample_rate)
        self._loop = loop
        self._pos = 0

    def __call__(self, frames: int) -> np.ndarray:
        out = np.zeros(frames, dtype=np.float32)
        n = len(self._data)
        if n == 0:
            return out
        if self._loop:
            idx = (np.arange(frames) + self._pos) % n
            self._pos = (self._pos + frames) % n
            return self._data[idx]
        chunk = self._data[self._pos:self._pos + frames]
        out[: len(chunk)] = chunk
        self._pos += len(chunk)
        return out


class SwitchableSource:
    """Lets tests (and the sim panel) switch between talking and silence."""

    def __init__(self, sample_rate: int = 48000) -> None:
        self.silence = SilenceSource()
        self.voice = VoiceLikeSource(sample_rate)
        self.talking = True

    def __call__(self, frames: int) -> np.ndarray:
        return self.voice(frames) if self.talking else self.silence(frames)
