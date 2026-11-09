# PhoneHome — Project Plan

> A wall-mounted 90s/2000s telephone that works as a permanent family guest book.
> Pick up the handset, hear a greeting, leave a message. Every message is archived
> forever, transcribed locally, and browsable from desktop or phone.
> Every so often, the phone rings on its own.

Status: **Approved, v1** · Phases 1, 3, 5 and 7 built: everything that doesn't need hardware · Last updated 2026-11-09

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

**Where the web app runs:** on the **Proxmox server**, not the Pi. You bookmark the
server's address in Homepage. The Pi only runs a small, token-protected control API that
the server talks to (ring now, status, logs, restart). Nobody browses to the Pi directly.
That keeps the Pi minimal, means the archive is viewable even when the phone is
unplugged, and keeps all the heavy lifting (transcription, search, exports) on the
machine with the CPU and storage.

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
3. **Room inside.** After removing the phone's main PCB you need about **80 × 45 × 35 mm**
   for the Pi Zero 2 W and its PoE board stacked together (65 × 30 mm footprint). Add room
   beside it for the USB sound card (about 60 × 20 mm), the small amp and the speaker.
   Check teardown photos or measure. Most phones from this era have plenty of hollow space.
4. **Modular handset cord** (4P4C / RJ22 at both ends). This makes the handset easy to
   test and replace.
5. **Bonus features to repurpose:** a ringer hi/lo/off slide switch (becomes a physical
   "do not ring" switch) and an "in use" or "message waiting" LED (becomes a "new
   messages" light).

Practical tip: thrift stores and eBay have these for $10–30. **Buy two.** One to tear
down and learn on, one for the final build (or as a spare handset).

### 3.3 Bill of materials

**The full shopping list, with quantities, what to search for and what to check before
buying, is in [parts-list.md](parts-list.md).** Summary:

| Part | Why | ~Cost |
|---|---|---|
| **Raspberry Pi Zero 2 W** (WH version, header pre-soldered) | Plenty of power: the agent uses ~5% of one core and ~63 MB of 512 MB. Small, cool, low power | $15–18 |
| **PoE + Ethernet + USB hub board for the Pi Zero** (802.3af) | The Zero has no Ethernet or PoE. One board adds both plus USB ports for the sound card. One cable to the wall | $20–25 |
| **USB sound card × 2** (C-Media CM108/CM119) | Stereo out (L = earpiece, R = ringer) + mic in, one stream. The second is a spare | $16–24 |
| **PAM8302 amp + 8 Ω speaker** | The ringer, with an enable pin so it's silent when idle | $7 |
| **DS3231 real-time clock** | Correct timestamps after power cuts, even with no internet | $5–14 |
| **High-endurance microSD × 2** | Second one pre-flashed as a spare | $20–24 |
| Electret mic capsules, 3.5 mm pigtails, 4P4C jacks, proto board, connectors, standoffs, heatsink | Wiring, everything detachable | $30 |
| Wall install: low-voltage bracket, RJ45 keystone, short flat patch cable | The phone hangs on a printed plate over the box | $10–15 |
| The phone × 2 | One to learn on, one to build | $20–60 |
| **Total** | | **≈ $145–205** |

**Why a Zero 2 W rather than a Pi 4:** transcription, search and exports all run on the
server, so the phone only plays, records and uploads. Measured with the finished agent:
about 0.4% of a desktop core, which works out to roughly 5% of one Zero 2 W core, and
about 63 MB of RAM. The Zero is smaller (fits more phones), runs cooler in a closed
plastic shell, uses 1–2 W instead of 3–4 W, and costs less.

**The one risk to check at the bench build:** on the Zero, USB audio through a hub has a
history of occasional crackles or dropouts. The agent counts audio glitches (`xruns` on
the status page), so a 10-minute test settles it. Fallbacks, in order: a different sound
card or hub port; plug the sound card straight into the Zero's own USB port and use
Wi-Fi with a PoE splitter for power only; or go back to a Pi 4 (the software is
unchanged either way).

PoE budget: about 1–2 W idle, under 4 W while ringing. **802.3af is plenty.**

### 3.4 Wiring

```
 Wall RJ45 (PoE) ──► Zero PoE/Ethernet/USB-hub board ──┬── 5 V ───────► Pi Zero 2 W power
                     (attached to the Pi Zero)         ├── Ethernet ──► Pi (via its USB bus)
                                                       └── USB-A port ─┐
                                                                       ▼
 Hub USB ──► USB sound card
              ├── Headphone L ─────────────────────────────► Handset earpiece (via 4P4C handset jack)
              ├── Headphone R ──► PAM8302 IN ──► OUT ──────► Ringer speaker
              └── Mic in (plug-in power) ◄───────────────────  Handset electret mic (polarity matters!)

 Pi GPIO 17 ◄── hook switch ── GND        (internal pull-up, 100 nF cap, software debounce)
 Pi GPIO 24 ◄── ringer switch "off" ── GND   (optional: physical "don't ring" switch)
 Pi GPIO 25 ◄── ringer switch "low" ── GND   (optional: quieter ringing)
 Pi GPIO 2/3 (I²C) ◄──► DS3231 RTC        (3.3 V + GND)
 Pi GPIO 22 ──► PAM8302 SD                (amp enable, ringer only)
 Pi GPIO 23 ──► 330 Ω ──► LED ── GND      (optional: "new messages" / "recording" light)
 PAM8302 VIN/GND ◄── Pi 5 V / GND
```

Full pin map with physical pin numbers: [hardware-notes.md](hardware-notes.md#gpio-pin-map-bcm-numbering).

Notes:
- **Identify the handset pairs with a multimeter.** The earpiece reads roughly 30–300 Ω.
  An electret mic has polarity: the case is usually ground. Pinouts vary by phone, so we
  will document yours in `hardware/` once it arrives.
- **Sidetone.** Real phones play your own voice faintly in the earpiece. Without it the
  handset feels "dead." The agent does this in software (about 10 ms delay, adjustable or
  off), so the card's hardware "Mic Playback" monitor should be **muted**.
- **Noise.** Keep the mic leads short and twisted (or shielded) and route them away from
  the amp output and the PoE board.
- **Heat.** The Zero 2 W runs cool at this load, but the PoE board's power converter
  adds some heat. Monitor CPU temperature on the dashboard and add a few discreet vent
  holes on the bottom or back if it runs above ~65 °C.

### 3.5 Mounting and serviceability
- **3D-printed internal carrier.** One printed plate holds the Pi + PoE board stack, sound
  card, amp, clock module and speaker, and screws into the phone's existing PCB bosses. It's designed
  once the phone is measured. Source files (parametric, e.g. OpenSCAD or STEP) plus STLs
  live in `hardware/cad/`.
- A printed **wall bracket/adapter** if the phone's mounting holes don't line up with a
  standard wall-phone plate over the low-voltage box.
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
| `server/worker` — **transcriber** | Same container, separate process | `faster-whisper` on CPU (int8), **English only** (language forced to `en`, which is faster and avoids misdetection on short clips). Start with `large-v3-turbo` or `medium.en` and benchmark. Your iGPU (OpenVINO/Vulkan via whisper.cpp) is an optional later speed-up |
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
- **Default greeting: a pleasant tone, no speech.** Pick up → a soft chime → a short beep
  → recording. It's obvious the phone records, so the greeting doesn't announce it.
  Spoken or seasonal greetings can be added any time.
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
- **Replay eligibility:** every message is eligible by default. Each message still has
  a `replay_eligible` flag, so we can add filtering later (exclude a message, favorites
  only, older than N months) without changing the data format. Recording and archiving
  come first; the ringer is a later phase.

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
| **1. Agent MVP (simulated)** ✅ | State machine, greeting playback, FLAC recording, local outbox | Pick up a "virtual phone" on your desktop and leave a message |
| **2. Bench build** | Pi + sound card + handset + hook switch on the desk, outside the shell | Leave a real message through a real handset. Tune mic gain, sidetone and volume |
| **3. Server MVP** ✅ | Ingest API, archive layout and sidecars, timeline and playback UI, agent upload. *First built with a server-rendered UI, replaced by the SvelteKit app in phase 5* | See messages on your desktop seconds after hanging up |
| **4. Install** | Pi in the phone shell, PoE, Cat6 pull, wall mount, Ansible deploy | **It's on the wall and working** |
| **5. Make it an archive** ✅ | Transcription, search, tags/people/notes editing, On this day, greetings manager | Search "Thanksgiving" and find every message about it |
| **6. The ringer** | Speaker + amp, ring scheduling, random rings, prompts, memory lane | The phone calls you |
| **7. Durability** ✅ | Export zip + offline viewer, `backup.ps1`, integrity checks, Homepage widget, health page | Back up everything to an off-site drive in one step |
| **8. Polish → "done"** | Docs, recovery runbook, pin dependencies | Forget about it for a few years |

**Phase 7 notes:** the archive keeps its own offline `index.html` (rebuilt after every
change), so the NAS share, an export zip and a `backup.ps1` copy are all browsable without
PhoneHome. The integrity check runs weekly (`PHONEHOME_INTEGRITY_CHECK_DAYS`) and its
result appears on the Backup page, the timeline (only when something's wrong) and the
Homepage widget. `backup.ps1` never overwrites a recording that differs; it reports which
side is damaged instead.

**Phase 5 notes:** greetings and prompts live in the archive as `greetings/<id>.json` +
`.flac` (+ the original upload), and the built-in chime is a greeting like any other so it
can join a rotation. The phone's behavior document is now settings (`behavior.json`) +
those clips, merged when the phone asks. Recording greetings in the browser needs HTTPS or
localhost (a browser rule), so a reverse proxy with a certificate is worth setting up.

**Built ahead of schedule in phase 3** (small enough to include): tags/people/notes
editing, favorites, trash with restore, phone status page with Ring now / test tones /
restart, the Homepage widget endpoint, `phonehome-server verify` against `SHA256SUMS`,
and the "new messages" LED signal in the heartbeat reply.

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
  hardware/               ← wiring diagrams, measurements
    cad/                  ← 3D-printed carrier and wall bracket (source + STL)
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

## 10. Decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Phone interface | **Direct wiring** to USB sound card + GPIO (§3.1) |
| 2 | Where the web app runs | **Proxmox server** (LXC). The Pi only has an internal control API |
| 3 | Memory lane replay | **All messages eligible** by default; filtering can be added later |
| 4 | Frontend | **SvelteKit** (static build) |
| 5 | Transcription | **English only**, local |
| 6 | Default greeting | **A pleasant tone, no speech**, and no recording notice |
| 7 | Mounting | **3D-printed** internal carrier and wall bracket |
| 8 | Phone computer | **Raspberry Pi Zero 2 W + PoE/Ethernet/USB-hub board** (§3.3). Pi 4 is the fallback |
| 9 | Clock | **DS3231 RTC module** (added after writing the agent) |

Still open (not blocking):
- **Exact phone model.** Pick using the §3.2 criteria. Pinouts and measurements get documented in `hardware/`.
- **Switch brand/model.** Only matters if we automate PoE power-cycling later.
