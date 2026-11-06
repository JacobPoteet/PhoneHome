"""The phone's state machine.

    IDLE ──lift──► SETTLING ──► GREETING ──► BEEP ──► RECORDING ──hang up──► IDLE
                       │(bump)                               │max length
                       ▼                                     ▼
                     IDLE                     TIME_UP ──► WAIT_ON_HOOK ──► IDLE

    IDLE ──ring request──► RINGING ──no answer──► IDLE
                              │lifted
                              ├─► MEMORY (old message) ──► BEEP ──► RECORDING (reply)
                              └─► PROMPT ──► BEEP ──► RECORDING (prompted)

Hanging up at any point ends the call. A recording is kept only if it is
long enough and not silent (see ``recording.finalize``).
"""

from __future__ import annotations

import asyncio
import collections
import logging
import random
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable

import numpy as np

from . import timeutil
from .audio.assets import AssetLibrary
from .audio.engine import AudioEngine
from .behavior import Behavior, BehaviorStore, select
from .hardware.base import Indicator, NullOutput, Output, RingerSwitch, StaticRingerSwitch, amp_settle
from .recording import ActiveRecording, Recorder, RecordingResult

log = logging.getLogger(__name__)


class State(str, Enum):
    IDLE = "idle"
    SETTLING = "settling"
    GREETING = "greeting"
    BEEP = "beep"
    RECORDING = "recording"
    TIME_UP = "time_up"
    WAIT_ON_HOOK = "wait_on_hook"
    RINGING = "ringing"
    MEMORY = "memory"
    PROMPT = "prompt"


@dataclass
class RingRequest:
    mode: str = "auto"      # auto | memory | prompt
    source: str = "manual"  # manual | random | scheduled
    force: bool = False     # ignore the phone's ringer-off switch


@dataclass
class Replay:
    message_id: str
    recorded_at: str | None
    samples: np.ndarray


ReplayProvider = Callable[[], Awaitable[Replay | None]]


class PhoneController:
    def __init__(self, *, engine: AudioEngine, recorder: Recorder, assets: AssetLibrary,
                 behavior: BehaviorStore, replay: ReplayProvider | None = None,
                 amp: Output | None = None, indicator: Indicator | None = None,
                 ringer_switch: RingerSwitch | None = None,
                 on_saved: Callable[[RecordingResult], None] | None = None,
                 on_ring: Callable[[RingRequest, bool], None] | None = None,
                 rng: random.Random | None = None, sidetone: bool = True) -> None:
        self.engine = engine
        self.recorder = recorder
        self.assets = assets
        self.behavior = behavior
        self.replay = replay
        self.amp = amp or NullOutput("amp")
        self.indicator = indicator or Indicator(NullOutput("led"))
        self.ringer_switch = ringer_switch or StaticRingerSwitch()
        self.on_saved = on_saved
        self.on_ring = on_ring
        self.rng = rng or random.Random()
        self.sidetone = sidetone

        self.state = State.IDLE
        self.state_since = timeutil.now()
        self._off_hook = False
        self._off_evt = asyncio.Event()
        self._on_evt = asyncio.Event()
        self._on_evt.set()
        self._rings: asyncio.Queue[RingRequest] = asyncio.Queue(maxsize=1)
        self._state_waiters: list[tuple[State, asyncio.Future]] = []
        self.active: ActiveRecording | None = None
        self._active_kind: str | None = None
        self._finalizing: set[asyncio.Task] = set()
        self.idle_indicator = "off"  # "on" when the server says there are unheard messages
        self.history: collections.deque[dict] = collections.deque(maxlen=25)
        self.counters = collections.Counter()

    # ------------------------------------------------------------------ inputs
    @property
    def off_hook(self) -> bool:
        return self._off_hook

    def set_hook(self, off_hook: bool) -> None:
        """Hook switch callback. Must be called on the event loop."""
        if off_hook == self._off_hook:
            return
        self._off_hook = off_hook
        if off_hook:
            self._on_evt.clear()
            self._off_evt.set()
        else:
            self._off_evt.clear()
            self._on_evt.set()

    def ringer_blocked_reason(self, req: RingRequest) -> str | None:
        b = self.behavior.current
        if req.force:
            return None
        if not b.ringer.enabled:
            return "ringer disabled in settings"
        if self.ringer_switch.position() == "off":
            return "ringer switch on the phone is off"
        return None

    def request_ring(self, mode: str = "auto", source: str = "manual", force: bool = False) -> tuple[bool, str]:
        req = RingRequest(mode=mode, source=source, force=force)
        if mode not in ("auto", "memory", "prompt"):
            return False, f"unknown mode {mode!r}"
        if self.state != State.IDLE or self._off_hook:
            return False, "phone is in use"
        reason = self.ringer_blocked_reason(req)
        if reason:
            return False, reason
        try:
            self._rings.put_nowait(req)
        except asyncio.QueueFull:
            return False, "a ring is already pending"
        return True, "ringing"

    # ------------------------------------------------------------------ status
    def _set_state(self, state: State) -> None:
        if state != self.state:
            log.debug("state %s -> %s", self.state.value, state.value)
            self.state = state
            self.state_since = timeutil.now()
            for want, fut in list(self._state_waiters):
                if want == state and not fut.done():
                    fut.set_result(True)
            self._state_waiters = [(s, f) for s, f in self._state_waiters if not f.done()]

    async def wait_for_state(self, state: State, timeout: float = 5.0) -> None:
        if self.state == state:
            return
        fut = asyncio.get_running_loop().create_future()
        self._state_waiters.append((state, fut))
        await asyncio.wait_for(fut, timeout)

    def status(self) -> dict:
        rec = None
        if self.active is not None:
            rec = {"id": self.active.id, "kind": self._active_kind, "seconds": round(self.active.seconds, 1)}
        return {
            "state": self.state.value,
            "state_since": self.state_since.isoformat(timespec="seconds"),
            "off_hook": self._off_hook,
            "recording": rec,
            "ringer_switch": self.ringer_switch.position(),
            "counters": dict(self.counters),
            "history": list(self.history),
        }

    def _log_event(self, event: str, **fields) -> None:
        entry = {"at": timeutil.now().isoformat(timespec="seconds"), "event": event, **fields}
        self.history.appendleft(entry)
        self.counters[event] += 1

    # ------------------------------------------------------------------ main loop
    async def run(self) -> None:
        log.info("phone ready")
        while True:
            off_task = asyncio.ensure_future(self._off_evt.wait())
            ring_task = asyncio.ensure_future(self._rings.get())
            done, _ = await asyncio.wait({off_task, ring_task}, return_when=asyncio.FIRST_COMPLETED)
            off_task.cancel()
            ring_task.cancel()
            try:
                if off_task in done and self._off_hook:
                    if ring_task in done:
                        log.info("ring request dropped: handset was lifted")
                    await self._handle_pickup()
                elif ring_task in done and not ring_task.cancelled():
                    await self._handle_ring(ring_task.result())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("error during call; returning to idle")
                self._log_event("error")
                await self._cleanup_after_error()
            finally:
                self.engine.sidetone = False
                self.indicator.set(self.idle_indicator)
                self._set_state(State.IDLE)

    async def _cleanup_after_error(self) -> None:
        self.engine.stop_route(None)
        self.amp.off()
        if self.active is not None:
            await self._stop_and_finalize("error")
        await self._on_evt.wait()

    # ------------------------------------------------------------------ helpers
    async def _until_hangup(self, aw: Awaitable) -> bool:
        """Await ``aw`` unless the handset is replaced first. True = aw finished."""
        task = asyncio.ensure_future(aw)
        if not self._off_hook:
            task.cancel()
            return False
        hang = asyncio.ensure_future(self._on_evt.wait())
        done, _ = await asyncio.wait({task, hang}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            hang.cancel()
            return True
        task.cancel()
        return False

    async def _play(self, samples: np.ndarray | None, route: str = "earpiece", gain_db: float = 0.0) -> bool:
        """Play to the earpiece; False if the caller hung up during it."""
        if samples is None:
            return self._off_hook
        handle = self.engine.play(samples, route=route, gain_db=gain_db)
        finished = await self._until_hangup(handle.wait())
        if not finished:
            handle.stop()
        return finished and self._off_hook

    def _sound(self, ref: str, fallback: str) -> np.ndarray | None:
        return self.assets.get(ref, fallback=fallback)

    # ------------------------------------------------------------------ guest call
    async def _handle_pickup(self) -> None:
        b = self.behavior.current
        self._set_state(State.SETTLING)
        await asyncio.sleep(b.recording.pickup_settle_ms / 1000)
        if not self._off_hook:
            log.info("handset bumped, ignoring")
            self._log_event("bump")
            return
        now = timeutil.now()
        greeting = select(b.greetings, now, self.rng)
        ref = greeting.asset if greeting else "builtin:chime"
        intro = self._sound(ref, "builtin:chime")
        await self._session("guest", intro, State.GREETING,
                            {"greeting_id": greeting.id if greeting else "builtin:chime"}, b)

    async def _session(self, kind: str, intro: np.ndarray | None, intro_state: State,
                       meta: dict, b: Behavior) -> None:
        """Intro → beep → record, then wait for the handset to go down."""
        self.engine.sidetone = self.sidetone
        self._log_event("call", kind=kind)
        await self._session_body(kind, intro, intro_state, meta, b)
        # Not in a ``finally``: on shutdown (cancellation) we must not block here.
        await self._wait_on_hook(b)

    async def _session_body(self, kind: str, intro: np.ndarray | None, intro_state: State,
                            meta: dict, b: Behavior) -> None:
        if intro is not None:
            self._set_state(intro_state)
            if not await self._play(intro):
                log.info("hung up during %s", intro_state.value)
                self._log_event("hangup_early", during=intro_state.value)
                return
        self._set_state(State.BEEP)
        if not await self._play(self._sound(b.sounds.beep, "builtin:beep")):
            self._log_event("hangup_early", during="beep")
            return
        max_hit = await self._record(kind, meta, b)
        if max_hit and self._off_hook:
            self._set_state(State.TIME_UP)
            await self._play(self._sound(b.sounds.time_up, "builtin:time_up"))

    async def _record(self, kind: str, meta: dict, b: Behavior) -> bool:
        """Record until hang-up or max length. Returns True if max length hit."""
        self._set_state(State.RECORDING)
        self.indicator.set("blink")
        self.active = self.recorder.start(kind=kind, settings=b.recording, extra=meta)
        self._active_kind = kind
        warn = asyncio.create_task(self._warn_near_end(self.active, b))
        try:
            max_hit = await self._until_hangup(self.active.max_reached.wait())
        finally:
            warn.cancel()
        await self._stop_and_finalize("max_duration" if max_hit else "hangup", background=True)
        self.indicator.set("off")
        return max_hit

    async def _warn_near_end(self, active: ActiveRecording, b: Behavior) -> None:
        await active.warn_reached.wait()
        if self.active is active and self._off_hook:
            self.engine.play(self._sound(b.sounds.warning, "builtin:warning"), route="earpiece")

    async def _stop_and_finalize(self, ended_by: str, background: bool = False) -> None:
        active, self.active = self.active, None
        self._active_kind = None
        if active is None:
            return
        settings = self.behavior.current.recording
        await asyncio.to_thread(self.recorder.stop, active)

        async def finish() -> None:
            try:
                result = await asyncio.to_thread(self.recorder.finalize, active.id, settings, ended_by=ended_by)
            except Exception:
                log.exception("finalizing %s failed (left in incoming/ for recovery)", active.id)
                return
            self._log_event("saved" if result.saved else "discarded", id=result.id,
                            seconds=round(result.duration_s, 1), reason=result.reason)
            if result.saved and self.on_saved:
                self.on_saved(result)

        if background:
            task = asyncio.create_task(finish())
            self._finalizing.add(task)
            task.add_done_callback(self._finalizing.discard)
        else:
            await finish()

    async def _wait_on_hook(self, b: Behavior) -> None:
        if not self._off_hook:
            return
        self._set_state(State.WAIT_ON_HOOK)
        tone = self._sound(b.sounds.hangup, "builtin:hangup")
        handle = self.engine.play(tone, route="earpiece", loop=True) if tone is not None else None
        try:
            await asyncio.wait_for(self._on_evt.wait(), b.recording.hangup_tone_timeout_s)
        except asyncio.TimeoutError:
            pass
        finally:
            if handle:
                handle.stop()
        await self._on_evt.wait()  # silent from here on, until it's hung up

    async def drain(self) -> None:
        """Wait for background finalization (used at shutdown and in tests)."""
        while self._finalizing:
            await asyncio.gather(*list(self._finalizing), return_exceptions=True)

    async def shutdown(self) -> None:
        """Called on SIGTERM after the run loop is cancelled: keep what was recorded."""
        self.engine.stop_route(None)
        self.amp.off()
        if self.active is not None:
            log.info("shutdown during a recording; saving it")
            await self._stop_and_finalize("shutdown")
        await self.drain()

    # ------------------------------------------------------------------ ringing
    def _choose_mode(self, requested: str, b: Behavior) -> str:
        if requested in ("memory", "prompt"):
            return requested
        modes = {k: v for k, v in b.ringer.answer_modes.items() if k in ("memory", "prompt") and v > 0}
        if not modes:
            return "prompt"
        return self.rng.choices(list(modes), weights=list(modes.values()), k=1)[0]

    async def _handle_ring(self, req: RingRequest) -> None:
        b = self.behavior.current
        reason = self.ringer_blocked_reason(req)
        if reason:
            log.info("ring skipped: %s", reason)
            return
        mode = self._choose_mode(req.mode, b)
        # Fetch the old message while the phone rings, so there is no pause on pickup.
        prefetch = asyncio.create_task(self.replay()) if (mode == "memory" and self.replay) else None
        answered = await self._ring(b)
        if self.on_ring:
            self.on_ring(req, answered)
        self._log_event("ring_answered" if answered else "ring_missed", mode=mode, source=req.source)
        if not answered:
            if prefetch:
                prefetch.cancel()
            return

        if mode == "memory":
            replay = None
            if prefetch:
                try:
                    replay = await asyncio.wait_for(prefetch, 5.0)
                except Exception as exc:
                    log.warning("memory lane fetch failed: %s", exc)
            if replay is not None:
                await self._memory_lane(replay, b)
                return
            log.info("no old message available; asking a prompt instead")

        prompt = select(b.prompts, timeutil.now(), self.rng)
        if prompt is None:
            greeting = select(b.greetings, timeutil.now(), self.rng)
            ref = greeting.asset if greeting else "builtin:chime"
            await self._session("guest", self._sound(ref, "builtin:chime"), State.GREETING,
                                {"greeting_id": greeting.id if greeting else "builtin:chime",
                                 "via_ring": True}, b)
            return
        await self._session("prompted", self._sound(prompt.asset, "builtin:chime"), State.PROMPT,
                            {"prompt_id": prompt.id}, b)

    async def _ring(self, b: Behavior) -> bool:
        """Ring until answered or timeout. True if the handset was lifted."""
        self._set_state(State.RINGING)
        ring = self._sound(b.ringer.sound, "builtin:ring_electronic")
        gain = b.ringer.volume_db
        if self.ringer_switch.position() == "low":
            gain += b.ringer.low_switch_offset_db
        self.amp.on()
        await amp_settle()
        handle = self.engine.play(ring, route="ringer", gain_db=gain, loop=True)
        try:
            await asyncio.wait_for(self._off_evt.wait(), b.ringer.ring_timeout_s)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            handle.stop()
            await amp_settle()
            self.amp.off()

    async def _memory_lane(self, replay: Replay, b: Behavior) -> None:
        self.engine.sidetone = self.sidetone
        self._set_state(State.MEMORY)
        log.info("memory lane: playing %s", replay.message_id)
        reply = (await self._play(self._sound(b.sounds.memory_intro, "builtin:chime"))
                 and await self._play(replay.samples))
        if not reply and self._off_hook is False:
            self._log_event("hangup_early", during="memory")
        if reply and b.ringer.record_after_memory \
                and await self._play(self._sound(b.sounds.reply_intro, "builtin:chime")):
            await self._session("reply", None, State.BEEP, {"reply_to": replay.message_id}, b)
            return
        await self._wait_on_hook(b)
