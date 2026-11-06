"""Raspberry Pi GPIO via gpiozero (lgpio backend on Raspberry Pi OS Bookworm+).

Wiring (BCM numbering, see docs/hardware-notes.md):
  hook switch      pin <-> GND, internal pull-up
  ringer switch    optional, same style
  amp enable       PAM8302 SD pin (high = amp on)
  LED              through a 330 ohm resistor to GND
"""

from __future__ import annotations

import asyncio
import logging

from .base import HookSwitch, Output, RingerSwitch

log = logging.getLogger(__name__)


class GpioHook(HookSwitch):
    def __init__(self, pin: int, off_hook_when: str = "closed", bounce_ms: int = 30) -> None:
        super().__init__()
        from gpiozero import Button

        self._button = Button(pin, pull_up=True, bounce_time=bounce_ms / 1000)
        self._closed_means_off = off_hook_when == "closed"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._poll_task: asyncio.Task | None = None

    def _read(self) -> bool:
        closed = bool(self._button.is_pressed)  # pressed = contact closed to GND
        return closed if self._closed_means_off else not closed

    async def start(self, callback) -> None:
        await super().start(callback)
        self._loop = asyncio.get_running_loop()
        edge = lambda: self._loop.call_soon_threadsafe(self._report, self._read())  # noqa: E731
        self._button.when_pressed = edge
        self._button.when_released = edge
        # The handset may already be off the hook at boot.
        self._report(self._read())
        self._poll_task = asyncio.create_task(self._poll(), name="hook-poll")

    async def _poll(self) -> None:
        # Belt and braces: re-read the pin every second in case an edge was missed.
        while True:
            await asyncio.sleep(1.0)
            self._report(self._read())

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
        self._button.close()


class GpioOutput(Output):
    def __init__(self, pin: int, name: str) -> None:
        from gpiozero import LED

        self.name = name
        self._dev = LED(pin)

    def on(self) -> None:
        self._dev.on()

    def off(self) -> None:
        self._dev.off()

    def blink(self, on_s: float = 0.5, off_s: float = 0.5) -> None:
        self._dev.blink(on_time=on_s, off_time=off_s, background=True)

    @property
    def is_on(self) -> bool:
        return bool(self._dev.is_lit)


class GpioRingerSwitch(RingerSwitch):
    def __init__(self, off_pin: int | None, low_pin: int | None) -> None:
        from gpiozero import Button

        self._off = Button(off_pin, pull_up=True) if off_pin is not None else None
        self._low = Button(low_pin, pull_up=True) if low_pin is not None else None

    def position(self) -> str:
        if self._off is not None and self._off.is_pressed:
            return "off"
        if self._low is not None and self._low.is_pressed:
            return "low"
        return "high"
