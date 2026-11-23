"""Animated HUD pieces for the desktop app: glowing progress bars, the arc-reactor spinner,
a typing console, hover glow and small tweens. Everything runs on Tk's own timer (after()),
so animations keep moving while the real work happens on a background thread.
Animations idle down to a slow tick when their widget isn't on screen.
"""
from __future__ import annotations

import math
import sys
import time
import tkinter as tk

import customtkinter as ctk

# ---- palette ---------------------------------------------------------------
BG = "#05090f"
SIDEBAR = "#070d17"
CARD = "#0b1422"
CARD_HOVER = "#0f1c30"
RAISED = "#13233a"
BORDER = "#18304d"
TEXT = "#e4f4ff"
MUTED = "#7d98b5"
FAINT = "#46607e"
GLOW = "#29e6ff"  # arc-reactor cyan
GLOW_HOVER = "#1fc2db"
GLOW_TEXT = "#011018"
GOLD = "#ffb627"
GOLD_HOVER = "#e39a0c"
GREEN = "#2fe08a"
RED = "#ff4766"

WIN = sys.platform.startswith("win")
BODY_FONT = "Segoe UI" if WIN else "Inter"
DISPLAY_FONT = "Bahnschrift" if WIN else "Inter Display"
MONO_FONT = "Consolas" if WIN else "JetBrains Mono"
FPS_MS = 33


def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def mix(a: str, b: str, t: float) -> str:
    """Colour between a (t=0) and b (t=1)."""
    t = max(0.0, min(1.0, t))
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    return "#%02x%02x%02x" % (round(ra + (rb - ra) * t), round(ga + (gb - ga) * t), round(ba + (bb - ba) * t))


def ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def alive(w) -> bool:
    try:
        return bool(w.winfo_exists())
    except tk.TclError:
        return False


# ---- tweens -----------------------------------------------------------------
def tween(widget, apply, frames: int = 10, ms: int = 16, done=None, key: str = "_tween") -> None:
    """Calls apply(t) with t going 0..1 (eased) over a few frames. A new tween on the same key cancels the old."""
    old = getattr(widget, key, None)
    if old:
        try:
            widget.after_cancel(old)
        except (tk.TclError, ValueError):
            pass

    def step(i=1):
        if not alive(widget):
            return
        apply(ease_out(i / frames))
        if i < frames:
            setattr(widget, key, widget.after(ms, step, i + 1))
        else:
            setattr(widget, key, None)
            if done:
                done()

    step()


def count_to(label: ctk.CTkLabel, target: int, suffix: str = "", frames: int = 24) -> None:
    """Numbers roll up to their new value instead of jumping."""
    try:
        start = int(str(label.cget("text")).rstrip(suffix) or 0)
    except ValueError:
        start = 0
    tween(label, lambda t: label.configure(text=f"{round(start + (target - start) * t)}{suffix}"),
          frames=frames, ms=20, key="_count")


def _inside(widget, x: int, y: int) -> bool:
    w = widget.winfo_containing(x, y)
    while w is not None:
        if w is widget:
            return True
        w = getattr(w, "master", None)
    return False


def glow_hover(widget, base: str = BORDER, glow: str = GLOW, strength: float = 0.75,
               fg: tuple[str, str] | None = None) -> None:
    """Border lights up (and optionally the fill changes) while the mouse is over the widget or its children."""
    lit = mix(base, glow, strength)
    state = {"t": 0.0}

    def paint(t):
        state["t"] = t
        widget.configure(border_color=mix(base, lit, t))

    def enabled() -> bool:
        try:
            return widget.cget("state") != "disabled"
        except (ValueError, tk.TclError, AttributeError):
            return True

    def go(to: float):
        if to > 0 and not enabled():
            return
        if fg:  # the fill switches at once: fading it would redraw every child several times
            widget.configure(fg_color=fg[1] if to > 0 else fg[0])
        frm = state["t"]
        tween(widget, lambda t: paint(frm + (to - frm) * t), frames=7, ms=18, key="_glow")

    def enter(_e):
        go(1.0)

    def leave(_e):
        if not alive(widget):
            return
        x, y = widget.winfo_pointerxy()
        if not _inside(widget, x, y):
            go(0.0)

    def walk(w):
        try:
            w.bind("<Enter>", enter, add="+")
            w.bind("<Leave>", leave, add="+")
        except NotImplementedError:  # e.g. CTkSegmentedButton: its own buttons get bound below
            pass
        for c in w.winfo_children():
            walk(c)

    walk(widget)


def materialize(widget, base: str = BORDER, glow: str = GLOW) -> None:
    """New cards flash their outline and fade it down, so they 'power on' as they appear."""
    tween(widget, lambda t: widget.configure(border_color=mix(glow, base, t)), frames=14, ms=22, key="_glow")


# ---- HUD progress bar --------------------------------------------------------
class HudBar(tk.Canvas):
    """A segmented bar with a light sweep that keeps moving while work runs, so it never looks frozen.

    set(v) moves the fill smoothly to v (0..1). start() turns the sweep on, stop() off.
    ambient=True keeps a faint sweep running all the time (used as a decorative header line).
    """

    def __init__(self, master, height: int = 6, bg: str = BG, color: str = GLOW, track: str = RAISED,
                 ambient: bool = False, **kw):
        kw.setdefault("width", 1)  # stretch with fill/sticky; a Canvas would otherwise ask for ~380px
        super().__init__(master, height=height, bg=bg, highlightthickness=0, bd=0, **kw)
        self.color, self.track, self.bgc = color, track, bg
        self.value = self.target = 0.0
        self.running = ambient
        self.ambient = ambient
        self.pos = 0.0
        self.bind("<Configure>", lambda _e: self._draw())
        self.after(FPS_MS, self._tick)

    def set(self, v: float) -> None:
        self.target = max(0.0, min(1.0, float(v)))

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = self.ambient

    def recolor(self, color: str) -> None:
        self.color = color
        self._draw()

    def _tick(self) -> None:
        if not alive(self):
            return
        mapped = self.winfo_ismapped()
        moving = self.running or abs(self.target - self.value) > 0.002
        if mapped and moving:
            self.value += (self.target - self.value) * 0.16
            if self.running:
                self.pos = (self.pos + (0.006 if self.ambient else 0.014)) % 1.6
            self._draw()
        self.after(FPS_MS if mapped and moving else 250, self._tick)

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:
            return
        track = mix(self.bgc, self.track, 0.35) if self.ambient else self.track
        self.create_rectangle(0, 0, w, h, fill=track, width=0)
        fw = w * self.value
        if fw >= 1:
            self.create_rectangle(0, 0, fw, h, fill=mix(self.track, self.color, 0.65), width=0)
            self.create_rectangle(max(0, fw - 3), 0, fw, h, fill=self.color, width=0)
        if self.running:
            peak = 0.35 if self.ambient else 1.0
            center = (self.pos - 0.3) * w
            span = w * (0.18 if self.ambient else 0.22)
            n = 14
            for i in range(n):
                x0 = center - span / 2 + span * i / n
                x1 = x0 + span / n + 1
                a = (1 - abs(i - n / 2) / (n / 2)) * peak
                lit = x0 < fw  # over the filled part the sweep goes white-hot, elsewhere it's the accent
                self.create_rectangle(x0, 0, x1, h, width=0, fill=mix(
                    mix(self.track, self.color, 0.65) if lit else track, "#ffffff" if lit else self.color, a))
        # segment notches give it the instrument look
        if not self.ambient and h >= 5:
            for i in range(1, 40):
                x = w * i / 40
                self.create_line(x, 0, x, h, fill=self.bgc)


# ---- arc reactor spinner ------------------------------------------------------
MODES = {  # colour, degrees per frame
    "idle": (GLOW, 0.5),
    "busy": (GLOW, 3.6),
    "ok": (GREEN, 0.8),
    "bad": (RED, 1.2),
    "warn": (GOLD, 1.2),
}


class Reactor(tk.Canvas):
    """Concentric rotating rings with a pulsing core and a status readout in the middle."""

    def __init__(self, master, size: int = 220, bg: str = BG, **kw):
        super().__init__(master, width=size, height=size, bg=bg, highlightthickness=0, bd=0, **kw)
        self.size, self.bgc = size, bg
        self.mode = "idle"
        self.color = GLOW
        self.speed = 0.5
        self.angle = 0.0
        self.t0 = time.time()
        c = size / 2
        self.c = c
        r_out, self.r_ticks = c - 4, c - 12
        self.ring = self.create_oval(c - r_out, c - r_out, c + r_out, c + r_out, outline=BORDER, width=1)
        self.ticks = [self.create_line(0, 0, 0, 0, width=2) for _ in range(48)]
        r1, r2, r3 = c * 0.74, c * 0.58, c * 0.44
        self.arcs1 = [self.create_arc(c - r1, c - r1, c + r1, c + r1, style="arc", width=7, extent=70)
                      for _ in range(3)]
        self.arcs2 = [self.create_arc(c - r2, c - r2, c + r2, c + r2, style="arc", width=3, extent=40)
                      for _ in range(4)]
        self.inner = self.create_oval(c - r3, c - r3, c + r3, c + r3, width=1)
        self.core = [self.create_oval(0, 0, 0, 0, width=0) for _ in range(3)]
        self.label = self.create_text(c, c - 6, text="READY", font=(DISPLAY_FONT, max(10, int(size / 14)), "bold"))
        self.sub = self.create_text(c, c + size / 12, text="", font=(BODY_FONT, max(8, int(size / 24))))
        self.set_mode("idle")
        self.after(FPS_MS, self._tick)

    def set_mode(self, mode: str, label: str | None = None, sub: str | None = None) -> None:
        self.mode = mode
        target, self.speed = MODES[mode]
        old = self.color
        tween(self, lambda t: self._recolor(mix(old, target, t)), frames=12, ms=25, key="_rc")
        if label is not None:
            self.itemconfigure(self.label, text=label)
        if sub is not None:
            self.itemconfigure(self.sub, text=sub)

    def set_text(self, label: str | None = None, sub: str | None = None) -> None:
        if label is not None:
            self.itemconfigure(self.label, text=label)
        if sub is not None:
            self.itemconfigure(self.sub, text=sub)

    def _recolor(self, color: str) -> None:
        self.color = color
        for a in self.arcs1:
            self.itemconfigure(a, outline=color)
        for a in self.arcs2:
            self.itemconfigure(a, outline=mix(self.bgc, color, 0.55))
        self.itemconfigure(self.inner, outline=mix(self.bgc, color, 0.4))
        self.itemconfigure(self.label, fill=mix(color, "#ffffff", 0.35))
        self.itemconfigure(self.sub, fill=MUTED)

    def _tick(self) -> None:
        if not alive(self):
            return
        if self.winfo_ismapped():
            self.angle = (self.angle + self.speed) % 360
            self._draw()
            self.after(FPS_MS, self._tick)
        else:
            self.after(300, self._tick)

    def _draw(self) -> None:
        c, a = self.c, self.angle
        for i, ln in enumerate(self.ticks):
            ang = math.radians(i * 7.5 - a * 0.4)
            r0 = self.r_ticks - (6 if i % 4 == 0 else 2)
            self.coords(ln, c + r0 * math.cos(ang), c + r0 * math.sin(ang),
                        c + self.r_ticks * math.cos(ang), c + self.r_ticks * math.sin(ang))
            lit = 0.5 + 0.5 * math.cos(ang * 1 + a * 0.05)  # a soft highlight travels round
            self.itemconfigure(ln, fill=mix(BORDER, self.color, 0.25 + 0.6 * lit if i % 4 == 0 else 0.15 + 0.35 * lit))
        for i, arc in enumerate(self.arcs1):
            self.itemconfigure(arc, start=a + i * 120)
        for i, arc in enumerate(self.arcs2):
            self.itemconfigure(arc, start=-a * 1.6 + i * 90)
        pulse = 0.5 + 0.5 * math.sin((time.time() - self.t0) * (5 if self.mode == "busy" else 2))
        base = c * 0.36
        for i, o in enumerate(self.core):
            r = base * (1 - i * 0.12) + pulse * 2
            self.coords(o, c - r, c - r, c + r, c + r)
            self.itemconfigure(o, fill=mix(self.bgc, self.color, 0.05 + 0.07 * i + 0.05 * pulse))


# ---- typing console -----------------------------------------------------------
def classify(text: str) -> str:
    """Colour for a log line: bad (red), ok (green), hi (cyan) or text (grey)."""
    low = text.lower()
    if (": problem" in low or low.startswith(("couldn't", "stopped:", "removed ", "something went wrong"))
            or any(w in low for w in ("still has problems", "isn't caused by a mod", "still happened"))):
        return "bad"
    if ": fine" in low or low.startswith(("fixed", "repaired", "confirmed", "the game starts", "no file problems")):
        return "ok"
    if low.startswith(("round", "found", "test", "control", "scanning", "game started", "check with")):
        return "hi"
    return "text"


class HudLog(ctk.CTkTextbox):
    """A console where new lines type themselves out. It speeds up when lines pile up, so it never lags."""

    def __init__(self, master, **kw):
        kw.setdefault("fg_color", BG)
        kw.setdefault("text_color", MUTED)
        kw.setdefault("font", ctk.CTkFont(MONO_FONT, 12))
        kw.setdefault("border_width", 1)
        kw.setdefault("border_color", BORDER)
        kw.setdefault("corner_radius", 10)
        super().__init__(master, **kw)
        self.pending: list[tuple[str, str, str]] = []
        self.tag_config("time", foreground=FAINT)
        self.tag_config("ok", foreground=GREEN)
        self.tag_config("bad", foreground=RED)
        self.tag_config("hi", foreground=GLOW)
        self.tag_config("text", foreground=MUTED)
        self.tag_config("caret", foreground=GLOW)
        self.configure(state="disabled")
        self._typing = False

    def clear(self) -> None:
        self.pending = []
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.configure(state="disabled")

    def line(self, text: str) -> None:
        kind = classify(text)
        self.pending.append((time.strftime("%H:%M:%S  "), text + "\n", kind))
        if not self._typing:
            self._typing = True
            self._type()

    def _type(self) -> None:
        if not alive(self):
            return
        if not self.pending:
            self._typing = False
            return
        stamp, text, kind = self.pending[0]
        self.configure(state="normal")
        if self._textbox.tag_ranges("caret"):
            self._textbox.delete("caret.first", "caret.last")
        if stamp:
            self.insert("end", stamp, "time")
            self.pending[0] = ("", text, kind)
        backlog = sum(len(t) for _s, t, _k in self.pending)
        n = max(3, backlog // 25)
        chunk, rest = text[:n], text[n:]
        self.insert("end", chunk, kind)
        if rest:
            self.pending[0] = ("", rest, kind)
            self.insert("end", "▌", "caret")
        else:
            self.pending.pop(0)
        self.see("end")
        self.configure(state="disabled")
        self.after(14, self._type)
