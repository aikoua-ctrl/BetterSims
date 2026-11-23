"""Desktop window (tkinter, ships with Python on Windows)."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, bisect, demo, fixer, report
from .model import Action, Finding, Severity
from .paths import app_dir, find_sims_dirs
from .rules import run_rules
from .scanner import scan

SEV_LABEL = {Severity.CRITICAL: "Critical", Severity.WARNING: "Warning", Severity.INFO: "Info"}
CHECK, UNCHECK, NOFIX = "☑", "☐", ""


def open_path(p: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(p)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(p)], check=False)
    else:
        subprocess.run(["xdg-open", str(p)], check=False)


class App(tk.Tk):
    def __init__(self, sims_dir: str | None):
        super().__init__()
        self.title(f"BetterSims {__version__}: Sims 4 mod doctor")
        self.geometry("1100x700")
        self.minsize(800, 500)
        self.findings: list[Finding] = []
        self.checked: set[int] = set()
        self.inv = None

        found = find_sims_dirs()
        self.dir_var = tk.StringVar(value=sims_dir or (str(found[0]) if found else ""))
        self.status = tk.StringVar(value="Pick your 'The Sims 4' folder and press Scan.")
        self._build()
        if self.dir_var.get():
            self.after(200, self.scan)

    # ---- layout -------------------------------------------------------------
    def _build(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Sims 4 folder:").pack(side="left")
        ttk.Entry(top, textvariable=self.dir_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(top, text="Browse...", command=self.browse).pack(side="left")
        ttk.Button(top, text="Scan", command=self.scan).pack(side="left", padx=6)
        ttk.Button(top, text="Try a demo folder", command=self.make_demo).pack(side="left")

        pan = ttk.PanedWindow(self, orient="vertical")
        pan.pack(fill="both", expand=True, padx=8)
        cols = ("fix", "sev", "title", "action")
        self.tree = ttk.Treeview(pan, columns=cols, show="headings", selectmode="extended")
        for c, w, t in (("fix", 40, "Fix"), ("sev", 80, "Severity"), ("title", 520, "Problem"), ("action", 380, "What the fix does")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, stretch=c in ("title", "action"), anchor="center" if c == "fix" else "w")
        for sev, color in ((Severity.CRITICAL, "#c62828"), (Severity.WARNING, "#9a5b00"), (Severity.INFO, "#2e6fbd")):
            self.tree.tag_configure(sev.value, foreground=color)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Button-1>", self.on_click)
        pan.add(self.tree, weight=3)
        self.detail = tk.Text(pan, height=9, wrap="word", relief="flat", padx=8, pady=6)
        pan.add(self.detail, weight=1)

        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")
        ttk.Button(bar, text="Fix checked", command=self.fix_checked).pack(side="left")
        ttk.Button(bar, text="Remove selected mod files", command=self.remove_selected).pack(side="left", padx=6)
        ttk.Button(bar, text="Undo last fix", command=self.undo_last).pack(side="left")
        ttk.Button(bar, text="Find a bad mod (50/50)", command=self.bisect_window).pack(side="left", padx=6)
        ttk.Button(bar, text="Open report", command=self.open_report).pack(side="left")
        ttk.Button(bar, text="Quarantine folder", command=self.open_quarantine).pack(side="left", padx=6)
        ttk.Button(bar, text="Empty quarantine...", command=self.purge).pack(side="left")
        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", padx=8)
        ttk.Label(self, textvariable=self.status, padding=(8, 4)).pack(fill="x")

    # ---- helpers ------------------------------------------------------------
    @property
    def sims_dir(self) -> Path | None:
        d = self.dir_var.get().strip()
        if not d:
            return None
        p = Path(d)
        return p.parent if p.name.lower() == "mods" else p

    def require_dir(self) -> Path | None:
        d = self.sims_dir
        if not d or not d.is_dir():
            messagebox.showwarning("BetterSims", "Pick your 'The Sims 4' folder first "
                                   "(Documents\\Electronic Arts\\The Sims 4).")
            return None
        return d

    def browse(self) -> None:
        d = filedialog.askdirectory(title="Select your 'The Sims 4' folder")
        if d:
            self.dir_var.set(d)
            self.scan()

    def make_demo(self) -> None:
        d = filedialog.askdirectory(title="Choose an empty folder for the demo")
        if not d:
            return
        root = demo.build(Path(d) / "Demo Sims 4")
        self.dir_var.set(str(root))
        self.scan()

    # ---- scanning -----------------------------------------------------------
    def scan(self) -> None:
        d = self.require_dir()
        if not d:
            return
        self.status.set("Scanning...")
        self.progress["value"] = 0

        def work():
            try:
                inv = scan(d)
                fs = run_rules(inv, lambda i, n, name: self.after(0, self._progress, i, n))
                report.write(inv, fs, app_dir(d) / "report.html")
                self.after(0, self._show, inv, fs)
            except Exception as e:  # noqa: BLE001 - show any failure to the user
                self.after(0, lambda: messagebox.showerror("Scan failed", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _progress(self, i: int, n: int) -> None:
        self.progress["value"] = 100 * (i + 1) / n

    def _show(self, inv, findings: list[Finding]) -> None:
        self.inv, self.findings = inv, findings
        self.checked = {i for i, f in enumerate(findings) if f.fixable and f.auto}
        self.tree.delete(*self.tree.get_children())
        for i, f in enumerate(findings):
            self.tree.insert("", "end", iid=str(i), tags=(f.severity.value,),
                             values=(self._mark(i), SEV_LABEL[f.severity], f.title, f.fix_label or "(nothing to fix)"))
        crit = sum(f.severity == Severity.CRITICAL for f in findings)
        self.progress["value"] = 100
        self.status.set(f"{len(inv.packages())} packages, {len(inv.scripts())} script mods. "
                        f"{len(findings)} finding(s), {crit} critical. Safe fixes are pre-checked; "
                        "click the box to change.")

    def _mark(self, i: int) -> str:
        if not self.findings[i].fixable:
            return NOFIX
        return CHECK if i in self.checked else UNCHECK

    def on_click(self, ev) -> None:
        if self.tree.identify_column(ev.x) != "#1":
            return
        row = self.tree.identify_row(ev.y)
        if not row:
            return
        i = int(row)
        if not self.findings[i].fixable:
            return
        self.checked.symmetric_difference_update({i})
        self.tree.set(row, "fix", self._mark(i))

    def on_select(self, _ev=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        f = self.findings[int(sel[0])]
        base = self.sims_dir or Path(".")
        lines = [f.title, "", f.detail, ""]
        if f.fixable:
            lines += [f"Fix: {f.fix_label}" + ("" if f.auto else "  (not pre-checked: your call)"), ""]
            lines += ["  " + a.describe(base) for a in f.actions[:20]]
        lines += ["", "Files:"] + [f"  {p}" for p in f.files[:50]]
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", "\n".join(lines))

    # ---- actions ------------------------------------------------------------
    def _apply(self, findings: list[Finding], what: str) -> None:
        d = self.require_dir()
        if not d or not findings:
            return
        n = sum(len(f.actions) for f in findings)
        if not messagebox.askokcancel("BetterSims", f"{what}: {n} change(s).\n\nClose The Sims 4 first. "
                                      "Nothing is deleted: removed files go to the quarantine folder and "
                                      "'Undo last fix' puts everything back."):
            return
        res = fixer.Fixer(d).apply(findings, log=lambda *_: None)
        msg = f"Done: {len(res.done)} change(s)."
        if res.errors:
            msg += f"\n{len(res.errors)} problem(s):\n" + "\n".join(res.errors[:10])
        messagebox.showinfo("BetterSims", msg)
        self.scan()

    def fix_checked(self) -> None:
        self._apply([self.findings[i] for i in sorted(self.checked)], "Apply checked fixes")

    def remove_selected(self) -> None:
        files = []
        for row in self.tree.selection():
            files += [p for p in self.findings[int(row)].files if p.exists()]
        if not files:
            messagebox.showinfo("BetterSims", "Select one or more rows first.")
            return
        f = Finding("manual", Severity.INFO, "Manual removal", "", files, [Action("quarantine", p) for p in files])
        self._apply([f], f"Remove {len(files)} file(s) into quarantine")

    def undo_last(self) -> None:
        d = self.require_dir()
        if not d:
            return
        sessions = [s for s in fixer.list_sessions(d) if not s.get("undone") and not s.get("purged")]
        if not sessions:
            messagebox.showinfo("BetterSims", "Nothing to undo.")
            return
        problems = fixer.undo(d, sessions[-1]["session"], log=lambda *_: None)
        messagebox.showinfo("BetterSims", "Undone." if not problems else "Undone, with issues:\n" + "\n".join(problems[:10]))
        self.scan()

    def open_report(self) -> None:
        d = self.require_dir()
        if d and (app_dir(d) / "report.html").exists():
            webbrowser.open((app_dir(d) / "report.html").as_uri())

    def open_quarantine(self) -> None:
        d = self.require_dir()
        if d:
            q = app_dir(d) / "Quarantine"
            q.mkdir(exist_ok=True)
            open_path(q)

    def purge(self) -> None:
        d = self.require_dir()
        if not d:
            return
        if messagebox.askyesno("Empty quarantine", "Permanently delete everything in the quarantine folder?\n\n"
                               "This can't be undone.", icon="warning"):
            n = fixer.purge(d)
            messagebox.showinfo("BetterSims", f"Deleted quarantine for {len(n)} fix session(s).")

    def bisect_window(self) -> None:
        d = self.require_dir()
        if not d:
            return
        win = tk.Toplevel(self)
        win.title("Find a bad mod (50/50 method)")
        win.geometry("560x320")
        txt = tk.StringVar()
        st = bisect.load_state(d)
        txt.set("BetterSims turns half your mods off at a time. After each round, start the game, check for "
                "the problem, and tell it what happened. A few rounds pin down the bad mod.\n\n"
                + (f"In progress: round {st['round']}, {len(st['suspects'])} suspects." if st else "Press Start."))
        ttk.Label(win, textvariable=txt, wraplength=520, padding=12, justify="left").pack(fill="both", expand=True)
        row = ttk.Frame(win, padding=8)
        row.pack()

        def run(fn):
            try:
                txt.set(fn())
            except SystemExit as e:
                txt.set(str(e))

        ttk.Button(row, text="Start", command=lambda: run(lambda: bisect.start(d))).pack(side="left", padx=4)
        ttk.Button(row, text="Still broken", command=lambda: run(lambda: bisect.answer(d, True))).pack(side="left", padx=4)
        ttk.Button(row, text="Problem gone", command=lambda: run(lambda: bisect.answer(d, False))).pack(side="left", padx=4)
        ttk.Button(row, text="Stop and turn all back on", command=lambda: run(lambda: bisect.reset(d))).pack(side="left", padx=4)


def main(sims_dir: str | None = None) -> None:
    App(sims_dir).mainloop()
