"""Hardware interfaces. Implementations: ``pi`` (GPIO) and ``sim``."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

log = logging.getLogger(__name__)

HookCallback = Callable[[bool], None]  # called with off_hook=True/False, on the event loop


class HookSwitch:
    """Reports handset lifted (off-hook) / replaced (on-hook)."""

    def __init__(self) -> None:
        self._callback: HookCallback | None = None
        self._off_hook = False

    @property
    def off_hook(self) -> bool:
        return self._off_hook

    async def start(self, callback: HookCallback) -> None:
        self._callback = callback

    async def stop(self) -> None:
        pass

    def _report(self, off_hook: bool) -> None:
        if off_hook == self._off_hook:
            return
        self._off_hook = off_hook
        if self._callback:
            self._callback(off_hook)


class Output:
    """A digital output (amp enable, LED)."""

    name = "output"

    def on(self) -> None: ...
    def off(self) -> None: ...
    def blink(self, on_s: float = 0.5, off_s: float = 0.5) -> None:
        self.on()
    @property
    def is_on(self) -> bool:
        return False


class NullOutput(Output):
    def __init__(self, name: str = "output", verbose: bool = False) -> None:
        self.name = name
        self._on = False
        self._verbose = verbose

    def on(self) -> None:
        if not self._on and self._verbose:
            log.info("[%s] on", self.name)
        self._on = True

    def off(self) -> None:
        if self._on and self._verbose:
            log.info("[%s] off", self.name)
        self._on = False

    def blink(self, on_s: float = 0.5, off_s: float = 0.5) -> None:
        if self._verbose:
            log.info("[%s] blink", self.name)
        self._on = True

    @property
    def is_on(self) -> bool:
        return self._on


class Indicator:
    """The phone's LED. Modes: off, on (new messages), blink (recording)."""

    def __init__(self, output: Output) -> None:
        self.output = output
        self.mode = "off"

    def set(self, mode: str) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        if mode == "on":
            self.output.on()
        elif mode == "blink":
            self.output.blink(0.4, 0.4)
        else:
            self.output.off()


class RingerSwitch:
    """The phone's ringer slide switch: 'high', 'low' or 'off'."""

    def position(self) -> str:
        return "high"


class StaticRingerSwitch(RingerSwitch):
    def __init__(self, value: str = "high") -> None:
        self.value = value

    def position(self) -> str:
        return self.value


async def amp_settle() -> None:
    """Class-D amps pop if audio starts the instant they are enabled."""
    await asyncio.sleep(0.05)
