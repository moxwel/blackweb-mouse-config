# Blackweb gaming mouse protocol

This protocol was reverse-engineered from `Blackweb Gaming AP.exe`, a 32-bit C++Builder build from May 2019 (SHA-256 `16c947cb…8902`). It comes from static analysis. Device matching, connecting and reading have since been confirmed on a real MA32AIY (PID 1170). Addresses below are virtual addresses in that exe.

## Devices
| Model | VID:PID | Notes |
|---|---|---|
| MA32AIY (Blackweb mouse) | 3938:1170 | 49-byte settings block + 2-byte checksum |
| AYC | 3938:1093 | same protocol, 48-byte settings block (no fluid-direction byte) |
| AYU | 3938:1146 | same as AYC |
| K90 (keyboard) | 3938:1095 | different interface (usage page FF19, report 9), not covered here |

The INI files store VID and PID as hex. They are read with the hex flag set, and the matching writer uses `%04X`.

The tool talks to the HID interface with usage page **0xFF32** and usage 0xFF32. Its input and output reports are 64 bytes and include the report ID (matcher at `0x452758`).

## Transport (`0x434e2c`, generic for all devices)
Output report, ID **4**, 64 bytes:
```
[04][cmd][addr lo][addr hi][len][data … up to 59 bytes][zero pad]
```
The device acknowledges with an input report, also ID 4, that echoes `cmd` in byte 1 (`0x4353c6`). The app waits up to 5 s for it. Read replies echo `addr` and `len` in bytes 2–4 and carry the requested data at byte 5. The app stores the data at the echoed address (`0x452a37`), so `bwmc.py` drops replies whose address does not match the request.

| cmd | meaning | used by |
|---|---|---|
| 01 | hello / init session | connect `0x44bf40` |
| 02 | begin write session | apply `0x44c338` |
| 03 | read `len` bytes at `addr` (chunks of ≤59) | `0x44be90` |
| 04 | write data at `addr` (flash) | `0x44bee0` |
| 05 | write data at `addr` (settings block) | `0x44bee0` with flag |
| 06 | erase 512-byte flash sector at `addr` | apply |
| 07 | end session / commit | apply |

Reply `82` exists in the input handler and copies data to a buffer nothing reads. No mouse code sends cmd 82; only the K90 keyboard's code sends variable commands. Treat it as unknown.

There is also a live-preview path. It sends **feature report 0x20** with the 49 settings bytes followed by `32 32` in place of the checksum (`0x44c27c` → `0x435018` → `HidD_SetFeature`, resolved at `0x4d60d7`). That tail is `32 32` only when both arguments are 0; otherwise it is `00 00`. The app sends the preview while you move sliders. `bwmc.py` skips it and always does a full save.

**Status report** (input report ID 4, byte 1 = `01`, `0x452868`). The device sends it in reply to cmd 01, and also on its own when the polling rate, DPI slot or LED mode is changed on the mouse.

| byte | content |
|---|---|
| 02 | polling code (accepted only if 1, 2, 4 or 8) |
| 03 | active DPI slot (accepted only if < 6) |
| 12 | LED mode/effect byte (same as settings offset 02) |
| 13 | unknown (stored at `0x834578`, never read) |
| 15–26 | 9 × u16le: the DPI value of level 0–8 |

The DPI table is the only source of DPI values. The exe has no built-in list, and it shows "---" until the mouse has reported (`0x44d3be`).

## Memory map
| addr | size | content |
|---|---|---|
| 0xBE00 | 0x33 | settings block (last 2 bytes = checksum) |
| 0xC000 | 0x1000 | 8 button sectors × 0x200 |

**Checksum** (`0x44af84`): `u16le(0x8032 + Σ bytes)`. It covers the first 0x31 bytes of the settings block and the first 0x1FE bytes of each sector.

**Apply sequence:**
1. `02`
2. `05 @BE00` (settings block)
3. For each changed sector: `06 @sector`, then `04 @sector` ×512 bytes
4. `07`. The app sends this even when an earlier step failed (`0x44c472`), and so does `bwmc.py`.

Steps 2 and 3 are skipped for blocks that already match what was read from the device, unless a forced save was requested. After connecting, the app checks the device's checksums and every button record (`0x44bc58`). If any check fails, it writes its own profile to the mouse.

## Settings block (MA32AIY, offset → meaning)
| off | meaning |
|---|---|
| 00 | polling: 1=1000 Hz, 2=500, 4=250, 8=125 |
| 01 | active DPI slot 0–5 |
| 02 | bit7: LED mode (0 single colour, 1 RGB); bits0–6: effect (0 PULSE, 1 BLINK, 2 ON, 3 FLUID) |
| 03 | light speed (1 fast … 3 slow) |
| 04 | brightness (0, 1, 2, 4; the app maps slider position 3 to 4. The AYC/AYU save path skips this, but their preview applies it) |
| 05–22 | 6 DPI slots × `[enabled][level 0–8][R][G][B]`; the DPI value of each level comes from the status report |
| 23–25 | not touched by the UI (defaults `00 0a 0a`) |
| 26 | bitmask: buttons assigned SHIFT (derived) |
| 27–28 | u16 bitmask: buttons assigned Smart Key (low byte = primary layer, high byte = shift layer; derived) |
| 29 | Smart-key delay 1–255 |
| 2A–2C | logo colour RGB |
| 2D–2F | side colour RGB |
| 30 | fluid direction (0 right, 1 left), MA32AIY only |
| 31–32 | checksum |

## Button sector (×8, primary layer at +0, shift layer at +0xFE, checksum at +0x1FE)
Each layer is a 254-byte action record:
```
[type][label id][mode][n][n × 2-byte tokens]
```
* `type`: UI category. 0 = mouse, 5 = media, 6/7 = shortcuts/windows, 8 = macro, 9 = device function. bit7 set = "repeat macro until any key is pressed".
* `label id`: index into the app's string table; used only by the UI.
* `mode`: 0 = active while the button is held, N = play N times (double-click uses 2, fire uses 3).

Tokens are `[value][kind]`:

| kind | meaning |
|---|---|
| 00–0F | key press; value = HID key usage; low nibble = modifier mask (1 Ctrl, 2 Shift, 4 Alt, 8 Win) |
| 10–1F | consumer (media) usage `((kind & 0xF) << 8) \| value` |
| 20 | mouse button press, value mask (1 L, 2 R, 4 M, 8 Back, 10 Fwd, 80 tilt-L, 40 tilt-R) |
| 21–2F | wheel; low nibble = signed delta (`21` up, `2F` down) |
| 30 | device function: 01–03 rate cycle/+/−, 04–06 DPI cycle/+/−, 07 shift, 08 smart key, 80 launch program, C0 open driver, C1–C3 profile cycle/+/− (80 and C0 rely on the vendor app) |
| +0x40 | release variant of 00/10/20 (for example `60` = mouse button up) |
| C0–FF | delay in ms = `((kind & 0x3F) << 8) \| value` |

The complete predefined action table is at `0x6b0c9c` (60 records). All 60 are copied byte for byte into `bwmc.py`. Label names come from a DFM `Items.Strings` list at `0x9ad2f1`.

"Single Key" (label 0x13) is not in the table; the UI builds it. `bwmc.py`'s `keys:` uses type 6 with label 0x13 and the same token layout as Copy/Paste. The tokens match the app's records, but the type and label bytes are inferred.

The app refuses to change a button unless another button's primary layer is still left-click, label 0x0D (`0x44d8a0`). `bwmc.py` applies the same rule.

## Host-only files
* `config/*.MA32AIY` is a 43,359-byte profile. It is a dump of the app's struct:
  * 0x98EC: 4 KiB flash image
  * 0xA8EC: settings block
  * 0xA91F: app-only data
  * File checksum: `0x49cb + Σ` over the first 0xA95D bytes
* `*.AYC` and `*.AYU` profiles are 43,358 bytes, because their settings block is one byte shorter. Their file checksum covers 0xA95C bytes. The seed is `0x49cb` for AYC and `0x4a7e` for AYU (`0x4431d8`).
* There are 5 profiles, but they exist only in the app. The mouse stores one configuration.
* `macro.ted` is the app's macro library: a u16 count, then 100 slots of 0x127 bytes, then a u16 checksum `0xDBDB + Σ` over everything before it (`0x412ba8`). Each slot holds:
  * +0x00: name, 16 UTF-16 characters
  * +0x20: 9 bytes of app-only metadata
  * +0x29: the 254-byte action record

## Usage
See [README.md](README.md).
