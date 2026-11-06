"""Command line: ``phonehome-agent run|tones|devices|check``."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .config import load_config
from .log import setup_logging


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="phonehome-agent", description="PhoneHome phone agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run the agent")
    run.add_argument("-c", "--config", help="path to agent.toml")
    run.add_argument("--simulate", action="store_true",
                     help="no phone attached: keyboard/web panel hook, computer's mic and speakers")
    run.add_argument("--virtual-audio", action="store_true",
                     help="no sound device at all: fake mic input, output discarded (or --output-wav)")
    run.add_argument("--input-wav", help="virtual audio: use this file as the microphone")
    run.add_argument("--output-wav", help="virtual audio: write everything the phone plays to this WAV")
    run.add_argument("--data-dir", help="override device.data_dir")
    run.add_argument("--server", help="override server.url")
    run.add_argument("--port", type=int, help="override control_api.port")

    tones = sub.add_parser("tones", help="export the built-in sounds as WAV files")
    tones.add_argument("--out", default="tones", help="output directory")
    tones.add_argument("--rate", type=int, default=48000)

    sub.add_parser("devices", help="list audio devices")

    check = sub.add_parser("check", help="validate a config file and print it")
    check.add_argument("-c", "--config", required=True)

    args = p.parse_args(argv)

    if args.cmd == "tones":
        return _tones(Path(args.out), args.rate)
    if args.cmd == "devices":
        from .audio.engine import list_devices

        print(list_devices())
        return 0
    if args.cmd == "check":
        from dataclasses import asdict

        cfg = load_config(args.config)
        d = asdict(cfg)
        for section in ("server", "control_api"):
            if d[section].get("token"):
                d[section]["token"] = "***"
        print(json.dumps(d, indent=2))
        return 0

    cfg = load_config(args.config, simulate=args.simulate)
    if args.virtual_audio:
        cfg.audio.backend = "virtual"
    if args.input_wav:
        cfg.audio.virtual_input_wav = args.input_wav
    if args.output_wav:
        cfg.audio.virtual_output_wav = args.output_wav
    if args.data_dir:
        cfg.device.data_dir = args.data_dir
    if args.server is not None:
        cfg.server.url = args.server
    if args.port:
        cfg.control_api.port = args.port
    setup_logging(cfg.device.log_level)

    from .app import Agent

    try:
        asyncio.run(Agent(cfg).run())
    except KeyboardInterrupt:
        pass
    return 0


def _tones(out: Path, rate: int) -> int:
    import soundfile as sf

    from .audio.tones import BUILTIN

    out.mkdir(parents=True, exist_ok=True)
    for name, fn in BUILTIN.items():
        path = out / f"{name}.wav"
        sf.write(str(path), fn(rate), rate, subtype="PCM_16")
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
