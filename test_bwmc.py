"""Offline checks (no mouse needed). Cross-checks against the vendor app's files run only if they are present."""
import pathlib
from bwmc import CATALOGUE, Config, Mouse, macro_record, combo_record

APP = pathlib.Path(__file__).parent / "Blackweb Gaming AP" / "config"   # vendor app, not redistributed

if APP.exists():
    prof = (APP / "Default1.MA32AIY").read_bytes()
    cfg = Config(prof[0xA8EC:0xA91F], prof[0x98EC:0xA8EC])   # profile file = raw device image
    orig = (bytes(cfg.st), bytes(cfg.fl))
    cfg.finalize()
    assert (bytes(cfg.st), bytes(cfg.fl)) == orig, "checksum/mask recompute differs from app"
    for i, name in enumerate(["left-click", "right-click", "middle-click", "forward", "back", "double-click"]):
        assert cfg.button(i) == CATALOGUE[name], name
    ted = (APP / "macro.ted").read_bytes()
    assert macro_record("+0xe3 20 +g 20 -g 20 -0xe3", "1")[:18] == ted[0x2B:0x3D]
else:
    print("vendor files not found: skipping cross-checks against the Windows app")
    cfg = Config(bytes(0x33), bytes(0x1000))
    cfg.set_button(0, CATALOGUE["left-click"])
    cfg.finalize()
    orig = (bytes(cfg.st), bytes(cfg.fl))

assert combo_record("ctrl+c")[4:] == CATALOGUE["copy"][4:]


class FakeHid:
    """Device stand-in: acks every command, serves reads from `mem`, can fail one write."""
    def __init__(self, mem, fail_at=None):
        self.mem, self.fail_at, self.log, self.q = mem, fail_at, [], []

    def write(self, pkt):
        c, addr, n = pkt[1], pkt[2] | pkt[3] << 8, pkt[4]
        self.log.append((c, addr))
        if (c, addr) == self.fail_at:
            return -1
        if c == 1:   # status: 9 DPI u16 at 0x15
            self.q.append([4, 1] + [0] * 0x13 + list(b"".join(d.to_bytes(2, "little") for d in range(100, 1000, 100))))
        if c == 3:
            self.q.append([4, 3, 9, 9, n] + [0] * n)                   # stale reply for another addr: must be skipped
            self.q.append([4, 3, pkt[2], pkt[3], n] + list(self.mem[addr:addr + n]))
        else:
            self.q.append([4, c, pkt[2], pkt[3], 0])
        return 64

    def read(self, n, timeout):
        return (self.q.pop(0) + [0] * 64)[:64] if self.q else []


m = Mouse.__new__(Mouse)
m.st_len = 0x33
mem = bytearray(0x10000)
mem[0xBE00:0xBE33], mem[0xC000:0xD000] = orig
m.h = FakeHid(mem)
dev = m.load()
assert dev.levels == list(range(100, 1000, 100)), dev.levels
assert (bytes(dev.st), bytes(dev.fl)) == orig, "read reply placed by echoed addr"

m.h = FakeHid(mem, fail_at=(4, 0xC200))
new = Config(dev.st, dev.fl)
new.set_button(1, CATALOGUE["dpi-cycle"])
try:
    m.save(new, dev)
    raise AssertionError("save should fail")
except SystemExit:
    pass
assert m.h.log[-1] == (7, 0), "session must be closed after a failed write"
print("ok")
