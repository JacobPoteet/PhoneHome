"""Deciding when the phone rings on its own.

Random rings are a memoryless (Poisson) process restricted to allowed hours
and days: each minute inside the window has the same small chance, chosen
so the average comes out to ``per_week``. Being memoryless means a reboot
never changes the odds, and nobody can predict the next ring. A minimum gap
prevents two rings close together.

Scheduled rings fire once at a set local time (one-shot or yearly).
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import Callable

from . import timeutil
from .behavior import BehaviorStore, RandomRing, _hm
from .store import LocalStore

log = logging.getLogger(__name__)

TICK_S = 60.0
LATE_GRACE = timedelta(minutes=10)  # still fire a scheduled ring if we were briefly down


def window_minutes(r: RandomRing) -> float:
    a, b = _hm(r.window_start), _hm(r.window_end)
    mins = (b.hour * 60 + b.minute) - (a.hour * 60 + a.minute)
    if mins <= 0:
        mins += 24 * 60
    return float(mins)


def in_window(r: RandomRing, now: datetime) -> bool:
    if r.weekdays and now.weekday() not in r.weekdays:
        return False
    a, b, t = _hm(r.window_start), _hm(r.window_end), now.time()
    return a <= t < b if a <= b else (t >= a or t < b)


def per_tick_probability(r: RandomRing, tick_s: float = TICK_S) -> float:
    days = len(r.weekdays) if r.weekdays else 7
    allowed_ticks = window_minutes(r) * 60 / tick_s * days
    return min(1.0, r.per_week / allowed_ticks) if allowed_ticks else 0.0


class RingScheduler:
    def __init__(self, behavior: BehaviorStore, store: LocalStore,
                 request_ring: Callable[..., tuple[bool, str]], *,
                 rng: random.Random | None = None, now: Callable[[], datetime] = timeutil.now) -> None:
        self.behavior = behavior
        self.store = store
        self.request_ring = request_ring
        self.rng = rng or random.Random()
        self.now = now

    def note_ring(self, when: datetime | None = None) -> None:
        self.store.update_state(last_ring_at=(when or self.now()).isoformat())

    def _last_ring(self) -> datetime | None:
        value = self.store.load_state().get("last_ring_at")
        return datetime.fromisoformat(value) if value else None

    def tick(self) -> str | None:
        """Check once. Returns the reason a ring was requested, else None."""
        now = self.now()
        ringer = self.behavior.current.ringer
        if not ringer.enabled:
            return None

        # Scheduled rings first.
        state = self.store.load_state()
        fired: list[str] = state.get("fired_schedules", [])
        for s in ringer.scheduled:
            due, key = None, None
            if s.at:
                due = datetime.fromisoformat(s.at)
                due = due if due.tzinfo else due.replace(tzinfo=now.tzinfo)
                key = f"{s.id}"
            elif s.yearly:
                md, hm = s.yearly.split(" ")
                month, day = (int(x) for x in md.split("-"))
                t = _hm(hm)
                try:
                    due = now.replace(month=month, day=day, hour=t.hour, minute=t.minute, second=0, microsecond=0)
                except ValueError:  # Feb 29 in a non-leap year
                    continue
                key = f"{s.id}@{now.year}"
            if due is None or key in fired:
                continue
            if due <= now < due + LATE_GRACE:
                ok, why = self.request_ring(mode=s.mode, source="scheduled")
                if ok:
                    self.store.update_state(fired_schedules=(fired + [key])[-200:])
                    self.note_ring(now)
                    log.info("scheduled ring %s", s.id)
                    return f"scheduled:{s.id}"
                log.info("scheduled ring %s deferred: %s", s.id, why)

        # Random rings.
        r = ringer.random
        if not r.enabled or not in_window(r, now):
            return None
        last = self._last_ring()
        if last and now - last < timedelta(hours=r.min_gap_hours):
            return None
        if self.rng.random() < per_tick_probability(r):
            ok, why = self.request_ring(mode="auto", source="random")
            if ok:
                self.note_ring(now)
                log.info("random ring")
                return "random"
            log.info("random ring skipped: %s", why)
        return None

    async def run(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(TICK_S)
