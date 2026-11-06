# PhoneHome parts list

A shopping checklist for one phone. The reasons behind each choice are in
[hardware-notes.md](hardware-notes.md); the wiring is in [PLAN.md §3.4](PLAN.md#34-wiring).
Prices are rough USD estimates. Search terms are given instead of exact listings,
because listings change. **Check each item's "check before buying" note.**

## 1. Computer and power

| ✓ | Qty | Part | Search for | Check before buying | ~Cost |
|---|---|---|---|---|---|
| ☐ | 1 | **Raspberry Pi Zero 2 W** (WH preferred) | "Raspberry Pi Zero 2 WH" | The **WH** version has the GPIO header pre-soldered. A plain **W** works too, but you solder a 2×20 header yourself. Buy from an approved reseller (PiShop, Adafruit, CanaKit, Micro Center) | $15–18 |
| ☐ | 1 | **PoE + Ethernet + USB hub board for the Pi Zero** | "PoE Ethernet USB HUB HAT Pi Zero" (Waveshare and others make these) | Must say **IEEE 802.3af PoE**. Some near-identical boards are Ethernet + USB only, with no PoE. It needs **at least 1 free USB-A port** for the sound card. Prefer boards that attach to the Zero's underside pads with pogo pins, which leaves the GPIO header free on top. If it uses the header instead, check that it passes the pins through | $20–25 |
| ☐ | 2 | **High-endurance microSD, 32 GB** | "Samsung PRO Endurance 32GB" or "SanDisk High Endurance 32GB" | Endurance-rated, not regular "EVO"/"Ultra". The second card is the pre-flashed spare | $20–24 |
| ☐ | 1 | **DS3231 real-time clock** | "Adafruit DS3231 Precision RTC breakout" (product 3013) | Adafruit's is the no-surprises choice (takes a CR1220 cell). A cheap **ZS-042** board also works, but remove its charging resistor or diode if you fit a normal CR2032. See [hardware-notes §2](hardware-notes.md) | $5–14 |
| ☐ | 1 | Coin cell for the RTC | CR1220 (Adafruit) or LIR2032 / CR2032 (ZS-042) | Matches the board you chose | $2 |
| ☐ | 1 | Small heatsink for the Zero 2 W | "Pi Zero heatsink" | Optional. The Zero runs cool at this load, but the phone shell is a closed box | $2 |

## 2. Audio

| ✓ | Qty | Part | Search for | Check before buying | ~Cost |
|---|---|---|---|---|---|
| ☐ | 2 | **USB sound card** | "USB audio adapter CM108" / "C-Media USB sound card" | **Stereo output + separate mic jack** with plug-in power. A C-Media chip shows as USB vendor ID `0d8c` (often listed in reviews). Avoid single combo-jack (TRRS) adapters, USB-C dongles and "mono output". The second card is a spare and a comparison on the bench | $16–24 |
| ☐ | 1 | **Adafruit PAM8302** mono 2.5 W amplifier | "Adafruit PAM8302" (product 2130) | Needs the **SD (shutdown) pin**, which the PAM8302 has. Any substitute must have an enable/shutdown pin | $4 |
| ☐ | 2 | **Small speaker, 8 Ω, 1–2 W, 28–40 mm** | "28mm 8 ohm 1W speaker" / "40mm 8 ohm speaker" | Pick the size that fits behind the phone's ringer grille. The second is for testing which sounds better | $5 |
| ☐ | 1 pack | **Electret mic capsules, 9.7 mm, 2-pin** | "9.7mm electret microphone capsule" (e.g. CMA-6542PF) | Only used if the handset mic is carbon or poor, but cheap insurance | $5 |
| ☐ | 3 | **3.5 mm stereo plug to bare-wire cable** | "3.5mm male stereo pigtail cable" | One each for the earpiece, ringer amp input and mic. The sound card stays uncut and swappable. Mic wiring: tip + ring → mic +, sleeve → ground | $6 |

## 3. Wiring and mounting (inside the phone)

| ✓ | Qty | Part | Search for | Notes | ~Cost |
|---|---|---|---|---|---|
| ☐ | 2 | **4P4C (RJ22) handset jack**, PCB-mount or breakout | "4P4C RJ22 PCB jack" / "RJ9 breakout board" | In case the phone's handset jack is on the PCB you remove | $4 |
| ☐ | 1 | Small perfboard, or Adafruit Perma-Proto Bonnet (Pi Zero size) | "Perma-Proto Bonnet" | Holds the resistors, capacitor and connectors. Nothing is soldered to the Pi itself | $5 |
| ☐ | 1 kit | JST-XH connectors (2–4 pin) with crimped leads | "JST XH connector kit pre-crimped" | Every connection is unpluggable | $8 |
| ☐ | 1 | Female Dupont jumper wires / 2×20 header connector | "female dupont jumper wires" | To connect to the Pi's GPIO header without soldering to the Pi | $4 |
| ☐ | — | 330 Ω resistor ×2, 100 nF capacitor ×2, LED (3 or 5 mm) | from any parts kit | LED only if the phone doesn't have one to reuse | $2 |
| ☐ | 1 | 24–26 AWG silicone wire, assorted colors | "26 AWG silicone wire kit" | Silicone insulation doesn't melt while soldering | $8 |
| ☐ | 1 | Heat-shrink tubing assortment | | | $5 |
| ☐ | 1 kit | M2.5 nylon standoffs and screws | "M2.5 nylon standoff kit" | Mounts the Pi stack to the 3D-printed carrier | $8 |

## 4. Wall install

| ✓ | Qty | Part | Search for | Notes | ~Cost |
|---|---|---|---|---|---|
| ☐ | — | Cat6 cable run to the phone location | | You're pulling this | — |
| ☐ | 1 | Single-gang low-voltage **old-work** mounting bracket | "low voltage old work bracket single gang" | Opens up the wall without a box | $3 |
| ☐ | 1 | RJ45 **keystone jack** (Cat6) | "Cat6 keystone jack" | Snaps into the 3D-printed wall plate | $3 |
| ☐ | 1 | **Short flat** Cat6 patch cable, 0.5–1 ft | "flat cat6 patch cable 6 inch" | Flat cable fits in the gap behind the phone | $4 |
| ☐ | — | 3D-printed wall plate with two phone-mounting studs and a keystone opening | (we design it, `hardware/cad/`) | Replaces a store-bought wall-phone plate; sized to your phone | — |

## 5. The phone

| ✓ | Qty | Part | Notes | ~Cost |
|---|---|---|---|---|
| ☐ | 2 | **90s/2000s corded wall phone**, same model if possible | Criteria in [PLAN.md §3.2](PLAN.md#32-choosing-the-phone): wall-mount, keypad on the base (not a Trimline), about 80 × 45 × 35 mm free inside plus room for the sound card, modular handset cord. Bonus: a High/Low/Off ringer switch and an LED. One to tear down, one to build | $20–60 |

## 6. Only if needed (don't buy up front)

| Part | When |
|---|---|
| Tiny headphone amp board (e.g. PAM8908/TPA6132-based) | The earpiece is too quiet on the bench even at full volume |
| 802.3af PoE splitter, 5 V micro-USB output | Fallback if USB audio through the hub crackles: power from the splitter, network over Wi-Fi |
| Raspberry Pi 4 (2 GB) | Last-resort fallback; the software is unchanged |

## Tools (you have most of these)
Soldering iron, multimeter (to identify the handset's earpiece and mic pairs), flush
cutters, wire strippers, crimper for JST-XH (or buy pre-crimped leads), hot glue, the 3D
printer, and a drywall saw for the old-work bracket.

## Estimated total
**≈ $145–205** for one phone, including a spare SD card, a spare sound card and two
phones. Without the spares and the second phone, roughly $115–150.
