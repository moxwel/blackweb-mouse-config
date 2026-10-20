# AGENTS.md

Guidance for AI coding agents working in this repository.

## What this is
Unofficial, cross-platform configurator for Blackweb gaming mice (USB VID 0x3938; MA32AIY 1170, AYC 1093, AYU 1146). It talks to the HID interface with usage page 0xFF32 through `hidapi`. The protocol was reverse-engineered from the vendor's Windows app, `Blackweb Gaming AP.exe`. `PROTOCOL.md` is the reference for it, with addresses in that exe for every claim. Keep `PROTOCOL.md` in sync whenever device-facing behaviour changes.

## Commands
```
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # (.venv/bin/... on Linux/macOS)
.venv/Scripts/python src/test_bwmc.py      # the only test file; plain asserts, prints "ok"
.venv/Scripts/python src/bwmc.py show      # read-only; safe against a real mouse
.venv/Scripts/python src/bwmc_gui.py
```
- There is no linter or build step.
- `test_bwmc.py` needs no mouse; it uses a `FakeHid` stand-in.
- The cross-checks against the vendor's own files only run when the proprietary `Blackweb Gaming AP/` folder sits in `src/`, next to `test_bwmc.py`. That folder is gitignored and must never be committed.

## Architecture
**`src/bwmc.py`** is everything device-related, in one file:
- **`CATALOGUE`**: the vendor's 60 predefined button actions, copied byte for byte from the exe table.
- **Record encoders**: `combo_record` and `macro_record`.
- **`Config`**: the device image, split into two parts:
  - `st`: the settings block at 0xBE00. It is 0x33 bytes on MA32AIY and 0x32 on AYC/AYU.
  - `fl`: 4 KiB of button flash at 0xC000. That is 8 sectors × [primary record 0xFE | SHIFT record 0xFE | checksum].
- **`Mouse`**: the transport. Packets are 64-byte output reports with ID 4: `[04][cmd][addr lo][addr hi][len][data…]`. The mouse acks by echoing `cmd`.
- **`main()`**: the argparse CLI.

**Write flow** in `main()`:
1. `load()`: cmd 1, then read the settings block and the button flash.
2. Mutate `Config`.
3. Save a timestamped backup to `src/backups/` (next to the script), before every write.
4. `save()`:
   - Call `finalize()` first. It recomputes the SHIFT and Smart-Key masks, remaps brightness 3→4, and recomputes every checksum (`0x8032 + Σ`).
   - Send `02`, then `05` to write the settings, then `06` erase + `04` write for each changed sector, then `07`.
   - Cmd `07` is sent even when a step fails.

**Rules that are easy to break:**
- DPI values are **not** constants. They come from the 9 u16 values in the cmd-1 status reply, stored in `Config.levels`. A slot stores a level index from 0 to 8.
- A read reply is only accepted when its echoed address matches the request.
- At least one button's primary layer must stay left-click (label 0x0D), the same rule the vendor app enforces.
- A record is 254 bytes: `[type][label][mode][n]` plus up to 125 two-byte tokens.

**`src/bwmc_gui.py`** (Tkinter/ttk) is a **wrapper**:
- It runs `bwmc.py` as a subprocess for every device action, streams the output into its log pane, and never opens the device itself.
- It imports `bwmc` only for its tables and encoders, which it uses to show the raw bytes a selection will write.
- It learns the mouse's current state by **regex-parsing the text output of `bwmc.py show`** in `App.parse()`. If you change `Config.show()`'s format, update `parse()` too.
- A worker thread runs the subprocesses and only talks to Tk through a queue drained by `pump()`.
- Each input row is "label | input | raw bytes | current value". Widgets store their reset value in a `.default` attribute, which `reset()` uses.
- The button diagram only knows the MA32AIY layout.

## Hardware status
Reading and writing have been confirmed on a real MA32AIY. AYC and AYU support comes from static analysis only. Changes that touch the protocol cannot be fully checked by the tests; a real mouse is needed, so say so instead of claiming they are verified.
