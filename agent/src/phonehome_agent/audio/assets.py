"""Resolving sound references to sample arrays.

A reference is either ``builtin:<name>`` (synthesized, always available) or
an asset id synced from the server into ``<data_dir>/assets/``. The server
converts uploads to 48 kHz mono FLAC, so resampling here is only a fallback.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import numpy as np

from . import tones

log = logging.getLogger(__name__)


def load_audio_file(path: str | Path, sample_rate: int) -> np.ndarray:
    import soundfile as sf

    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1).astype(np.float32)
    if sr != sample_rate and len(mono):
        n_out = int(round(len(mono) * sample_rate / sr))
        src_t = np.arange(len(mono)) / sr
        dst_t = np.arange(n_out) / sample_rate
        mono = np.interp(dst_t, src_t, mono).astype(np.float32)
        log.warning("resampled %s from %d to %d Hz (server should pre-convert)", path, sr, sample_rate)
    return mono


class AssetLibrary:
    def __init__(self, assets_dir: Path, sample_rate: int,
                 filename_for: Callable[[str], str | None]) -> None:
        self.assets_dir = assets_dir
        self.sample_rate = sample_rate
        self._filename_for = filename_for
        self._cache: dict[tuple[str, float], np.ndarray] = {}

    def get(self, ref: str | None, fallback: str | None = None) -> np.ndarray | None:
        samples = self._resolve(ref) if ref else None
        if samples is None and fallback:
            samples = self._resolve(fallback)
        return samples

    def _resolve(self, ref: str) -> np.ndarray | None:
        if ref.startswith("builtin:"):
            name = ref.split(":", 1)[1]
            key = (ref, 0.0)
            if key not in self._cache:
                fn = tones.BUILTIN.get(name)
                if fn is None:
                    log.warning("unknown builtin sound %r", name)
                    return None
                self._cache[key] = fn(self.sample_rate)
            return self._cache[key]
        filename = self._filename_for(ref)
        if not filename:
            log.warning("asset %r is not in the synced manifest", ref)
            return None
        path = self.assets_dir / filename
        try:
            key = (ref, path.stat().st_mtime)
        except FileNotFoundError:
            log.warning("asset %r missing on disk (%s)", ref, path)
            return None
        if key not in self._cache:
            try:
                self._cache = {k: v for k, v in self._cache.items() if k[0] != ref}
                self._cache[key] = load_audio_file(path, self.sample_rate)
            except Exception as exc:
                log.error("could not load asset %r: %s", ref, exc)
                return None
        return self._cache[key]
