"""Audio engine: one persistent full-duplex stream with a small mixer.

Why persistent: the USB sound card is a single ALSA device that drives both
the earpiece (left) and the ringer amp (right). Opening and closing streams
per sound would fight over the device, click, and add latency. Instead the
engine keeps one stream open, mixes any active "voices" into the output
channels they are routed to, and hands every input block to listeners
(the recorder) and back to the earpiece (sidetone).

Two implementations share the same core (``_process``):
- ``SoundDeviceEngine``: real hardware via PortAudio (Pi and desktop sim).
- ``VirtualAudioEngine``: no hardware; a clock task pulls blocks and reads
  "microphone" input from a source. Used by tests and headless simulation.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

InputListener = Callable[[np.ndarray], None]


def db_to_gain(db: float) -> float:
    return float(10 ** (db / 20.0))


def dbfs(rms: float) -> float:
    return float(20 * np.log10(max(rms, 1e-10)))


class _Voice:
    __slots__ = ("samples", "pos", "channels", "gain", "loop", "on_finish", "route")

    def __init__(self, samples: np.ndarray, channels: list[int], gain: float, loop: bool,
                 route: str, on_finish: Callable[[bool], None]) -> None:
        self.samples = samples
        self.pos = 0
        self.channels = channels
        self.gain = gain
        self.loop = loop
        self.route = route
        self.on_finish = on_finish

    def next(self, frames: int) -> tuple[np.ndarray, bool]:
        n = len(self.samples)
        if n == 0:
            return np.zeros(0, dtype=np.float32), True
        if self.loop:
            idx = (np.arange(frames) + self.pos) % n
            self.pos = (self.pos + frames) % n
            return self.samples[idx], False
        chunk = self.samples[self.pos:self.pos + frames]
        self.pos += len(chunk)
        return chunk, self.pos >= n


class Mixer:
    def __init__(self, out_channels: int) -> None:
        self.out_channels = out_channels
        self._voices: list[_Voice] = []
        self._lock = threading.Lock()

    def add(self, voice: _Voice) -> None:
        with self._lock:
            self._voices.append(voice)

    def remove(self, voice: _Voice) -> bool:
        with self._lock:
            if voice in self._voices:
                self._voices.remove(voice)
                return True
            return False

    def active_routes(self) -> list[str]:
        with self._lock:
            return [v.route for v in self._voices]

    def voices_for(self, route: str | None) -> list[_Voice]:
        with self._lock:
            return [v for v in self._voices if route is None or v.route == route]

    def render(self, frames: int) -> np.ndarray:
        out = np.zeros((frames, self.out_channels), dtype=np.float32)
        with self._lock:
            voices = list(self._voices)
        finished: list[_Voice] = []
        for v in voices:
            chunk, done = v.next(frames)
            if len(chunk):
                scaled = chunk * v.gain
                for c in v.channels:
                    out[: len(chunk), c] += scaled
            if done:
                finished.append(v)
        if finished:
            with self._lock:
                for v in finished:
                    if v in self._voices:
                        self._voices.remove(v)
            for v in finished:
                v.on_finish(True)
        return out


class PlayHandle:
    """Returned by ``AudioEngine.play``. Await ``wait()`` or call ``stop()``."""

    def __init__(self, engine: "AudioEngine", voice: _Voice | None, future: asyncio.Future[bool]) -> None:
        self._engine = engine
        self._voice = voice
        self._future = future

    @property
    def done(self) -> bool:
        return self._future.done()

    async def wait(self) -> bool:
        """True if the sound played to the end, False if it was stopped."""
        return await asyncio.shield(self._future)

    def stop(self) -> None:
        if self._voice is not None and self._engine.mixer.remove(self._voice):
            self._voice.on_finish(False)


class AudioEngine:
    def __init__(self, *, sample_rate: int, blocksize: int, output_channels: int,
                 routes: dict[str, list[int]], route_gain_db: dict[str, float] | None = None,
                 sidetone_gain_db: float = -18.0, sidetone_allowed: bool = True) -> None:
        self.sample_rate = sample_rate
        self.blocksize = blocksize
        self.output_channels = output_channels
        self.routes = routes
        self.route_gain = {k: db_to_gain(v) for k, v in (route_gain_db or {}).items()}
        self.mixer = Mixer(output_channels)
        self._listeners: list[InputListener] = []
        self._listeners_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.sidetone_allowed = sidetone_allowed
        self.sidetone = False
        self._sidetone_gain = db_to_gain(sidetone_gain_db)
        self.healthy = False
        self.last_error: str | None = None
        self.xruns = 0
        self.blocks = 0
        self.bad_input_blocks = 0
        self._input_peak = 0.0
        self._input_level_dbfs = -100.0

    # -- public API -----------------------------------------------------
    def play(self, samples: np.ndarray, route: str = "earpiece", gain_db: float = 0.0,
             loop: bool = False) -> PlayHandle:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = self._loop.create_future()
        channels = self.routes.get(route)
        if channels is None:
            raise ValueError(f"unknown audio route {route!r}")
        gain = db_to_gain(gain_db) * self.route_gain.get(route, 1.0)
        loop_ref = self._loop

        def on_finish(completed: bool) -> None:
            def resolve() -> None:
                if not future.done():
                    future.set_result(completed)
            if _in_loop(loop_ref):
                resolve()
            else:
                loop_ref.call_soon_threadsafe(resolve)

        samples = np.asarray(samples, dtype=np.float32)
        if len(samples) == 0:
            future.set_result(True)
            return PlayHandle(self, None, future)
        voice = _Voice(samples, channels, gain, loop, route, on_finish)
        self.mixer.add(voice)
        return PlayHandle(self, voice, future)

    def stop_route(self, route: str | None = None) -> None:
        for v in self.mixer.voices_for(route):
            if self.mixer.remove(v):
                v.on_finish(False)

    def add_input_listener(self, fn: InputListener) -> None:
        with self._listeners_lock:
            self._listeners.append(fn)

    def remove_input_listener(self, fn: InputListener) -> None:
        with self._listeners_lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    @property
    def input_level_dbfs(self) -> float:
        return self._input_level_dbfs

    def status(self) -> dict:
        return {
            "backend": type(self).__name__,
            "healthy": self.healthy,
            "last_error": self.last_error,
            "sample_rate": self.sample_rate,
            "xruns": self.xruns,
            "bad_input_blocks": self.bad_input_blocks,
            "playing": self.mixer.active_routes(),
            "input_level_dbfs": round(self._input_level_dbfs, 1),
            "sidetone": self.sidetone,
        }

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()

    async def stop(self) -> None:
        self.stop_route(None)

    # -- core (called from the audio thread) ------------------------------
    def _process(self, mono_in: np.ndarray | None, frames: int) -> np.ndarray:
        self.blocks += 1
        out = self.mixer.render(frames)
        if mono_in is not None and len(mono_in):
            # A glitching device can deliver NaN/inf; never let that reach a recording.
            if not np.all(np.isfinite(mono_in)):
                mono_in = np.nan_to_num(mono_in, nan=0.0, posinf=1.0, neginf=-1.0)
                self.bad_input_blocks += 1
            with self._listeners_lock:
                listeners = list(self._listeners)
            for fn in listeners:
                try:
                    fn(mono_in)
                except Exception:  # never let a listener kill the audio thread
                    log.exception("input listener failed")
            rms = float(np.sqrt(np.mean(np.square(mono_in, dtype=np.float64))))
            # Fast attack, slow release level meter for the status page.
            self._input_peak = max(rms, self._input_peak * 0.97)
            self._input_level_dbfs = dbfs(self._input_peak)
            if self.sidetone and self.sidetone_allowed:
                st = mono_in[:frames] * self._sidetone_gain
                for c in self.routes.get("earpiece", []):
                    out[: len(st), c] += st
        np.clip(out, -1.0, 1.0, out=out)
        return out


def _in_loop(loop: asyncio.AbstractEventLoop) -> bool:
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


# ---------------------------------------------------------------------------
class VirtualAudioEngine(AudioEngine):
    """Hardware-free engine. ``speed`` > 1 runs faster than real time (tests)."""

    def __init__(self, *, input_source: Callable[[int], np.ndarray] | None = None,
                 speed: float = 1.0, output_wav: str = "", **kw) -> None:
        super().__init__(**kw)
        from .sources import SilenceSource

        self.input_source = input_source or SilenceSource()
        self.speed = speed
        self.output_wav = output_wav
        self._task: asyncio.Task | None = None
        self._out_file = None

    async def start(self) -> None:
        await super().start()
        if self.output_wav:
            import soundfile as sf

            self._out_file = sf.SoundFile(self.output_wav, "w", samplerate=self.sample_rate,
                                          channels=self.output_channels, subtype="PCM_16")
        self.healthy = True
        self._task = asyncio.create_task(self._run(), name="virtual-audio")

    async def stop(self) -> None:
        await super().stop()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._out_file is not None:
            self._out_file.close()
        self.healthy = False

    async def _run(self) -> None:
        block_s = self.blocksize / self.sample_rate
        next_t = time.monotonic()
        while True:
            inp = np.asarray(self.input_source(self.blocksize), dtype=np.float32)
            out = self._process(inp, self.blocksize)
            if self._out_file is not None:
                self._out_file.write(out)
            if self.speed > 0:
                next_t += block_s / self.speed
                await asyncio.sleep(max(0.0, next_t - time.monotonic()))
            else:
                await asyncio.sleep(0)


# ---------------------------------------------------------------------------
class SoundDeviceEngine(AudioEngine):
    """Real audio through PortAudio. Reopens the device if it disappears."""

    def __init__(self, *, device: str = "", input_channels: int = 1, input_channel: int = 0, **kw) -> None:
        super().__init__(**kw)
        self.device_query = device
        self.input_channels = input_channels
        self.input_channel = input_channel
        self.device_name: str | None = None
        self._stream = None
        self._supervisor: asyncio.Task | None = None

    async def start(self) -> None:
        await super().start()
        self._open()
        self._supervisor = asyncio.create_task(self._supervise(), name="audio-supervisor")

    async def stop(self) -> None:
        await super().stop()
        if self._supervisor:
            self._supervisor.cancel()
        self._close()

    def status(self) -> dict:
        s = super().status()
        s["device"] = self.device_name
        return s

    def _find_device(self):
        import sounddevice as sd

        if not self.device_query:
            return None, None
        q = self.device_query.lower()
        for idx, dev in enumerate(sd.query_devices()):
            if q in dev["name"].lower() and dev["max_input_channels"] >= self.input_channels \
                    and dev["max_output_channels"] >= self.output_channels:
                return idx, dev["name"]
        raise RuntimeError(f"no audio device matching {self.device_query!r} with "
                           f"{self.input_channels} in / {self.output_channels} out channels")

    def _open(self) -> None:
        import sounddevice as sd

        try:
            idx, name = self._find_device()
            self._stream = sd.Stream(
                samplerate=self.sample_rate, blocksize=self.blocksize, device=(idx, idx),
                channels=(self.input_channels, self.output_channels), dtype="float32",
                latency="low", callback=self._callback,
            )
            self._stream.start()
            self.device_name = name or str(sd.query_devices(kind="output")["name"])
            self.healthy = True
            self.last_error = None
            log.info("audio stream open on %s (%d Hz, block %d)", self.device_name,
                     self.sample_rate, self.blocksize)
        except Exception as exc:
            self.healthy = False
            self.last_error = str(exc)
            log.error("could not open audio device: %s", exc)
            self._close()

    def _close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close(ignore_errors=True)
            except Exception:
                pass
            self._stream = None

    def _callback(self, indata, outdata, frames, _time, status) -> None:
        if status:
            self.xruns += 1
        mono = indata[:, self.input_channel].copy()
        outdata[:] = self._process(mono, frames)

    async def _supervise(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            if self._stream is None or not self._stream.active:
                if self.healthy:
                    log.error("audio stream stopped; reopening")
                self.healthy = False
                self._close()
                self._open()


def list_devices() -> str:
    import sounddevice as sd

    return str(sd.query_devices())
