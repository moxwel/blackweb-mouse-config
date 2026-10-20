#!/usr/bin/env python3
"""Tk front-end for bwmc.py: native widgets and dialogs, runs bwmc.py and shows its output in a log."""
import pathlib, queue, re, shlex, subprocess, sys, threading
import tkinter as tk
from tkinter import ttk, colorchooser, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
import bwmc

HERE = pathlib.Path(__file__).parent
BACKUPS = HERE / "backups"
KEEP = "(keep)"
SPEED = {"fast": "1", "medium": "2", "slow": "3"}


def hx(*b):
    return bytes(b).hex(" ")


def rec_hex(rec, limit=36):
    s = rec[:4 + 2 * rec[3]].hex(" ")   # [type][label][mode][n] + n tokens
    return s if len(s) <= limit else s[:limit] + "…"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("blackweb-mouse-config")
        self.q, self.busy, self.cur, self.notes = queue.Queue(), False, {}, []
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=8)
        ttk.Button(top, text="Read mouse", command=lambda: self.run(["show"])).pack(side="left")
        self.model = ttk.Label(top, text="no mouse read yet", foreground="gray")
        self.model.pack(side="left", padx=12)
        nb = ttk.Notebook(self)
        nb.pack(fill="x", padx=8)
        for name, build in (("Polling", self.tab_polling), ("DPI", self.tab_dpi), ("Lighting", self.tab_light),
                            ("Buttons", self.tab_buttons), ("Backup", self.tab_backup)):
            f = ttk.Frame(nb, padding=12)
            nb.add(f, text=name)
            build(f)
        self.log = ScrolledText(self, height=16, font="TkFixedFont", state="disabled")
        self.log.pack(fill="both", expand=True, padx=8, pady=8)
        self.log.tag_config("cmd", foreground="#2a6fdb")
        self.log.tag_config("err", foreground="#c62828")
        self.after(50, self.pump)
        self.run(["show"])

    def row(self, f, r, text, w, cur=None, raw=None):
        """Label | input | 'raw: …' | 'current: …'.
        raw(value) gives the bytes the selection becomes; cur() reads self.cur, filled from the last `show`."""
        ttk.Label(f, text=text).grid(row=r, column=0, sticky="w", pady=4, padx=(0, 12))
        w.grid(row=r, column=1, sticky="w", pady=4)
        w.bind("<KeyRelease>", lambda e: self.refresh(), add="+")
        val = (lambda: w["text"]) if isinstance(w, ttk.Button) else getattr(w, "get", None)
        for col, prefix, fn, colour in ((2, "raw", raw and (lambda: None if val() == KEEP else raw(val())), "#2a6fdb"),
                                        (3, "current", cur, "gray")):
            if fn:
                note = ttk.Label(f, foreground=colour)
                note.grid(row=r, column=col, sticky="w", padx=(12, 0))
                self.notes.append((note, prefix, fn))
        return w

    def choice(self, f, r, text, values, default=KEEP, cur=None, raw=None, state="readonly"):
        cb = self.row(f, r, text, ttk.Combobox(f, values=values, state=state, width=22), cur, raw)
        cb.set(default)
        cb.default = default
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        return cb

    def color(self, f, r, text, cur=None):
        b = self.row(f, r, text, ttk.Button(f, text=KEEP), cur, lambda v: bytes.fromhex(v[1:]).hex(" "))
        b.config(command=lambda: (b.config(text=colorchooser.askcolor(parent=self, title=text)[1] or KEEP),
                                  self.refresh()))   # native colour dialog; cancel = keep current colour
        b.default = KEEP
        return b

    def check(self, f, r, text):
        var = tk.BooleanVar()
        w = self.row(f, r, "", ttk.Checkbutton(f, text=text, variable=var, command=self.refresh))
        w.var, w.default = var, False
        return var

    def apply(self, f, r, build):
        bar = ttk.Frame(f)
        bar.grid(row=r, column=1, sticky="w", pady=(12, 0))
        ttk.Button(bar, text="Apply", command=lambda: self.change(build())).pack(side="left")
        ttk.Button(bar, text="Reset", command=lambda: self.reset(f)).pack(side="left", padx=6)

    def reset(self, f):
        """Put every input of a tab back to its default (mostly '(keep)')."""
        for w in f.winfo_children():
            if not hasattr(w, "default"):
                continue
            if isinstance(w, ttk.Combobox): w.set(w.default)
            elif isinstance(w, ttk.Entry): w.delete(0, "end"); w.insert(0, w.default)
            elif isinstance(w, ttk.Checkbutton): w.var.set(w.default)
            else: w.config(text=w.default)
        self.refresh()

    def tab_polling(self, f):
        c = self.cur
        rate = self.choice(f, 0, "Polling rate (Hz)", [str(k) for k in sorted(bwmc.POLL)], "1000", lambda: c["polling"],
                           lambda v: hx(bwmc.POLL[int(v)]))
        self.apply(f, 1, lambda: ["polling", rate.get()])

    def tab_dpi(self, f):
        c = self.cur
        s = lambda: c["slot" + slot.get()]                        # (on/off, level, dpi, #colour)
        slot = self.choice(f, 0, "Slot", [str(i) for i in range(1, 7)], "1", lambda: f"slot {c['active']} active",
                           lambda v: f"{hx(int(v) - 1)} @ offset {5 + 5 * (int(v) - 1):#04x}")
        self.dpi = self.choice(f, 1, "DPI", [KEEP] + [f"level {i}" for i in range(1, 10)],
                               cur=lambda: f"{s()[2]} (level {s()[1]})",
                               raw=lambda v: hx(int(re.search(r"level (\d)", v)[1]) - 1))
        enable = self.choice(f, 2, "Enabled", [KEEP, "on", "off"], cur=lambda: s()[0],
                             raw=lambda v: {"on": "01", "off": "00"}[v])
        col = self.color(f, 3, "Colour", lambda: s()[3])
        active = self.check(f, 4, "Make active slot")

        def build():
            a = ["dpi", slot.get()]
            if m := re.search(r"level (\d)", self.dpi.get()): a += ["--level", m[1]]
            if enable.get() != KEEP: a.append("--enable" if enable.get() == "on" else "--no-enable")
            if col["text"] != KEEP: a += ["--color", col["text"]]
            return a + ["--activate"] * active.get()
        self.apply(f, 5, build)

    def tab_light(self, f):
        c = self.cur
        speed = {v: k for k, v in SPEED.items()}
        opts = [("--effect", self.choice(f, 0, "Effect", [KEEP] + bwmc.EFFECTS, cur=lambda: c["effect"],
                                         raw=lambda v: f"{hx(bwmc.EFFECTS.index(v))} (bits 0-6)")),
                ("--mode", self.choice(f, 1, "Mode", [KEEP, "single", "rgb"], cur=lambda: c["mode"],
                                       raw=lambda v: {"single": "00", "rgb": "80"}[v] + " (bit 7)")),
                ("--speed", self.choice(f, 2, "Speed", [KEEP] + list(SPEED), cur=lambda: speed[c["speed"]],
                                        raw=lambda v: hx(int(SPEED[v])))),
                ("--brightness", self.choice(f, 3, "Brightness", [KEEP, "0", "1", "2", "3"],
                                             cur=lambda: {"4": "3"}.get(c["brightness"], c["brightness"]),
                                             raw=lambda v: hx(bwmc.BRIGHT[int(v)]))),
                ("--direction", self.choice(f, 4, "Fluid direction", [KEEP, "right", "left"], cur=lambda: c["direction"],
                                            raw=lambda v: {"right": "00", "left": "01"}[v])),
                ("--logo", self.color(f, 5, "Logo colour", lambda: c["logo"])),
                ("--side", self.color(f, 6, "Side colour", lambda: c["side"]))]

        def build():
            a = ["light"]
            for opt, w in opts:
                v = w.get() if isinstance(w, ttk.Combobox) else w["text"]
                if v != KEEP: a += [opt, SPEED.get(v, v)]
            return a
        self.apply(f, 7, build)

    def tab_buttons(self, f):
        c = self.cur
        num = self.choice(f, 0, "Button", [str(i) for i in range(1, 9)], "6",
                          raw=lambda v: f"record @ {0xC000 + (int(v) - 1) * 0x200 + 0xFE * shift.get():#06x}")
        kind = self.choice(f, 1, "Action type", ["predefined", "keys", "macro"], "predefined")
        preset = self.choice(f, 2, "Predefined", list(bwmc.CATALOGUE), "dpi-cycle",
                             cur=lambda: c["btn" + num.get()][shift.get()],
                             raw=lambda v: rec_hex(bwmc.CATALOGUE[v]) if kind.get() == "predefined" else None)
        keys = self.row(f, 3, "Keys / macro", ttk.Entry(f, width=40), raw=lambda v: rec_hex(
            bwmc.combo_record(v) if kind.get() == "keys" else bwmc.macro_record(v, repeat.get())) if kind.get() != "predefined" else None)
        keys.default = "ctrl+shift+t"
        keys.insert(0, keys.default)
        repeat = self.choice(f, 4, "Macro repeat", ["1", "2", "3", "hold", "until-key"], "1", state="normal")
        shift = self.check(f, 5, "SHIFT layer")
        self.diagram(f, num)

        def build():
            k = kind.get()
            a = ["button", num.get(), preset.get() if k == "predefined" else f"{k}:{keys.get()}"]
            return a + ["--repeat", repeat.get()] * (k == "macro") + ["--shift"] * shift.get()
        self.apply(f, 6, build)

    def diagram(self, f, num):
        """Top view, front up (MA32AIY layout from the vendor app's button page). Click a number to select it."""
        cv = tk.Canvas(f, width=190, height=250, highlightthickness=0)
        cv.grid(row=0, column=4, rowspan=7, padx=(16, 0))
        cv.create_oval(45, 10, 155, 225, width=2)                                  # body
        cv.create_line(100, 10, 100, 110, width=2)                                 # left/right split
        cv.create_rectangle(93, 40, 107, 75, width=2)                              # wheel
        for y in (120, 155):
            cv.create_rectangle(37, y, 47, y + 28, width=2)                        # side buttons
        marks = {}
        for n, (x, y) in {1: (72, 60), 2: (128, 60), 3: (100, 57), 6: (100, 95), 7: (100, 125),
                          4: (22, 134), 5: (22, 169)}.items():
            marks[n] = cv.create_oval(x - 11, y - 11, x + 11, y + 11, fill="white"), cv.create_text(x, y, text=n)
            for item in marks[n]:
                cv.tag_bind(item, "<Button-1>", lambda e, n=n: num.set(n))
        cv.create_text(95, 240, text="8 = spare slot, no physical button", fill="gray")

        def highlight(*_):
            for n, (o, t) in marks.items():
                sel = num.get() == str(n)
                cv.itemconfig(o, fill="#2a6fdb" if sel else "white")
                cv.itemconfig(t, fill="white" if sel else "black")
            self.refresh()
        num.var = var = tk.StringVar(value=num.get())   # catches .set() from clicks and Reset; keep a ref or GC drops it
        num.config(textvariable=var)
        var.trace_add("write", highlight)
        highlight()

    def tab_backup(self, f):
        BACKUPS.mkdir(exist_ok=True)
        types = [("Mouse image", "*.bin")]
        ttk.Button(f, text="Back up to file…", command=lambda: (p := filedialog.asksaveasfilename(
            parent=self, initialdir=BACKUPS, defaultextension=".bin", filetypes=types)) and self.run(["backup", p])
        ).grid(row=0, column=0, sticky="w", pady=4)
        ttk.Button(f, text="Restore from file…", command=lambda: (p := filedialog.askopenfilename(
            parent=self, initialdir=BACKUPS, filetypes=types)) and self.change(["restore", p])
        ).grid(row=1, column=0, sticky="nw", pady=4)
        ttk.Label(f, text="Backups (double-click to restore)").grid(row=0, column=1, sticky="w", padx=(24, 0))
        self.backups = tk.Listbox(f, height=10, width=44, activestyle="none")
        self.backups.grid(row=1, column=1, rowspan=3, sticky="nsew", padx=(24, 0))
        sb = ttk.Scrollbar(f, command=self.backups.yview)
        sb.grid(row=1, column=2, rowspan=3, sticky="ns")
        self.backups.config(yscrollcommand=sb.set)
        self.backups.bind("<Double-Button-1>", lambda e: self.restore_selected())
        f.bind("<Map>", lambda e: self.list_backups())   # refresh whenever the tab is shown

    def list_backups(self):
        self.backups.delete(0, "end")
        for p in sorted(BACKUPS.glob("*.bin"), key=lambda p: p.stat().st_mtime, reverse=True):   # newest first
            self.backups.insert("end", p.name)

    def restore_selected(self):
        sel = self.backups.curselection()
        if sel and messagebox.askyesno("Restore backup", f"Write {self.backups.get(sel[0])} to the mouse?\n\n"
                                       "The current configuration is backed up first.", parent=self):
            self.change(["restore", str(BACKUPS / self.backups.get(sel[0]))])

    def parse(self, line):
        """Pick current values out of `bwmc.py show` output (format defined in bwmc.Config.show)."""
        c = self.cur
        if m := re.match(r"model (\w+)", line): self.model.config(text=f"model: {m[1]}", foreground="")
        elif line.startswith("no Blackweb mouse found"): self.model.config(text="no mouse found", foreground="#c62828")
        elif m := re.match(r"polling\s+(.+)", line): c["polling"] = m[1]
        elif m := re.match(r"active dpi\s+slot (\d)", line): c["active"] = m[1]
        elif m := re.match(r"dpi levels\s+(.*)", line):
            self.dpi["values"] = [KEEP] + [f"{d} DPI (level {i})" for i, d in (t.split(":") for t in m[1].split())]
        elif m := re.match(r"\s+dpi slot (\d): (on|off)\s+level (\d) =\s+(\S+)\s+(#\w+)", line):
            c["slot" + m[1]] = m.groups()[1:]
        elif m := re.search(r"effect=(\S+) mode=(\S+) speed=(\d) brightness=(\d)", line):
            c.update(zip(("effect", "mode", "speed", "brightness"), m.groups()))
        elif m := re.search(r"logo=(#\w+) side=(#\w+)(?: direction=(\w+))?", line):
            c.update(logo=m[1], side=m[2], direction=m[3])
        elif m := re.match(r"button (\d): (.*?)\s*\| shift: (.*)", line):
            c["btn" + m[1]] = (m[2], m[3].strip())

    def refresh(self):
        for note, prefix, fn in self.notes:
            try:
                v = fn()
            except (KeyError, TypeError, ValueError, IndexError, NameError, SystemExit):   # SystemExit: macro too long
                v = "invalid" if prefix == "raw" else None                              # missing current = not read yet
            note.config(text=f"{prefix}: {v}" if v else "")

    def change(self, args):
        if args == ["light"] or args[0] == "dpi" and len(args) == 2:
            return self.q.put(("err", "nothing to change\n"))
        self.run(args, ["show"])   # read back so the log and current values show the result

    def run(self, *cmds):
        if not self.busy:
            self.busy = True
            threading.Thread(target=self.worker, args=(cmds,), daemon=True).start()

    def worker(self, cmds):   # background thread: only talks to Tk through the queue
        for args in cmds:
            self.q.put(("cmd", "$ bwmc.py " + shlex.join(args) + "\n"))
            p = subprocess.Popen([sys.executable, "-u", str(HERE / "bwmc.py"), *args], cwd=HERE, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, errors="replace",
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for line in p.stdout:
                self.q.put(("out", line))
            if p.wait():
                self.q.put(("err", f"exit code {p.returncode}\n"))
                break
        self.q.put(("done", ""))

    def pump(self):
        while not self.q.empty():
            tag, text = self.q.get()
            if tag == "out":
                self.parse(text.rstrip("\n"))
            if tag == "done":
                self.busy = False
                self.refresh()
                self.list_backups()   # changes and backups add files
            self.log.config(state="normal")
            self.log.insert("end", text, tag)
            self.log.see("end")
            self.log.config(state="disabled")
        self.after(50, self.pump)


if __name__ == "__main__":
    App().mainloop()
