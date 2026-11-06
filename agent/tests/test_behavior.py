import random
from collections import Counter
from datetime import datetime

from phonehome_agent.behavior import Behavior, BehaviorStore, Clip, DateRange, Rules, select


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def test_yearly_date_range_wraps_new_year():
    r = DateRange("12-20", "01-05")
    assert r.matches(dt("2026-12-25").date())
    assert r.matches(dt("2027-01-03").date())
    assert not r.matches(dt("2027-01-06").date())
    assert not r.matches(dt("2026-07-04").date())


def test_one_off_date_range():
    r = DateRange("2027-06-12", "2027-06-13")
    assert r.matches(dt("2027-06-12").date())
    assert not r.matches(dt("2028-06-12").date())


def test_rules_weekdays_and_time_window_wrapping_midnight():
    rules = Rules(weekdays=[4, 5], time_from="22:00", time_to="02:00")  # Fri/Sat nights
    assert rules.matches(dt("2026-11-06T23:30"))      # Friday
    assert rules.matches(dt("2026-11-07T01:00"))      # Saturday, after midnight
    assert not rules.matches(dt("2026-11-06T12:00"))
    assert not rules.matches(dt("2026-11-08T23:30"))  # Sunday


def test_select_prefers_highest_matching_priority():
    clips = [
        Clip(id="default", asset="builtin:chime"),
        Clip(id="xmas", asset="a1", priority=10, rules=Rules(dates=[DateRange("12-01", "12-31")])),
    ]
    rng = random.Random(1)
    assert select(clips, dt("2026-12-10T12:00"), rng).id == "xmas"
    assert select(clips, dt("2026-11-10T12:00"), rng).id == "default"


def test_select_weighted_random_and_disabled():
    clips = [Clip(id="a", asset="x", weight=3), Clip(id="b", asset="y", weight=1),
             Clip(id="c", asset="z", enabled=False)]
    rng = random.Random(42)
    counts = Counter(select(clips, dt("2026-11-06T12:00"), rng).id for _ in range(4000))
    assert "c" not in counts
    assert 0.70 < counts["a"] / 4000 < 0.80


def test_select_none_when_nothing_matches():
    clips = [Clip(id="x", asset="x", rules=Rules(weekdays=[0]))]
    assert select(clips, dt("2026-11-06T12:00"), random.Random()) is None  # a Friday


def test_from_dict_ignores_unknown_keys_and_fills_defaults():
    b = Behavior.from_dict({"version": "v9", "future_feature": {"x": 1},
                            "recording": {"max_duration_s": 60, "something_new": True},
                            "ringer": {"random": {"enabled": True, "per_week": 3}}})
    assert b.version == "v9"
    assert b.recording.max_duration_s == 60
    assert b.recording.min_duration_s == 2.0
    assert b.ringer.random.enabled and b.ringer.random.per_week == 3
    assert b.greetings[0].asset == "builtin:chime"


def test_behavior_store_persists_and_reloads(tmp_path):
    s = BehaviorStore(tmp_path)
    assert s.current.version == "builtin"
    s.update({"version": "v2", "recording": {"max_duration_s": 42}})
    again = BehaviorStore(tmp_path)
    assert again.current.version == "v2"
    assert again.current.recording.max_duration_s == 42
