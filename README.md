# PhoneHome

A wall-mounted 90s-style landline phone that works as a permanent family guest book.
Pick up the handset, hear a greeting, leave a message. Messages are archived on the home
server, transcribed locally, and browsable from desktop or mobile. Now and then the phone
rings on its own, to play an old memory or ask a new question.

- **Plan:** [docs/PLAN.md](docs/PLAN.md)
- **Parts list:** [docs/parts-list.md](docs/parts-list.md)
- **Hardware notes** (what the software needs from the hardware): [docs/hardware-notes.md](docs/hardware-notes.md)
- **Try the phone on your desktop:** [agent/README.md](agent/README.md)

Components:
- `agent/`: runs on the Raspberry Pi Zero 2 W inside the phone (audio, hook switch, ringer). **Built; runs in simulation**
- `server/`: archive, web app, transcription; runs in an LXC on Proxmox
- `web/`: the browser UI for desktop and mobile
- `deploy/`: Ansible (Pi) and Docker Compose (server)
- `tools/`: Windows backup script
