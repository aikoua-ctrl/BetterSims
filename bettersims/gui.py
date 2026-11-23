"""Desktop window (tkinter, ships with Python on Windows)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, bisect, demo, fixer, report
from .model import Action, Finding, Severity
from .model import PACKAGE_EXT, SCRIPT_EXT
from .paths import app_dir, check_sims_dir, find_sims_dirs, game_running
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
        self.busy = False
        self.events: queue.Queue = queue.Queue()  # worker threads never touch widgets directly
        self.after(100, self._pump)

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
        if not d:
            messagebox.showwarning("BetterSims", "Pick your 'The Sims 4' folder first "
                                   "(Documents\\Electronic Arts\\The Sims 4).")
            return None
        problem = check_sims_dir(d)
        if problem:
            messagebox.showwarning("BetterSims", problem)
            return None
        if self.busy:
            messagebox.showinfo("BetterSims", "Please wait, still working on the last step.")
            return None
        return d

    def _pump(self) -> None:
        try:
            while True:
                fn, args = self.events.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def _background(self, work, done) -> None:
        """Run work() off the UI thread, then done(result) on it. Errors are shown, never swallowed."""
        self.busy = True

        def run():
            try:
                result = work()
            except Exception as e:  # noqa: BLE001
                self.events.put((self._failed, (e,)))
                return
            self.events.put((done, (result,)))

        threading.Thread(target=run, daemon=True).start()

    def _failed(self, e: Exception) -> None:
        self.busy = False
        self.status.set("Something went wrong.")
        messagebox.showerror("BetterSims", f"Something went wrong:\n{e}")

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
        self.status.set("Scanning... (big Mods folders can take a minute)")
        self.progress["value"] = 0

        def work():
            inv = scan(d)
            fs = run_rules(inv, lambda i, n, name: self.events.put((self._progress, (i, n))))
            report.write(inv, fs, app_dir(d) / "report.html")
            return inv, fs

        self._background(work, lambda r: self._show(*r))

    def _progress(self, i: int, n: int) -> None:
        self.progress["value"] = 100 * (i + 1) / n

    def _show(self, inv, findings: list[Finding]) -> None:
        self.busy = False
        self.detail.delete("1.0", "end")
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
        if game_running():
            messagebox.showwarning("BetterSims", "The Sims 4 is still running. Close the game first, "
                                   "because it locks mod files while it's open.")
            return
        n = sum(len(f.actions) for f in findings)
        if not messagebox.askokcancel("BetterSims", f"{what}: {n} change(s).\n\nClose The Sims 4 first. "
                                      "Nothing is deleted: removed files go to the quarantine folder and "
                                      "'Undo last fix' puts everything back."):
            return
        self.status.set("Fixing...")

        def done(res):
            self.busy = False
            msg = f"Done: {len(res.done)} change(s)."
            if res.errors:
                msg += f"\n\n{len(res.errors)} couldn't be done:\n" + "\n".join(res.errors[:10])
            messagebox.showinfo("BetterSims", msg)
            self.scan()

        self._background(lambda: fixer.Fixer(d).apply(findings, log=lambda *_: None), done)

    def fix_checked(self) -> None:
        self._apply([self.findings[i] for i in sorted(self.checked)], "Apply checked fixes")

    def remove_selected(self) -> None:
        """Quarantine the mod files of the selected rows. Only mod files: never Options.ini, logs etc."""
        mod_exts = (PACKAGE_EXT, SCRIPT_EXT, ".zip", ".rar", ".7z", ".packageoff", ".ts4scriptoff")
        files: list[Path] = []
        for row in self.tree.selection():
            for p in self.findings[int(row)].files:
                if p.is_file() and p.suffix.lower() in mod_exts and p not in files:
                    files.append(p)
        if not files:
            messagebox.showinfo("BetterSims", "Select one or more rows that list mod files first.")
            return
        f = Finding("manual", Severity.INFO, "Manual removal", "", files, [Action("quarantine", p) for p in files])
        self._apply([f], f"Remove {len(files)} mod file(s) into quarantine")

    def undo_last(self) -> None:
        d = self.require_dir()
        if not d:
            return
        sessions = [s for s in fixer.list_sessions(d) if not s.get("undone") and not s.get("purged")]
        if not sessions:
            messagebox.showinfo("BetterSims", "Nothing to undo.")
            return
        try:
            problems = fixer.undo(d, sessions[-1]["session"], log=lambda *_: None)
        except (fixer.FixError, OSError) as e:
            messagebox.showerror("BetterSims", f"Couldn't undo: {e}")
            return
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

        found_btn = ttk.Button(win, text="Remove the bad mod (quarantine)")

        def run(fn):
            if game_running():
                txt.set("Close The Sims 4 before each step: the game locks mod files while it's open.")
                return
            try:
                txt.set(fn())
            except SystemExit as e:
                txt.set(str(e))
            except OSError as e:
                txt.set(f"Couldn't rename a mod file: {e}")
            if txt.get().startswith("Found it"):
                found_btn.pack(pady=6)

        def remove_found():
            p = bisect.last_found_path(d)
            if not p.exists():
                return
            files = [d / "Mods" / f for f in json.loads(p.read_text())["files"]]
            files = [f for f in files if f.exists()]
            win.destroy()
            f = Finding("manual", Severity.INFO, "Bisect culprit", "", files, [Action("quarantine", x) for x in files])
            self._apply([f], f"Remove {len(files)} file(s) into quarantine")

        found_btn.configure(command=remove_found)

        ttk.Button(row, text="Start", command=lambda: run(lambda: bisect.start(d))).pack(side="left", padx=4)
        ttk.Button(row, text="Still broken", command=lambda: run(lambda: bisect.answer(d, True))).pack(side="left", padx=4)
        ttk.Button(row, text="Problem gone", command=lambda: run(lambda: bisect.answer(d, False))).pack(side="left", padx=4)
        ttk.Button(row, text="Stop and turn all back on", command=lambda: run(lambda: bisect.reset(d))).pack(side="left", padx=4)


def main(sims_dir: str | None = None) -> None:
    App(sims_dir).mainloop()
