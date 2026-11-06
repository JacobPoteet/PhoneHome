"""Call-flow tests against the virtual audio engine (runs ~40x real time)."""

import asyncio

import pytest

from conftest import FAST_BEHAVIOR, make_rig, wait_until
from phonehome_agent.controller import State
from phonehome_agent.store import read_json


def outbox_meta(rig):
    return [read_json(rig.store.outbox / f"{i}.json") for i in rig.store.outbox_ids()]


async def record_for(rig, seconds: float):
    await rig.controller.wait_for_state(State.RECORDING)
    await wait_until(lambda: rig.controller.active is not None and rig.controller.active.seconds >= seconds)


async def test_guest_message_saved_with_metadata(rig):
    await rig.lift()
    await rig.controller.wait_for_state(State.GREETING)
    await record_for(rig, 2.0)
    await rig.hang_up()
    await rig.settle()

    metas = outbox_meta(rig)
    assert len(metas) == 1
    m = metas[0]
    assert m["kind"] == "guest"
    assert m["greeting_id"] == "builtin:chime"
    assert m["ended_by"] == "hangup"
    assert 1.9 < m["duration_s"] < 3.0
    assert m["audio"]["format"] == "flac" and len(m["audio"]["sha256"]) == 64
    assert rig.saved and rig.saved[0].id == m["id"]
    assert not list(rig.store.incoming.iterdir())  # nothing left behind


async def test_hang_up_during_greeting_saves_nothing(rig):
    await rig.lift()
    await rig.controller.wait_for_state(State.GREETING)
    await rig.hang_up()
    await rig.settle()
    assert rig.store.outbox_ids() == []
    assert rig.controller.counters["hangup_early"] == 1


async def test_bumped_handset_is_ignored(rig):
    await rig.lift()
    await rig.controller.wait_for_state(State.SETTLING)
    await rig.hang_up()  # back down within pickup_settle_ms
    await rig.settle()
    assert rig.controller.counters["bump"] == 1
    assert rig.controller.counters["call"] == 0


async def test_silent_message_is_discarded(rig):
    rig.source.talking = False
    await rig.lift()
    await record_for(rig, 2.0)
    await rig.hang_up()
    await rig.settle()
    assert rig.store.outbox_ids() == []
    discarded = list(rig.store.discarded.glob("*.json"))
    assert len(discarded) == 1
    assert read_json(discarded[0])["discard_reason"] == "silent"


async def test_too_short_message_is_discarded(rig):
    await rig.lift()
    await record_for(rig, 0.3)
    await rig.hang_up()
    await rig.settle()
    assert rig.store.outbox_ids() == []
    assert read_json(next(rig.store.discarded.glob("*.json")))["discard_reason"] == "too_short"


async def test_max_length_warns_then_stops_and_waits_for_hang_up(rig):
    await rig.lift()
    await rig.controller.wait_for_state(State.RECORDING)
    await rig.controller.wait_for_state(State.TIME_UP, timeout=10)
    await rig.controller.wait_for_state(State.WAIT_ON_HOOK, timeout=10)
    await rig.controller.drain()
    m = outbox_meta(rig)[0]
    assert m["ended_by"] == "max_duration"
    assert m["duration_s"] == pytest.approx(FAST_BEHAVIOR["recording"]["max_duration_s"], abs=0.05)
    # Still off-hook: a new call must not start until it is hung up.
    await asyncio.sleep(0.1)
    assert rig.controller.state == State.WAIT_ON_HOOK
    await rig.hang_up()
    await rig.settle()
    assert rig.controller.counters["call"] == 1


async def test_unanswered_ring_times_out_and_turns_amp_off(rig):
    ok, _ = rig.controller.request_ring(mode="prompt")
    assert ok
    await rig.controller.wait_for_state(State.RINGING)
    assert rig.amp.is_on  # amp first; ring audio starts ~50 ms later (avoids a pop)
    await wait_until(lambda: "ringer" in rig.engine.mixer.active_routes())
    await rig.settle()
    assert rig.controller.counters["ring_missed"] == 1
    assert not rig.amp.is_on
    assert rig.engine.mixer.active_routes() == []


async def test_answered_ring_plays_prompt_and_links_recording(rig):
    rig.controller.request_ring(mode="prompt")
    await rig.controller.wait_for_state(State.RINGING)
    await rig.lift()
    await rig.controller.wait_for_state(State.PROMPT)
    await record_for(rig, 1.5)
    await rig.hang_up()
    await rig.settle()
    m = outbox_meta(rig)[0]
    assert m["kind"] == "prompted"
    assert m["prompt_id"] == "p-mind"


async def test_memory_lane_replays_old_message_then_records_reply(rig):
    # Leave a first message so there is something to replay.
    await rig.lift()
    await record_for(rig, 1.5)
    await rig.hang_up()
    await rig.settle()
    first_id = rig.store.outbox_ids()[0]

    rig.controller.request_ring(mode="memory")
    await rig.controller.wait_for_state(State.RINGING)
    await rig.lift()
    await rig.controller.wait_for_state(State.MEMORY)
    await record_for(rig, 1.5)
    await rig.hang_up()
    await rig.settle()

    replies = [m for m in outbox_meta(rig) if m["kind"] == "reply"]
    assert len(replies) == 1 and replies[0]["reply_to"] == first_id


async def test_memory_lane_without_old_messages_falls_back_to_prompt(rig):
    rig.controller.request_ring(mode="memory")
    await rig.controller.wait_for_state(State.RINGING)
    await rig.lift()
    await rig.controller.wait_for_state(State.PROMPT)
    await rig.hang_up()
    await rig.settle()


async def test_ring_refused_while_in_use_and_when_switch_off(rig):
    await rig.lift()
    await rig.controller.wait_for_state(State.GREETING)
    ok, why = rig.controller.request_ring()
    assert not ok and "in use" in why
    await rig.hang_up()
    await rig.settle()

    rig.ringer_switch.value = "off"
    ok, why = rig.controller.request_ring()
    assert not ok and "switch" in why
    ok, _ = rig.controller.request_ring(force=True)
    assert ok
    await rig.settle()


async def test_shutdown_mid_recording_keeps_the_message(tmp_path):
    rig = make_rig(tmp_path)
    await rig.engine.start()
    task = asyncio.create_task(rig.controller.run())
    await rig.lift()
    await record_for(rig, 2.0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await rig.controller.shutdown()
    await rig.engine.stop()
    m = outbox_meta(rig)[0]
    assert m["ended_by"] == "shutdown"


async def test_custom_greeting_asset_is_used(tmp_path):
    import numpy as np
    import soundfile as sf

    from conftest import SR

    behavior = dict(FAST_BEHAVIOR, greetings=[{"id": "g-hello", "asset": "a-hello"}],
                    assets={"a-hello": {"filename": "a-hello.flac"}})
    rig = make_rig(tmp_path, behavior)
    sf.write(str(rig.store.assets / "a-hello.flac"), np.zeros(SR // 2, dtype=np.float32), SR, format="FLAC")
    await rig.engine.start()
    task = asyncio.create_task(rig.controller.run())
    try:
        await rig.lift()
        await record_for(rig, 1.5)
        await rig.hang_up()
        await rig.settle()
        assert outbox_meta(rig)[0]["greeting_id"] == "g-hello"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await rig.engine.stop()
