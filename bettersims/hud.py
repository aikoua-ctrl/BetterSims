"""Animated HUD pieces for the desktop app: glowing progress bars, the arc-reactor spinner,
a typing console, hover glow and small tweens. Everything runs on Tk's own timer (after()),
so animations keep moving while the real work happens on a background thread.

Performance rules (Tk redraws are expensive, especially with Windows display scaling):
  * nothing loops while the app is idle: spinners and sweeps only run while work is running,
    and stop completely when their widget isn't on screen;
  * animations move existing canvas items (coords/itemconfigure), they never rebuild them;
  * hover is a single instant colour change, never a fade, and never re-colours a whole card;
  * pages switch instantly (moving a page re-lays out every widget on it).
MOTION = False (the Reduce motion switch, or Windows' "Show animations" turned off) makes every
animation jump straight to its end state.
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
FPS_MS = 40  # 25 fps is plenty for a spinner and keeps CPU low
MOTION = True  # set by the app: False = reduce motion


def system_reduce_motion() -> bool:
    """True when Windows' 'Show animations in Windows' setting is off."""
    if not WIN:
        return False
    try:
        import ctypes

        on = ctypes.c_int(1)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)  # SPI_GETCLIENTAREAANIMATION
        return not on.value
    except Exception:  # noqa: BLE001 - any failure: keep animations
        return False


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

    if not MOTION:
        frames = 1  # reduce motion: straight to the end state

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


def count_to(label: ctk.CTkLabel, target: int, suffix: str = "", frames: int = 12) -> None:
    """Numbers roll up to their new value instead of jumping."""
    try:
        start = int(str(label.cget("text")).rstrip(suffix) or 0)
    except ValueError:
        start = 0
    tween(label, lambda t: label.configure(text=f"{round(start + (target - start) * t)}{suffix}"),
          frames=frames, ms=30, key="_count")


def _over(widget) -> bool:
    """Is the pointer inside the widget's rectangle? (Cheap: no widget-tree walk.)"""
    try:
        x, y = widget.winfo_pointerxy()
        x0, y0 = widget.winfo_rootx(), widget.winfo_rooty()
        return x0 <= x < x0 + widget.winfo_width() and y0 <= y < y0 + widget.winfo_height()
    except tk.TclError:
        return False


def glow_hover(widget, base: str = BORDER, glow: str = GLOW, strength: float = 0.75) -> None:
    """Outline lights up while the mouse is over the widget or any of its children.

    One configure() when the hover state actually changes, nothing else: moving the mouse around
    inside a card costs nothing.
    """
    lit = mix(base, glow, strength)
    state = {"on": False}

    def enabled() -> bool:
        try:
            return widget.cget("state") != "disabled"
        except (ValueError, tk.TclError, AttributeError):
            return True

    def set_on(on: bool) -> None:
        if on == state["on"] or not alive(widget):
            return
        if on and not enabled():
            return
        state["on"] = on
        widget.configure(border_color=lit if on else base)

    def walk(w):
        try:
            w.bind("<Enter>", lambda _e: set_on(True), add="+")
            w.bind("<Leave>", lambda _e: set_on(_over(widget)), add="+")
        except NotImplementedError:  # e.g. CTkSegmentedButton: its own buttons get bound below
            pass
        for c in w.winfo_children():
            walk(c)

    walk(widget)


def materialize(widget, base: str = BORDER, glow: str = GLOW) -> None:
    """New cards flash their outline once as they appear (two redraws, not a fade)."""
    if not MOTION:
        return
    widget.configure(border_color=mix(base, glow, 0.6))
    widget.after(220, lambda: alive(widget) and widget.configure(border_color=base))


# ---- HUD progress bar --------------------------------------------------------
class HudBar(tk.Canvas):
    """A segmented bar. While work runs a light sweep moves along it, so it never looks frozen.

    set(v) moves the fill smoothly to v (0..1). start() turns the sweep on, stop() off.
    ambient=True draws it as a faint static header line. All items are created once; animation
    only moves them, and the timer stops completely when nothing is moving.
    """

    N = 6  # sweep slices

    def __init__(self, master, height: int = 6, bg: str = BG, color: str = GLOW, track: str = RAISED,
                 ambient: bool = False, **kw):
        kw.setdefault("width", 1)  # stretch with fill/sticky; a Canvas would otherwise ask for ~380px
        super().__init__(master, height=height, bg=bg, highlightthickness=0, bd=0, **kw)
        self.color, self.track, self.bgc, self.h = color, track, bg, height
        self.ambient = ambient
        self.value = self.target = 0.0
        self.running = False
        self.pos = 0.0
        self._job = None
        trk = mix(bg, track, 0.4) if ambient else track
        self.i_track = self.create_rectangle(0, 0, 0, height, fill=trk, width=0)
        self.i_fill = self.create_rectangle(0, 0, 0, height, fill=mix(track, color, 0.65), width=0)
        self.i_head = self.create_rectangle(0, 0, 0, height, fill=color, width=0)
        self.i_sweep = [self.create_rectangle(-10, 0, -10, height, width=0) for _ in range(self.N)]
        self._sweep_colors()
        self.notches: list[int] = []
        self.bind("<Configure>", lambda _e: self._layout())

    def _sweep_colors(self) -> None:
        for i, it in enumerate(self.i_sweep):
            a = 1 - abs(i - (self.N - 1) / 2) / (self.N / 2)
            self.itemconfigure(it, fill=mix(self.track, "#ffffff" if a > 0.8 else self.color, 0.35 + 0.6 * a))

    def _layout(self) -> None:
        w = self.winfo_width()
        self.coords(self.i_track, 0, 0, w, self.h)
        for n in self.notches:
            self.delete(n)
        self.notches = []
        if not self.ambient and self.h >= 5:  # segment notches: static, drawn once per resize
            self.notches = [self.create_line(w * i / 40, 0, w * i / 40, self.h, fill=self.bgc) for i in range(1, 40)]
        self._paint()

    def set(self, v: float) -> None:
        self.target = max(0.0, min(1.0, float(v)))
        if not MOTION:
            self.value = self.target
        self._wake()

    def start(self) -> None:
        self.running = True
        self._wake()

    def stop(self) -> None:
        self.running = False
        self._wake()

    def recolor(self, color: str) -> None:
        self.color = color
        self.itemconfigure(self.i_fill, fill=mix(self.track, color, 0.65))
        self.itemconfigure(self.i_head, fill=color)
        self._sweep_colors()

    def _wake(self) -> None:
        if self._job is None and alive(self):
            self._job = self.after(FPS_MS, self._tick)

    def _tick(self) -> None:
        self._job = None
        if not alive(self):
            return
        moving = abs(self.target - self.value) > 0.002
        sweeping = self.running and MOTION
        if not (moving or sweeping):
            self.value = self.target
            self._paint()
            return  # nothing to animate: the timer stops until set()/start()
        if not self.winfo_ismapped():
            self._job = self.after(400, self._tick)  # off screen: check back rarely
            return
        self.value += (self.target - self.value) * 0.2
        if sweeping:
            self.pos = (self.pos + 0.02) % 1.5
        self._paint()
        self._job = self.after(FPS_MS, self._tick)

    def _paint(self) -> None:
        w, h = self.winfo_width(), self.h
        fw = w * self.value
        self.coords(self.i_fill, 0, 0, fw, h)
        self.coords(self.i_head, max(0, fw - 3), 0, fw if fw >= 1 else 0, h)
        if self.running and MOTION:
            span = w * 0.16
            x = (self.pos - 0.25) * w
            for i, it in enumerate(self.i_sweep):
                x0 = x + span * i / self.N
                self.coords(it, x0, 0, x0 + span / self.N + 1, h)
        else:
            for it in self.i_sweep:
                self.coords(it, -10, 0, -10, h)


# ---- arc reactor spinner ------------------------------------------------------
MODES = {  # colour, degrees per frame (0 = holds still: no timer runs at all)
    "idle": (GLOW, 0),
    "busy": (GLOW, 4.5),
    "ok": (GREEN, 0),
    "bad": (RED, 0),
    "warn": (GOLD, 0),
}


class Reactor(tk.Canvas):
    """Concentric rings with a status readout in the middle. The rings spin and the core pulses only
    while work is running; otherwise it is a still picture and costs nothing."""

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
        self._job = None
        self.set_mode("idle")
        self._draw()

    def set_mode(self, mode: str, label: str | None = None, sub: str | None = None) -> None:
        self.mode = mode
        target, self.speed = MODES[mode]
        old = self.color
        tween(self, lambda t: self._recolor(mix(old, target, t)), frames=12, ms=25, key="_rc")
        if label is not None:
            self.itemconfigure(self.label, text=label)
        if sub is not None:
            self.itemconfigure(self.sub, text=sub)
        if self.speed and MOTION and self._job is None:
            self._job = self.after(FPS_MS, self._tick)

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
        for i, ln in enumerate(self.ticks):
            self.itemconfigure(ln, fill=mix(BORDER, color, 0.7 if i % 4 == 0 else 0.3))
        for i, o in enumerate(self.core):
            self.itemconfigure(o, fill=mix(self.bgc, color, 0.07 + 0.07 * i))
        self.itemconfigure(self.label, fill=mix(color, "#ffffff", 0.35))
        self.itemconfigure(self.sub, fill=MUTED)

    def _tick(self) -> None:
        self._job = None
        if not alive(self) or not self.speed or not MOTION:
            return
        if self.winfo_ismapped():
            self.angle = (self.angle + self.speed) % 360
            self._draw()
            self._job = self.after(FPS_MS, self._tick)
        else:
            self._job = self.after(500, self._tick)

    def _draw(self) -> None:
        c, a = self.c, self.angle
        if not getattr(self, "_ticks_drawn", False):  # tick ring is static: placed once
            self._ticks_drawn = True
            for i, ln in enumerate(self.ticks):
                ang = math.radians(i * 7.5)
                r0 = self.r_ticks - (6 if i % 4 == 0 else 2)
                self.coords(ln, c + r0 * math.cos(ang), c + r0 * math.sin(ang),
                            c + self.r_ticks * math.cos(ang), c + self.r_ticks * math.sin(ang))
        for i, arc in enumerate(self.arcs1):
            self.itemconfigure(arc, start=a + i * 120)
        for i, arc in enumerate(self.arcs2):
            self.itemconfigure(arc, start=-a * 1.6 + i * 90)
        pulse = 0.5 + 0.5 * math.sin((time.time() - self.t0) * 5) if self.speed else 0.5
        base = c * 0.36
        for i, o in enumerate(self.core):
            r = base * (1 - i * 0.12) + pulse * 2
            self.coords(o, c - r, c - r, c + r, c + r)


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
        n = max(6, backlog // 15) if MOTION else len(text)
        chunk, rest = text[:n], text[n:]
        self.insert("end", chunk, kind)
        if rest:
            self.pending[0] = ("", rest, kind)
            self.insert("end", "▌", "caret")
        else:
            self.pending.pop(0)
        self.see("end")
        self.configure(state="disabled")
        self.after(30, self._type)
