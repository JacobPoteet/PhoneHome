"""Simulated hardware: the hook is toggled from the keyboard or the web panel."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from typing import Callable

from .base import HookSwitch

log = logging.getLogger(__name__)


class SimHook(HookSwitch):
    def set(self, off_hook: bool) -> None:
        log.info("[sim] handset %s", "LIFTED" if off_hook else "replaced")
        self._report(off_hook)

    def toggle(self) -> None:
        self.set(not self.off_hook)


HELP = """
  Simulated phone. Keys:
    [space]  pick up / hang up
    r        ring (auto)     m  ring -> memory lane     p  ring -> prompt
    s        print status    h  help                    q  quit
"""


class KeyboardInput:
    """Reads single key presses in a background thread (Windows, macOS, Linux)."""

    def __init__(self, loop: asyncio.AbstractEventLoop, actions: dict[str, Callable[[], None]]) -> None:
        self._loop = loop
        self._actions = actions
        self._restore: Callable[[], None] | None = None

    def start(self) -> None:
        if not sys.stdin or not sys.stdin.isatty():
            log.info("[sim] stdin is not a terminal; use the web panel instead")
            return
        print(HELP, flush=True)
        threading.Thread(target=self._run, name="sim-keyboard", daemon=True).start()

    def stop(self) -> None:
        if self._restore:
            self._restore()
            self._restore = None

    def _dispatch(self, key: str) -> None:
        action = self._actions.get(key.lower())
        if action:
            self._loop.call_soon_threadsafe(action)

    def _run(self) -> None:
        if os.name == "nt":
            import msvcrt

            while True:
                self._dispatch(msvcrt.getwch())
        else:
            import termios
            import tty

            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            self._restore = lambda: termios.tcsetattr(fd, termios.TCSADRAIN, old)
            tty.setcbreak(fd)
            try:
                while True:
                    ch = sys.stdin.read(1)
                    if not ch:
                        break
                    self._dispatch(ch)
            finally:
                self.stop()
