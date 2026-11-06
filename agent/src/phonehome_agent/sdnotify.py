"""Minimal systemd sd_notify (READY / WATCHDOG / STATUS) without dependencies."""

from __future__ import annotations

import os
import socket


def notify(message: str) -> bool:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(message.encode())
        return True
    except OSError:
        return False


def watchdog_interval_s() -> float | None:
    """Half the systemd WatchdogSec, or None when no watchdog is configured."""
    usec = os.environ.get("WATCHDOG_USEC")
    if not usec:
        return None
    try:
        return int(usec) / 1_000_000 / 2
    except ValueError:
        return None
