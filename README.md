# PhoneHome

A wall-mounted 90s-style landline phone that works as a permanent family guest book.
Pick up the handset, hear a greeting, leave a message. Messages are archived on the home
server, transcribed locally, and browsable from desktop or mobile. Now and then the phone
rings on its own, to play an old memory or ask a new question.

- **Plan:** [docs/PLAN.md](docs/PLAN.md)
- **Parts list:** [docs/parts-list.md](docs/parts-list.md)
- **Hardware notes** (what the software needs from the hardware): [docs/hardware-notes.md](docs/hardware-notes.md)
- **Try the phone on your desktop:** [agent/README.md](agent/README.md)
- **Run the server (desktop or Proxmox):** [server/README.md](server/README.md) · [deploy/docker/README.md](deploy/docker/README.md)

Components:
- `agent/`: runs on the Raspberry Pi Zero 2 W inside the phone (audio, hook switch, ringer). **Built; runs in simulation**
- `server/`: archive, transcription, search, greetings, and the API; runs in Docker in an LXC on Proxmox. **Built**
- `web/`: the browser app for desktop and mobile (SvelteKit, built to static files). **Built**
- `deploy/`: Ansible (Pi) and Docker Compose (server)
- `tools/`: Windows backup script
