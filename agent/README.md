# phonehome-agent

The program that runs on the Raspberry Pi inside the phone. It watches the hook switch,
plays greetings, records messages, rings, and pushes every recording to the PhoneHome
server. It can run fully **simulated** on a desktop, so it's usable before any hardware exists.

## Try it on your desktop (Windows, macOS, Linux)

Requires Python 3.11+.

```powershell
cd agent
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -e ".[audio,dev]"
phonehome-agent run --simulate
```

- The **keyboard** is the phone. **Space** picks up and hangs up. **r** rings, **m**
  rings into memory lane, **p** rings into a prompt, **s** prints status, **q** quits.
- Or open the **sim panel** at <http://127.0.0.1:8081/sim>, which has a big Pick
  up / Hang up button and ring buttons.
- Your computer's speakers are the earpiece and the ringer, and its mic is the handset mic.
- Messages land in `./sim-data/outbox/` as `.flac` + `.json`. Too short or silent takes
  go to `./sim-data/discarded/`.

No sound device (or a headless box)? Add `--virtual-audio`. A fake caller "talks" into
the mic, and you can toggle that from the sim panel. `--output-wav out.wav` records
everything the phone plays, and `--input-wav file.wav` uses a file as the caller.

```bash
phonehome-agent tones --out tones/   # listen to the built-in chime, beep, rings...
phonehome-agent devices              # list sound devices (pick audio.device on the Pi)
phonehome-agent check -c config/agent.example.toml
```

## What a call does

```
lift → (300 ms settle) → greeting (default: a soft chime) → beep → record
     → hang up: saved if ≥ 2 s and not silent, otherwise discarded
     → at the length limit (default 3 min, warning pips 30 s before): falling chime, quiet "please hang up" boops
ring → nobody answers in 30 s: stop
     → answered: memory lane (an old message, then you can record a reply)
                 or a prompt ("What's been on your mind lately?") → beep → record
```

All of the numbers above, plus greetings, prompts and the ring schedule, come from the
server's web app (`behavior.json`). The agent caches the last copy so it keeps working
when the server is down.

## Layout

| Module | Role |
|---|---|
| `controller.py` | the call state machine |
| `audio/engine.py` | one persistent full-duplex stream + mixer; real (PortAudio) and virtual backends |
| `audio/tones.py` | synthesized built-in sounds |
| `recording.py` | crash-safe recording (raw PCM → FLAC), validation, power-loss recovery |
| `behavior.py` | server-managed settings; greeting/prompt rule selection |
| `scheduler.py` | random and scheduled rings |
| `sync.py` | uploads with checksum verification, heartbeat, config and asset sync, memory-lane fetch |
| `api.py` | control API for the server (+ the sim panel) |
| `hardware/` | GPIO (gpiozero) and simulated hook/amp/LED/ringer switch |
| `store.py` | on-device folders: `incoming/ outbox/ sent/ discarded/ assets/` |

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The suite runs the real state machine against the virtual audio engine at 40× speed. It
covers every call path, the length limit, silence and too-short discards, ringing, memory
lane, shutdown mid-recording, power-loss recovery, upload checksum verification, config
and asset sync against a fake server, the scheduler's odds, and the control API.

## Deploying to the Pi

See [`../deploy/ansible/README.md`](../deploy/ansible/README.md) and
[`../docs/hardware-notes.md`](../docs/hardware-notes.md).
