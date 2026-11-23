"""Auto Repair: the all-in-one mode.

1. Scan the Mods folder and apply every safe fix (duplicates, old versions, broken files, wrong folders...).
2. Launch the game. If it runs clean, stop: done.
3. Once, launch it with every mod off. If the problem is still there, it isn't a mod, so stop and say so.
4. Run the automatic 50/50 search to find the mod that breaks the game, and move it to quarantine.
5. Go back to 2, until the game runs clean or the removal limit is reached.

Everything goes into one fix session, so a single Undo puts every file back. Mods are only ever
quarantined, never deleted. It sees the same things as the automatic 50/50: crashes and new
error logs while the game loads, not problems that only show up after you load a save.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import autobisect, bisect, fixer
from .model import Action, Finding, Severity
from .paths import game_running
from .rules import run_rules
from .scanner import scan

PHASES = ("scan", "fix", "test", "search", "remove", "done")


@dataclass
class RepairResult:
    clean: bool  # the last test launch had no crash and no new errors
    fixed: int  # changes made by the safe fixes
    removed: list[str] = field(default_factory=list)  # mods taken out because they broke the game
    session: str | None = None  # undo this to put everything back
    message: str = ""
    stopped: bool = False


def run_repair(sims_dir: Path, runner: autobisect.Runner, stop: threading.Event | None = None,
               say: Callable[[str], None] = print, phase: Callable[[str], None] = lambda _p: None,
               tries: int = 1, max_removals: int = 5, clear_cache: bool = True) -> RepairResult:
    stop = stop or threading.Event()
    sims_dir = Path(sims_dir)
    if bisect.load_state(sims_dir):
        raise SystemExit("A 50/50 search is already running. Stop it first.")
    if game_running():
        raise SystemExit("Close The Sims 4 first.")
    fx = fixer.Fixer(sims_dir)
    removed: list[str] = []
    fixed = 0

    def finish(clean: bool, msg: str, stopped: bool = False) -> RepairResult:
        phase("done")
        session = fx.journal.session if fx.journal.path.exists() else None
        say(msg)
        return RepairResult(clean, fixed, removed, session, msg, stopped)

    # 1. safe fixes
    phase("scan")
    say("Scanning your Mods folder...")
    findings = run_rules(scan(sims_dir))
    chosen = [f for f in findings if f.fixable and (f.auto or (clear_cache and f.rule == "cache"))]
    phase("fix")
    if chosen:
        res = fx.apply(chosen, log=lambda *_: None)
        fixed = len(res.done)
        say(f"Fixed {len(chosen)} problem(s) in your files ({fixed} change(s)).")
        for e in res.errors[:5]:
            say(f"Couldn't do: {e}")
    else:
        say("No file problems to fix.")
    if stop.is_set():
        return finish(False, "Stopped after fixing files. Your mods are all turned on.", True)

    test = autobisect.tester(sims_dir, runner, stop, say, tries)
    control_done = False
    while True:
        # 2. does the game run clean now?
        phase("test")
        label = "Test launch" if not removed else f"Test launch without {removed[-1]}"
        broken = test(label)
        if stop.is_set():
            return finish(False, "Stopped. Your mods are all turned on.", True)
        if not broken:
            if removed:
                return finish(True, f"Repaired. The game now starts without crashes or new errors after removing "
                                    f"{len(removed)} mod(s).")
            return finish(True, "The game starts without crashes or new errors."
                          + (" Your files are fixed too." if fixed else ""))
        # 3. control: is it a mod at all?
        if not control_done:
            control_done = True
            phase("test")
            if autobisect.with_all_off(sims_dir, lambda: test("Control launch with every mod off")):
                return finish(False, "The game has the problem even with every mod turned off, so it isn't caused "
                                     "by a mod. Try 'Repair' on The Sims 4 in the EA app, and update the game.")
            if stop.is_set():
                return finish(False, "Stopped. Your mods are all turned on.", True)
        if len(removed) >= max_removals:
            return finish(False, f"Removed {len(removed)} mods and the game still has problems. Stopped here so "
                                 "it doesn't strip out too many. Check the error log, or raise the limit and run again.")
        # 4. find the mod that breaks it
        phase("search")
        r = autobisect.run_auto(sims_dir, runner, stop=stop, say=say, tries=tries, skip_baseline=True, confirm=False)
        if stop.is_set():
            return finish(False, "Stopped. Your mods are all turned on.", True)
        if not r.found:
            return finish(False, r.message)
        phase("remove")
        files = [sims_dir / "Mods" / f for f in r.files if (sims_dir / "Mods" / f).exists()]
        fx.apply([Finding("auto-repair", Severity.CRITICAL, f"Broke the game: {r.found}",
                          "Found by Auto Repair's 50/50 search.", files,
                          [Action("quarantine", p) for p in files], "Quarantine")], log=lambda *_: None)
        removed.append(r.found)
        say(f"Removed {r.found} (moved to quarantine).")
