"""Device configuration (agent.toml).

This file only holds things that are specific to *this device*: where the
server is, which pins and sound card are wired up, where to keep data.
Everything about behavior (greetings, prompts, ring schedule, limits) comes
from the server; see ``behavior.py``.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


@dataclass
class DeviceConfig:
    name: str = "phonehome"
    data_dir: str = "/var/lib/phonehome"
    log_level: str = "INFO"


@dataclass
class ServerConfig:
    # Empty URL = standalone mode: recordings stay in the local outbox.
    url: str = ""
    token: str = ""
    heartbeat_interval_s: float = 60.0
    config_sync_interval_s: float = 300.0
    upload_retry_max_s: float = 300.0
    timeout_s: float = 30.0


@dataclass
class AudioConfig:
    # "sounddevice" (real hardware / desktop) or "virtual" (headless, tests).
    backend: str = "sounddevice"
    # Substring of the device name to use, e.g. "USB". Empty = system default.
    device: str = "USB"
    sample_rate: int = 48000
    blocksize: int = 256
    output_channels: int = 2
    input_channels: int = 1
    input_channel: int = 0
    # Output channel routing. On the phone: left = earpiece, right = ringer amp.
    earpiece_channels: list[int] = field(default_factory=lambda: [0])
    ringer_channels: list[int] = field(default_factory=lambda: [1])
    earpiece_gain_db: float = 0.0
    mic_gain_db: float = 0.0
    # Software sidetone: your own voice, faintly, in the earpiece. Makes the
    # handset feel alive. Set gain to ~-60 or disable if it causes feedback.
    sidetone: bool = True
    sidetone_gain_db: float = -18.0
    # Virtual backend only: playback speed multiplier (0 = as fast as possible)
    # and an optional WAV file to feed in as the "microphone".
    virtual_speed: float = 1.0
    virtual_input_wav: str = ""
    virtual_output_wav: str = ""


@dataclass
class GpioConfig:
    # "pi" (gpiozero), "sim" (keyboard / web panel) or "none".
    backend: str = "pi"
    hook_pin: int = 17
    # Which contact state means the handset is lifted. With the switch wired
    # between the pin and GND (internal pull-up), "closed" reads as low.
    off_hook_when: str = "closed"  # "closed" | "open"
    hook_bounce_ms: int = 30
    amp_enable_pin: int | None = 22
    led_pin: int | None = 23
    # Phone's ringer slide switch, optional. Off pin closed = ringer off,
    # low pin closed = quiet ringing. Harmless if not wired: an unconnected
    # pin with pull-up reads "open", which means normal ("high") ringing.
    ringer_off_pin: int | None = 24
    ringer_low_pin: int | None = 25


@dataclass
class ControlApiConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8081
    # Shared with the server. Empty = no auth (only acceptable in simulation).
    token: str = ""
    allow_reboot: bool = True


@dataclass
class SimConfig:
    keyboard: bool = True
    web_panel: bool = True


@dataclass
class AgentConfig:
    device: DeviceConfig = field(default_factory=DeviceConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    gpio: GpioConfig = field(default_factory=GpioConfig)
    control_api: ControlApiConfig = field(default_factory=ControlApiConfig)
    sim: SimConfig = field(default_factory=SimConfig)
    simulate: bool = False

    @property
    def data_path(self) -> Path:
        return Path(self.device.data_dir).expanduser()


def _apply(obj: Any, values: dict[str, Any], path: str = "") -> None:
    known = {f.name: f for f in fields(obj)}
    for key, value in values.items():
        if key not in known:
            raise ValueError(f"Unknown config key: {path}{key}")
        current = getattr(obj, key)
        if is_dataclass(current):
            if not isinstance(value, dict):
                raise ValueError(f"Config section {path}{key} must be a table")
            _apply(current, value, f"{path}{key}.")
        else:
            setattr(obj, key, value)


def load_config(path: str | Path | None = None, *, simulate: bool = False) -> AgentConfig:
    cfg = simulation_defaults() if simulate else AgentConfig()
    if path:
        with open(path, "rb") as fh:
            _apply(cfg, tomllib.load(fh))
    cfg.simulate = simulate or cfg.simulate
    validate(cfg)
    return cfg


def simulation_defaults() -> AgentConfig:
    """Sensible defaults for running on a desktop with no phone attached."""
    cfg = AgentConfig(simulate=True)
    cfg.device.name = "phonehome-sim"
    cfg.device.data_dir = "./sim-data"
    cfg.audio.device = ""  # system default sound device
    cfg.audio.earpiece_channels = [0, 1]  # hear the "earpiece" on both speakers
    cfg.audio.ringer_channels = [0, 1]
    cfg.audio.sidetone = False  # laptop mic + speakers would feed back
    cfg.gpio.backend = "sim"
    cfg.control_api.host = "127.0.0.1"
    cfg.control_api.allow_reboot = False
    return cfg


def validate(cfg: AgentConfig) -> None:
    a = cfg.audio
    if a.backend not in ("sounddevice", "virtual"):
        raise ValueError("audio.backend must be 'sounddevice' or 'virtual'")
    for ch in [*a.earpiece_channels, *a.ringer_channels]:
        if not 0 <= ch < a.output_channels:
            raise ValueError(f"audio channel {ch} out of range for {a.output_channels} output channels")
    if not 0 <= a.input_channel < a.input_channels:
        raise ValueError("audio.input_channel out of range")
    if cfg.gpio.backend not in ("pi", "sim", "none"):
        raise ValueError("gpio.backend must be 'pi', 'sim' or 'none'")
    if cfg.gpio.off_hook_when not in ("closed", "open"):
        raise ValueError("gpio.off_hook_when must be 'closed' or 'open'")
    if cfg.control_api.enabled and not cfg.control_api.token and not cfg.simulate:
        raise ValueError("control_api.token is required outside simulation")
