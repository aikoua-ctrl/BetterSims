"""Desktop app: a modern dark UI built with CustomTkinter."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from . import __version__, bisect, demo, fixer, report
from .model import PACKAGE_EXT, SCRIPT_EXT, Action, Finding, Severity
from .paths import app_dir, check_sims_dir, find_sims_dirs, game_running
from .rules import run_rules
from .scanner import scan

# ---- theme -----------------------------------------------------------------
BG = "#0e1016"
SIDEBAR = "#13161f"
CARD = "#191d29"
CARD_HOVER = "#20253a"
RAISED = "#232a3d"
BORDER = "#2a3146"
TEXT = "#eceef6"
MUTED = "#8c94ab"
FAINT = "#5d6580"
ACCENT = "#2fd27a"  # plumbob green
ACCENT_HOVER = "#25b366"
ACCENT_TEXT = "#06140c"
VIOLET = "#8b6cff"
VIOLET_HOVER = "#7656f0"
SEV = {
    Severity.CRITICAL: ("#ff5c72", "#3a1a22", "Critical"),
    Severity.WARNING: ("#ffb547", "#3a2c14", "Warning"),
    Severity.INFO: ("#5aa9ff", "#162a42", "Info"),
}
MOD_EXTS = (PACKAGE_EXT, SCRIPT_EXT, ".zip", ".rar", ".7z", ".packageoff", ".ts4scriptoff")


def _family() -> str:
    return "Segoe UI" if sys.platform.startswith("win") else "Inter"


def open_path(p: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(p)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(p)], check=False)
    else:
        subprocess.run(["xdg-open", str(p)], check=False)


def short_path(p: str, n: int = 34) -> str:
    return p if len(p) <= n else "..." + p[-(n - 3):]


# ---- small building blocks -----------------------------------------------
def accent_button(master, text, command, **kw) -> ctk.CTkButton:
    return ctk.CTkButton(master, text=text, command=command, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                         text_color=ACCENT_TEXT, corner_radius=10, height=38,
                         font=ctk.CTkFont(_family(), 14, "bold"), **kw)


def ghost_button(master, text, command, color=TEXT, **kw) -> ctk.CTkButton:
    kw.setdefault("height", 36)
    return ctk.CTkButton(master, text=text, command=command, fg_color="transparent", hover_color=RAISED,
                         text_color=color, border_width=1, border_color=BORDER, corner_radius=10,
                         font=ctk.CTkFont(_family(), 13), **kw)


def danger_button(master, text, command, **kw) -> ctk.CTkButton:
    kw.setdefault("height", 36)
    return ctk.CTkButton(master, text=text, command=command, fg_color="transparent", hover_color="#3a1a22",
                         text_color=SEV[Severity.CRITICAL][0], border_width=1, border_color="#5a2633",
                         corner_radius=10, font=ctk.CTkFont(_family(), 13), **kw)


def pill(master, text, fg, bg) -> ctk.CTkLabel:
    return ctk.CTkLabel(master, text=f"  {text}  ", fg_color=bg, text_color=fg, corner_radius=8, height=22,
                        font=ctk.CTkFont(_family(), 11, "bold"))


def bind_hover(widget, normal: str, hover: str) -> None:
    """Highlight a card when the mouse is anywhere over it, including its children."""
    def enter(_e):
        widget.configure(fg_color=hover)

    def leave(e):
        x, y = widget.winfo_pointerxy()
        w = widget.winfo_containing(x, y)
        while w is not None:
            if w is widget:
                return
            w = getattr(w, "master", None)
        widget.configure(fg_color=normal)

    def walk(w):
        w.bind("<Enter>", enter, add="+")
        w.bind("<Leave>", leave, add="+")
        for c in w.winfo_children():
            walk(c)

    walk(widget)


class Confirm(ctk.CTkToplevel):
    """A themed yes/no dialog."""

    def __init__(self, parent, title: str, message: str, ok: str, danger: bool = False):
        super().__init__(parent, fg_color=CARD)
        self.result = False
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.pack(padx=28, pady=24, fill="both", expand=True)
        ctk.CTkLabel(f, text=title, font=ctk.CTkFont(_family(), 18, "bold"), text_color=TEXT,
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(f, text=message, font=ctk.CTkFont(_family(), 13), text_color=MUTED, wraplength=420,
                     justify="left", anchor="w").pack(fill="x", pady=(8, 20))
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack(fill="x")
        mk = danger_button if danger else accent_button
        mk(row, ok, self._ok, width=140).pack(side="right")
        ghost_button(row, "Cancel", self.destroy, width=100).pack(side="right", padx=8)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(50, self._center)

    def _center(self):
        p = self.master
        self.update_idletasks()
        x = p.winfo_rootx() + (p.winfo_width() - self.winfo_width()) // 2
        y = p.winfo_rooty() + (p.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()

    def _ok(self):
        self.result = True
        self.destroy()


# ---- the app ---------------------------------------------------------------
class App(ctk.CTk):
    def __init__(self, sims_dir: str | None):
        ctk.set_appearance_mode("dark")
        super().__init__(fg_color=BG)
        self.title(f"BetterSims {__version__}")
        self.geometry("1240x800")
        self.minsize(1000, 640)
        self.f = lambda size, weight="normal": ctk.CTkFont(_family(), size, weight)

        self.findings: list[Finding] = []
        self.checked: set[int] = set()
        self.inv = None
        self.busy = False
        self.filter = "All"
        self.search = ""
        self.expanded: set[int] = set()
        self.events: queue.Queue = queue.Queue()

        found = find_sims_dirs()
        self.dir = sims_dir or (str(found[0]) if found else "")

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._sidebar()
        self.pages: dict[str, ctk.CTkFrame] = {}
        self._page_problems()
        self._page_bisect()
        self._page_history()
        self.show("problems")
        self.after(100, self._pump)
        if self.dir:
            self.after(300, self.scan)

    # ---- plumbing -------------------------------------------------------
    def _pump(self) -> None:
        try:
            while True:
                fn, args = self.events.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.after(80, self._pump)

    def _background(self, work, done) -> None:
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
        self._set_scanning(False)
        self.toast(f"Something went wrong: {e}", "error")

    def confirm(self, title: str, message: str, ok: str, danger: bool = False) -> bool:
        d = Confirm(self, title, message, ok, danger)
        self.wait_window(d)
        return d.result

    def toast(self, text: str, kind: str = "ok", ms: int = 4200) -> None:
        color = {"ok": ACCENT, "warn": SEV[Severity.WARNING][0], "error": SEV[Severity.CRITICAL][0]}[kind]
        t = ctk.CTkFrame(self, fg_color=RAISED, corner_radius=12, border_width=1, border_color=color)
        ctk.CTkLabel(t, text="●", text_color=color, font=self.f(14)).pack(side="left", padx=(14, 6), pady=12)
        ctk.CTkLabel(t, text=text, text_color=TEXT, font=self.f(13), wraplength=420, justify="left").pack(
            side="left", padx=(0, 16), pady=12)
        t.place(relx=1.0, rely=1.0, x=-24, y=-92, anchor="se")
        self.last_toast = text
        self.after(ms, t.destroy)

    def require_dir(self) -> Path | None:
        if not self.dir:
            self.toast("Pick your 'The Sims 4' folder first (Documents\\Electronic Arts\\The Sims 4).", "warn")
            return None
        d = Path(self.dir)
        if d.name.lower() == "mods":
            d = d.parent
        problem = check_sims_dir(d)
        if problem:
            self.toast(problem, "warn", 6000)
            return None
        if self.busy:
            self.toast("Still working on the last step...", "warn")
            return None
        return d

    # ---- sidebar --------------------------------------------------------
    def _sidebar(self) -> None:
        sb = ctk.CTkFrame(self, fg_color=SIDEBAR, corner_radius=0, width=240)
        sb.grid(row=0, column=0, sticky="nsw")
        sb.grid_propagate(False)
        logo = ctk.CTkFrame(sb, fg_color="transparent")
        logo.pack(fill="x", padx=20, pady=(26, 30))
        cv = ctk.CTkCanvas(logo, width=30, height=40, bg=SIDEBAR, highlightthickness=0)
        cv.create_polygon(15, 2, 28, 20, 15, 38, 2, 20, fill=ACCENT, outline="")
        cv.create_polygon(15, 2, 28, 20, 15, 20, fill="#7ff0b0", outline="")
        cv.pack(side="left")
        txt = ctk.CTkFrame(logo, fg_color="transparent")
        txt.pack(side="left", padx=10)
        ctk.CTkLabel(txt, text="BetterSims", font=self.f(20, "bold"), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(txt, text=f"Mod doctor  v{__version__}", font=self.f(11), text_color=FAINT).pack(anchor="w")

        self.nav: dict[str, ctk.CTkButton] = {}
        for key, label in (("problems", "✦  Mod health"), ("bisect", "◑  50/50 finder"),
                           ("history", "↺  History & undo")):
            b = ctk.CTkButton(sb, text=label, anchor="w", height=42, corner_radius=10, fg_color="transparent",
                              hover_color=RAISED, text_color=MUTED, font=self.f(14),
                              command=lambda k=key: self.show(k))
            b.pack(fill="x", padx=14, pady=3)
            self.nav[key] = b

        folder = ctk.CTkFrame(sb, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
        folder.pack(side="bottom", fill="x", padx=14, pady=18)
        ctk.CTkLabel(folder, text="SIMS 4 FOLDER", font=self.f(10, "bold"), text_color=FAINT).pack(
            anchor="w", padx=14, pady=(12, 0))
        self.dir_label = ctk.CTkLabel(folder, text=short_path(self.dir) or "Not selected", font=self.f(12),
                                      text_color=TEXT, anchor="w", justify="left", wraplength=190)
        self.dir_label.pack(anchor="w", fill="x", padx=14, pady=(2, 10))
        ghost_button(folder, "Change folder", self.browse, height=32).pack(fill="x", padx=12)
        ctk.CTkButton(folder, text="Try a demo folder", command=self.make_demo, fg_color="transparent",
                      hover_color=RAISED, text_color=VIOLET, height=30, font=self.f(12)).pack(
            fill="x", padx=12, pady=(4, 12))

    def show(self, key: str) -> None:
        for k, b in self.nav.items():
            on = k == key
            b.configure(fg_color=RAISED if on else "transparent", text_color=TEXT if on else MUTED)
        for k, p in self.pages.items():
            if k == key:
                p.grid(row=0, column=1, sticky="nsew")
            else:
                p.grid_forget()
        if key == "history":
            self.refresh_history()
        if key == "bisect":
            self.refresh_bisect()

    def browse(self) -> None:
        d = filedialog.askdirectory(title="Select your 'The Sims 4' folder")
        if d:
            self.set_dir(d)

    def set_dir(self, d: str) -> None:
        self.dir = d
        self.dir_label.configure(text=short_path(d))
        self.scan()

    def make_demo(self) -> None:
        d = filedialog.askdirectory(title="Choose a folder to create the demo in")
        if not d:
            return
        root = demo.build(Path(d) / "Demo Sims 4")
        self.toast("Built a deliberately broken demo folder. Scanning it now.")
        self.set_dir(str(root))

    # ---- problems page ---------------------------------------------------
    def _page_problems(self) -> None:
        pg = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        pg.grid_columnconfigure(0, weight=1)
        pg.grid_rowconfigure(3, weight=1)
        self.pages["problems"] = pg

        head = ctk.CTkFrame(pg, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=32, pady=(28, 4))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Mod health", font=self.f(28, "bold"), text_color=TEXT).grid(row=0, column=0, sticky="w")
        self.subtitle = ctk.CTkLabel(head, text="Pick your Sims 4 folder to start.", font=self.f(13),
                                     text_color=MUTED)
        self.subtitle.grid(row=1, column=0, sticky="w")
        self.scan_btn = accent_button(head, "⟳  Scan now", self.scan, width=150)
        self.scan_btn.grid(row=0, column=1, rowspan=2, sticky="e")
        self.progress = ctk.CTkProgressBar(head, height=4, corner_radius=2, progress_color=ACCENT, fg_color=BG)
        self.progress.set(0)
        self.progress.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.progress.grid_remove()

        # stat tiles (clickable filters)
        tiles = ctk.CTkFrame(pg, fg_color="transparent")
        tiles.grid(row=1, column=0, sticky="ew", padx=26, pady=(18, 6))
        self.tiles: dict[str, ctk.CTkLabel] = {}
        spec = [("score", "Health score", ACCENT, None), ("mods", "Mods scanned", TEXT, None),
                ("critical", "Critical", SEV[Severity.CRITICAL][0], "Critical"),
                ("warning", "Warnings", SEV[Severity.WARNING][0], "Warning"),
                ("info", "Info", SEV[Severity.INFO][0], "Info")]
        for i, (key, label, color, flt) in enumerate(spec):
            tiles.grid_columnconfigure(i, weight=1, uniform="t")
            t = ctk.CTkFrame(tiles, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER, height=92)
            t.grid(row=0, column=i, sticky="ew", padx=6)
            t.grid_propagate(False)
            v = ctk.CTkLabel(t, text="-", font=self.f(30, "bold"), text_color=color)
            v.place(x=18, y=12)
            ctk.CTkLabel(t, text=label, font=self.f(12), text_color=MUTED).place(x=18, y=56)
            self.tiles[key] = v
            if flt:
                for w in (t, v):
                    w.bind("<Button-1>", lambda _e, f=flt: self.set_filter(f))
                    w.configure(cursor="hand2")
            bind_hover(t, CARD, CARD_HOVER)

        # filter bar
        bar = ctk.CTkFrame(pg, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=32, pady=(12, 8))
        self.seg = ctk.CTkSegmentedButton(
            bar, values=["All", "Critical", "Warning", "Info"], command=self.set_filter, height=34,
            selected_color=RAISED, selected_hover_color=RAISED, unselected_color=CARD,
            unselected_hover_color=CARD_HOVER, fg_color=CARD, text_color=TEXT, font=self.f(13), corner_radius=10)
        self.seg.set("All")
        self.seg.pack(side="left")
        self.search_entry = ctk.CTkEntry(bar, placeholder_text="Search mods or problems...", width=260, height=34,
                                         fg_color=CARD, border_color=BORDER, text_color=TEXT, corner_radius=10,
                                         font=self.f(13))
        self.search_entry.pack(side="left", padx=12)
        self.search_entry.bind("<KeyRelease>", lambda _e: self._on_search())
        ctk.CTkButton(bar, text="Select all fixes", command=self.select_all, fg_color="transparent",
                      hover_color=RAISED, text_color=VIOLET, width=110, height=34, font=self.f(13)).pack(side="right")
        ctk.CTkButton(bar, text="Clear", command=self.clear_selection, fg_color="transparent",
                      hover_color=RAISED, text_color=MUTED, width=60, height=34, font=self.f(13)).pack(side="right")

        self.list = ctk.CTkScrollableFrame(pg, fg_color="transparent", scrollbar_button_color=RAISED,
                                           scrollbar_button_hover_color=BORDER)
        self.list.grid(row=3, column=0, sticky="nsew", padx=22)
        self.list.grid_columnconfigure(0, weight=1)

        # action bar
        act = ctk.CTkFrame(pg, fg_color=SIDEBAR, corner_radius=0, height=72)
        act.grid(row=4, column=0, sticky="ew")
        act.grid_propagate(False)
        self.sel_label = ctk.CTkLabel(act, text="", font=self.f(13), text_color=MUTED)
        self.sel_label.pack(side="left", padx=32)
        self.fix_btn = accent_button(act, "Fix selected", self.fix_checked, width=170)
        self.fix_btn.pack(side="right", padx=(8, 32), pady=16)
        ghost_button(act, "↺  Undo last fix", self.undo_last, width=140).pack(side="right", padx=4)
        ghost_button(act, "Open report", self.open_report, width=120).pack(side="right", padx=4)
        self._empty_state("Scan your Mods folder to see what's wrong.", "✦")

    def _empty_state(self, text: str, icon: str, color: str = FAINT) -> None:
        for w in self.list.winfo_children():
            w.destroy()
        box = ctk.CTkFrame(self.list, fg_color="transparent")
        box.grid(row=0, column=0, pady=80)
        ctk.CTkLabel(box, text=icon, font=self.f(54), text_color=color).pack()
        ctk.CTkLabel(box, text=text, font=self.f(16), text_color=MUTED).pack(pady=6)

    def _set_scanning(self, on: bool) -> None:
        self.scan_btn.configure(state="disabled" if on else "normal", text="Scanning..." if on else "⟳  Scan now")
        if on:
            self.progress.grid()
        else:
            self.progress.set(0)
            self.progress.grid_remove()

    def scan(self) -> None:
        d = self.require_dir()
        if not d:
            return
        self._set_scanning(True)
        self.progress.set(0.02)
        self.subtitle.configure(text="Reading your mods... big folders can take a minute.")

        def work():
            inv = scan(d)
            fs = run_rules(inv, lambda i, n, name: self.events.put((self.progress.set, ((i + 1) / n,))))
            report.write(inv, fs, app_dir(d) / "report.html")
            return inv, fs

        self._background(work, lambda r: self._show(*r))

    def _show(self, inv, findings: list[Finding]) -> None:
        self.busy = False
        self._set_scanning(False)
        self.inv, self.findings = inv, findings
        self.checked = {i for i, f in enumerate(findings) if f.fixable and f.auto}
        self.expanded = set()
        n = {s: sum(f.severity == s for f in findings) for s in Severity}
        score = max(0, 100 - 8 * n[Severity.CRITICAL] - 3 * n[Severity.WARNING])
        score_color = ACCENT if score >= 80 else SEV[Severity.WARNING][0] if score >= 50 else SEV[Severity.CRITICAL][0]
        self.tiles["score"].configure(text=str(score), text_color=score_color)
        self.tiles["mods"].configure(text=str(len(inv.packages()) + len(inv.scripts())))
        self.tiles["critical"].configure(text=str(n[Severity.CRITICAL]))
        self.tiles["warning"].configure(text=str(n[Severity.WARNING]))
        self.tiles["info"].configure(text=str(n[Severity.INFO]))
        self.subtitle.configure(text=f"{len(inv.packages())} packages and {len(inv.scripts())} script mods "
                                     f"scanned at {time.strftime('%H:%M')}. Safe fixes are pre-selected.")
        self.render()

    def set_filter(self, value: str) -> None:
        self.filter = value
        self.seg.set(value)
        self.render()

    def _on_search(self) -> None:
        self.search = self.search_entry.get().strip().lower()
        self.render()

    def visible(self) -> list[int]:
        out = []
        for i, f in enumerate(self.findings):
            if self.filter != "All" and SEV[f.severity][2] != self.filter:
                continue
            if self.search and self.search not in (f.title + " " + f.detail + " " +
                                                   " ".join(p.name for p in f.files)).lower():
                continue
            out.append(i)
        return out

    def render(self) -> None:
        for w in self.list.winfo_children():
            w.destroy()
        if not self.findings:
            if self.inv is not None:
                self._empty_state("No problems found. Your mods look healthy!", "✔", ACCENT)
            self._update_selection()
            return
        rows = self.visible()
        if not rows:
            self._empty_state("Nothing matches this filter.", "∅")
        for r, i in enumerate(rows):
            self._card(r, i)
        self._update_selection()

    def _card(self, row: int, i: int) -> None:
        f = self.findings[i]
        color, dim, label = SEV[f.severity]
        card = ctk.CTkFrame(self.list, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
        card.grid(row=row, column=0, sticky="ew", padx=10, pady=6)
        card.grid_columnconfigure(2, weight=1)
        ctk.CTkFrame(card, fg_color=color, width=4, height=1, corner_radius=2).grid(row=0, column=0, rowspan=3, sticky="ns",
                                                                         padx=(10, 0), pady=14)
        if f.fixable:
            var = ctk.IntVar(value=1 if i in self.checked else 0)
            ctk.CTkCheckBox(card, text="", variable=var, width=24, checkbox_width=22, checkbox_height=22,
                            corner_radius=6, fg_color=ACCENT, hover_color=ACCENT_HOVER, border_color=FAINT,
                            checkmark_color=ACCENT_TEXT,
                            command=lambda: self.toggle(i, bool(var.get()))).grid(row=0, column=1, padx=(14, 4),
                                                                                   pady=(16, 0), sticky="n")
        else:
            ctk.CTkLabel(card, text="", width=24).grid(row=0, column=1, padx=(14, 4))
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=2, sticky="ew", pady=(14, 0), padx=(6, 0))
        pill(top, label.upper(), color, dim).pack(side="left")
        if f.fixable and not f.auto:
            pill(top, "YOUR CALL", VIOLET, "#251d47").pack(side="left", padx=6)
        ctk.CTkLabel(card, text=f.title, font=self.f(15, "bold"), text_color=TEXT, anchor="w", justify="left",
                     wraplength=640).grid(row=1, column=2, sticky="ew", padx=(6, 0), pady=(6, 0))
        sub = f"→  {f.fix_label}" if f.fixable else "Nothing to fix here, just good to know."
        ctk.CTkLabel(card, text=sub, font=self.f(13), text_color=MUTED, anchor="w", justify="left",
                     wraplength=640).grid(row=2, column=2, sticky="ew", padx=(6, 0), pady=(2, 14))

        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.grid(row=0, column=3, rowspan=3, padx=14, sticky="e")
        open_ = i in self.expanded
        ghost_button(btns, "Hide" if open_ else "Details", lambda: self.toggle_details(i), width=84,
                     height=32).pack(side="left", padx=4)
        if f.fixable:
            ctk.CTkButton(btns, text="Fix", command=lambda: self.fix_one(i), width=64, height=32, corner_radius=10,
                          fg_color=RAISED, hover_color=ACCENT_HOVER, text_color=TEXT,
                          font=self.f(13, "bold")).pack(side="left", padx=4)
        if open_:
            d = ctk.CTkFrame(card, fg_color=BG, corner_radius=10)
            d.grid(row=3, column=1, columnspan=3, sticky="ew", padx=(14, 14), pady=(0, 14))
            ctk.CTkLabel(d, text=f.detail, font=self.f(13), text_color=TEXT, wraplength=820, justify="left",
                         anchor="w").pack(fill="x", padx=14, pady=(12, 6))
            for p in f.files[:8]:
                ctk.CTkLabel(d, text="•  " + self._rel(p), font=ctk.CTkFont("Consolas", 12), text_color=MUTED,
                             anchor="w", justify="left", wraplength=820).pack(fill="x", padx=14)
            if len(f.files) > 8:
                ctk.CTkLabel(d, text=f"   ...and {len(f.files) - 8} more", font=self.f(12),
                             text_color=FAINT, anchor="w").pack(fill="x", padx=14)
            mods = self._mod_files(f)
            if mods:
                danger_button(d, f"Remove {len(mods)} mod file(s)", lambda: self.remove_files(mods), width=180,
                              height=32).pack(anchor="w", padx=14, pady=12)
            else:
                ctk.CTkLabel(d, text="", height=6).pack()
        bind_hover(card, CARD, CARD_HOVER)

    def _rel(self, p: Path) -> str:
        try:
            return str(p.relative_to(self.dir))
        except ValueError:
            return str(p)

    @staticmethod
    def _mod_files(f: Finding) -> list[Path]:
        return [p for p in f.files if p.suffix.lower() in MOD_EXTS and p.is_file()]

    def toggle(self, i: int, on: bool) -> None:
        (self.checked.add if on else self.checked.discard)(i)
        self._update_selection()

    def toggle_details(self, i: int) -> None:
        self.expanded.symmetric_difference_update({i})
        self.render()

    def select_all(self) -> None:
        self.checked = {i for i in self.visible() if self.findings[i].fixable} | self.checked
        self.render()

    def clear_selection(self) -> None:
        self.checked = set()
        self.render()

    def _update_selection(self) -> None:
        n = len(self.checked)
        steps = sum(len(self.findings[i].actions) for i in self.checked)
        self.sel_label.configure(text=f"{n} fix(es) selected  ·  {steps} change(s)" if n else "Nothing selected")
        self.fix_btn.configure(state="normal" if n else "disabled",
                               fg_color=ACCENT if n else RAISED, text_color=ACCENT_TEXT if n else FAINT)

    # ---- actions --------------------------------------------------------
    def _apply(self, findings: list[Finding], what: str) -> None:
        d = self.require_dir()
        if not d or not findings:
            return
        if game_running():
            self.toast("The Sims 4 is still running. Close the game first, it locks mod files.", "warn")
            return
        n = sum(len(f.actions) for f in findings)
        if not self.confirm(what, f"{n} change(s). Nothing is deleted: removed files go to the quarantine "
                                  "folder, and 'Undo last fix' puts everything back.", "Go ahead"):
            return
        self.fix_btn.configure(text="Fixing...", state="disabled")

        def done(res):
            self.busy = False
            self.fix_btn.configure(text="Fix selected")
            if res.errors:
                self.toast(f"Done {len(res.done)} change(s). {len(res.errors)} couldn't be done: "
                           + res.errors[0], "warn", 8000)
            else:
                self.toast(f"Fixed! {len(res.done)} change(s) made. You can undo this from History.")
            self.scan()

        self._background(lambda: fixer.Fixer(d).apply(findings, log=lambda *_: None), done)

    def fix_checked(self) -> None:
        self._apply([self.findings[i] for i in sorted(self.checked)], "Apply the selected fixes?")

    def fix_one(self, i: int) -> None:
        f = self.findings[i]
        self._apply([f], f"Fix: {f.title}?")

    def remove_files(self, files: list[Path]) -> None:
        f = Finding("manual", Severity.INFO, "Manual removal", "", files, [Action("quarantine", p) for p in files])
        self._apply([f], f"Remove {len(files)} mod file(s)?")

    def undo_last(self) -> None:
        d = self.require_dir()
        if not d:
            return
        sessions = [s for s in fixer.list_sessions(d) if not s.get("undone") and not s.get("purged")]
        if not sessions:
            self.toast("Nothing to undo.", "warn")
            return
        self.undo(sessions[-1]["session"])

    def undo(self, session: str) -> None:
        d = self.require_dir()
        if not d:
            return
        try:
            problems = fixer.undo(d, session, log=lambda *_: None)
        except (fixer.FixError, OSError) as e:
            self.toast(f"Couldn't undo: {e}", "error")
            return
        self.toast("Undone. Everything is back where it was." if not problems else
                   f"Undone, but {len(problems)} item(s) couldn't be restored: {problems[0]}",
                   "ok" if not problems else "warn")
        self.refresh_history()
        self.scan()

    def open_report(self) -> None:
        d = self.require_dir()
        if d and (app_dir(d) / "report.html").exists():
            webbrowser.open((app_dir(d) / "report.html").as_uri())

    # ---- 50/50 page -----------------------------------------------------
    def _page_bisect(self) -> None:
        pg = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.pages["bisect"] = pg
        ctk.CTkLabel(pg, text="50/50 finder", font=self.f(28, "bold"), text_color=TEXT).pack(anchor="w", padx=32,
                                                                                            pady=(28, 0))
        ctk.CTkLabel(pg, text="When the game breaks and nothing points at a mod, this finds it by elimination.",
                     font=self.f(13), text_color=MUTED).pack(anchor="w", padx=32)
        steps = ctk.CTkFrame(pg, fg_color="transparent")
        steps.pack(fill="x", padx=26, pady=20)
        for i, (t, s) in enumerate((("Half off", "BetterSims turns half of your mods off."),
                                    ("You test", "Start the game and check for the problem."),
                                    ("Tell it", "Still broken, or gone? It narrows it down."))):
            steps.grid_columnconfigure(i, weight=1, uniform="s")
            c = ctk.CTkFrame(steps, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
            c.grid(row=0, column=i, sticky="ew", padx=6)
            ctk.CTkLabel(c, text=str(i + 1), font=self.f(14, "bold"), text_color=ACCENT_TEXT, fg_color=ACCENT,
                         corner_radius=12, width=28, height=28).pack(anchor="w", padx=16, pady=(16, 6))
            ctk.CTkLabel(c, text=t, font=self.f(15, "bold"), text_color=TEXT).pack(anchor="w", padx=16)
            ctk.CTkLabel(c, text=s, font=self.f(12), text_color=MUTED, wraplength=230, justify="left").pack(
                anchor="w", padx=16, pady=(2, 16))

        self.bis_card = ctk.CTkFrame(pg, fg_color=CARD, corner_radius=16, border_width=1, border_color=BORDER)
        self.bis_card.pack(fill="x", padx=32, pady=6)
        self.bis_title = ctk.CTkLabel(self.bis_card, text="", font=self.f(20, "bold"), text_color=TEXT, anchor="w")
        self.bis_title.pack(fill="x", padx=24, pady=(22, 4))
        self.bis_text = ctk.CTkLabel(self.bis_card, text="", font=self.f(13), text_color=MUTED, anchor="w",
                                     justify="left", wraplength=820)
        self.bis_text.pack(fill="x", padx=24)
        self.bis_bar = ctk.CTkProgressBar(self.bis_card, height=8, corner_radius=4, progress_color=VIOLET,
                                          fg_color=RAISED)
        self.bis_btns = ctk.CTkFrame(self.bis_card, fg_color="transparent")
        self.bis_btns.pack(fill="x", padx=20, pady=(16, 20))

    def refresh_bisect(self, message: str | None = None) -> None:
        for w in self.bis_btns.winfo_children():
            w.destroy()
        d = Path(self.dir) if self.dir else None
        st = bisect.load_state(d) if d and d.is_dir() else None
        if st:
            total = max(1, len(st["units"]))
            left = len(st["suspects"])
            self.bis_title.configure(text=f"Round {st['round']}  ·  {left} suspects left")
            self.bis_text.configure(text=message or f"{len(st['testing_off'])} mod(s) are OFF right now. Start the "
                                    "game, check for the problem, then tell BetterSims what happened.")
            self.bis_bar.pack(fill="x", padx=24, pady=16, before=self.bis_btns)
            self.bis_bar.set(1 - left / total if total > 1 else 1)
            ctk.CTkButton(self.bis_btns, text="Still broken", command=lambda: self._bis(lambda: bisect.answer(d, True)),
                          fg_color=SEV[Severity.CRITICAL][1], hover_color="#4a2030",
                          text_color=SEV[Severity.CRITICAL][0], corner_radius=10, height=40, width=160,
                          font=self.f(14, "bold")).pack(side="left", padx=4)
            accent_button(self.bis_btns, "Problem's gone", lambda: self._bis(lambda: bisect.answer(d, False)),
                          width=160).pack(side="left", padx=4)
            ghost_button(self.bis_btns, "Stop and turn everything back on",
                         lambda: self._bis(lambda: bisect.reset(d)), height=40).pack(side="right", padx=4)
            return
        found = bisect.last_found_path(d) if d and d.is_dir() else None
        self.bis_bar.set(0)
        self.bis_bar.pack_forget()
        if message and message.startswith("Found it") and found and found.exists():
            data = json.loads(found.read_text())
            self.bis_title.configure(text=f"Found it: {data['label']}")
            self.bis_text.configure(text="All your mods are turned back on. If the problem still happens without this "
                                    "one, it isn't caused by a mod.")
            self.bis_bar.pack(fill="x", padx=24, pady=16, before=self.bis_btns)
            self.bis_bar.set(1)
            files = [d / "Mods" / f for f in data["files"]]
            danger_button(self.bis_btns, "Remove this mod", lambda: self.remove_files([f for f in files if f.exists()]),
                          width=170, height=40).pack(side="left", padx=4)
            ghost_button(self.bis_btns, "Start over", lambda: self._bis(lambda: bisect.start(d)),
                         height=40).pack(side="left", padx=4)
            return
        self.bis_title.configure(text="Ready when you are")
        self.bis_text.configure(text=message or "Close The Sims 4, then press Start. Each round halves the "
                                "suspects, so even 1,000 mods take about 10 rounds. Nothing is moved or deleted: "
                                "mods are just renamed to .packageOFF, and everything is turned back on at the end.")
        accent_button(self.bis_btns, "Start", lambda: self._bis(lambda: bisect.start(d)), width=140).pack(
            side="left", padx=4)

    def _bis(self, fn) -> None:
        if not self.require_dir():
            return
        if game_running():
            self.toast("Close The Sims 4 first: the game locks mod files while it's open.", "warn")
            return
        try:
            msg = fn()
        except SystemExit as e:
            msg = str(e)
        except OSError as e:
            msg = f"Couldn't rename a mod file: {e}"
        self.refresh_bisect(msg)

    # ---- history page ---------------------------------------------------
    def _page_history(self) -> None:
        pg = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        pg.grid_columnconfigure(0, weight=1)
        pg.grid_rowconfigure(2, weight=1)
        self.pages["history"] = pg
        head = ctk.CTkFrame(pg, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=32, pady=(28, 0))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="History & undo", font=self.f(28, "bold"), text_color=TEXT).grid(row=0, column=0,
                                                                                               sticky="w")
        ghost_button(head, "Open quarantine folder", self.open_quarantine, width=190).grid(row=0, column=1, padx=6)
        danger_button(head, "Empty quarantine...", self.purge, width=170).grid(row=0, column=2)
        ctk.CTkLabel(pg, text="Every fix is recorded. Undo any of them to put the files back exactly where they were.",
                     font=self.f(13), text_color=MUTED).grid(row=1, column=0, sticky="w", padx=32)
        self.hist = ctk.CTkScrollableFrame(pg, fg_color="transparent", scrollbar_button_color=RAISED)
        self.hist.grid(row=2, column=0, sticky="nsew", padx=22, pady=16)
        self.hist.grid_columnconfigure(0, weight=1)

    def refresh_history(self) -> None:
        for w in self.hist.winfo_children():
            w.destroy()
        d = Path(self.dir) if self.dir else None
        sessions = list(reversed(fixer.list_sessions(d))) if d and d.is_dir() else []
        if not sessions:
            ctk.CTkLabel(self.hist, text="No fixes yet.", font=self.f(15), text_color=MUTED).grid(row=0, column=0,
                                                                                                pady=60)
            return
        for r, s in enumerate(sessions):
            state = "undone" if s.get("undone") else "deleted" if s.get("purged") else "active"
            c = ctk.CTkFrame(self.hist, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
            c.grid(row=r, column=0, sticky="ew", padx=10, pady=6)
            c.grid_columnconfigure(1, weight=1)
            when = time.strftime("%b %d, %H:%M", time.localtime(s.get("created", 0)))
            ctk.CTkLabel(c, text=when, font=self.f(15, "bold"), text_color=TEXT).grid(row=0, column=0, sticky="w",
                                                                                      padx=20, pady=(14, 0))
            colors = {"active": (ACCENT, "#10331f"), "undone": (MUTED, RAISED), "deleted": (FAINT, RAISED)}
            pill(c, state.upper(), *colors[state]).grid(row=0, column=1, sticky="w", padx=10, pady=(14, 0))
            what = ", ".join(s.get("findings", [])[:3]) or "Fixes"
            more = len(s.get("findings", [])) - 3
            ctk.CTkLabel(c, text=f"{len(s['ops'])} step(s)  ·  {what}" + (f" and {more} more" if more > 0 else ""),
                         font=self.f(12), text_color=MUTED, anchor="w", wraplength=720, justify="left").grid(
                row=1, column=0, columnspan=2, sticky="w", padx=20, pady=(2, 14))
            if state == "active":
                ghost_button(c, "↺  Undo", lambda sid=s["session"]: self.undo(sid), width=100).grid(
                    row=0, column=2, rowspan=2, padx=16)
            bind_hover(c, CARD, CARD_HOVER)

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
        if self.confirm("Empty the quarantine?", "This permanently deletes every quarantined file. Those fixes can't "
                        "be undone afterwards.", "Delete forever", danger=True):
            n = fixer.purge(d)
            self.toast(f"Quarantine emptied ({len(n)} fix session(s)).")
            self.refresh_history()


def main(sims_dir: str | None = None) -> None:
    App(sims_dir).mainloop()
