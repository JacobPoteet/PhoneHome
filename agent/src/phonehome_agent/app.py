"""Wires the agent together and runs it."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import signal
import time

from . import __version__, sdnotify, timeutil
from .audio.assets import AssetLibrary
from .audio.engine import AudioEngine, SoundDeviceEngine, VirtualAudioEngine
from .audio.sources import SwitchableSource, WavFileSource
from .behavior import BehaviorStore
from .config import AgentConfig
from .controller import PhoneController
from .hardware.base import HookSwitch, Indicator, NullOutput, Output, RingerSwitch, StaticRingerSwitch
from .hardware.sim import HELP, KeyboardInput, SimHook
from .recording import Recorder
from .scheduler import RingScheduler
from .store import LocalStore
from .sync import ReplaySource, ServerClient, SyncService

log = logging.getLogger(__name__)

MIN_FREE_BYTES = 200 * 1024 * 1024  # keep this much space free on the SD card
SENT_RETENTION_DAYS = 30
DISCARDED_RETENTION_DAYS = 7


def build_engine(cfg: AgentConfig) -> AudioEngine:
    a = cfg.audio
    common = dict(
        sample_rate=a.sample_rate, blocksize=a.blocksize, output_channels=a.output_channels,
        routes={"earpiece": a.earpiece_channels, "ringer": a.ringer_channels},
        route_gain_db={"earpiece": a.earpiece_gain_db},
        sidetone_gain_db=a.sidetone_gain_db, sidetone_allowed=a.sidetone,
    )
    if a.backend == "virtual":
        source = WavFileSource(a.virtual_input_wav, a.sample_rate) if a.virtual_input_wav \
            else SwitchableSource(a.sample_rate)
        return VirtualAudioEngine(input_source=source, speed=a.virtual_speed,
                                  output_wav=a.virtual_output_wav, **common)
    return SoundDeviceEngine(device=a.device, input_channels=a.input_channels,
                             input_channel=a.input_channel, **common)


def build_hardware(cfg: AgentConfig) -> tuple[HookSwitch, Output, Output, RingerSwitch]:
    g = cfg.gpio
    if g.backend == "pi":
        from .hardware.pi import GpioHook, GpioOutput, GpioRingerSwitch

        hook = GpioHook(g.hook_pin, g.off_hook_when, g.hook_bounce_ms)
        amp = GpioOutput(g.amp_enable_pin, "amp") if g.amp_enable_pin is not None else NullOutput("amp")
        led = GpioOutput(g.led_pin, "led") if g.led_pin is not None else NullOutput("led")
        switch = GpioRingerSwitch(g.ringer_off_pin, g.ringer_low_pin) \
            if (g.ringer_off_pin is not None or g.ringer_low_pin is not None) else StaticRingerSwitch()
        return hook, amp, led, switch
    verbose = g.backend == "sim"
    hook = SimHook() if g.backend == "sim" else HookSwitch()
    return hook, NullOutput("amp", verbose), NullOutput("led", verbose), StaticRingerSwitch()


class Agent:
    def __init__(self, cfg: AgentConfig, *, engine: AudioEngine | None = None,
                 hardware: tuple[HookSwitch, Output, Output, RingerSwitch] | None = None,
                 client: ServerClient | None = None, rng: random.Random | None = None) -> None:
        self.cfg = cfg
        self.version = __version__
        self.started_at = timeutil.now()
        self._t0 = time.monotonic()
        self.rng = rng or random.Random()
        self.store = LocalStore(cfg.data_path)
        self.behavior = BehaviorStore(self.store.config)
        self.engine = engine or build_engine(cfg)
        self.hook, self.amp, led, self.ringer_switch = hardware or build_hardware(cfg)
        self.sim_hook = self.hook if isinstance(self.hook, SimHook) else None
        self.indicator = Indicator(led)
        self.assets = AssetLibrary(self.store.assets, cfg.audio.sample_rate, self.behavior.asset_filename)
        self.recorder = Recorder(self.engine, self.store, device_name=cfg.device.name,
                                 mic_gain_db=cfg.audio.mic_gain_db)
        if client is None and cfg.server.url:
            client = ServerClient(cfg.server.url, cfg.server.token, cfg.device.name,
                                  timeout=cfg.server.timeout_s)
        self.client = client
        self.replay = ReplaySource(client, self.store, cfg.audio.sample_rate, self.rng)
        self.controller = PhoneController(
            engine=self.engine, recorder=self.recorder, assets=self.assets, behavior=self.behavior,
            replay=self.replay, amp=self.amp, indicator=self.indicator, ringer_switch=self.ringer_switch,
            on_saved=lambda _r: self.sync.nudge_upload(),
            on_ring=lambda _req, _ans: self.scheduler.note_ring(),
            rng=self.rng, sidetone=cfg.audio.sidetone,
        )
        self.sync = SyncService(
            client, self.store, self.behavior, self.assets, self.heartbeat_payload,
            heartbeat_interval_s=cfg.server.heartbeat_interval_s,
            config_sync_interval_s=cfg.server.config_sync_interval_s,
            retry_max_s=cfg.server.upload_retry_max_s,
            on_indicator=self._server_indicator,
        )
        self.scheduler = RingScheduler(self.behavior, self.store, self.controller.request_ring, rng=self.rng)
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        return {
            "device": self.cfg.device.name,
            "version": self.version,
            "simulate": self.cfg.simulate,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "uptime_s": round(time.monotonic() - self._t0),
            "clock_synced": timeutil.clock_synced(),
            "controller": self.controller.status(),
            "audio": self.engine.status(),
            "store": {**self.store.counts(), "free_mb": self.store.free_bytes() // (1024 * 1024)},
            "sync": self.sync.status(),
            "system": system_stats(),
        }

    def heartbeat_payload(self) -> dict:
        s = self.status()
        s["controller"].pop("history", None)
        return s

    def _server_indicator(self, mode: str) -> None:
        self.controller.idle_indicator = mode
        if self.controller.state.value == "idle":
            self.indicator.set(mode)

    def request_stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ lifecycle
    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, self.request_stop)

        log.info("PhoneHome agent %s starting (%s)", self.version,
                 "SIMULATION" if self.cfg.simulate else self.cfg.device.name)
        if timeutil.clock_synced() is False:
            log.warning("system clock is not NTP-synced yet; timestamps may be wrong")

        recovered = self.recorder.recover(self.behavior.current.recording)
        if recovered:
            log.warning("recovered %d interrupted recording(s)", len(recovered))

        await self.engine.start()
        await self.hook.start(self.controller.set_hook)

        tasks = [asyncio.create_task(self.controller.run(), name="controller"),
                 asyncio.create_task(self.scheduler.run(), name="scheduler"),
                 asyncio.create_task(self._housekeeping(), name="housekeeping"),
                 asyncio.create_task(self._watchdog(), name="watchdog")]
        tasks += [asyncio.create_task(t) for t in self.sync.tasks()]
        api_server = None
        if self.cfg.control_api.enabled:
            api_server = _QuietServer(self)
            tasks.append(asyncio.create_task(api_server.serve(), name="control-api"))

        keyboard = None
        if self.sim_hook is not None and self.cfg.sim.keyboard:
            keyboard = KeyboardInput(loop, {
                " ": self.sim_hook.toggle,
                "r": lambda: self._sim_ring("auto"),
                "m": lambda: self._sim_ring("memory"),
                "p": lambda: self._sim_ring("prompt"),
                "s": lambda: print(_short_status(self.status()), flush=True),
                "h": lambda: print(HELP, flush=True),
                "q": self.request_stop,
            })
            keyboard.start()
        if self.cfg.simulate and self.cfg.control_api.enabled and self.cfg.sim.web_panel:
            log.info("sim panel: http://%s:%d/sim", self.cfg.control_api.host, self.cfg.control_api.port)

        sdnotify.notify("READY=1\nSTATUS=ready")
        stop_wait = asyncio.create_task(self._stop.wait())
        await asyncio.wait([stop_wait, *tasks], return_when=asyncio.FIRST_COMPLETED)
        for t in tasks:
            if t.done() and not t.cancelled():
                log.error("task %s ended unexpectedly: %r", t.get_name(), t.exception())

        log.info("shutting down")
        sdnotify.notify("STOPPING=1")
        if keyboard:
            keyboard.stop()
        if api_server:
            api_server.should_exit = True
        for t in tasks:
            if t.get_name() != "control-api":
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.controller.shutdown()
        await self.hook.stop()
        await self.engine.stop()
        if self.client:
            await self.client.aclose()
        self.amp.off()
        self.indicator.set("off")
        log.info("stopped")

    def _sim_ring(self, mode: str) -> None:
        ok, msg = self.controller.request_ring(mode=mode, force=True)
        log.info("[sim] ring %s: %s", mode, msg)

    async def _housekeeping(self) -> None:
        while True:
            try:
                self.store.prune(sent_days=SENT_RETENTION_DAYS, discarded_days=DISCARDED_RETENTION_DAYS)
                if self.store.free_bytes() < MIN_FREE_BYTES:
                    n = self.store.make_room(MIN_FREE_BYTES)
                    log.warning("low disk space: removed %d old already-uploaded files", n)
            except Exception:
                log.exception("housekeeping failed")
            await asyncio.sleep(3600)

    async def _watchdog(self) -> None:
        interval = sdnotify.watchdog_interval_s()
        if not interval:
            await asyncio.Event().wait()  # no systemd watchdog configured: idle forever
        last_blocks = -1
        while True:
            # Only pet the watchdog while audio is flowing: a wedged audio
            # stream then gets the whole service restarted by systemd.
            blocks = self.engine.blocks
            if blocks != last_blocks or not self.engine.healthy:
                sdnotify.notify(f"WATCHDOG=1\nSTATUS={self.controller.state.value}")
            last_blocks = blocks
            await asyncio.sleep(interval)


def system_stats() -> dict:
    stats: dict = {}
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as fh:
            stats["cpu_temp_c"] = round(int(fh.read().strip()) / 1000, 1)
    except (OSError, ValueError):
        pass
    try:
        import os

        stats["load_1m"] = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        pass
    try:
        with open("/proc/uptime") as fh:
            stats["system_uptime_s"] = int(float(fh.read().split()[0]))
    except (OSError, ValueError):
        pass
    return stats


def _short_status(s: dict) -> str:
    c = s["controller"]
    return (f"state={c['state']} off_hook={c['off_hook']} outbox={s['store']['outbox']} "
            f"sent={s['store']['sent']} discarded={s['store']['discarded']} "
            f"audio={'ok' if s['audio']['healthy'] else 'DOWN'} mic={s['audio']['input_level_dbfs']}dBFS")


class _QuietServer:
    """uvicorn without its own signal handling (the agent owns shutdown)."""

    def __init__(self, agent: Agent) -> None:
        import uvicorn

        from .api import create_app

        class Server(uvicorn.Server):
            @contextlib.contextmanager
            def capture_signals(self):  # type: ignore[override]
                yield

            def install_signal_handlers(self) -> None:  # older uvicorn
                pass

        api = agent.cfg.control_api
        config = uvicorn.Config(create_app(agent), host=api.host, port=api.port,
                                log_level="warning", access_log=False, lifespan="off")
        self._server = Server(config)

    @property
    def should_exit(self) -> bool:
        return self._server.should_exit

    @should_exit.setter
    def should_exit(self, value: bool) -> None:
        self._server.should_exit = value

    async def serve(self) -> None:
        await self._server.serve()
