"""Built-in sounds, synthesized so the phone works with zero uploaded assets.

All functions return mono float32 arrays in [-1, 1] at the requested sample
rate. Run ``phonehome-agent tones --out DIR`` to listen to them as WAVs.
"""

from __future__ import annotations

from typing import Callable

import numpy as np

C5, E5, G5, C6 = 523.25, 659.25, 783.99, 1046.50


def _t(duration_s: float, sr: int) -> np.ndarray:
    return np.arange(int(duration_s * sr), dtype=np.float64) / sr


def _fade(x: np.ndarray, sr: int, attack_s: float = 0.005, release_s: float = 0.02) -> np.ndarray:
    n = len(x)
    a = min(n, max(1, int(attack_s * sr)))
    r = min(n, max(1, int(release_s * sr)))
    env = np.ones(n)
    env[:a] = np.linspace(0, 1, a)
    env[n - r:] *= np.linspace(1, 0, r)
    return x * env


def _normalize(x: np.ndarray, peak: float) -> np.ndarray:
    m = float(np.max(np.abs(x))) if len(x) else 0.0
    if m > 0:
        x = x * (peak / m)
    return x.astype(np.float32)


def silence(duration_s: float, sr: int) -> np.ndarray:
    return np.zeros(int(duration_s * sr), dtype=np.float32)


def sine(freqs: float | list[float], duration_s: float, sr: int, peak: float = 0.3) -> np.ndarray:
    t = _t(duration_s, sr)
    fs = freqs if isinstance(freqs, list) else [freqs]
    x = sum(np.sin(2 * np.pi * f * t) for f in fs)
    return _normalize(_fade(np.asarray(x), sr, 0.01, 0.04), peak)


def bell_note(freq: float, duration_s: float, sr: int, decay_s: float = 0.45) -> np.ndarray:
    """A soft, slightly inharmonic bell/marimba-like note."""
    t = _t(duration_s, sr)
    partials = [(1.0, 1.0, 1.0), (2.0, 0.35, 0.6), (3.01, 0.12, 0.4), (4.2, 0.05, 0.3)]
    x = np.zeros_like(t)
    for ratio, amp, decay_scale in partials:
        x += amp * np.sin(2 * np.pi * freq * ratio * t) * np.exp(-t / (decay_s * decay_scale))
    return _fade(x, sr, 0.003, 0.05)


def _sequence(notes: list[tuple[float, float]], total_s: float, sr: int, note_s: float) -> np.ndarray:
    out = np.zeros(int(total_s * sr))
    for start_s, freq in notes:
        n = bell_note(freq, note_s, sr)
        i = int(start_s * sr)
        out[i:i + len(n)] += n[: len(out) - i]
    return out


def chime(sr: int) -> np.ndarray:
    """Default greeting: a gentle rising C-E-G."""
    x = _sequence([(0.0, C5), (0.18, E5), (0.36, G5)], 1.6, sr, 1.2)
    return _normalize(x, 0.35)


def beep(sr: int) -> np.ndarray:
    """The 'start talking now' beep. Soft, not an answering-machine shriek."""
    return sine(880.0, 0.35, sr, peak=0.3)


def warning(sr: int) -> np.ndarray:
    """Two short pips: about 30 seconds left."""
    pip = sine(660.0, 0.1, sr, peak=0.2)
    return np.concatenate([pip, silence(0.12, sr), pip])


def time_up(sr: int) -> np.ndarray:
    """Falling G-E-C: recording ended at the length limit."""
    x = _sequence([(0.0, G5), (0.2, E5), (0.4, C5)], 1.6, sr, 1.2)
    return _normalize(x, 0.3)


def hangup(sr: int) -> np.ndarray:
    """A quiet, polite 'please hang up' boop, looped with gaps."""
    return np.concatenate([sine(480.0, 0.25, sr, peak=0.12), silence(2.75, sr)])


def ring_electronic(sr: int) -> np.ndarray:
    """90s electronic tone ringer: warble, US cadence (2 s on, 4 s off)."""
    t = _t(2.0, sr)
    switch = (np.floor(t * 16) % 2).astype(bool)  # alternate 16x per second
    freq = np.where(switch, 1250.0, 1000.0)
    phase = 2 * np.pi * np.cumsum(freq) / sr
    on = _fade(np.sign(np.sin(phase)) * 0.5 + np.sin(phase) * 0.5, sr, 0.01, 0.05)
    return np.concatenate([_normalize(on, 0.5), silence(4.0, sr)])


def ring_bell(sr: int) -> np.ndarray:
    """Mechanical bell: clapper striking ~20x/second, 2 s on, 4 s off."""
    on_s = 2.0
    out = np.zeros(int(on_s * sr))
    strike = bell_note(1400.0, 0.25, sr, decay_s=0.12) + 0.6 * bell_note(1720.0, 0.25, sr, decay_s=0.1)
    for k in range(int(on_s * 20)):
        i = int(k / 20 * sr)
        seg = strike[: len(out) - i]
        out[i:i + len(seg)] += seg
    return np.concatenate([_normalize(_fade(out, sr, 0.005, 0.1), 0.5), silence(4.0, sr)])


def dial_tone(sr: int, duration_s: float = 3.0) -> np.ndarray:
    """North American dial tone (350 + 440 Hz). Reserved for future features."""
    return sine([350.0, 440.0], duration_s, sr, peak=0.2)


def test_tone(sr: int) -> np.ndarray:
    """1 kHz for one second; used by the remote 'test tone' button."""
    return sine(1000.0, 1.0, sr, peak=0.25)


BUILTIN: dict[str, Callable[[int], np.ndarray]] = {
    "chime": chime,
    "beep": beep,
    "warning": warning,
    "time_up": time_up,
    "hangup": hangup,
    "ring_electronic": ring_electronic,
    "ring_bell": ring_bell,
    "dial_tone": dial_tone,
    "test_tone": test_tone,
}
