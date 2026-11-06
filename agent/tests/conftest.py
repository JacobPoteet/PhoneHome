from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass

import pytest

from phonehome_agent.audio.assets import AssetLibrary
from phonehome_agent.audio.engine import VirtualAudioEngine
from phonehome_agent.audio.sources import SwitchableSource
from phonehome_agent.behavior import BehaviorStore
from phonehome_agent.controller import PhoneController
from phonehome_agent.hardware.base import Indicator, NullOutput, StaticRingerSwitch
from phonehome_agent.recording import Recorder
from phonehome_agent.store import LocalStore
from phonehome_agent.sync import ReplaySource

SR = 16000  # smaller than production to keep tests quick
SPEED = 40.0  # virtual audio runs 40x real time

FAST_BEHAVIOR = {
    "version": "test-1",
    "recording": {
        "max_duration_s": 6.0,
        "warning_before_end_s": 2.0,
        "min_duration_s": 1.0,
        "min_voiced_s": 0.3,
        "silence_threshold_dbfs": -50.0,
        "pickup_settle_ms": 20,
        "hangup_tone_timeout_s": 0.5,
    },
    "ringer": {"ring_timeout_s": 0.4, "answer_modes": {"memory": 1, "prompt": 0}},
    "prompts": [{"id": "p-mind", "asset": "builtin:chime", "title": "What's on your mind?"}],
}


async def wait_until(cond, timeout: float = 5.0, interval: float = 0.002) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(interval)


@dataclass
class Rig:
    store: LocalStore
    behavior: BehaviorStore
    engine: VirtualAudioEngine
    source: SwitchableSource
    recorder: Recorder
    controller: PhoneController
    amp: NullOutput
    ringer_switch: StaticRingerSwitch
    saved: list
    task: asyncio.Task | None = None

    async def lift(self) -> None:
        self.controller.set_hook(True)

    async def hang_up(self) -> None:
        self.controller.set_hook(False)

    async def settle(self) -> None:
        """Wait for the call to end and background finalization to finish."""
        from phonehome_agent.controller import State

        await wait_until(lambda: self.controller.state == State.IDLE)
        await self.controller.drain()


def make_rig(tmp_path, behavior: dict | None = None, *, seed: int = 7) -> Rig:
    store = LocalStore(tmp_path / "data")
    bstore = BehaviorStore(store.config)
    bstore.update(behavior or FAST_BEHAVIOR)
    source = SwitchableSource(SR)
    engine = VirtualAudioEngine(input_source=source, speed=SPEED, sample_rate=SR, blocksize=256,
                                output_channels=2, routes={"earpiece": [0], "ringer": [1]})
    recorder = Recorder(engine, store, device_name="test-phone")
    assets = AssetLibrary(store.assets, SR, bstore.asset_filename)
    amp = NullOutput("amp")
    switch = StaticRingerSwitch("high")
    saved: list = []
    controller = PhoneController(
        engine=engine, recorder=recorder, assets=assets, behavior=bstore,
        replay=ReplaySource(None, store, SR, random.Random(seed)), amp=amp,
        indicator=Indicator(NullOutput("led")), ringer_switch=switch,
        on_saved=saved.append, rng=random.Random(seed),
    )
    return Rig(store, bstore, engine, source, recorder, controller, amp, switch, saved)


@pytest.fixture
async def rig(tmp_path):
    r = make_rig(tmp_path)
    await r.engine.start()
    r.task = asyncio.create_task(r.controller.run())
    yield r
    r.task.cancel()
    await asyncio.gather(r.task, return_exceptions=True)
    await r.controller.shutdown()
    await r.engine.stop()
