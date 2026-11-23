"""Automatic 50/50 search: BetterSims launches the game itself each round.

Each round it turns half the suspects off, starts The Sims 4, and watches for
  * the game crashing (its process disappears before the time is up), or
  * a new error log (lastException, Better Exceptions, MCCC) being written,
optionally only counting errors that contain some text the user gives.
If neither happens within the wait time, that half counts as "fine". Then it closes the
game and moves to the next round, until one mod is left.

What it can't see: problems that only show up after loading a save and playing (broken
interactions, invisible CC, visual glitches). Those still need the manual 50/50 finder.

Before spending an hour on rounds it runs a baseline with all mods on: if the problem
doesn't show up automatically there, it stops and says so instead of guessing.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from . import bisect
from .paths import GAME_EXES, game_running
from .rules.game import LOG_NAME_RE


@dataclass
class Outcome:
    broken: bool
    reason: str
    failed: bool = False  # the test itself couldn't run (game didn't start): never a "fine" answer


class Runner(Protocol):
    def run(self, sims_dir: Path, stop: threading.Event) -> Outcome: ...


# ---- finding the game --------------------------------------------------------
def find_game_exe() -> Path | None:
    """Where TS4_x64.exe lives: registry first, then the usual EA app / Origin / Steam folders."""
    candidates: list[Path] = []
    if os.name == "nt":
        try:
            import winreg  # type: ignore

            for hive, key in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Maxis\The Sims 4"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Maxis\The Sims 4")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        d, _ = winreg.QueryValueEx(k, "Install Dir")
                        candidates.append(Path(d) / "Game" / "Bin" / "TS4_x64.exe")
                except OSError:
                    pass
        except ImportError:
            pass
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            for sub in (r"EA Games\The Sims 4", r"Origin Games\The Sims 4", r"Steam\steamapps\common\The Sims 4"):
                candidates.append(Path(base) / sub / "Game" / "Bin" / "TS4_x64.exe")
    for c in candidates:
        if c.is_file():
            return c
    return None


# ---- error log watching --------------------------------------------------------
def log_snapshot(sims_dir: Path) -> dict[Path, float]:
    out: dict[Path, float] = {}
    for d in (sims_dir, sims_dir / "Mods"):
        if not d.is_dir():
            continue
        it = d.iterdir() if d == sims_dir else d.rglob("*")
        for p in it:
            try:
                if p.is_file() and LOG_NAME_RE.search(p.name):
                    out[p] = p.stat().st_mtime
            except OSError:
                continue
    return out


def new_errors(sims_dir: Path, before: dict[Path, float], match: str = "") -> list[Path]:
    """Error logs created or rewritten since `before`, optionally only those containing `match`."""
    out = []
    for p, mtime in log_snapshot(sims_dir).items():
        if before.get(p) == mtime:
            continue
        if match:
            try:
                if match.lower() not in p.read_text(encoding="utf-8", errors="replace").lower():
                    continue
            except OSError:
                continue
        out.append(p)
    return out


# ---- the real game runner -------------------------------------------------------
class GameRunner:
    """Launches The Sims 4 and watches it for `minutes`."""

    def __init__(self, exe: Path, minutes: float = 5, match: str = "", startup_minutes: float = 4,
                 log: Callable[[str], None] = print):
        self.exe, self.minutes, self.match = exe, minutes, match
        self.startup_minutes = startup_minutes
        self.log = log

    def _kill(self) -> None:
        if os.name == "nt":
            for name in GAME_EXES:
                subprocess.run(["taskkill", "/IM", name, "/F"], capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        # wait until it's really gone so files aren't locked for the next rename
        for _ in range(30):
            if not game_running():
                return
            time.sleep(1)

    def run(self, sims_dir: Path, stop: threading.Event) -> Outcome:
        before = log_snapshot(sims_dir)
        subprocess.Popen([str(self.exe)], cwd=str(self.exe.parent))
        # The EA app may restart the game in a new process, so watch by process name, not our child.
        started = False
        deadline = time.time() + self.startup_minutes * 60
        while time.time() < deadline and not stop.is_set():
            if game_running():
                started = True
                break
            time.sleep(2)
        if not started:
            self._kill()
            return Outcome(False, "the game didn't start within "
                           f"{self.startup_minutes:g} minutes (is the EA app installed and signed in?)", failed=True)
        self.log("Game started, watching for errors...")
        end = time.time() + self.minutes * 60
        try:
            while time.time() < end and not stop.is_set():
                errs = new_errors(sims_dir, before, self.match)
                if errs:
                    return Outcome(True, f"new error log: {errs[0].name}")
                if not game_running():
                    return Outcome(True, "the game crashed or closed")
                time.sleep(3)
            return Outcome(False, f"no errors in {self.minutes:g} minutes")
        finally:
            self._kill()


# ---- the automatic search ------------------------------------------------------
@dataclass
class AutoResult:
    found: str | None
    files: list[str]
    message: str
    rounds: int


def _confirm(sims_dir: Path, files: list[str], test: Callable[[str], bool]) -> str:
    """One last run with only the culprit turned off, everything else on: it should now be fine."""
    mods = sims_dir / "Mods"
    renamed = []
    try:
        for rel in files:
            f = mods / rel
            if f.exists() and not f.with_name(f.name + "OFF").exists():
                f.rename(f.with_name(f.name + "OFF"))
                renamed.append(f)
        still = test("Double-check with only this mod off")
    finally:
        for f in renamed:
            off = f.with_name(f.name + "OFF")
            if off.exists() and not f.exists():
                off.rename(f)
    if still:
        return ("The problem still happened with this mod off, so more than one mod is involved (or a mod plus "
                "something else). Remove this one, then run the automatic search again to find the next.")
    return "Confirmed: with only this mod off, the game ran without the problem."


class DemoRunner:
    """Pretend game for the demo folder: 'crashes' while any mod file matching `culprit` is on."""

    def __init__(self, culprit: str = "crashy", seconds: float = 0.6):
        self.culprit, self.seconds = culprit.lower(), seconds

    def run(self, sims_dir: Path, stop: threading.Event) -> Outcome:
        time.sleep(self.seconds)
        on = [p for p in (sims_dir / "Mods").rglob("*") if p.is_file() and self.culprit in p.name.lower()
              and p.suffix.lower() in (".package", ".ts4script")]
        return Outcome(True, f"simulated crash ({on[0].name} is on)") if on else Outcome(False, "simulated: no errors")


def run_auto(sims_dir: Path, runner: Runner, stop: threading.Event | None = None,
             say: Callable[[str], None] = print, tries: int = 1) -> AutoResult:
    """Runs the whole search. Always leaves every mod turned back on, even when stopped or on error."""
    stop = stop or threading.Event()
    sims_dir = Path(sims_dir)
    if bisect.load_state(sims_dir):
        raise SystemExit("A 50/50 search is already running. Stop it first.")
    if game_running():
        raise SystemExit("Close The Sims 4 first.")

    def test(label: str) -> bool:
        for t in range(tries):
            say(f"{label}: launching the game" + (f" (try {t + 1}/{tries})" if tries > 1 else "") + "...")
            o = runner.run(sims_dir, stop)
            if o.failed:
                raise SystemExit(f"Stopped: {o.reason}. All your mods are turned back on.")
            say(f"{label}: {'PROBLEM' if o.broken else 'fine'} ({o.reason})")
            if o.broken:
                return True
            if stop.is_set():
                break
        return False

    # Baseline: does the problem show up automatically with everything on?
    if not test("Check with all mods on"):
        msg = ("With all your mods on, the game started without a crash or a new error log, so there's "
               "nothing for the automatic search to track. If the problem only shows up in-game (after "
               "loading a save), use the manual 50/50 finder instead.")
        return AutoResult(None, [], msg, 0)
    rounds = 0
    try:
        say(bisect.start(sims_dir).splitlines()[0])
        while not stop.is_set():
            rounds += 1
            st = bisect.load_state(sims_dir)
            broken = test(f"Round {st['round']} ({len(st['suspects'])} suspects)")
            if stop.is_set():
                break
            msg = bisect.answer(sims_dir, still_broken=broken)
            if msg.startswith("Found it") or bisect.load_state(sims_dir) is None:
                found_file = bisect.last_found_path(sims_dir)
                if msg.startswith("Found it") and found_file.exists():
                    data = json.loads(found_file.read_text())
                    say(msg.splitlines()[0])
                    return AutoResult(data["label"], data["files"], _confirm(sims_dir, data["files"], test), rounds)
                return AutoResult(None, [], msg, rounds)
            say(msg.splitlines()[0])
        return AutoResult(None, [], "Stopped. All your mods are turned back on.", rounds)
    finally:
        if bisect.load_state(sims_dir):
            bisect.reset(sims_dir)
