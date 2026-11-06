# PhoneHome — Project Plan

> A wall-mounted 90s/2000s telephone that works as a permanent family guest book.
> Pick up the handset, hear a greeting, leave a message. Every message is archived
> forever, transcribed locally, and browsable from desktop or phone.
> Every so often, the phone rings on its own.

Status: **DRAFT for review** · Last updated 2026-11-06

---

## 1. Goals and non-goals

### What this project is for
This should be part of the house for **years, ideally decades**. Every design decision
below favors that:

| Requirement | What it means for the design |
|---|---|
| Permanent, no maintenance | PoE power, no batteries, wired Ethernet, hardware watchdog, auto-recovery |
| Never lose a message | Messages leave the Pi within seconds; the Pi is disposable and rebuildable |
| Archive outlives the software | Archive = plain open-format files (FLAC, MP3, JSON, TXT, HTML). The database is only an index and can be rebuilt from the files |
| Easy to browse and revisit | Web app on the home server: timeline, search over transcripts, tags, "On this day" |
| Manual full backups | One-click "export everything" zip plus a Windows script for your own off-site copies |
| Flexible greetings | Greetings and prompts are managed in the web UI with scheduling rules, never hard-coded |
| Grows over time, then settles | Modular code, simulated hardware for development, low-dependency stack |

### Non-goals (for now)
- Real phone-line or VoIP calling. It is never a working telephone.
- Cloud anything. Everything runs on your LAN; remote access is through your own VPN.
- Dial/keypad features. Not in v1, but the hardware and software leave room for them (§9).

---

## 2. System overview

```
                         ┌──────────────────────── Proxmox mini PC ────────────────────────┐
  ┌─────── Wall ───────┐ │  LXC "phonehome" (Debian + Docker)                               │
  │  90s wall phone    │ │  ┌──────────────────────────────┐   ┌─────────────────────────┐  │
  │  ┌──────────────┐  │ │  │ phonehome-server (FastAPI)    │   │ transcription worker    │  │
  │  │ Raspberry Pi │◄─┼─┼─►│  • ingest API for the phone   │──►│ faster-whisper (CPU)    │  │
  │  │ phone-agent  │  │ │  │  • web app + archive browser  │   │ (iGPU later, optional)  │  │
  │  └──────────────┘  │ │  │  • greetings/prompts/schedule │   └─────────────────────────┘  │
  └─────────┬──────────┘ │  │  • export / backup / health   │                                │
            │ PoE        │  └──────────────┬────────────────┘                                │
   ┌────────┴────────┐   │                 │ bind mount                                      │
   │ Managed PoE+    │   │        /archive (NAS share, mounted on the Proxmox host)          │
   │ switch          │   └───────────────────────────────────────────────────────────────────┘
   └─────────────────┘                     ▲
                                           │ SMB / HTTPS export
                                   ┌───────┴────────┐
                                   │ Windows desktop│  manual full backup → off-site
                                   └────────────────┘
```

**Roles**
- **Phone (Pi):** captures audio and nothing else important. It plays greetings, records,
  rings, and pushes each recording to the server right away. It keeps working if the
  server is down (it queues uploads and uses cached greetings).
- **Server (LXC on Proxmox):** the source of truth. It stores the archive on the NAS,
  runs the web app, transcribes, and holds all configuration.
- **Desktop:** a viewer, and the place you run manual off-site backups.

---

## 3. Hardware

### 3.1 Key decision: rewire the handset directly (recommended) vs. a SLIC module

There are two ways to connect a phone to a Pi:

| | **A. Direct wiring (recommended)** | B. SLIC module (e.g. Ag1171 / KS0835F) |
|---|---|---|
| How | Remove the phone's line circuit. Wire the handset earpiece and mic to a USB sound card and the hook switch to a GPIO pin | Keep the phone electrically stock. A small SLIC board emulates a phone line (loop current, ring voltage, audio) |
| Audio quality | **Best.** Clean mic straight into the sound card, which matters for a decades-long archive | "Telephone quality," through the phone's own hybrid circuit |
| Ringer | Software-driven speaker or piezo. **Volume fully adjustable**, any ring sound | Real ring voltage. Volume limited to the phone's hi/lo switch |
| Keypad for the future | Needs the keypad matrix wired to GPIO (about 7 wires) | Free: decode DTMF tones in software |
| Parts risk | Common, cheap, easy to replace | SLIC modules are niche, with inconsistent supply and documentation |
| Assembly | More soldering inside the phone | Less modification, but more analog tuning |

**Recommendation: A.** It gives better audio, uses only commodity parts, and everything
(ring volume, ring sounds, timing) is controlled in software. The ring volume needs to be
low, which a 90s electronic ringer handles well: those phones use a piezo or small
transducer, not a mechanical bell. That means no high voltage is involved at all.

### 3.2 Choosing the phone

You want something from the 90s/2000s, not antique. Criteria:

1. **Corded and natively wall-mount.** It mounts on a standard wall plate's two studs.
2. **Keypad on the base, not in the handset.** Trimline-style phones put the keypad in the
   handset. That makes future keypad features hard, because only 4 wires run through the cord.
3. **Room inside.** After removing the phone's main PCB you need about **95 × 65 × 30 mm**
   of free space for a Pi 4 (85 × 56 mm board). Check teardown photos or measure. Phones
   from this era usually have plenty of hollow space.
4. **Modular handset cord** (4P4C / RJ22 at both ends). This makes the handset easy to
   test and replace.
5. **Bonus features to repurpose:** a ringer hi/lo/off slide switch (becomes a physical
   "do not ring" switch) and an "in use" or "message waiting" LED (becomes a "new
   messages" light).

Practical tip: thrift stores and eBay have these for $10–30. **Buy two.** One to tear
down and learn on, one for the final build (or as a spare handset).

### 3.3 Bill of materials

| # | Part | Notes | ~Cost |
|---|---|---|---|
| 1 | **Raspberry Pi 4 Model B, 2 GB** | Built-in Ethernet, mature, low heat. A Pi 5 is unnecessary because transcription runs on the server. A Pi 3B+ also works | $45 |
| 2 | **802.3af PoE splitter, 5 V / ≥2.5 A USB-C out, with Ethernet passthrough** | Isolated output. Can sit inside the phone or in the wall box. No fan, flexible placement | $12–18 |
| 2-alt | Official Raspberry Pi PoE+ HAT | One fewer box, but adds ~20 mm height and a small fan. Use only if the phone has room | $20 |
| 3 | **USB sound card, CM108/CM119-based, separate mic and headphone jacks** | Stereo out (L = earpiece, R = ringer) plus mono mic in with plug-in power for electret mics. Avoid combo TRRS-only adapters | $8–12 |
| 4 | **Adafruit PAM8302** (mono 2.5 W class-D amp) | Drives the ringer speaker. Its SD pin goes to a GPIO pin so the amp is fully off (no hiss) when not ringing | $4 |
| 5 | **Small speaker**, 8 Ω, 1–2 W, 28–40 mm | The ringer. Alternatively drive the phone's existing piezo | $3 |
| 6 | **Electret mic capsules**, 9.7 mm (pack of 5) | Replacement in case the handset mic is carbon, dynamic or noisy | $5 |
| 7 | **High-endurance microSD, 32 GB × 2** | Samsung PRO Endurance or SanDisk High Endurance. Keep the second one flashed as a spare | $20 |
| 8 | **3.5 mm plug pigtails** (×2) | Connect the handset and amp to the sound card without cutting the card, so the card can be swapped | $5 |
| 9 | Perma-Proto board or small perfboard, JST-XH connectors, 24–26 AWG wire, heat shrink, M2.5 nylon standoffs | Everything detachable. No soldering directly to the Pi | $15 |
| 10 | Pi heatsink (passive) | The phone shell is a closed plastic box | $3 |
| 11 | **Wall install:** Cat6 run, low-voltage old-work bracket, wall-phone mounting plate (studs) with a keystone/RJ45 opening, short patch cable | The phone hangs on the studs; Ethernet enters through the back | $15–25 |
| 12 | The phone (×2) | | $20–60 |
| | **Total** | | **≈ $150–210** |

PoE budget: Pi 4 at idle is about 3–4 W, under 8 W peak with the amp ringing.
**802.3af is plenty.** Your PoE+ switch is more than enough.

### 3.4 Wiring

```
 Wall RJ45 (PoE) ──► PoE splitter ──┬── 5 V USB-C ──────────► Pi 4 power
                                    └── Ethernet (data) ─────► Pi 4 RJ45

 Pi USB ──► USB sound card
              ├── Headphone L ─────────────────────────────► Handset earpiece (via 4P4C handset jack)
              ├── Headphone R ──► PAM8302 IN ──► OUT ──────► Ringer speaker
              └── Mic in (plug-in power) ◄───────────────────  Handset electret mic (polarity matters!)

 Pi GPIO 17 ◄── hook switch ── GND        (internal pull-up, 100 nF cap, software debounce)
 Pi GPIO 27 ◄── ringer on/off switch ── GND   (optional: physical "don't ring" switch)
 Pi GPIO 22 ──► PAM8302 SD                (amp enable, ringer only)
 Pi GPIO 23 ──► 330 Ω ──► LED ── GND      (optional: "new messages" / "recording" light)
 PAM8302 VIN/GND ◄── Pi 5 V / GND
```

Notes:
- **Identify the handset pairs with a multimeter.** The earpiece reads roughly 30–300 Ω.
  An electret mic has polarity: the case is usually ground. Pinouts vary by phone, so we
  will document yours in `hardware/` once it arrives.
- **Sidetone.** Real phones play your own voice faintly in the earpiece. Without it the
  handset feels "dead." Most CM108 cards have a hardware "Mic Playback" mixer control that
  gives zero-latency sidetone. We tune it during the bench build.
- **Noise.** Keep the mic leads short and twisted (or shielded) and route them away from
  the amp output and the PoE splitter.
- **Heat.** Monitor CPU temperature from the dashboard. Add a few discreet vent holes on
  the bottom or back if it runs above ~65 °C.

### 3.5 Mounting and serviceability
- The Pi sits on nylon standoffs on a plate glued or screwed into the phone shell, or a
  3D-printed carrier if you have access to a printer.
- Every connection to the phone's parts uses connectors, so the Pi, sound card and amp
  can each be pulled out without desoldering.
- The phone lifts off its wall studs → unplug Ethernet → the whole unit is on your bench.

---

## 4. Software architecture

### 4.1 Components and stack

| Component | Runs on | Stack |
|---|---|---|
| `agent/` — **phone-agent** | Pi (Raspberry Pi OS Lite 64-bit) | Python 3.11+, asyncio, `gpiod`/`gpiozero`, `sounddevice` + `soundfile` (FLAC), tiny FastAPI control API, systemd with watchdog |
| `server/` — **phonehome-server** | LXC on Proxmox (Docker Compose) | Python, FastAPI, SQLite (with FTS5 for search), ffmpeg, background job queue stored in SQLite (no Redis) |
| `server/worker` — **transcriber** | Same container, separate process | `faster-whisper` on CPU (int8). Start with `large-v3-turbo` or `medium` and benchmark. Your iGPU (OpenVINO/Vulkan via whisper.cpp) is an optional later speed-up |
| `web/` — **web app** | Built to static files, served by the server | SvelteKit (static adapter) + wavesurfer.js. Mobile-first and installable as a PWA |
| `deploy/` | Your desktop or the server | Ansible playbook for the Pi (rebuild from a blank SD card in one command), Docker Compose for the server |
| `tools/` | Windows desktop | `backup.ps1`, plus an offline archive viewer generator |

Why this stack: Python on both ends keeps one language for hardware and backend.
SQLite plus plain files means no database server to maintain. SvelteKit builds to static
files, so no Node runs in production and there are few moving parts years from now.

### 4.2 The call flow (phone-agent state machine)

```
            ┌──────────── on-hook at any point → save if valid, else discard ────────────┐
            ▼                                                                             │
        ┌──────┐ off-hook ┌──────────┐ done ┌──────┐   ┌────────────┐ max length ┌───────┴──┐
        │ IDLE │─────────►│ GREETING │─────►│ BEEP │──►│ RECORDING  │───────────►│ TIME UP  │
        └──┬───┘          └──────────┘      └──────┘   └─────┬──────┘  msg + beep└────┬─────┘
           │                                                 │ on-hook               │
           │ schedule /                                      ▼                       ▼
           │ "Ring now"                               ┌────────────┐         ┌──────────────┐
           ▼                                          │   SAVE +   │◄────────│ WAIT ON-HOOK │
       ┌─────────┐  off-hook  ┌───────────────────┐   │   UPLOAD   │         │ (soft "please│
       │ RINGING │───────────►│ REPLAY old msg OR │   └────────────┘         │  hang up")   │
       └────┬────┘            │ PROMPT → BEEP →   │                          └──────────────┘
            │ no answer       │ RECORDING         │
            ▼ (~30 s)         └───────────────────┘
          IDLE
```

Behavior details:
- **Debounce** the hook (~50 ms) and wait ~300 ms after pickup before playing, so a bumped
  handset doesn't trigger a call.
- **Hang up during the greeting** → nothing saved.
- **Too short or silent** (under ~2 s, or RMS below a threshold) → discarded and only logged.
  Thresholds are configurable.
- **Max length** (default 3 min, configurable) with a soft warning beep 30 s before the end.
- **Recording is written incrementally** to an `incoming/` folder. After a power loss
  mid-message, the partial file is recovered and uploaded on boot instead of lost.
- **Timestamps:** the agent doesn't start until NTP has synced (`time-sync.target`).
  The server also records its own receive time as a cross-check.
- **Uploads never block audio.** A separate task drains the outbox.

### 4.3 Greetings and prompts (built for frequent change)

All managed in the web UI. Each greeting has:
- the audio: uploaded, or recorded directly in the browser (MediaRecorder). The server
  normalizes loudness with ffmpeg `loudnorm` so every greeting plays at the same volume.
- **rules:** active date range (e.g. Dec 1–31), optional weekdays and time of day,
  priority and weight.
- Selection = the highest-priority matching rule set, then a weighted random pick. This
  covers "a default greeting," "a holiday greeting," "a birthday week greeting," and
  "a random rotation" with one mechanism.

**Prompts** (used when the phone rings) work the same way, e.g. *"What's been on your
mind lately?"* or *"Tell us your favorite memory from this year."* Each message records
which greeting or prompt it answered, so you can later browse "all answers to prompt X."

The server keeps the canonical copies. The agent syncs them to local storage and works
from its cache.

### 4.4 The ringer: "the phone calls you"

- **Triggers:** a "Ring now" button, scheduled rings (anniversaries, New Year's, dinner),
  and **random rings**.
- **Random ring settings:** allowed hours (e.g. 10:00–20:00), quiet days, an average
  frequency (e.g. ~2 per week, randomized), a minimum gap, and ring duration
  (~20–30 s, then give up).
- **When answered, the call is one of:**
  1. **Memory lane.** Plays an old message, introduced with *"This message was left on
     March 3rd, 2027…"* That intro can be generated locally with Piper TTS, or you record
     a generic intro yourself.
  2. **Prompt.** Plays a prompt, then beep, then records. The recording is linked to that prompt.
  3. A configurable mix of the two (e.g. 50/50).
- **Ring volume and sound** are set in software: a vintage bell sample, a 90s electronic
  warble, or anything you upload. The default is low.
- **Physical override:** if the phone's ringer switch is wired up, "off" disables all rings.
- **Replay safety:** guests may leave things meant only for you. Each message has a
  `replay_eligible` flag. The default is decided in review (§10).

### 4.5 Data model and archive layout (the long-term part)

**The archive folder is the product.** The app is just a nice way to view it.

```
/archive/                              ← NAS share, bind-mounted into the LXC
  README.txt                           ← explains this folder for a human in 2045
  messages/
    2026/
      11/
        2026-11-06_193210_k3f9.flac    ← original recording, never modified
        2026-11-06_193210_k3f9.mp3     ← normalized, trimmed copy for web playback
        2026-11-06_193210_k3f9.json    ← metadata sidecar (canonical)
        2026-11-06_193210_k3f9.txt     ← transcript (plain text)
        2026-11-06_193210_k3f9.vtt     ← transcript with timings (for synced playback)
  greetings/   prompts/   ring-sounds/
  .trash/                              ← soft-deleted items, purged after 30 days
  SHA256SUMS                           ← integrity manifest, updated on each new file
```

Example sidecar:
```json
{
  "schema_version": 1,
  "id": "k3f9",
  "recorded_at": "2026-11-06T19:32:10-05:00",
  "duration_s": 48.2,
  "kind": "guest",                 // guest | prompted | (future: keypad modes)
  "greeting_id": "default-2026",
  "prompt_id": null,
  "title": "Grandma's visit",
  "notes": "",
  "people": ["Grandma"],
  "tags": ["thanksgiving"],
  "favorite": true,
  "replay_eligible": true,
  "transcript": {"model": "faster-whisper large-v3-turbo", "edited": false},
  "audio": {"format": "flac", "sample_rate": 48000, "sha256": "…"},
  "agent_version": "0.3.1"
}
```

Principles:
- **FLAC master (lossless) + MP3 for playback.** Both play natively on Windows and in any
  browser. Storage is tiny: about 2–3 MB per minute of FLAC, so even 500 messages a year
  is only around 1–2 GB.
- **Sidecar JSON is canonical.** The SQLite DB is an index and cache. `phonehome reindex`
  rebuilds it entirely from the folder. When you edit metadata in the UI, the DB updates
  and the sidecar is rewritten.
- **Human transcript edits** are kept separately from the machine version, so
  re-transcribing with a better model years from now never overwrites your corrections.
- **Filenames sort chronologically** and use local time. The sidecar carries the full
  ISO timestamp with offset.

### 4.6 Web app (desktop + mobile)

| Page | Contents |
|---|---|
| **Home / Timeline** | Newest first, "unheard" badges, cards with date, length, transcript snippet and tags, one-tap play |
| **On this day** | Messages from this date in previous years, the long-term nostalgia feature |
| **Search** | Full-text search over transcripts, titles and notes (SQLite FTS5). Filters by year, tag, person, favorite and kind |
| **Message** | Waveform player, transcript that highlights as it plays (click a word to jump there), edit title/notes/tags/people, favorite, replay flag, download, delete (to trash) |
| **Greetings & Prompts** | Upload or record in the browser, preview, rules and schedule |
| **Ringer** | Random-ring settings, quiet hours, scheduled rings, ring sound and volume, **Ring now** |
| **Phone status** | Online/offline, last heartbeat, agent version, CPU temp, disk, upload queue, sound card detected, hook state, recent logs, restart agent, reboot Pi |
| **Backup** | Export the full archive, time since last export, archive stats, last integrity check |

- Login: two accounts (you and your wife) with long-lived sessions. It's LAN/VPN only,
  but this keeps it from being wide open on the network.
- **Homepage integration:** `/api/homepage` returns JSON for Homepage's `customapi`
  widget, e.g. *Phone: online · New: 2 · Total: 312 · Last: 2 days ago*.

### 4.7 APIs (sketch)

```
Phone → Server   POST /api/agent/messages     multipart: flac + meta, sha256 → 201 once stored and verified
                 POST /api/agent/heartbeat    status payload → returns current config_version
                 GET  /api/agent/config       greetings, prompts, rules, settings (ETag)
                 GET  /api/agent/assets/{id}  audio files for the cache

Server → Phone   POST /ring {mode}            ring now (memory lane / prompt / mix)
                 GET  /status · GET /logs · POST /restart · POST /reload

Web → Server     /api/messages (list, get, PATCH, DELETE) · /api/search · /api/greetings
                 /api/prompts · /api/ringer · /api/export · /api/phone/* · /api/homepage
```
The phone and server authenticate each other with a shared token (set by the Ansible
playbook). The phone only deletes a local recording after the server returns a matching
sha256.

---

## 5. Backups: the "never lose a message" plan

| Layer | What | Protects against |
|---|---|---|
| 1. Pi outbox | A recording stays on the Pi until the server confirms its checksum, then is kept 30 more days | Server or network down; upload bugs |
| 2. NAS archive | Canonical files on the NAS | Pi or SD card death (the Pi holds nothing irreplaceable) |
| 3. Proxmox backup | Your normal `vzdump` of the LXC (DB, config, users) | Container breakage. The DB is rebuildable anyway |
| 4. **Manual full export** | **Web UI → "Export everything"** streams a zip of the whole archive, including `index.html` (below) | Anything. This is the copy that goes off-site |
| 5. **Windows `backup.ps1`** | Copies new files from the NAS share (or the export endpoint) into a folder you choose, e.g. an external drive. **Additive only**, never deletes, and verifies against `SHA256SUMS` | Accidental deletion; bitrot |

**Offline viewer:** every export includes a self-contained `index.html`, a static page
listing every message with a player, date, tags and transcript. You can open it straight
from a USB drive in any browser in 20 years, with no server and no app.

The server also runs a **weekly integrity check** that re-hashes the archive against
`SHA256SUMS` and shows the result on the Backup page and in the Homepage widget.

---

## 6. Remote management (once it's on the wall)

You should never need to open the phone. In order of escalation:

1. **Web UI → Phone status page.** Health, logs, restart the agent, reboot the Pi,
   ring test, play a test tone to the earpiece. This covers almost everything.
2. **Config changes** (greetings, prompts, ring schedule, max length, thresholds) are all
   made in the web UI and sync to the phone automatically. No SSH needed.
3. **Code updates:** `ansible-playbook deploy/pi.yml` from your desktop (via WSL) or from
   the server container. It is idempotent: it installs or updates the agent, systemd
   units and config, then restarts. Server updates are `docker compose pull && up -d`
   (or a rebuild from the repo).
4. **SSH** (key-only, LAN/VPN) for anything else.
5. **Self-healing:** the systemd watchdog restarts a hung agent, the Pi hardware
   watchdog reboots a hung Pi, and the agent auto-recovers if the USB sound card drops out.
6. **Last resort:** power-cycle the PoE port from your managed switch's UI. This could
   be automated later from the server if your switch has an API.
7. **SD card died?** Flash the spare card with stock Raspberry Pi OS, run the playbook,
   and the phone comes back with its full configuration. No messages are lost, because
   they're all on the server.

**SD card longevity on the Pi:** high-endurance card, logs in RAM (volatile journald,
optionally log2ram), `noatime`, no swap. A full read-only root is possible, but makes
updates more awkward. Since the Pi is fully rebuildable, I'd skip it unless we see problems.

---

## 7. Server deployment on Proxmox

- **Unprivileged Debian LXC** with nesting enabled, running Docker Compose. Docker makes
  the app portable, so it can move off Proxmox someday without changes.
- **NAS storage:** mount the NAS share on the Proxmox host, then bind-mount it into the
  LXC (`mp0: /mnt/nas/phonehome,mp=/archive`). This is the standard pattern, because
  unprivileged LXCs can't mount SMB/NFS themselves. *Gotcha:* map the UID/GID so the
  container can write. We'll document the exact steps.
- **iGPU passthrough** (`/dev/dri`) is optional, only if CPU transcription turns out slow.
  For short clips on a modern mini PC, CPU will very likely be fine.
- **HTTPS:** through your existing reverse proxy if you have one. Plain HTTP on the LAN
  is acceptable otherwise.

---

## 8. Development approach and roadmap

**Hardware isn't a blocker.** The agent has a `--simulate` mode: the keyboard toggles the
hook and it uses the computer's own mic and speakers. Software work can start before
any parts arrive, and the logic can be tested without a phone.

| Phase | Deliverable | You'll be able to… |
|---|---|---|
| **0. Order parts** | BOM above, two phones | |
| **1. Agent MVP (simulated)** | State machine, greeting playback, FLAC recording, local outbox | Pick up a "virtual phone" on your desktop and leave a message |
| **2. Bench build** | Pi + sound card + handset + hook switch on the desk, outside the shell | Leave a real message through a real handset. Tune mic gain, sidetone and volume |
| **3. Server MVP** | Ingest API, archive layout and sidecars, timeline and playback UI, agent upload | See messages on your desktop seconds after hanging up |
| **4. Install** | Pi in the phone shell, PoE, Cat6 pull, wall mount, Ansible deploy | **It's on the wall and working** |
| **5. Make it an archive** | Transcription, search, tags/people/notes editing, On this day, greetings manager | Search "Thanksgiving" and find every message about it |
| **6. The ringer** | Speaker + amp, ring scheduling, random rings, prompts, memory lane | The phone calls you |
| **7. Durability** | Export zip + offline viewer, `backup.ps1`, integrity checks, Homepage widget, health page | Back up everything to an off-site drive in one step |
| **8. Polish → "done"** | Docs, recovery runbook, pin dependencies | Forget about it for a few years |

### Future ideas (parking lot)
- **Keypad features:** dial 1 to leave a message, 2 for a random memory, 0 for "operator"
  easter eggs. With direct wiring, the keypad matrix goes to GPIO (or an I²C expander).
  The state machine already has a slot for "kind" of call.
- Auto titles and summaries from a local LLM (e.g. Ollama on the mini PC).
- A yearly "year in review" compilation audio file.
- "New messages" LED on the phone.
- Speaker identification ("this sounds like Grandma") with local models.
- Automating PoE port power-cycling via the switch's API.

---

## 9. Proposed repository layout

```
PhoneHome/
  README.md
  docs/
    PLAN.md               ← this file
    hardware.md           ← build guide with photos, pinouts for *your* phone (later)
    operations.md         ← install, deploy, recovery runbook (later)
  hardware/               ← wiring diagrams, measurements, 3D-print files if any
  agent/                  ← phone-agent (Python package, runs on the Pi)
  server/                 ← phonehome-server + transcription worker (Python)
  web/                    ← SvelteKit frontend (builds to static files)
  deploy/
    ansible/              ← Pi provisioning and deploy playbook
    docker/               ← Dockerfile + compose for the server LXC
  tools/
    backup.ps1            ← Windows additive backup + verify
```

---

## 10. Open decisions for review

1. **Direct wiring vs. SLIC** (§3.1). I recommend direct wiring.
2. **PoE splitter vs. PoE+ HAT** (§3.3). I recommend the splitter. Final call once the phone's interior is measured.
3. **Memory lane default** (§4.4). Should old messages be replay-eligible by default, or
   only ones you explicitly mark? Leaning: *opt-in* (only favorites, or messages you
   flag), so a private message never plays to a random guest.
4. **Frontend:** SvelteKit (recommended, richer UI) vs. server-rendered HTMX (even fewer
   dependencies, simpler but less slick on mobile).
5. **Transcription language(s):** English only, or a multilingual family?
6. **Recording notice:** should the default greeting mention that messages are saved?
   It's a nice courtesy for guests.
7. **3D printer:** do you have access to one? It decides custom brackets vs. standoffs and hot glue.
8. **Switch brand/model:** only matters if we want to automate PoE power-cycling later.
