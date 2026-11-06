# Provisioning the phone's Raspberry Pi

> **Status: written ahead of the hardware, not yet run against a real Pi.**
> Expect small fixes during the bench build (phase 2).

One command takes a freshly flashed SD card (Raspberry Pi OS Lite 64-bit,
SSH enabled, your key installed via Raspberry Pi Imager) to a working phone,
and the same command later deploys updates:

```bash
# from WSL on the desktop, or from the server LXC
cd deploy/ansible
cp inventory.example.ini inventory.ini        # set the Pi's address
cp vars.example.yml vars.yml                  # set server URL + tokens (or use ansible-vault)
ansible-playbook -i inventory.ini pi.yml
```

What it does:

| Step | Why |
|---|---|
| apt: `python3-venv python3-gpiozero python3-lgpio libportaudio2 libsndfile1 alsa-utils` | runtime deps (GPIO libs come from Raspberry Pi OS, not pip) |
| `phonehome` system user in `audio` + `gpio` groups | least privilege |
| agent source → `/opt/phonehome/src`, venv with `--system-site-packages` | gpiozero/lgpio from apt are visible in the venv |
| `/etc/phonehome/agent.toml` from template | device config + tokens |
| systemd unit with `WatchdogSec` | auto-restart if the agent or audio hangs |
| hardware watchdog (`RuntimeWatchdogSec=15`) | auto-reboot if the whole Pi hangs |
| journald in RAM, swap off | fewer SD card writes |
| polkit rule: `phonehome` may reboot | the web app's "Reboot phone" button |
| DS3231 RTC overlay (`phonehome_rtc: true`) | correct timestamps even with no network after a power cut |

Manual, once, during the bench build: set mixer levels with `alsamixer -c <card>`
(turn **Auto Gain Control off**, **Mic Playback** (hardware monitor) **muted**,
capture gain so normal speech peaks around -12 dBFS on the status page), then
`sudo alsactl store` so they persist.
