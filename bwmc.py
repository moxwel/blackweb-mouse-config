#!/usr/bin/env python3
"""blackweb-mouse-config (bwmc): unofficial cross-platform configurator for Blackweb gaming mice
(VID 0x3938; MA32AIY / AYC / AYU). Protocol reverse-engineered from
"Blackweb Gaming AP.exe" -- see PROTOCOL.md.  Needs: pip install hidapi
"""
import argparse, pathlib, re, sys, time

VID = 0x3938
PIDS = {0x1170: "MA32AIY", 0x1093: "AYC", 0x1146: "AYU"}
USAGE_PAGE = 0xFF32
RID = 4                      # output/input report id, 64 bytes incl. id
ST_ADDR, FL_ADDR, FL_LEN = 0xBE00, 0xC000, 0x1000
POLL = {125: 8, 250: 4, 500: 2, 1000: 1}
EFFECTS = ["pulse", "blink", "on", "fluid"]
BRIGHT = [0, 1, 2, 4]        # app maps slider pos 3 -> 4


def cksum(b):
    return (0x8032 + sum(b)) & 0xFFFF


# Predefined actions, copied verbatim from the app's catalogue (0x6b0c9c, 254-byte records).
CATALOGUE = {
    "left-click": "00 0d 00 01 01 20", "right-click": "00 0e 00 01 02 20",
    "middle-click": "00 0f 00 01 04 20", "back": "00 14 00 01 08 20",
    "forward": "00 15 00 01 10 20", "double-click": "00 10 02 04 01 20 14 c0 01 60 14 c0",
    "tilt-left": "00 39 00 01 80 20", "tilt-right": "00 3a 00 01 40 20",
    "fire": "00 3d 03 04 01 20 64 c0 01 60 64 c0",
    "scroll-up": "00 3b 01 01 00 21", "scroll-down": "00 3c 01 01 00 2f",
    "media-player": "05 16 00 01 83 11", "prev-track": "05 17 00 01 b6 10",
    "next-track": "05 18 00 01 b5 10", "play-pause": "05 19 00 01 cd 10",
    "stop": "05 1a 00 01 b7 10", "mute": "05 1b 00 01 e2 10",
    "volume-up": "05 1c 00 01 e9 10", "volume-down": "05 1d 00 01 ea 10",
    "copy": "06 24 00 02 00 01 06", "cut": "06 25 00 02 00 01 1b",
    "new": "06 26 00 02 00 01 11", "open": "06 27 00 02 00 01 12",
    "paste": "06 28 00 02 00 01 19", "print": "06 29 00 02 00 01 13",
    "redo": "06 2a 00 02 00 01 1c", "save": "06 2b 00 02 00 01 16",
    "select-all": "06 2c 00 02 00 01 04", "undo": "06 2d 00 02 00 01 1d",
    "find": "07 31 00 02 00 01 09", "my-computer": "07 2e 00 01 94 11",
    "www-home": "07 2f 00 01 23 12",
    "close-window": "07 30 01 05 00 04 3d 00 0a c0 3d 40 00 44",
    "maximize": "07 32 01 09 00 04 2c 00 0a c0 2c 40 00 44 0a c0 1b 00 0a c0 1b 40",
    "minimize": "07 33 01 09 00 04 2c 00 0a c0 2c 40 00 44 0a c0 11 00 0a c0 11 40",
    "show-desktop": "07 34 00 02 00 08 07", "run": "07 35 00 02 00 08 15",
    "calculator": "07 3f 00 01 92 11", "email": "07 40 00 01 8a 11",
    "www-favorites": "07 41 00 01 2a 12", "www-forward": "07 42 00 01 25 12",
    "www-back": "07 43 00 01 24 12", "www-stop": "07 44 00 01 26 12",
    "www-refresh": "07 45 00 01 27 12", "www-search": "07 46 00 01 21 12",
    "windows-key": "07 3e 00 01 00 08",
    "dpi-cycle": "09 1e 01 01 04 30", "dpi-up": "09 1f 01 01 05 30",
    "dpi-down": "09 20 01 01 06 30", "rate-cycle": "09 21 01 01 01 30",
    "rate-up": "09 22 01 01 02 30", "rate-down": "09 23 01 01 03 30",
    "shift": "09 38 01 01 07 30", "off": "07 4d 01",
    "profile-cycle": "09 49 01 01 c1 30", "profile-up": "09 4a 01 01 c2 30",
    "profile-down": "09 4b 01 01 c3 30", "smart-key": "09 04 01 01 08 30",
    "open-driver": "09 47 01 01 c0 30", "launch": "09 4c 01 01 80 30",   # acted on by the vendor app
}
CATALOGUE = {k: bytes.fromhex(v).ljust(0xFE, b"\0") for k, v in CATALOGUE.items()}

MODS = {"ctrl": 1, "shift": 2, "alt": 4, "win": 8, "gui": 8,
        "rctrl": 0x10, "rshift": 0x20, "ralt": 0x40, "rwin": 0x80}
BUTTONS = {"lmb": 1, "rmb": 2, "mmb": 4, "back": 8, "fwd": 0x10}
KEYS = {c: 4 + i for i, c in enumerate("abcdefghijklmnopqrstuvwxyz")}
KEYS.update({c: 30 + i for i, c in enumerate("1234567890")})
KEYS.update({f"f{i}": 57 + i for i in range(1, 13)})
KEYS.update(enter=40, esc=41, backspace=42, tab=43, space=44, minus=45, equal=46,
            insert=73, home=74, pgup=75, delete=76, end=77, pgdn=78,
            right=79, left=80, down=81, up=82)


def key(name):
    return int(name, 16) if name.startswith("0x") else KEYS[name]


def combo_record(spec):
    """'ctrl+shift+esc' -> held key combo (same layout as the app's Copy/Paste entries)."""
    parts = spec.lower().split("+")
    mods = sum(MODS[p] for p in parts if p in MODS)
    keys = [key(p) for p in parts if p not in MODS]
    toks = [(0, mods & 0x0F)] if mods & 0x0F else []   # ponytail: right-hand mods only via raw macro
    toks += [(k, 0) for k in keys]
    return record(6, 0x13, 0, toks)


def macro_record(text, repeat):
    """Steps separated by spaces/commas: +a -a +ctrl -ctrl +lmb -lmb wheel+1 wheel-1 50 (=50 ms)."""
    toks = []
    for s in re.split(r"[\s,]+", text.strip().lower()):
        if re.fullmatch(r"\d+", s):
            ms = min(int(s), 0x3FFF)
            toks.append((ms & 0xFF, 0xC0 | ms >> 8))
        elif s.startswith("wheel"):
            toks.append((0, 0x20 | (int(s[5:]) & 0x0F)))
        else:
            up = 0x40 if s[0] == "-" else 0
            n = s[1:]
            if n in BUTTONS: toks.append((BUTTONS[n], 0x20 | up))
            elif n in MODS and MODS[n] < 0x10: toks.append((0, MODS[n] | up))
            else: toks.append((key(n), up))
    flag = 0x80 if repeat == "until-key" else 0
    mode = 0 if repeat in ("hold", "until-key") else int(repeat)
    return record(8 | flag, 0x07, mode, toks)


def record(typ, fid, mode, toks):
    if len(toks) > 125:
        sys.exit("macro too long (max 125 steps)")
    b = bytes([typ, fid, mode, len(toks)]) + bytes(x for t in toks for x in t)
    return b.ljust(0xFE, b"\0")


def describe(rec):
    for k, v in CATALOGUE.items():
        if v[:4 + 2 * v[3]] == rec[:4 + 2 * rec[3]] and v[:2] == rec[:2]:
            return k
    return f"type={rec[0]:#x} mode={rec[2]} " + rec[4:4 + 2 * rec[3]].hex(" ")


class Config:
    """Settings block (0xBE00) + 4 KiB button flash (0xC000: 8 sectors x [primary 254B | shift 254B | cksum])."""

    def __init__(self, st, fl, levels=()):
        self.st, self.fl = bytearray(st), bytearray(fl)
        self.levels = list(levels)   # DPI per level 0-8, as reported by the sensor (not stored in the exe)

    def button(self, i, shift=False):
        o = i * 0x200 + (0xFE if shift else 0)
        return bytes(self.fl[o:o + 0xFE])

    def set_button(self, i, rec, shift=False):
        o = i * 0x200 + (0xFE if shift else 0)
        self.fl[o:o + 0xFE] = rec

    def finalize(self):
        """Recompute derived masks + checksums exactly like the app (0x44ba00)."""
        st, n = self.st, len(self.st) - 2
        st[0x26] = sum(1 << i for i in range(8) if self.button(i)[1] == 0x38)          # SHIFT buttons
        sk = sum(1 << i for i in range(8) if self.button(i)[1] == 0x04)                 # Smart Key
        sk |= sum(1 << (8 + i) for i in range(8) if self.button(i, True)[1] == 0x04)
        st[0x27:0x29] = sk.to_bytes(2, "little")
        if st[4] == 3:
            st[4] = 4
        st[n:] = cksum(st[:n]).to_bytes(2, "little")
        for s in range(8):
            o = s * 0x200
            self.fl[o + 0x1FE:o + 0x200] = cksum(self.fl[o:o + 0x1FE]).to_bytes(2, "little")

    def show(self):
        st = self.st
        rate = {v: k for k, v in POLL.items()}.get(st[0], f"?{st[0]}")
        print(f"polling     {rate} Hz")
        print(f"active dpi  slot {st[1] + 1}")
        if any(self.levels):
            print("dpi levels  " + " ".join(f"{i + 1}:{d}" for i, d in enumerate(self.levels)))
        for i in range(6):
            en, lv, r, g, b = st[5 + 5 * i:10 + 5 * i]
            dpi = self.levels[lv] if lv < len(self.levels) and self.levels[lv] else "?"
            print(f"  dpi slot {i + 1}: {'on ' if en else 'off'} level {lv + 1} = {dpi:>5}  #{r:02x}{g:02x}{b:02x}")
        eff = st[2] & 0x7F
        print(f"lighting    effect={EFFECTS[eff] if eff < 4 else eff} mode={'rgb' if st[2] & 0x80 else 'single'} "
              f"speed={st[3]} brightness={st[4]}")
        print(f"            logo=#{st[0x2A:0x2D].hex()} side=#{st[0x2D:0x30].hex()}", end="")
        print(f" direction={'left' if st[0x30] else 'right'}" if len(st) > 0x32 else "")
        print(f"smart-key delay {st[0x29]}")
        for i in range(8):
            print(f"button {i + 1}: {describe(self.button(i)):<14} | shift: {describe(self.button(i, True))}")


class Mouse:
    def __init__(self, path=None):
        import hid
        devs = [d for d in hid.enumerate(VID) if d["product_id"] in PIDS]
        if path:
            devs = [d for d in devs if d["path"] == path.encode()]
        else:
            devs = [d for d in devs if d["usage_page"] == USAGE_PAGE]
        if not devs:
            sys.exit("no Blackweb mouse found (Linux: check udev permissions, see PROTOCOL.md)")
        self.model = PIDS[devs[0]["product_id"]]
        self.st_len = 0x33 if self.model == "MA32AIY" else 0x32
        self.h = hid.device()
        self.h.open_path(devs[0]["path"])

    def cmd(self, c, addr=0, data=b"", ln=None):
        pkt = bytes([RID, c, addr & 0xFF, addr >> 8, len(data) if ln is None else ln]) + data
        if self.h.write(pkt.ljust(64, b"\0")) < 0:
            sys.exit(f"write failed (cmd {c:#x})")
        end = time.time() + 5
        while time.time() < end:                 # device echoes cmd in byte 1 (0x4353c6)
            r = self.h.read(64, 500)             # read replies also echo addr; the app files data by it (0x452a37)
            if len(r) >= 5 and r[0] == RID and r[1] == c and (c != 3 or r[2] | r[3] << 8 == addr):
                return bytes(r)
        sys.exit(f"timeout waiting for ack of cmd {c:#x} @ {addr:#06x}")

    def read(self, addr, n):
        out = b""
        while len(out) < n:
            k = min(59, n - len(out))
            out += self.cmd(3, addr + len(out), ln=k)[5:5 + k]
        return out

    def write(self, addr, data, c=4):
        for o in range(0, len(data), 59):
            self.cmd(c, addr + o, data[o:o + 59])

    def load(self):
        r = self.cmd(1)                          # status reply carries the sensor's 9 DPI values as u16 (0x452868)
        levels = [int.from_bytes(r[0x15 + 2 * i:0x17 + 2 * i], "little") for i in range(9)]
        return Config(self.read(ST_ADDR, self.st_len), self.read(FL_ADDR, FL_LEN), levels)

    def save(self, cfg, old):
        cfg.finalize()
        self.cmd(2)
        try:
            self.write(ST_ADDR, bytes(cfg.st), c=5)
            for s in range(8):
                a, o = FL_ADDR + s * 0x200, s * 0x200
                if cfg.fl[o:o + 0x200] != old.fl[o:o + 0x200]:
                    self.cmd(6, a)               # erase sector
                    self.write(a, bytes(cfg.fl[o:o + 0x200]))
        except SystemExit:                       # app closes the session even after a failed write (0x44c472)
            try:
                self.cmd(7)
            except SystemExit:
                pass
            raise
        self.cmd(7)


def color(s):
    return bytes.fromhex(s.lstrip("#"))


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--path", help="hidraw/HID path if auto-detect fails")
    sub = p.add_subparsers(dest="op", required=True)
    sub.add_parser("show")
    sub.add_parser("actions", help="list predefined button actions")
    sub.add_parser("backup").add_argument("file")
    sub.add_parser("restore").add_argument("file")
    sub.add_parser("polling").add_argument("hz", type=int, choices=POLL)
    d = sub.add_parser("dpi")
    d.add_argument("slot", type=int, choices=range(1, 7))
    d.add_argument("--dpi", type=int, help="a DPI value from the mouse's level table (see 'show')")
    d.add_argument("--level", type=int, choices=range(1, 10), help="raw DPI level 1-9")
    d.add_argument("--color")
    d.add_argument("--enable", action=argparse.BooleanOptionalAction)
    d.add_argument("--activate", action="store_true")
    li = sub.add_parser("light")
    li.add_argument("--effect", choices=EFFECTS)
    li.add_argument("--mode", choices=["single", "rgb"])
    li.add_argument("--speed", type=int, choices=[1, 2, 3], help="1=fast 3=slow")
    li.add_argument("--brightness", type=int, choices=range(4))
    li.add_argument("--logo"); li.add_argument("--side")
    li.add_argument("--direction", choices=["right", "left"])
    b = sub.add_parser("button")
    b.add_argument("n", type=int, choices=range(1, 9))
    b.add_argument("action", help="predefined action, keys:ctrl+c, or macro:'+a 20 -a'")
    b.add_argument("--shift", action="store_true", help="set the SHIFT-layer function")
    b.add_argument("--repeat", default="1", help="macro: N times, 'hold' or 'until-key'")
    a = p.parse_args()

    if a.op == "actions":
        return print("\n".join(CATALOGUE))
    m = Mouse(a.path)
    cfg = m.load()
    old = Config(cfg.st, cfg.fl)
    st = cfg.st
    if a.op == "show":
        print(f"model {m.model}")
        return cfg.show()
    if a.op == "backup":
        return open(a.file, "wb").write(bytes(st) + bytes(cfg.fl))
    bak = pathlib.Path(__file__).parent / "backups" / f"bwmc-{m.model}-{time.strftime('%Y%m%d%H%M%S')}.bin"   # device state before every write
    bak.parent.mkdir(exist_ok=True)
    bak.write_bytes(bytes(st) + bytes(cfg.fl))
    print(f"previous config saved to {bak}")
    if a.op == "restore":
        raw = open(a.file, "rb").read()
        if len(raw) != m.st_len + FL_LEN:
            sys.exit("backup size mismatch for this model")
        cfg = Config(raw[:m.st_len], raw[m.st_len:])
    elif a.op == "polling":
        st[0] = POLL[a.hz]
    elif a.op == "dpi":
        o = 5 + 5 * (a.slot - 1)
        if a.enable is not None: st[o] = int(a.enable)
        if a.dpi:
            if a.dpi not in cfg.levels:
                sys.exit(f"mouse offers DPI levels {cfg.levels}; use one of those or --level")
            st[o + 1] = cfg.levels.index(a.dpi)
        if a.level: st[o + 1] = a.level - 1
        if a.color: st[o + 2:o + 5] = color(a.color)
        if a.activate: st[1], st[o] = a.slot - 1, 1
    elif a.op == "light":
        if a.effect: st[2] = st[2] & 0x80 | EFFECTS.index(a.effect)
        if a.mode: st[2] = st[2] & 0x7F | (0x80 if a.mode == "rgb" else 0)
        if a.speed: st[3] = a.speed
        if a.brightness is not None: st[4] = BRIGHT[a.brightness]
        if a.logo: st[0x2A:0x2D] = color(a.logo)
        if a.side: st[0x2D:0x30] = color(a.side)
        if a.direction and len(st) > 0x32: st[0x30] = a.direction == "left"
    elif a.op == "button":
        act = a.action
        rec = (combo_record(act[5:]) if act.startswith("keys:") else
               macro_record(act[6:], a.repeat) if act.startswith("macro:") else
               CATALOGUE.get(act) or sys.exit(f"unknown action {act!r}; see 'actions'"))
        cfg.set_button(a.n - 1, rec, a.shift)
        if not any(cfg.button(i)[1] == 0x0D for i in range(8)):
            sys.exit("refusing: at least one button must stay left-click (app rule)")
    m.save(cfg, old)
    print("saved")


if __name__ == "__main__":
    main()
