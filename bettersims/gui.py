"""Desktop app: a dark, animated HUD-style UI built with CustomTkinter.

Heavy work (scans, fixes, game test runs) always happens on a background thread and reports
back through a queue, so the window and its animations keep moving the whole time.
"""
from __future__ import annotations

import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from . import __version__, bisect, demo, fixer, report
from .explain import explain, summary
from .hud import (BG, BODY_FONT, BORDER, CARD, CARD_HOVER, DISPLAY_FONT, FAINT, GLOW, GLOW_HOVER, GLOW_TEXT, GOLD,
                  GREEN, MONO_FONT, MUTED, RAISED, RED, SIDEBAR, TEXT, HudBar, HudLog, Reactor, alive,
                  count_to, glow_hover, materialize, mix, tween)
from .model import PACKAGE_EXT, SCRIPT_EXT, Action, Finding, Severity
from .paths import app_dir, check_sims_dir, find_sims_dirs, game_running
from .rules import run_rules
from .scanner import scan

ACCENT, ACCENT_HOVER, ACCENT_TEXT = GLOW, GLOW_HOVER, GLOW_TEXT
ORANGE = "#ff9f43"
INDIGO = "#8c9bff"
SEV = {
    Severity.CRITICAL: (RED, mix(CARD, RED, 0.2), "Critical"),
    Severity.WARNING: (ORANGE, mix(CARD, ORANGE, 0.2), "Warning"),
    Severity.INFO: (INDIGO, mix(CARD, INDIGO, 0.2), "Info"),
}
MOD_EXTS = (PACKAGE_EXT, SCRIPT_EXT, ".zip", ".rar", ".7z", ".packageoff", ".ts4scriptoff")
SPIN = "◐◓◑◒"


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
def accent_button(master, text, command, color=GLOW, hover=GLOW_HOVER, text_color=GLOW_TEXT, **kw) -> ctk.CTkButton:
    kw.setdefault("height", 40)
    b = ctk.CTkButton(master, text=text, command=command, fg_color=color, hover_color=hover, text_color=text_color,
                      corner_radius=8, border_width=1, border_color=color,
                      font=ctk.CTkFont(DISPLAY_FONT, 14, "bold"), **kw)
    glow_hover(b, base=color, glow="#ffffff", strength=0.9)
    return b


def set_enabled(btn: ctk.CTkButton, on: bool, color: str = GLOW, text_color: str = GLOW_TEXT) -> None:
    """Disabled accent buttons go dark instead of staying bright with faded text."""
    btn.configure(state="normal" if on else "disabled", fg_color=color if on else RAISED,
                  border_color=color if on else RAISED, text_color=text_color if on else FAINT)


def ghost_button(master, text, command, color=TEXT, **kw) -> ctk.CTkButton:
    kw.setdefault("height", 36)
    b = ctk.CTkButton(master, text=text, command=command, fg_color="transparent", hover_color=RAISED,
                      text_color=color, border_width=1, border_color=BORDER, corner_radius=8,
                      font=ctk.CTkFont(BODY_FONT, 13), **kw)
    glow_hover(b, BORDER, GLOW)
    return b


def danger_button(master, text, command, **kw) -> ctk.CTkButton:
    kw.setdefault("height", 36)
    base = mix(CARD, RED, 0.45)
    b = ctk.CTkButton(master, text=text, command=command, fg_color="transparent", hover_color=mix(CARD, RED, 0.18),
                      text_color=RED, border_width=1, border_color=base, corner_radius=8,
                      font=ctk.CTkFont(BODY_FONT, 13), **kw)
    glow_hover(b, base, RED)
    return b


def link_button(master, text, command, color=GOLD, **kw) -> ctk.CTkButton:
    kw.setdefault("height", 32)
    return ctk.CTkButton(master, text=text, command=command, fg_color="transparent", hover_color=RAISED,
                         text_color=color, corner_radius=8, font=ctk.CTkFont(BODY_FONT, 13), **kw)


def pill(master, text, fg, bg) -> ctk.CTkLabel:
    return ctk.CTkLabel(master, text=f"  {text}  ", fg_color=bg, text_color=fg, corner_radius=6, height=22,
                        font=ctk.CTkFont(DISPLAY_FONT, 11, "bold"))


def segmented(master, values, command=None, **kw) -> ctk.CTkSegmentedButton:
    kw.setdefault("height", 32)
    return ctk.CTkSegmentedButton(master, values=values, command=command, font=ctk.CTkFont(BODY_FONT, 12),
                                  selected_color=mix(CARD, GLOW, 0.35), selected_hover_color=mix(CARD, GLOW, 0.45),
                                  unselected_color=BG, unselected_hover_color=CARD_HOVER, fg_color=BG,
                                  text_color=TEXT, corner_radius=8, **kw)


def brackets(frame, color=GLOW, bg=CARD, size=12) -> None:
    """HUD corner brackets on a panel."""
    for anchor, pts, kw in (("nw", (1, size, 1, 1, size, 1), dict(x=6, y=6)),
                            ("se", (size - 1, 0, size - 1, size - 1, 0, size - 1), dict(relx=1, rely=1, x=-6, y=-6))):
        c = tk.Canvas(frame, width=size, height=size, bg=bg, highlightthickness=0, bd=0)
        c.create_line(*pts, fill=mix(bg, color, 0.7), width=2)
        c.place(anchor=anchor, **kw)


def hud_panel(master, **kw) -> ctk.CTkFrame:
    kw.setdefault("corner_radius", 12)
    return ctk.CTkFrame(master, fg_color=CARD, border_width=1, border_color=BORDER, **kw)


class Confirm(ctk.CTkToplevel):
    """A themed yes/no dialog."""

    def __init__(self, parent, title: str, message: str, ok: str, danger: bool = False):
        super().__init__(parent, fg_color=CARD)
        self.result = False
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        color = RED if danger else GLOW
        frame = ctk.CTkFrame(self, fg_color=CARD, border_width=1, border_color=mix(CARD, color, 0.6), corner_radius=0)
        frame.pack(fill="both", expand=True)
        f = ctk.CTkFrame(frame, fg_color="transparent")
        f.pack(padx=28, pady=24, fill="both", expand=True)
        ctk.CTkLabel(f, text=title.upper(), font=ctk.CTkFont(DISPLAY_FONT, 18, "bold"), text_color=color,
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(f, text=message, font=ctk.CTkFont(BODY_FONT, 13), text_color=MUTED, wraplength=460,
                     justify="left", anchor="w").pack(fill="x", pady=(8, 20))
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack(fill="x")
        if danger:
            danger_button(row, ok, self._ok, width=150, height=38).pack(side="right")
        else:
            accent_button(row, ok, self._ok, width=150).pack(side="right")
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


# Auto Repair steps: (phase key, title, explanation)
REPAIR_STEPS = (
    ("scan", "Scan & fix files", "Duplicates, old versions, broken files, wrong folders, cache"),
    ("test", "Test launch", "Starts the game and watches for crashes and new error logs"),
    ("search", "Find the bad mod", "Automatic 50/50: half the suspects off each round"),
    ("remove", "Remove it", "Moved to quarantine, never deleted"),
    ("done", "Runs clean", "Repeats until the game starts without problems"),
)
STEP_OF = {"scan": 0, "fix": 0, "test": 1, "search": 2, "remove": 3, "done": 4}


# ---- the app ---------------------------------------------------------------
class App(ctk.CTk):
    def __init__(self, sims_dir: str | None):
        ctk.set_appearance_mode("dark")
        super().__init__(fg_color=BG)
        self.title(f"BetterSims {__version__}")
        self.geometry("1280x820")
        self.minsize(1040, 660)
        self.f = lambda size, weight="normal": ctk.CTkFont(BODY_FONT, size, weight)
        self.d = lambda size, weight="bold": ctk.CTkFont(DISPLAY_FONT, size, weight)

        self.findings: list[Finding] = []
        self.checked: set[int] = set()
        self.inv = None
        self._busy = False
        self.filter = "All"
        self.search = ""
        self.expanded: set[int] = set()
        self.events: queue.Queue = queue.Queue()
        self.cards: dict[int, ctk.CTkFrame] = {}
        self._gen = 0
        self.current = ""
        self.auto_stop_event: threading.Event | None = None
        self.rep_stop: threading.Event | None = None
        from . import autobisect
        self.game_exe = autobisect.find_game_exe()
        self.exe_labels: list[ctk.CTkLabel] = []

        found = find_sims_dirs()
        self.dir = sims_dir or (str(found[0]) if found else "")

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._sidebar()
        self.content = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.pages: dict[str, ctk.CTkFrame] = {}
        self._page_problems()
        self._page_repair()
        self._page_bisect()
        self._page_history()
        self.show("problems", animate=False)
        self.after(100, self._pump)
        self.after(250, self._blink)
        if self.dir:
            self.after(400, self.scan)

    # ---- plumbing -------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._busy

    @busy.setter
    def busy(self, on: bool) -> None:
        self._busy = on
        if hasattr(self, "logo"):
            self.logo.set_mode("busy" if on else "idle")

    def _pump(self) -> None:
        try:
            while True:
                fn, args = self.events.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.after(60, self._pump)

    def _blink(self) -> None:
        """Sidebar status light: steady when idle, blinking while working."""
        on = not self.busy or int(time.time() * 2.5) % 2 == 0
        self.sys_dot.configure(text_color=(GOLD if self.busy else GREEN) if on else mix(SIDEBAR, GOLD, 0.25))
        self.sys_text.configure(text="WORKING" if self.busy else "SYSTEMS READY")
        self.after(200, self._blink)

    def _background(self, work, done) -> None:
        self.busy = True

        def run():
            try:
                result = work()
            except (Exception, SystemExit) as e:  # noqa: BLE001 - SystemExit carries plain-English stop reasons
                self.events.put((self._failed, (e,)))
                return
            self.events.put((done, (result,)))

        threading.Thread(target=run, daemon=True).start()

    def _failed(self, e: BaseException) -> None:
        self.busy = False
        set_enabled(self.auto_start, True)
        self.auto_stop.configure(state="disabled")
        self.auto_status.configure(text="Stopped")
        self.auto_bar.stop()
        if self.rep_stop is not None:
            self._repair_ui(False)
            self.reactor.set_mode("warn", "STOPPED", "see the log")
            self.rep_log.line(str(e))
        self.fix_btn.configure(text="Fix selected")
        self._set_scanning(False)
        self.toast(str(e) if isinstance(e, SystemExit) else f"Something went wrong: {e}", "error", 7000)

    def confirm(self, title: str, message: str, ok: str, danger: bool = False) -> bool:
        d = Confirm(self, title, message, ok, danger)
        self.wait_window(d)
        return d.result

    def toast(self, text: str, kind: str = "ok", ms: int = 4200) -> None:
        color = {"ok": GLOW, "warn": GOLD, "error": RED}[kind]
        t = ctk.CTkFrame(self, fg_color=RAISED, corner_radius=10, border_width=1, border_color=color)
        ctk.CTkLabel(t, text="◆", text_color=color, font=self.f(14)).pack(side="left", padx=(14, 6), pady=12)
        ctk.CTkLabel(t, text=text, text_color=TEXT, font=self.f(13), wraplength=420, justify="left").pack(
            side="left", padx=(0, 16), pady=12)
        self.last_toast = text
        # slides in from the right, then out again
        tween(t, lambda p: t.place(relx=1.0, rely=1.0, x=-24 + 460 * (1 - p), y=-92, anchor="se"), frames=14, ms=16)

        def out():
            if alive(t):
                tween(t, lambda p: t.place(relx=1.0, rely=1.0, x=-24 + 460 * p, y=-92, anchor="se"),
                      frames=12, ms=16, done=t.destroy)
        self.after(ms, out)

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

    def _heading(self, parent, title: str, subtitle: str) -> tuple[ctk.CTkFrame, ctk.CTkLabel]:
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.grid_columnconfigure(0, weight=1)
        row = ctk.CTkFrame(head, fg_color="transparent")
        row.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(row, text="◢", font=self.f(14), text_color=GLOW).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(row, text=title.upper(), font=self.d(26), text_color=TEXT).pack(side="left")
        sub = ctk.CTkLabel(head, text=subtitle, font=self.f(13), text_color=MUTED)
        sub.grid(row=1, column=0, sticky="w")
        return head, sub

    # ---- sidebar --------------------------------------------------------
    def _sidebar(self) -> None:
        sb = self.sidebar = ctk.CTkFrame(self, fg_color=SIDEBAR, corner_radius=0, width=250)
        sb.grid(row=0, column=0, sticky="nsw")
        sb.grid_propagate(False)
        logo = ctk.CTkFrame(sb, fg_color="transparent")
        logo.pack(fill="x", padx=16, pady=(22, 6))
        self.logo = Reactor(logo, size=52, bg=SIDEBAR)
        self.logo.set_text("", "")
        self.logo.pack(side="left")
        txt = ctk.CTkFrame(logo, fg_color="transparent")
        txt.pack(side="left", padx=10)
        ctk.CTkLabel(txt, text="BETTERSIMS", font=self.d(20), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(txt, text=f"MOD DIAGNOSTICS  v{__version__}", font=self.d(10, "normal"), text_color=FAINT).pack(
            anchor="w")
        HudBar(sb, height=2, bg=SIDEBAR, ambient=True).pack(fill="x", padx=16, pady=(10, 18))

        self.nav: dict[str, ctk.CTkButton] = {}
        for key, label in (("problems", "✦   Mod health"), ("repair", "⚡   Auto Repair"),
                           ("bisect", "◑   50/50 finder"), ("history", "↺   History & undo")):
            b = ctk.CTkButton(sb, text=label, anchor="w", height=44, corner_radius=8, fg_color="transparent",
                              hover_color=CARD_HOVER, text_color=MUTED, font=self.f(14),
                              command=lambda k=key: self.show(k))
            b.pack(fill="x", padx=(18, 14), pady=3)
            self.nav[key] = b
        self.nav_ind = ctk.CTkFrame(sb, width=3, height=26, fg_color=GLOW, corner_radius=2)

        status = ctk.CTkFrame(sb, fg_color="transparent")
        status.pack(side="bottom", fill="x", padx=22, pady=(0, 14))
        self.sys_dot = ctk.CTkLabel(status, text="●", font=self.f(12), text_color=GREEN)
        self.sys_dot.pack(side="left")
        self.sys_text = ctk.CTkLabel(status, text="SYSTEMS READY", font=self.d(10), text_color=FAINT)
        self.sys_text.pack(side="left", padx=6)

        folder = hud_panel(sb)
        folder.pack(side="bottom", fill="x", padx=14, pady=(18, 10))
        ctk.CTkLabel(folder, text="SIMS 4 FOLDER", font=self.d(10), text_color=FAINT).pack(
            anchor="w", padx=14, pady=(12, 0))
        self.dir_label = ctk.CTkLabel(folder, text=short_path(self.dir) or "Not selected", font=self.f(12),
                                      text_color=TEXT, anchor="w", justify="left", wraplength=200)
        self.dir_label.pack(anchor="w", fill="x", padx=14, pady=(2, 10))
        ghost_button(folder, "Change folder", self.browse, height=32).pack(fill="x", padx=12)
        link_button(folder, "Try a demo folder", self.make_demo, height=30).pack(fill="x", padx=12, pady=(4, 12))
        glow_hover(folder)

    def _move_indicator(self, key: str, animate: bool = True) -> None:
        b = self.nav[key]
        self.update_idletasks()
        y_to = b.winfo_y() + (b.winfo_height() - 26) // 2
        scale = self.nav_ind._get_widget_scaling()
        y_to = y_to / scale
        if not self.nav_ind.winfo_ismapped() or not animate:
            self.nav_ind.place(x=8, y=y_to)
            return
        y_from = self.nav_ind.winfo_y() / scale
        tween(self.nav_ind, lambda t: self.nav_ind.place(x=8, y=y_from + (y_to - y_from) * t), frames=10, ms=16)

    def show(self, key: str, animate: bool = True) -> None:
        for k, b in self.nav.items():
            on = k == key
            b.configure(fg_color=mix(SIDEBAR, GLOW, 0.1) if on else "transparent", text_color=TEXT if on else MUTED)
        self.after(10, lambda: self._move_indicator(key, animate))
        for k, p in self.pages.items():
            if k != key:
                p.place_forget()
        page = self.pages[key]
        if animate and key != self.current:
            tween(page, lambda t: page.place(x=50 * (1 - t), y=0, relwidth=1, relheight=1), frames=12, ms=16,
                  key="_slide")
        else:
            page.place(x=0, y=0, relwidth=1, relheight=1)
        self.current = key
        if key == "history":
            self.refresh_history()
        if key == "bisect":
            self.refresh_bisect()
            d = Path(self.dir) if self.dir else None
            if d and d.is_dir() and bisect.load_state(d) and not (self.auto_stop_event and not self.auto_stop_event.is_set()):
                self._bis_mode("Manual")

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
        pg = ctk.CTkFrame(self.content, fg_color=BG, corner_radius=0)
        pg.grid_columnconfigure(0, weight=1)
        pg.grid_rowconfigure(3, weight=1)
        self.pages["problems"] = pg

        head, self.subtitle = self._heading(pg, "Mod health", "Pick your Sims 4 folder to start.")
        head.grid(row=0, column=0, sticky="ew", padx=32, pady=(26, 4))
        self.scan_btn = accent_button(head, "⟳  SCAN NOW", self.scan, width=160)
        self.scan_btn.grid(row=0, column=1, rowspan=2, sticky="e")
        self.progress = HudBar(head, height=4, ambient=True)
        self.progress.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))

        # stat tiles (clickable filters)
        tiles = ctk.CTkFrame(pg, fg_color="transparent")
        tiles.grid(row=1, column=0, sticky="ew", padx=26, pady=(16, 6))
        self.tiles: dict[str, ctk.CTkLabel] = {}
        spec = [("score", "HEALTH SCORE", GLOW, None), ("mods", "MODS SCANNED", TEXT, None),
                ("critical", "CRITICAL", RED, "Critical"), ("warning", "WARNINGS", ORANGE, "Warning"),
                ("info", "INFO", INDIGO, "Info")]
        for i, (key, label, color, flt) in enumerate(spec):
            tiles.grid_columnconfigure(i, weight=1, uniform="t")
            t = hud_panel(tiles, height=94)
            t.grid(row=0, column=i, sticky="ew", padx=6)
            t.grid_propagate(False)
            v = ctk.CTkLabel(t, text="-", font=self.d(32), text_color=color)
            v.place(x=20, y=12)
            ctk.CTkLabel(t, text=label, font=self.d(10, "normal"), text_color=MUTED).place(x=20, y=60)
            brackets(t, color)
            self.tiles[key] = v
            if flt:
                for w in (t, v):
                    w.bind("<Button-1>", lambda _e, f=flt: self.set_filter(f))
                    w.configure(cursor="hand2")
            glow_hover(t, BORDER, color)

        # filter bar
        bar = ctk.CTkFrame(pg, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=32, pady=(12, 8))
        self.seg = segmented(bar, ["All", "Critical", "Warning", "Info"], self.set_filter, height=34)
        self.seg.set("All")
        self.seg.pack(side="left")
        self.search_entry = ctk.CTkEntry(bar, placeholder_text="⌕  Search mods or problems...", width=280, height=34,
                                         fg_color=CARD, border_color=BORDER, text_color=TEXT, corner_radius=8,
                                         font=self.f(13))
        self.search_entry.pack(side="left", padx=12)
        self.search_entry.bind("<KeyRelease>", lambda _e: self._on_search())
        self.search_entry.bind("<FocusIn>", lambda _e: self.search_entry.configure(border_color=GLOW))
        self.search_entry.bind("<FocusOut>", lambda _e: self.search_entry.configure(border_color=BORDER))
        link_button(bar, "Select all fixes", self.select_all, width=120, height=34).pack(side="right")
        link_button(bar, "Clear", self.clear_selection, color=MUTED, width=60, height=34).pack(side="right")

        self.list = ctk.CTkScrollableFrame(pg, fg_color="transparent", scrollbar_button_color=RAISED,
                                           scrollbar_button_hover_color=BORDER)
        self.list.grid(row=3, column=0, sticky="nsew", padx=22)
        self.list.grid_columnconfigure(0, weight=1)

        # action bar
        act = ctk.CTkFrame(pg, fg_color=SIDEBAR, corner_radius=0, height=74)
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
        self.cards = {}
        box = ctk.CTkFrame(self.list, fg_color="transparent")
        box.grid(row=0, column=0, pady=80)
        ctk.CTkLabel(box, text=icon, font=self.f(54), text_color=color).pack()
        ctk.CTkLabel(box, text=text, font=self.f(16), text_color=MUTED).pack(pady=6)

    def _set_scanning(self, on: bool) -> None:
        self.scanning = on
        if on:
            self.scan_btn.configure(state="disabled")
            self.progress.ambient = False
            self.progress.set(0.03)
            self.progress.start()
            self._spin_scan()
        else:
            self.scan_btn.configure(state="normal", text="⟳  SCAN NOW")
            self.progress.set(1)
            self.progress.ambient = True

            def settle():
                if not getattr(self, "scanning", False):
                    self.progress.set(0)
                    self.progress.start()
            self.after(700, settle)

    def _spin_scan(self, i: int = 0) -> None:
        if not getattr(self, "scanning", False):
            return
        self.scan_btn.configure(text=f"{SPIN[i % 4]}  SCANNING")
        self.after(120, self._spin_scan, i + 1)

    def scan(self) -> None:
        d = self.require_dir()
        if not d:
            return
        self._set_scanning(True)
        self.subtitle.configure(text="Reading your mods... big folders can take a minute.")

        def work():
            inv = scan(d)
            self.events.put((self.progress.set, (0.25,)))
            fs = run_rules(inv, lambda i, n, name: self.events.put((self.progress.set, (0.25 + 0.7 * (i + 1) / n,))))
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
        score_color = GREEN if score >= 80 else ORANGE if score >= 50 else RED
        self.tiles["score"].configure(text_color=score_color)
        count_to(self.tiles["score"], score)
        count_to(self.tiles["mods"], len(inv.packages()) + len(inv.scripts()))
        count_to(self.tiles["critical"], n[Severity.CRITICAL])
        count_to(self.tiles["warning"], n[Severity.WARNING])
        count_to(self.tiles["info"], n[Severity.INFO])
        self.subtitle.configure(text=f"{len(inv.packages())} packages and {len(inv.scripts())} script mods "
                                     f"scanned at {time.strftime('%H:%M')}. Safe fixes are pre-selected.")
        self.render()

    def set_filter(self, value: str) -> None:
        self.filter = value
        self.seg.set(value)
        self.render()

    def _on_search(self) -> None:
        self.search = self.search_entry.get().strip().lower()
        self.render(animate=False)

    def visible(self) -> list[int]:
        out = []
        for i, f in enumerate(self.findings):
            if self.filter != "All" and SEV[f.severity][2] != self.filter:
                continue
            if self.search and self.search not in (f.title + " " + f.detail + " " + summary(f) + " " +
                                                   " ".join(p.name for p in f.files)).lower():
                continue
            out.append(i)
        return out

    def render(self, animate: bool = True) -> None:
        """Builds the cards a few at a time, so long lists never freeze the window."""
        self._gen += 1
        gen = self._gen
        for w in self.list.winfo_children():
            w.destroy()
        self.cards = {}
        self._update_selection()
        if not self.findings:
            if self.inv is not None:
                self._empty_state("No problems found. Your mods look healthy!", "✔", GREEN)
            return
        rows = self.visible()
        if not rows:
            self._empty_state("Nothing matches this filter.", "∅")
            return

        def chunk(start=0):
            if gen != self._gen:
                return
            for r in range(start, min(start + 4, len(rows))):
                card = self._card(r, rows[r])
                if animate:
                    materialize(card)
            if start + 4 < len(rows):
                self.after(16 if animate else 1, chunk, start + 4)
        chunk()

    def _card(self, row: int, i: int) -> ctk.CTkFrame:
        f = self.findings[i]
        color, dim, label = SEV[f.severity]
        card = ctk.CTkFrame(self.list, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
        card.grid(row=row, column=0, sticky="ew", padx=10, pady=5)
        card.grid_columnconfigure(2, weight=1)
        self.cards[i] = card
        ctk.CTkFrame(card, fg_color=color, width=3, height=1, corner_radius=2).grid(row=0, column=0, rowspan=3,
                                                                                   sticky="ns", padx=(10, 0), pady=14)
        if f.fixable:
            var = ctk.IntVar(value=1 if i in self.checked else 0)
            ctk.CTkCheckBox(card, text="", variable=var, width=24, checkbox_width=22, checkbox_height=22,
                            corner_radius=5, fg_color=GLOW, hover_color=GLOW_HOVER, border_color=FAINT,
                            checkmark_color=GLOW_TEXT,
                            command=lambda: self.toggle(i, bool(var.get()))).grid(row=0, column=1, padx=(14, 4),
                                                                                   pady=(16, 0), sticky="n")
        else:
            ctk.CTkLabel(card, text="", width=24).grid(row=0, column=1, padx=(14, 4))
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=2, sticky="ew", pady=(14, 0), padx=(6, 0))
        pill(top, label.upper(), color, dim).pack(side="left")
        if f.fixable and not f.auto:
            pill(top, "YOUR CALL", GOLD, mix(CARD, GOLD, 0.18)).pack(side="left", padx=6)
        ctk.CTkLabel(card, text=f.title, font=self.f(15, "bold"), text_color=TEXT, anchor="w", justify="left",
                     wraplength=640).grid(row=1, column=2, sticky="ew", padx=(6, 0), pady=(6, 0))
        ex = explain(f)
        sub = summary(f)
        if f.fixable and ex.fix:
            sub += "\n→  Fix: " + ex.fix.split(". ")[0].rstrip(".") + "."
        ctk.CTkLabel(card, text=sub, font=self.f(13), text_color=MUTED, anchor="w", justify="left",
                     wraplength=640).grid(row=2, column=2, sticky="ew", padx=(6, 0), pady=(2, 14))

        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.grid(row=0, column=3, rowspan=3, padx=14, sticky="e")
        open_ = i in self.expanded
        ghost_button(btns, "Hide" if open_ else "Details", lambda: self.toggle_details(i), width=84,
                     height=32).pack(side="left", padx=4)
        if f.fixable:
            accent_button(btns, "Fix", lambda: self.fix_one(i), width=64, height=32, color=RAISED,
                          hover=mix(RAISED, GLOW, 0.4), text_color=TEXT).pack(side="left", padx=4)
        if open_:
            self._details(card, f, ex)
        glow_hover(card, BORDER, color, fg=(CARD, CARD_HOVER))
        return card

    def _details(self, card, f: Finding, ex) -> None:
        d = ctk.CTkFrame(card, fg_color=BG, corner_radius=10)
        d.grid(row=3, column=1, columnspan=3, sticky="ew", padx=(14, 14), pady=(0, 14))
        self._section(d, "WHAT'S WRONG", ex.wrong)
        if f.fixable and ex.fix:
            self._section(d, "WHAT FIX WILL DO", ex.fix)
        if ex.safety:
            self._section(d, "IS IT SAFE?", ex.safety, GREEN if ex.safety.startswith("Safe") else GOLD)
        ctk.CTkLabel(d, text="YOUR CHOICES", font=self.d(10), text_color=FAINT, anchor="w").pack(
            fill="x", padx=14, pady=(12, 2))
        for name, what in ex.options:
            row = ctk.CTkFrame(d, fg_color=CARD, corner_radius=8, border_width=1, border_color=CARD)
            row.pack(fill="x", padx=14, pady=2)
            ctk.CTkLabel(row, text=name, font=self.f(12, "bold"), text_color=TEXT, width=130, anchor="w").pack(
                side="left", padx=(12, 6), pady=8, anchor="n")
            ctk.CTkLabel(row, text=what, font=self.f(12), text_color=MUTED, wraplength=640, justify="left",
                         anchor="w").pack(side="left", fill="x", expand=True, padx=(0, 12), pady=8)
            glow_hover(row, CARD, GLOW, 0.5)
        ctk.CTkLabel(d, text="TECHNICAL DETAILS", font=self.d(10), text_color=FAINT, anchor="w").pack(
            fill="x", padx=14, pady=(14, 2))
        ctk.CTkLabel(d, text=f.detail, font=self.f(12), text_color=FAINT, wraplength=820, justify="left",
                     anchor="w").pack(fill="x", padx=14, pady=(0, 4))
        for p in f.files[:8]:
            ctk.CTkLabel(d, text="•  " + self._rel(p), font=ctk.CTkFont(MONO_FONT, 12), text_color=MUTED,
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

    def _section(self, parent, title: str, body: str, color: str = TEXT) -> None:
        ctk.CTkLabel(parent, text=title, font=self.d(10), text_color=FAINT, anchor="w").pack(
            fill="x", padx=14, pady=(12, 0))
        ctk.CTkLabel(parent, text=body, font=self.f(13), text_color=color, wraplength=820, justify="left",
                     anchor="w").pack(fill="x", padx=14)

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
        """Rebuilds just this one card, not the whole list."""
        self.expanded.symmetric_difference_update({i})
        old = self.cards.get(i)
        if old is None or not alive(old):
            self.render(animate=False)
            return
        row = int(old.grid_info()["row"])
        old.destroy()
        materialize(self._card(row, i))

    def select_all(self) -> None:
        self.checked = {i for i in self.visible() if self.findings[i].fixable} | self.checked
        self.render(animate=False)

    def clear_selection(self) -> None:
        self.checked = set()
        self.render(animate=False)

    def _update_selection(self) -> None:
        n = len(self.checked)
        steps = sum(len(self.findings[i].actions) for i in self.checked)
        self.sel_label.configure(text=f"{n} fix(es) selected  ·  {steps} change(s)" if n else "Nothing selected")
        set_enabled(self.fix_btn, bool(n))

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
        self.progress.ambient = False
        self.progress.start()

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

        def done(problems):
            self.busy = False
            self.toast("Undone. Everything is back where it was." if not problems else
                       f"Undone, but {len(problems)} item(s) couldn't be restored: {problems[0]}",
                       "ok" if not problems else "warn")
            self.refresh_history()
            self.scan()

        self._background(lambda: fixer.undo(d, session, log=lambda *_: None), done)

    def open_report(self) -> None:
        d = self.require_dir()
        if d and (app_dir(d) / "report.html").exists():
            webbrowser.open((app_dir(d) / "report.html").as_uri())

    # ---- shared game settings ----------------------------------------------
    def _exe_row(self, card, row: int) -> None:
        self._label(card, row, "The Sims 4 game")
        box = ctk.CTkFrame(card, fg_color="transparent")
        box.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(0, 20))
        ghost_button(box, "Browse...", self._pick_exe, height=30, width=86).pack(side="right")
        lbl = ctk.CTkLabel(box, text=short_path(str(self.game_exe), 40) if self.game_exe else "TS4_x64.exe not found yet",
                           font=self.f(12), text_color=TEXT if self.game_exe else FAINT, anchor="w")
        lbl.pack(side="left", fill="x", expand=True)
        self.exe_labels.append(lbl)

    def _label(self, card, row: int, text: str) -> None:
        ctk.CTkLabel(card, text=text, font=self.f(13), text_color=MUTED, anchor="w").grid(
            row=row, column=0, sticky="w", padx=(24, 16), pady=6)

    def _pick_exe(self) -> None:
        f = filedialog.askopenfilename(title="Find TS4_x64.exe (in The Sims 4\\Game\\Bin)",
                                       filetypes=[("The Sims 4", "TS4*.exe"), ("Programs", "*.exe")])
        if f:
            self.game_exe = Path(f)
            for lbl in self.exe_labels:
                lbl.configure(text=short_path(f, 40), text_color=TEXT)

    def _runner(self, simulate: bool, minutes: float, match: str, log, demo_culprits):
        from . import autobisect

        if simulate:
            return autobisect.DemoRunner(demo_culprits, seconds=1.2)
        if not self.game_exe or not Path(self.game_exe).is_file():
            self.toast("Point BetterSims at TS4_x64.exe first (The Sims 4\\Game\\Bin).", "warn")
            return None
        return autobisect.GameRunner(Path(self.game_exe), minutes=minutes, match=match, log=log)

    # ---- Auto Repair page ---------------------------------------------------
    def _page_repair(self) -> None:
        pg = ctk.CTkFrame(self.content, fg_color=BG, corner_radius=0)
        self.pages["repair"] = pg
        body = ctk.CTkScrollableFrame(pg, fg_color="transparent", scrollbar_button_color=RAISED)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        head, _ = self._heading(body, "Auto Repair", "Fixes your files, then tests the game and removes whatever breaks "
                                "it. Over and over, until it runs clean.")
        head.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 4))
        pill(head, "ALL-IN-ONE", GOLD, mix(BG, GOLD, 0.16)).grid(row=0, column=1, rowspan=2, sticky="e")

        # reactor + steps
        top = ctk.CTkFrame(body, fg_color="transparent")
        top.grid(row=1, column=0, sticky="ew", padx=22, pady=(12, 0))
        top.grid_columnconfigure(1, weight=1)
        core = hud_panel(top, width=280, height=300)
        core.grid(row=0, column=0, sticky="ns", padx=6)
        core.grid_propagate(False)
        brackets(core)
        self.reactor = Reactor(core, size=236, bg=CARD)
        self.reactor.set_text("READY", "press start")
        self.reactor.place(relx=0.5, rely=0.47, anchor="center")
        self.rep_pass = ctk.CTkLabel(core, text="", font=self.d(11, "normal"), text_color=FAINT)
        self.rep_pass.place(relx=0.5, rely=0.94, anchor="center")

        right = ctk.CTkFrame(top, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=6)
        right.grid_columnconfigure(0, weight=1)
        steps = hud_panel(right)
        steps.grid(row=0, column=0, sticky="ew")
        steps.grid_columnconfigure(1, weight=1)
        self.rep_steps = []
        for k, (_key, title, sub) in enumerate(REPAIR_STEPS):
            pad = (14 if k == 0 else 4, 14 if k == 4 else 4)
            dot = tk.Canvas(steps, width=24, height=24, bg=CARD, highlightthickness=0, bd=0)
            dot.grid(row=k, column=0, padx=(20, 12), pady=pad)
            ring = dot.create_oval(3, 3, 21, 21, outline=FAINT, width=2)
            mark = dot.create_text(12, 12, text=str(k + 1), fill=FAINT, font=(DISPLAY_FONT, 9, "bold"))
            txt = ctk.CTkFrame(steps, fg_color="transparent")
            txt.grid(row=k, column=1, sticky="w", pady=pad)
            t = ctk.CTkLabel(txt, text=title.upper(), font=self.d(13), text_color=FAINT, anchor="w", height=18)
            t.pack(anchor="w")
            s = ctk.CTkLabel(txt, text=sub, font=self.f(11), text_color=FAINT, anchor="w", height=16)
            s.pack(anchor="w")
            self.rep_steps.append((dot, ring, mark, t, s))
        self.rep_state = ["todo"] * 5
        glow_hover(steps)

        chips = ctk.CTkFrame(right, fg_color="transparent")
        chips.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.rep_stats: dict[str, ctk.CTkLabel] = {}
        for i, (key, label, color) in enumerate((("fixed", "FILES FIXED", GLOW), ("removed", "MODS REMOVED", RED),
                                                 ("launches", "TEST LAUNCHES", GOLD), ("time", "ELAPSED", TEXT))):
            chips.grid_columnconfigure(i, weight=1, uniform="c")
            c = hud_panel(chips, height=74)
            c.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 5, 0 if i == 3 else 5))
            c.grid_propagate(False)
            v = ctk.CTkLabel(c, text="0" if key != "time" else "0:00", font=self.d(24), text_color=color)
            v.place(x=16, y=8)
            ctk.CTkLabel(c, text=label, font=self.d(10, "normal"), text_color=MUTED).place(x=16, y=46)
            brackets(c, color)
            glow_hover(c, BORDER, color)
            self.rep_stats[key] = v

        self.rep_bar = HudBar(body, height=8)
        self.rep_bar.grid(row=2, column=0, sticky="ew", padx=28, pady=(16, 6))

        # settings + console
        low = ctk.CTkFrame(body, fg_color="transparent")
        low.grid(row=3, column=0, sticky="ew", padx=22, pady=(6, 20))
        low.grid_columnconfigure(0, weight=11, uniform="l")
        low.grid_columnconfigure(1, weight=9, uniform="l")
        card = hud_panel(low)
        card.grid(row=0, column=0, sticky="nsew", padx=6)
        card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(card, text="SETTINGS", font=self.d(12), text_color=FAINT).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=24, pady=(16, 4))
        self._exe_row(card, 1)
        self._label(card, 2, "Watch each launch for")
        self.rep_minutes = segmented(card, ["3 min", "5 min", "10 min"])
        self.rep_minutes.set("5 min")
        self.rep_minutes.grid(row=2, column=1, sticky="w")
        self._label(card, 3, "Launches per test")
        self.rep_tries = segmented(card, ["1", "2", "3"])
        self.rep_tries.set("1")
        self.rep_tries.grid(row=3, column=1, sticky="w")
        self._label(card, 4, "Remove at most")
        self.rep_max = segmented(card, ["3 mods", "5 mods", "10 mods"])
        self.rep_max.set("5 mods")
        self.rep_max.grid(row=4, column=1, sticky="w")
        self.rep_cache = ctk.BooleanVar(value=True)
        self.rep_sim = ctk.BooleanVar(value=False)
        for r, (var, text) in enumerate(((self.rep_cache, "Clear the game's cache files too (the game rebuilds them)"),
                                         (self.rep_sim, "Simulate the game (try it on the demo folder)"))):
            ctk.CTkCheckBox(card, text=text, variable=var, font=self.f(12), text_color=MUTED, fg_color=GLOW,
                            hover_color=GLOW_HOVER, border_color=FAINT, checkmark_color=GLOW_TEXT, checkbox_width=20,
                            checkbox_height=20, corner_radius=5).grid(row=5 + r, column=0, columnspan=3, sticky="w",
                                                                      padx=24, pady=(6, 2))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=7, column=0, columnspan=3, sticky="ew", padx=20, pady=(14, 8))
        self.rep_start = accent_button(row, "⚡  START AUTO REPAIR", self.start_repair, width=240, height=44)
        self.rep_start.pack(side="left", padx=4)
        self.rep_stop_btn = danger_button(row, "Stop", self.stop_repair, width=90, height=44, state="disabled")
        self.rep_stop_btn.pack(side="left", padx=8)
        self.rep_result = ctk.CTkFrame(card, fg_color="transparent")
        self.rep_result.grid(row=8, column=0, columnspan=3, sticky="ew", padx=20, pady=(0, 6))
        note = ctk.CTkLabel(card, text="Sees crashes and errors while the game loads. Problems that only show up after "
                            "you load a save (broken interactions, invisible CC) still need the manual 50/50.",
                            font=self.f(11), text_color=FAINT, wraplength=520, justify="left", anchor="w")
        note.grid(row=9, column=0, columnspan=3, sticky="w", padx=24, pady=(0, 16))
        glow_hover(card)

        con = hud_panel(low)
        con.grid(row=0, column=1, sticky="nsew", padx=6)
        ctk.CTkLabel(con, text="LIVE LOG", font=self.d(12), text_color=FAINT).pack(anchor="w", padx=20, pady=(16, 6))
        self.rep_log = HudLog(con, height=330)
        self.rep_log.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.rep_log.line("Auto Repair standing by.")
        glow_hover(con)

    def _paint_steps(self, phase: str, clean: bool | None = None) -> None:
        idx = STEP_OF[phase]
        for k in range(5):
            if phase == "done":
                state = "done" if clean else ("fail" if k == 4 else self.rep_state[k] if self.rep_state[k] == "done"
                                              else "todo")
            else:
                state = "done" if k < idx else "active" if k == idx else "todo"
            self.rep_state[k] = state
            dot, ring, mark, t, s = self.rep_steps[k]
            color = {"done": GREEN, "active": GLOW, "todo": FAINT, "fail": RED}[state]
            dot.itemconfigure(ring, outline=color)
            dot.itemconfigure(mark, text={"done": "✓", "fail": "✕"}.get(state, str(k + 1)), fill=color)
            t.configure(text_color=TEXT if state in ("active", "done") else FAINT)
            s.configure(text_color=MUTED if state == "active" else FAINT)
        self._pulse_step()

    def _pulse_step(self) -> None:
        if getattr(self, "_pulsing", False):
            return
        self._pulsing = True

        def tick():
            if "active" not in self.rep_state:
                self._pulsing = False
                return
            k = self.rep_state.index("active")
            dot, ring, _mark, _t, _s = self.rep_steps[k]
            p = 0.5 + 0.5 * math.sin(time.time() * 6)
            dot.itemconfigure(ring, outline=mix(mix(CARD, GLOW, 0.3), "#ffffff", p * 0.6), width=2 + p)
            self.after(40, tick)
        tick()

    def _repair_ui(self, running: bool) -> None:
        set_enabled(self.rep_start, not running)
        self.rep_stop_btn.configure(state="normal" if running else "disabled")
        if running:
            self.rep_bar.start()
        else:
            self.rep_bar.stop()
            self.rep_stop = None

    def _tick_clock(self) -> None:
        if self.rep_stop is None:
            return
        s = int(time.time() - self.rep_t0)
        self.rep_stats["time"].configure(text=f"{s // 60}:{s % 60:02d}")
        self.after(500, self._tick_clock)

    def start_repair(self) -> None:
        from . import autorepair

        d = self.require_dir()
        if not d:
            return
        minutes = float(self.rep_minutes.get().split()[0])
        say = lambda m: self.events.put((self._repair_say, (m,)))  # noqa: E731
        runner = self._runner(self.rep_sim.get(), minutes, "", say, ("crashy", "notforsims"))
        if runner is None:
            return
        if game_running():
            self.toast("Close The Sims 4 first. BetterSims will start it for each test.", "warn")
            return
        limit = int(self.rep_max.get().split()[0])
        if not self.confirm("Start Auto Repair?",
                            "BetterSims will:\n"
                            "1. Fix the safe problems in your files.\n"
                            "2. Start and close The Sims 4 by itself, possibly many times.\n"
                            f"3. Remove each mod that breaks the game, up to {limit}.\n\n"
                            "Nothing is deleted. Removed mods go to quarantine, and one Undo in History puts "
                            "every file back. Don't play while it runs.", "Start"):
            return
        for w in self.rep_result.winfo_children():
            w.destroy()
        self.rep_log.clear()
        for key in ("fixed", "removed", "launches"):
            self.rep_stats[key].configure(text="0")
        self.rep_launches = 0
        self.rep_passes = 0
        self.rep_pass.configure(text="")
        self.rep_bar.set(0.02)
        self.rep_stop = stop = threading.Event()
        self._repair_ui(True)
        self.rep_t0 = time.time()
        self._tick_clock()
        tries = int(self.rep_tries.get())
        self._background(
            lambda: autorepair.run_repair(d, runner, stop=stop, say=say, tries=tries, max_removals=limit,
                                          clear_cache=self.rep_cache.get(),
                                          phase=lambda p: self.events.put((self._repair_phase, (p,)))),
            self._repair_done)

    def stop_repair(self) -> None:
        if self.rep_stop:
            self.rep_stop.set()
            self.reactor.set_mode("warn", "STOPPING", "after this launch")
            self.rep_stop_btn.configure(state="disabled")

    def _repair_phase(self, phase: str) -> None:
        if phase == "done":
            return  # _repair_done paints the final state with the result
        if phase == "test":
            self.rep_passes += 1
            if self.rep_passes > 1:
                self.rep_pass.configure(text=f"PASS {self.rep_passes}")
        self._paint_steps(phase)
        label, sub, bar = {"scan": ("SCANNING", "reading your mods", 0.06), "fix": ("FIXING", "safe fixes", 0.16),
                           "test": ("TESTING", "game launching", 0.3), "search": ("SEARCHING", "50/50 rounds", 0.34),
                           "remove": ("REMOVING", "to quarantine", 0.92)}[phase]
        self.reactor.set_mode("busy", label, sub)
        self.rep_bar.set(bar)

    def _repair_say(self, text: str) -> None:
        self.rep_log.line(text)
        if "launching the game" in text:
            self.rep_launches += 1
            count_to(self.rep_stats["launches"], self.rep_launches, frames=10)
        m = re.search(r"\((\d+) change", text)
        if text.startswith("Fixed ") and m:
            count_to(self.rep_stats["fixed"], int(m.group(1)))
        if text.startswith("Removed "):
            count_to(self.rep_stats["removed"], int(self.rep_stats["removed"].cget("text") or 0) + 1, frames=10)
        if text.startswith("Round") and "suspects left" in text:
            st = bisect.load_state(Path(self.dir))
            if st:
                total = max(2, len(st["units"]))
                self.reactor.set_text("SEARCHING", f"{len(st['suspects'])} suspects left")
                self.rep_bar.set(0.34 + 0.55 * min(1.0, 1 - len(st["suspects"]) / total + 0.05))

    def _repair_done(self, res) -> None:
        self.busy = False
        self._repair_ui(False)
        self._paint_steps("done", clean=res.clean)
        self.rep_bar.set(1 if res.clean else self.rep_bar.target)
        if res.clean:
            self.reactor.set_mode("ok", "CLEAN", "no crashes, no new errors")
            self.rep_bar.recolor(GREEN)
            self.after(2500, lambda: self.rep_bar.recolor(GLOW))
        elif res.stopped:
            self.reactor.set_mode("warn", "STOPPED", "mods all turned on")
        else:
            self.reactor.set_mode("bad", "NEEDS YOU", "see the log")
        count_to(self.rep_stats["fixed"], res.fixed)
        count_to(self.rep_stats["removed"], len(res.removed))
        if res.session:
            ghost_button(self.rep_result, "↺  Undo everything", lambda: self.undo(res.session), width=170).pack(
                side="left", padx=4)
            link_button(self.rep_result, "See history", lambda: self.show("history"), width=100).pack(side="left")
        self.toast(res.message, "ok" if res.clean else "warn", 7000)
        self.after(300, self.scan)

    # ---- 50/50 page -----------------------------------------------------
    def _page_bisect(self) -> None:
        pg = ctk.CTkFrame(self.content, fg_color=BG, corner_radius=0)
        self.pages["bisect"] = pg
        head, _ = self._heading(pg, "50/50 finder",
                                "When the game breaks and nothing points at a mod, this finds it by elimination.")
        head.pack(fill="x", padx=32, pady=(26, 0))
        self.bis_mode = segmented(head, ["Automatic", "Manual"], self._bis_mode, height=36)
        self.bis_mode.grid(row=0, column=1, rowspan=2, sticky="e")
        HudBar(pg, height=2, ambient=True).pack(fill="x", padx=32, pady=(12, 0))
        self.bis_body = ctk.CTkScrollableFrame(pg, fg_color="transparent", scrollbar_button_color=RAISED)
        self.bis_body.pack(fill="both", expand=True, padx=8, pady=(10, 8))
        self._manual_panel()
        self._auto_panel()
        self.bis_mode.set("Automatic")
        self._bis_mode("Automatic")

    def _bis_mode(self, mode: str) -> None:
        self.bis_mode.set(mode)
        if mode == "Automatic":
            self.manual_panel.pack_forget()
            self.auto_panel.pack(fill="x")
        else:
            self.auto_panel.pack_forget()
            self.manual_panel.pack(fill="x")

    def _manual_panel(self) -> None:
        mp = self.manual_panel = ctk.CTkFrame(self.bis_body, fg_color="transparent")
        steps = ctk.CTkFrame(mp, fg_color="transparent")
        steps.pack(fill="x", padx=12, pady=(4, 12))
        for i, (t, s) in enumerate((("Half off", "BetterSims turns half of your mods off."),
                                    ("You test", "Start the game and check for the problem."),
                                    ("Tell it", "Still broken, or gone? It narrows it down."))):
            steps.grid_columnconfigure(i, weight=1, uniform="s")
            c = hud_panel(steps)
            c.grid(row=0, column=i, sticky="ew", padx=6)
            ctk.CTkLabel(c, text=str(i + 1), font=self.d(14), text_color=GLOW_TEXT, fg_color=GLOW,
                         corner_radius=12, width=28, height=28).pack(anchor="w", padx=16, pady=(16, 6))
            ctk.CTkLabel(c, text=t.upper(), font=self.d(15), text_color=TEXT).pack(anchor="w", padx=16)
            ctk.CTkLabel(c, text=s, font=self.f(12), text_color=MUTED, wraplength=230, justify="left").pack(
                anchor="w", padx=16, pady=(2, 16))
            glow_hover(c)
        self.bis_card = hud_panel(mp, corner_radius=14)
        self.bis_card.pack(fill="x", padx=18, pady=6)
        self.bis_title = ctk.CTkLabel(self.bis_card, text="", font=self.d(20), text_color=TEXT, anchor="w")
        self.bis_title.pack(fill="x", padx=24, pady=(22, 4))
        self.bis_text = ctk.CTkLabel(self.bis_card, text="", font=self.f(13), text_color=MUTED, anchor="w",
                                     justify="left", wraplength=820)
        self.bis_text.pack(fill="x", padx=24)
        self.bis_bar = HudBar(self.bis_card, height=8, bg=CARD)
        self.bis_btns = ctk.CTkFrame(self.bis_card, fg_color="transparent")
        self.bis_btns.pack(fill="x", padx=20, pady=(16, 20))

    def _auto_panel(self) -> None:
        ap = self.auto_panel = ctk.CTkFrame(self.bis_body, fg_color="transparent")
        card = hud_panel(ap, corner_radius=14)
        card.pack(fill="x", padx=18, pady=(4, 8))
        card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(card, text="LET BETTERSIMS TEST FOR YOU", font=self.d(18), text_color=TEXT,
                     anchor="w").grid(row=0, column=0, columnspan=3, sticky="w", padx=24, pady=(22, 2))
        ctk.CTkLabel(card, text="Each round it turns half the suspects off, starts The Sims 4 by itself, and watches "
                     "for a crash or a new error log. Then it closes the game and goes again until one mod is left. "
                     "About 5 to 8 minutes per round, so roughly an hour for 1,000 mods. Leave the PC alone meanwhile. "
                     "Want it to remove the bad mods and keep going too? Use Auto Repair.",
                     font=self.f(13), text_color=MUTED, wraplength=840, justify="left", anchor="w").grid(
            row=1, column=0, columnspan=3, sticky="w", padx=24)
        note = ctk.CTkFrame(card, fg_color=mix(CARD, GOLD, 0.1), corner_radius=8)
        note.grid(row=2, column=0, columnspan=3, sticky="ew", padx=24, pady=(12, 8))
        ctk.CTkLabel(note, text="It catches crashes and errors while the game loads. It can't see problems that only "
                     "show up after you load a save and play (broken interactions, invisible CC, glitches). For "
                     "those, use Manual.", font=self.f(12), text_color=mix(GOLD, TEXT, 0.5), wraplength=820,
                     justify="left", anchor="w").pack(fill="x", padx=14, pady=10)
        self._exe_row(card, 3)
        self._label(card, 4, "Watch each round for")
        self.auto_minutes = segmented(card, ["3 min", "5 min", "10 min"])
        self.auto_minutes.set("5 min")
        self.auto_minutes.grid(row=4, column=1, sticky="w")
        self._label(card, 5, "Launches per round")
        self.auto_tries = segmented(card, ["1", "2", "3"])
        self.auto_tries.set("1")
        self.auto_tries.grid(row=5, column=1, sticky="w")
        ctk.CTkLabel(card, text="Use 2 or 3 if the problem doesn't happen every time", font=self.f(11),
                     text_color=FAINT).grid(row=5, column=1, sticky="w", padx=(150, 0))
        self._label(card, 6, "Only count errors mentioning")
        self.auto_match = ctk.CTkEntry(card, placeholder_text="optional, e.g. a word from your error",
                                       width=320, height=32, fg_color=BG, border_color=BORDER, text_color=TEXT,
                                       corner_radius=8, font=self.f(12))
        self.auto_match.grid(row=6, column=1, sticky="w")
        self.auto_sim = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(card, text="Simulate the game (try it on the demo folder without the game)",
                        variable=self.auto_sim, font=self.f(12), text_color=MUTED, fg_color=GLOW,
                        hover_color=GLOW_HOVER, border_color=FAINT, checkmark_color=GLOW_TEXT, checkbox_width=20,
                        checkbox_height=20, corner_radius=5).grid(row=7, column=0, columnspan=3, sticky="w", padx=24,
                                                                  pady=(8, 4))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=8, column=0, columnspan=3, sticky="ew", padx=20, pady=(12, 22))
        self.auto_start = accent_button(row, "▶  START AUTOMATIC SEARCH", self.start_auto, width=250)
        self.auto_start.pack(side="left", padx=4)
        self.auto_stop = danger_button(row, "Stop", self.stop_auto, width=100, height=40, state="disabled")
        self.auto_stop.pack(side="left", padx=8)
        glow_hover(card)

        live = hud_panel(ap, corner_radius=14)
        live.pack(fill="x", padx=18, pady=8)
        top = ctk.CTkFrame(live, fg_color="transparent")
        top.pack(fill="x", padx=24, pady=(18, 6))
        self.auto_status = ctk.CTkLabel(top, text="Not running", font=self.d(16), text_color=TEXT)
        self.auto_status.pack(side="left")
        self.auto_bar = HudBar(live, height=8, bg=CARD)
        self.auto_bar.pack(fill="x", padx=24, pady=(4, 10))
        self.auto_log = HudLog(live, height=170)
        self.auto_log.pack(fill="x", padx=24, pady=(0, 12))
        self.auto_result = ctk.CTkFrame(live, fg_color="transparent")
        self.auto_result.pack(fill="x", padx=20, pady=(0, 16))

    def _auto_say(self, text: str) -> None:
        self.auto_log.line(text)
        if text.startswith("Round") and "suspects" in text:
            self.auto_status.configure(text=text.split(":")[0])
            st = bisect.load_state(Path(self.dir))
            if st:
                total = max(2, len(st["units"]))
                self.auto_bar.set(min(0.95, 1 - len(st["suspects"]) / total + 0.05))

    def start_auto(self) -> None:
        from . import autobisect

        d = self.require_dir()
        if not d:
            return
        minutes = float(self.auto_minutes.get().split()[0])
        say = lambda m: self.events.put((self._auto_say, (m,)))  # noqa: E731
        runner = self._runner(self.auto_sim.get(), minutes, self.auto_match.get().strip(), say, "crashy")
        if runner is None:
            return
        if game_running():
            self.toast("Close The Sims 4 first. BetterSims will start it for each round.", "warn")
            return
        if not self.auto_sim.get() and not self.confirm(
                "Start the automatic search?", "BetterSims will start and close The Sims 4 by itself several times. "
                "Don't use the game while it runs. Your mods are only renamed, and all of them are turned back on "
                "at the end, even if you press Stop.", "Start"):
            return
        for w in self.auto_result.winfo_children():
            w.destroy()
        self.auto_log.clear()
        self.auto_bar.set(0.02)
        self.auto_bar.start()
        self.auto_status.configure(text="Running...")
        set_enabled(self.auto_start, False)
        self.auto_stop.configure(state="normal")
        self.auto_stop_event = stop = threading.Event()
        tries = int(self.auto_tries.get())
        self._background(lambda: autobisect.run_auto(d, runner, stop=stop, tries=tries, say=say), self._auto_done)

    def stop_auto(self) -> None:
        if self.auto_stop_event:
            self.auto_stop_event.set()
            self.auto_status.configure(text="Stopping after this launch...")
            self.auto_stop.configure(state="disabled")

    def _auto_done(self, res) -> None:
        self.busy = False
        set_enabled(self.auto_start, True)
        self.auto_stop.configure(state="disabled")
        self.auto_bar.stop()
        self._auto_say(res.message)
        if res.found:
            self.auto_bar.set(1)
            self.auto_status.configure(text=f"Found it: {res.found}")
            files = [Path(self.dir) / "Mods" / f for f in res.files]
            danger_button(self.auto_result, "Remove this mod", lambda: self.remove_files([f for f in files if f.exists()]),
                          width=170, height=40).pack(side="left", padx=4)
            self.toast(f"Found the bad mod: {res.found}")
        else:
            self.auto_bar.set(0)
            self.auto_status.configure(text="Finished without a culprit")

    def refresh_bisect(self, message: str | None = None) -> None:
        for w in self.bis_btns.winfo_children():
            w.destroy()
        d = Path(self.dir) if self.dir else None
        st = bisect.load_state(d) if d and d.is_dir() else None
        if st:
            total = max(1, len(st["units"]))
            left = len(st["suspects"])
            self.bis_title.configure(text=f"ROUND {st['round']}  ·  {left} SUSPECTS LEFT")
            self.bis_text.configure(text=message or f"{len(st['testing_off'])} mod(s) are OFF right now. Start the "
                                    "game, check for the problem, then tell BetterSims what happened.")
            self.bis_bar.pack(fill="x", padx=24, pady=16, before=self.bis_btns)
            self.bis_bar.set(1 - left / total if total > 1 else 1)
            accent_button(self.bis_btns, "Still broken", lambda: self._bis(lambda: bisect.answer(d, True)),
                          color=mix(CARD, RED, 0.25), hover=mix(CARD, RED, 0.4), text_color=RED, width=160).pack(
                side="left", padx=4)
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
            self.bis_title.configure(text=f"FOUND IT: {data['label']}")
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
        self.bis_title.configure(text="READY WHEN YOU ARE")
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
        pg = ctk.CTkFrame(self.content, fg_color=BG, corner_radius=0)
        pg.grid_columnconfigure(0, weight=1)
        pg.grid_rowconfigure(2, weight=1)
        self.pages["history"] = pg
        head, _ = self._heading(pg, "History & undo",
                                "Every fix is recorded. Undo any of them to put the files back exactly where they were.")
        head.grid(row=0, column=0, sticky="ew", padx=32, pady=(26, 0))
        ghost_button(head, "Open quarantine folder", self.open_quarantine, width=190).grid(row=0, column=1, rowspan=2,
                                                                                           padx=6)
        danger_button(head, "Empty quarantine...", self.purge, width=170).grid(row=0, column=2, rowspan=2)
        HudBar(pg, height=2, ambient=True).grid(row=1, column=0, sticky="ew", padx=32, pady=(12, 0))
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
            c = ctk.CTkFrame(self.hist, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
            c.grid(row=r, column=0, sticky="ew", padx=10, pady=5)
            c.grid_columnconfigure(1, weight=1)
            when = time.strftime("%b %d, %H:%M", time.localtime(s.get("created", 0)))
            ctk.CTkLabel(c, text=when, font=self.d(15), text_color=TEXT).grid(row=0, column=0, sticky="w",
                                                                              padx=20, pady=(14, 0))
            colors = {"active": (GREEN, mix(CARD, GREEN, 0.18)), "undone": (MUTED, RAISED), "deleted": (FAINT, RAISED)}
            pill(c, state.upper(), *colors[state]).grid(row=0, column=1, sticky="w", padx=10, pady=(14, 0))
            what = ", ".join(s.get("findings", [])[:3]) or "Fixes"
            more = len(s.get("findings", [])) - 3
            ctk.CTkLabel(c, text=f"{len(s['ops'])} step(s)  ·  {what}" + (f" and {more} more" if more > 0 else ""),
                         font=self.f(12), text_color=MUTED, anchor="w", wraplength=720, justify="left").grid(
                row=1, column=0, columnspan=2, sticky="w", padx=20, pady=(2, 14))
            if state == "active":
                ghost_button(c, "↺  Undo", lambda sid=s["session"]: self.undo(sid), width=100).grid(
                    row=0, column=2, rowspan=2, padx=16)
            glow_hover(c, BORDER, GLOW, fg=(CARD, CARD_HOVER))
            if r < 12:
                self.after(r * 40, lambda c=c: materialize(c))

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
