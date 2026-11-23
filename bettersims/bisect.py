"""50/50 method, automated: find a bad mod by turning half the suspects off at a time.

Mods are turned off by renaming them to .packageOFF / .ts4scriptOFF (the same convention
as Sims 4 Mod Manager), so nothing is moved and 'reset' always puts everything back.

A "suspect" is a folder, or a group of loose files that belong together (a mod's .package and
.ts4script share a name, so they are always switched together). When the search narrows down
to a single folder that holds several mods, it opens that folder and keeps going inside it.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .model import JUNK_DIRS, PACKAGE_EXT, SCRIPT_EXT
from .paths import app_dir
from .rules.packages import base_name

MOD_EXTS = (PACKAGE_EXT, SCRIPT_EXT)


def _state_path(sims_dir: Path) -> Path:
    return app_dir(sims_dir) / "bisect.json"


def last_found_path(sims_dir: Path) -> Path:
    return app_dir(sims_dir) / "bisect_found.json"


def load_state(sims_dir: Path) -> dict | None:
    p = _state_path(sims_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def _save(sims_dir: Path, state: dict) -> None:
    _state_path(sims_dir).write_text(json.dumps(state, indent=1))


def _mod_files(d: Path) -> list[Path]:
    return [f for f in d.rglob("*") if f.is_file() and f.suffix.lower() in MOD_EXTS
            and not any(p.lower() in JUNK_DIRS for p in f.parts)]


def units(mods_dir: Path, inside: Path | None = None) -> dict[str, list[str]]:
    """Suspect label -> mod files (relative to Mods) in one folder level."""
    base = inside or mods_dir
    out: dict[str, list[str]] = {}
    loose: dict[str, list[str]] = defaultdict(list)
    for p in sorted(base.iterdir(), key=lambda q: q.name.lower()):  # same order on Windows and Linux
        if p.name.lower() in JUNK_DIRS:
            continue
        if p.is_dir():
            files = _mod_files(p)
            if files:
                out[str(p.relative_to(mods_dir)) + "/"] = [str(f.relative_to(mods_dir)) for f in files]
        elif p.suffix.lower() in MOD_EXTS:
            loose[base_name(p.stem) or p.stem.lower()].append(str(p.relative_to(mods_dir)))
    for files in loose.values():
        out[" + ".join(Path(f).name for f in files)] = files
    return out


def _disable(mods: Path, state: dict, labels: list[str]) -> None:
    rels = [rel for label in labels for rel in state["units"][label]] + state.get("parked", [])
    for rel in rels:
        f = mods / rel
        off = f.with_name(f.name + "OFF")
        if not f.exists() or off.exists():
            continue  # already off, or a stray OFF copy we must not overwrite
        f.rename(off)
        state["renamed"].append(str(off))


def _enable_all(state: dict) -> list[str]:
    problems = []
    for off in state["renamed"]:
        p = Path(off)
        on = p.with_name(p.name[:-3])
        if not p.exists():
            continue
        if on.exists():
            problems.append(f"{on.name} exists twice; left {p.name} turned off")
            continue
        p.rename(on)
    state["renamed"] = []
    return problems


def _split(state: dict, sims_dir: Path) -> str:
    s = state["suspects"]
    half = len(s) // 2
    state["testing_on"], state["testing_off"] = s[:half], s[half:]
    _disable(sims_dir / "Mods", state, state["testing_off"])
    state["round"] += 1
    _save(sims_dir, state)
    return (
        f"Round {state['round']}: {len(s)} suspects left. Turned OFF {len(state['testing_off'])}, "
        f"kept ON {len(state['testing_on'])}.\n"
        "Start the game and check for the problem, then answer:\n"
        "  still broken  ->  bettersims bisect broken\n  problem gone  ->  bettersims bisect fixed"
    )


def start(sims_dir: Path) -> str:
    if load_state(sims_dir):
        raise SystemExit("A 50/50 search is already running. Use 'bettersims bisect reset' to stop it first.")
    u = units(sims_dir / "Mods")
    if len(u) < 2:
        raise SystemExit("You need at least 2 mods to use the 50/50 search.")
    state = {"units": u, "suspects": list(u), "renamed": [], "round": 0}
    return _split(state, sims_dir)


def answer(sims_dir: Path, still_broken: bool) -> str:
    state = load_state(sims_dir)
    if not state:
        raise SystemExit("No 50/50 search running. Start one with 'bettersims bisect start'.")
    # Still broken with half off -> the culprit is among the ones left ON, else among the OFF ones.
    state["suspects"] = state["testing_on"] if still_broken else state["testing_off"]
    if still_broken:
        # The problem happened without the OFF half, so they aren't needed to cause it: keep them off from
        # now on. Otherwise a second bad mod among them would make every later round look "broken" and
        # send the search to an innocent mod.
        state.setdefault("parked", []).extend(rel for lbl in state["testing_off"] for rel in state["units"][lbl])
    problems = _enable_all(state)
    mods = sims_dir / "Mods"
    if len(state["suspects"]) == 1:
        label = state["suspects"][0]
        # Narrowed to one folder holding several mods: look inside it.
        if label.endswith("/"):
            inner = units(mods, mods / label.rstrip("/"))
            if len(inner) > 1:
                state["units"] = inner
                state["suspects"] = list(inner)
                return f"Narrowed it down to the folder {label}. Now searching inside it.\n" + _split(state, sims_dir)
        _state_path(sims_dir).unlink()
        files = state["units"][label]
        last_found_path(sims_dir).write_text(json.dumps({"label": label, "files": files}))
        warn = ("\nCouldn't turn everything back on: " + "; ".join(problems)) if problems else ""
        return (f"Found it: {label}\nAll your mods are turned back on.{warn}\n"
                "If the problem still happens with only this one removed, it isn't caused by a mod.\n"
                "To remove it safely: bettersims quarantine " + " ".join(f'"{f}"' for f in files))
    if not state["suspects"]:
        _state_path(sims_dir).unlink()
        return "Something went wrong: no suspects left. All mods are turned back on."
    return _split(state, sims_dir)


def reset(sims_dir: Path) -> str:
    state = load_state(sims_dir)
    if not state:
        return "No 50/50 search running."
    problems = _enable_all(state)
    _state_path(sims_dir).unlink()
    return "Stopped. All mods are turned back on." + ("\n" + "\n".join(problems) if problems else "")
