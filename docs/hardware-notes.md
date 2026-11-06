# Hardware notes: what the software needs from the hardware

These requirements came out of writing the agent. Read them **before buying parts**.
The shopping list in [parts-list.md](parts-list.md) reflects all of this.

## Must-knows before you buy

### 1. The USB sound card must be stereo out, mono mic in, full duplex at 48 kHz
The agent opens **one** audio stream on **one** card and splits it:

- **left channel → handset earpiece**
- **right channel → ringer amp → speaker**
- **mic in ← handset mic**

So the card needs:
- **stereo output**
- a **separate mic jack with plug-in power** (bias voltage for electret mics)
- **playback and recording at the same time at 48 kHz**

C-Media CM108/CM119-based adapters do all of this. To check a card, look for USB vendor
ID `0d8c` in reviews or in `lsusb`. Avoid:
- single combo (TRRS) jack adapters
- USB-C "headphone dongles" (often no usable mic input)
- anything that says "mono output"

**Buy two.** They're about $10, one is a drop-in spare, and individual cards differ
(noise, mixer controls), so it's nice to compare on the bench.

Mixer settings to apply once on the bench, then `alsactl store`:
- **Auto Gain Control: OFF.** It pumps the background noise up and down between words.
- **Mic Playback (hardware monitor): muted.** The agent does sidetone in software; leaving
  this on doubles it.
- **Capture gain:** set so normal speech peaks around −12 dBFS. The status page shows a
  live mic level for this.

### 2. Add a DS3231 real-time clock module (~$5–14)
Timestamps are the backbone of the archive. The Pi Zero 2 W has no clock: after a power cut it
boots with the time of its last shutdown and waits for the internet to correct it.
After a power outage the router and internet are often the last things to come back.
Anyone who picks up the phone in that window would get a message dated wrongly forever.

- The agent records `clock_synced: true/false` in every message's metadata, so a bad
  date can be spotted and fixed later. It never refuses to record.
- A DS3231 makes the problem go away. It uses I²C on physical pins 1, 3, 5 and 6, has
  no conflicts with anything else, and the provisioning playbook enables it
  (`phonehome_rtc: true`).
- The **Adafruit DS3231** (product 3013, CR1220 coin cell) is the no-surprises choice.
  The cheap **ZS-042** board also works, but it has a charging circuit meant for a
  rechargeable LIR2032. With a normal CR2032, remove its charging resistor or diode
  first, or the battery can swell.

### 3. The ringer is a speaker driven by an amp with an enable pin
- The **PAM8302** is on the list because it has an **SD (shutdown) pin**. The agent turns
  the amp on 50 ms before ringing and off afterwards, which avoids hiss when idle and the
  "pop" at turn-on. Whatever amp you buy needs an enable/shutdown pin.
- Use an **8 Ω, 1–2 W, 28–40 mm speaker**. The phone's original piezo ringer can be driven
  instead, but a speaker can actually play the bell or warble sample you choose in the web app.
- Ring volume is set in software (default −12 dB) and also with the PAM8302's trim
  potentiometer. Set the pot so the *loudest* software setting is the loudest you'd ever want.

### 4. Prefer a phone with a 3-position ringer switch (High / Low / Off)
The agent reads it with two GPIO pins: **Off** = never ring, **Low** = 10 dB quieter.
That makes it a physical "do not disturb" switch. Nothing needs configuring: an
unconnected switch reads as "High".

### 5. Earpiece and mic: test them on the bench before committing to the shell
- **Earpiece.** Typical handset receivers (≈ 30–300 Ω) are fine on a headphone output.
  Software can add gain, but not beyond the card's maximum. If it's too quiet on the
  bench, a tiny headphone amp board fixes it, and you want to find that out before
  everything is glued in.
- **Mic.**
  - *Carbon* mic (a big round capsule, common in older handsets): replace it with the
    9.7 mm electret capsules on the list.
  - *Dynamic* mic: works with no bias but gives a low level, so use capture gain.
  - *Electret*: works, but polarity matters (the case is usually ground).

### 6. The hook switch can be wired either way round
Wire any hook contact between **GPIO 17 and GND**. If lifting the handset *opens* the
contact instead of closing it, set `off_hook_when = "open"` in the config. You don't need
to hunt for a particular contact type. The contact does need to be **isolated from the
phone's old circuitry**, so cut or lift its traces. Debouncing and a once-per-second
safety re-read are done in software.

## GPIO pin map (BCM numbering)

| Function | GPIO | Physical pin | Notes |
|---|---|---|---|
| Hook switch | 17 | 11 | to GND (pin 9); internal pull-up |
| Amp enable | 22 | 15 | PAM8302 SD |
| LED | 23 | 16 | via 330 Ω to GND; on = new messages, blinking = recording |
| Ringer switch "off" | 24 | 18 | optional; to GND |
| Ringer switch "low" | 25 | 22 | optional; to GND |
| RTC SDA / SCL | 2 / 3 | 3 / 5 | DS3231; power from 3.3 V (pin 1), GND (pin 6) |
| Amp power | 5 V | 2 or 4 | PAM8302 VIN, GND pin 14 |

All pins can be changed in `agent.toml`.

## Software choices that affect hardware (already decided)

- **Sidetone is done in software.** Your own voice plays faintly in the earpiece, like
  a real phone. With 256-sample blocks at 48 kHz the delay is about 10 ms, which is not
  noticeable, so the card's hardware monitor isn't needed (keep it muted). The volume
  is adjustable and can be turned off if the handset shell causes feedback.
- **Recordings survive power cuts.** Audio streams to disk as raw PCM and is synced
  every 2 seconds. On boot, anything interrupted is recovered and saved. A power cut
  loses at most the last ~2 seconds of a message.
- **SD card wear is minimal.** The journal is kept in RAM, swap is off, and recordings
  leave the Pi right after upload (local copies are kept 30 days). A 32 GB
  high-endurance card is plenty; 16 GB would be fine too.
- **Watchdogs.** The agent stops petting the systemd watchdog if audio stops flowing,
  so the service restarts. The Pi's hardware watchdog reboots it if the whole system hangs.
- **Power stays under 4 W** even while ringing (Zero 2 W + PoE board + amp), so 802.3af PoE is plenty.

### 7. Pi Zero 2 W: test USB audio through the hub early
The PoE board connects the Ethernet chip and the sound card through a small USB hub. On
the Zero's USB controller, audio through a hub has a history of occasional crackles or
dropouts with some combinations of card and hub. During the bench build:

1. Leave the phone off the hook and recording for 10 minutes while the status page is open.
2. `xruns` and `bad_input_blocks` should stay at or near 0, and the recording should
   sound clean.
3. If they climb, try the other hub port or the spare sound card. If that doesn't fix
   it, plug the card straight into the Zero's own USB port and use Wi-Fi with a PoE
   splitter for power only. If that fails too, use a Pi 4. The software is the same either way.

## Things that don't matter (so don't spend on them)
- A Pi 4 or Pi 5. All transcription happens on the server, so the Zero 2 W has plenty
  of headroom (measured: about 5% of one core and 63 MB of RAM).
- Fancy audio HATs. Speech through a phone handset doesn't benefit, and USB cards are
  easier to swap.
- A UPS/battery. Recordings are crash-safe, and the RTC keeps the time.
