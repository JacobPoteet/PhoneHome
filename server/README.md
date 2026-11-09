# phonehome-server

The home server half of PhoneHome. It receives messages from the phone, files them into
the archive folder, makes a normalized MP3 for easy listening, and serves the web app you
browse them with. Runs in Docker on the Proxmox LXC ([deploy/docker](../deploy/docker/README.md)).

## Try it on your desktop with the simulated phone

Two terminals, Python 3.11+, and ffmpeg on your PATH (for the MP3 copies; without it,
the web app plays the FLAC originals instead).

```bash
cd server
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
PHONEHOME_ARCHIVE_DIR=./dev-archive PHONEHOME_DATA_DIR=./dev-data PHONEHOME_AGENT_TOKEN=dev phonehome-server serve
```

On Windows PowerShell, set the variables first: `$env:PHONEHOME_AGENT_TOKEN="dev"` (and the
other two), then run `phonehome-server serve`.

Open <http://127.0.0.1:8080>. The first visit asks you to create an account.

In the second terminal, run the simulated phone (see [agent/README.md](../agent/README.md))
with a small `sim.toml` that points it at the server with the same token:

```toml
[server]
url = "http://127.0.0.1:8080"
token = "dev"
```

Then run `phonehome-agent run --simulate -c sim.toml` from the `agent` folder. Pick up (space bar or the sim
panel), talk, hang up, and the message shows up in the web app a moment later. To get
the **Ring now** and test-tone buttons on the Phone page, also start the server with
`PHONEHOME_AGENT_CONTROL_URL=http://127.0.0.1:8081`.

## What it does

| | |
|---|---|
| **Ingest** | `POST /api/agent/messages` checks the shared token, checks that the FLAC is intact (sha256), writes it to `messages/YYYY/MM/`, adds a line to `SHA256SUMS`, writes the JSON sidecar, then answers with the checksum. The phone deletes nothing until that checksum matches its own. Re-sending the same message is harmless. |
| **Playback copy** | A background job runs ffmpeg: trims the leading silence, normalizes loudness, saves an MP3 next to the original. The original FLAC is never touched. |
| **Web app** | Timeline (newest first, "new" badges, favorites), a message page (player, title, people, tags, notes, download), trash with restore, phone status with Ring now / test tones / restart, and a settings page for the phone's behavior JSON. Works on a phone screen and in dark mode. |
| **Phone config** | `GET /api/agent/config` serves the behavior JSON (greetings, prompts, ringer, recording limits) with an ETag. Heartbeats carry its version, so a change reaches the phone within a minute. |
| **Memory lane** | `GET /api/agent/replay/random` picks a random message whose "may play back" box is ticked. |
| **New-message light** | The heartbeat reply tells the phone to turn its LED on while there are unheard messages. |
| **Homepage** | `/api/homepage?token=…` returns `{"phone": "online", "new": 2, "total": 312, "last": "2 days ago"}` for a `customapi` widget. |

## The archive is the product

```
/archive/
  README.txt                         explains the folder to a human in 2045
  messages/2026/11/
    2026-11-06_193210_k3f9.flac      original, never modified
    2026-11-06_193210_k3f9.mp3       normalized copy for listening
    2026-11-06_193210_k3f9.json      metadata sidecar (canonical)
  greetings/  prompts/  ring-sounds/
  .trash/                            deleted messages, purged after 30 days
  SHA256SUMS                         checksums of every audio file
```

The SQLite database in `/data` is only an index. Delete it and run
`phonehome-server reindex`, and it comes back from the sidecars. The only things you'd lose
are "heard" marks and login sessions.

## Commands

```bash
phonehome-server serve                 # web app + background worker
phonehome-server adduser NAME          # add an account or change a password
phonehome-server deluser NAME
phonehome-server reindex               # rebuild the database from the archive folder
phonehome-server verify                # re-hash every file against SHA256SUMS
phonehome-server worker                # just the job worker (if you disable run_worker)
```

## Settings

Environment variables (what Docker Compose uses) or a TOML file at `PHONEHOME_CONFIG`
(default `/etc/phonehome/server.toml`). Environment variables win.

| Variable | Default | |
|---|---|---|
| `PHONEHOME_AGENT_TOKEN` | *(required)* | Shared secret with the phone (`server.token` in the agent config) |
| `PHONEHOME_ARCHIVE_DIR` | `/archive` | The NAS share |
| `PHONEHOME_DATA_DIR` | `/data` | Database and behavior config. Local disk, not the NAS |
| `PHONEHOME_AGENT_CONTROL_URL` | *(empty)* | e.g. `http://hallway-phone.lan:8081`; enables the Phone page's buttons |
| `PHONEHOME_AGENT_CONTROL_TOKEN` | *(empty)* | The phone's `control_api.token` |
| `PHONEHOME_HOMEPAGE_TOKEN` | *(empty)* | Lets `/api/homepage` be read without logging in |
| `PHONEHOME_OFFLINE_AFTER_S` | `180` | No heartbeat for this long = offline |
| `PHONEHOME_COOKIE_SECURE` | `false` | Set `true` behind HTTPS |
| `PHONEHOME_TRASH_DAYS` | `30` | |
| `PHONEHOME_MP3_BITRATE` | `96k` | |
| `PHONEHOME_PORT` | `8080` | |

## Tests

```bash
pip install -e ".[dev]"
pip install -e "../agent"      # optional: enables the end-to-end tests
pytest
```

The suite covers ingest (checksums, duplicates, conflicts, bad ids), MP3 jobs, trash,
restore and purge (including `SHA256SUMS` bookkeeping), rebuilding the index from files,
logins, every page, and two end-to-end tests that run the **real agent** (its state
machine, virtual audio, and sync code) against this server.

## Layout

| Module | Role |
|---|---|
| `app.py` | routes: agent API, JSON API, HTML pages |
| `archive.py` | the folder layout, atomic writes, `SHA256SUMS`, trash |
| `messages.py` | ingest, edits, trash, reindex |
| `db.py` | SQLite schema (index + sessions + jobs + heartbeats) |
| `jobs.py` | SQLite-backed job queue and worker (MP3 now, transcription later) |
| `media.py` | ffmpeg |
| `phone.py` | behavior config, heartbeats, calls to the phone's control API |
| `auth.py` | accounts (scrypt) and sessions |
| `templates/`, `static/` | the server-rendered UI. A SvelteKit front end replaces this in phase 5 |
