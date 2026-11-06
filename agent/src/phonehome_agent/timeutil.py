"""Wall-clock helpers.

The Pi 4 has no real-time clock. Until NTP syncs after boot, the system
clock can be wrong, so every recording notes whether the clock was synced.
A DS3231 RTC module (see docs/hardware-notes.md) removes the problem.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

_SYNC_FLAG = Path("/run/systemd/timesync/synchronized")
_cache: tuple[float, bool | None] = (0.0, None)


def now() -> datetime:
    """Timezone-aware local time."""
    return datetime.now().astimezone()


def clock_synced() -> bool | None:
    """True/False when we can tell, None when we can't (e.g. Windows, macOS)."""
    global _cache
    checked_at, value = _cache
    if time.monotonic() - checked_at < 60 and checked_at:
        return value
    value = _probe()
    _cache = (time.monotonic(), value)
    return value


def _probe() -> bool | None:
    if _SYNC_FLAG.exists():
        return True
    if shutil.which("timedatectl"):
        try:
            out = subprocess.run(
                ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()
            if out in ("yes", "no"):
                return out == "yes"
        except (OSError, subprocess.SubprocessError):
            pass
    return None
