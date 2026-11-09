"""Audio processing with ffmpeg."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# Trim leading silence (the beep gap), then normalize loudness so every
# message plays at about the same volume. Mono speech at 96 kbit/s is
# indistinguishable from the original for this purpose.
PLAYBACK_FILTER = (
    "silenceremove=start_periods=1:start_duration=0.2:start_threshold=-50dB,"
    "loudnorm=I=-16:TP=-1.5:LRA=11"
)


class MediaError(Exception):
    pass


def ffmpeg_available(ffmpeg: str = "ffmpeg") -> bool:
    return shutil.which(ffmpeg) is not None


def make_playback_mp3(src: Path, dest: Path, *, ffmpeg: str = "ffmpeg", bitrate: str = "96k") -> None:
    """Encode ``src`` to a normalized MP3 at ``dest`` (written atomically)."""
    if not ffmpeg_available(ffmpeg):
        raise MediaError(f"{ffmpeg} not found")
    tmp = dest.with_name(f".{dest.stem}.tmp.mp3")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(src),
           "-af", PLAYBACK_FILTER, "-ac", "1", "-ar", "44100", "-codec:a", "libmp3lame",
           "-b:a", bitrate, str(tmp)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        tmp.unlink(missing_ok=True)
        raise MediaError("ffmpeg timed out") from exc
    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise MediaError(f"ffmpeg failed: {proc.stderr.strip()[-500:]}")
    from .archive import move_file_atomic

    move_file_atomic(tmp, dest)
