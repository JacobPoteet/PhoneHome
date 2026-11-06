import random
from datetime import datetime, timedelta, timezone

from phonehome_agent.behavior import BehaviorStore, RandomRing
from phonehome_agent.scheduler import RingScheduler, in_window, per_tick_probability
from phonehome_agent.store import LocalStore

TZ = timezone(timedelta(hours=-5))


class Clock:
    def __init__(self, start: str) -> None:
        self.t = datetime.fromisoformat(start).replace(tzinfo=TZ)

    def __call__(self) -> datetime:
        return self.t


def make(tmp_path, ringer: dict, start="2026-11-06T12:00", seed=3):
    store = LocalStore(tmp_path)
    b = BehaviorStore(store.config)
    b.update({"ringer": ringer})
    calls = []

    def request_ring(**kw):
        calls.append(kw)
        return True, "ringing"

    clock = Clock(start)
    return RingScheduler(b, store, request_ring, rng=random.Random(seed), now=clock), calls, clock


def test_probability_matches_requested_rate():
    r = RandomRing(enabled=True, per_week=2, window_start="10:00", window_end="20:00")
    # 10 h/day * 7 days = 4200 minutes per week
    assert abs(per_tick_probability(r) - 2 / 4200) < 1e-12


def test_window_wraps_midnight_and_respects_weekdays():
    r = RandomRing(window_start="22:00", window_end="01:00", weekdays=[4])
    assert in_window(r, datetime(2026, 11, 6, 23, 0))   # Friday
    assert not in_window(r, datetime(2026, 11, 5, 23, 0))  # Thursday
    assert not in_window(r, datetime(2026, 11, 6, 12, 0))


def test_random_rings_average_out_and_respect_min_gap(tmp_path):
    sched, calls, clock = make(tmp_path, {"random": {"enabled": True, "per_week": 3, "min_gap_hours": 0,
                                                     "window_start": "00:00", "window_end": "23:59"}})
    weeks = 12
    for _ in range(weeks * 7 * 24 * 60):
        sched.tick()
        clock.t += timedelta(minutes=1)
    assert 1.5 < len(calls) / weeks < 4.5  # Poisson, mean 3/week


def test_min_gap_blocks_back_to_back_rings(tmp_path):
    sched, calls, clock = make(tmp_path, {"random": {"enabled": True, "per_week": 10000, "min_gap_hours": 24,
                                                     "window_start": "00:00", "window_end": "23:59"}})
    for _ in range(12 * 60):
        sched.tick()
        clock.t += timedelta(minutes=1)
    assert len(calls) == 1


def test_scheduled_one_shot_fires_once_within_grace(tmp_path):
    sched, calls, clock = make(tmp_path, {"scheduled": [{"id": "nye", "at": "2026-12-31T23:59", "mode": "prompt"}]},
                               start="2026-12-31T23:58")
    assert sched.tick() is None
    clock.t += timedelta(minutes=1)
    assert sched.tick() == "scheduled:nye"
    clock.t += timedelta(minutes=1)
    assert sched.tick() is None
    assert calls == [{"mode": "prompt", "source": "scheduled"}]


def test_scheduled_ring_missed_by_too_long_is_skipped(tmp_path):
    sched, calls, _ = make(tmp_path, {"scheduled": [{"id": "x", "at": "2026-11-06T11:00"}]})
    assert sched.tick() is None and calls == []


def test_yearly_schedule_fires_each_year(tmp_path):
    sched, calls, clock = make(tmp_path, {"scheduled": [{"id": "anniv", "yearly": "06-12 18:00"}]},
                               start="2027-06-12T18:00")
    assert sched.tick() == "scheduled:anniv"
    assert sched.tick() is None
    clock.t = clock.t.replace(year=2028)
    assert sched.tick() == "scheduled:anniv"


def test_disabled_ringer_never_rings(tmp_path):
    sched, calls, clock = make(tmp_path, {"enabled": False, "random": {"enabled": True, "per_week": 10000},
                                          "scheduled": [{"id": "x", "at": "2026-11-06T12:00"}]})
    assert sched.tick() is None and calls == []
