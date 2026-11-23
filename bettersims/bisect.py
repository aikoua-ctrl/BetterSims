"""50/50 method, automated: find a bad mod by turning half the suspects off at a time.

Mods are turned off by renaming them to .packageOFF / .ts4scriptOFF (the same convention
as Sims 4 Mod Manager), so nothing is moved and 'reset' always puts everything back.
"""
from __future__ import annotations

import json
from pathlib import Path

from .model import JUNK_DIRS, PACKAGE_EXT, SCRIPT_EXT
from .paths import app_dir


def _state_path(sims_dir: Path) -> Path:
    return app_dir(sims_dir) / "bisect.json"


def load_state(sims_dir: Path) -> dict | None:
    p = _state_path(sims_dir)
    return json.loads(p.read_text()) if p.exists() else None


def _save(sims_dir: Path, state: dict) -> None:
    _state_path(sims_dir).write_text(json.dumps(state, indent=1))


def units(mods_dir: Path) -> list[str]:
    """A 'mod' for bisecting = a top-level folder in Mods, or a loose file directly in Mods."""
    out = []
    for p in sorted(mods_dir.iterdir()):
        if p.name.lower() in JUNK_DIRS:
            continue
        if p.is_dir():
            if any(f.suffix.lower() in (PACKAGE_EXT, SCRIPT_EXT) for f in p.rglob("*")):
                out.append(p.name)
        elif p.suffix.lower() in (PACKAGE_EXT, SCRIPT_EXT):
            out.append(p.name)
    return out


def _files_of(mods_dir: Path, unit: str) -> list[Path]:
    p = mods_dir / unit
    if p.is_file():
        return [p]
    return [f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in (PACKAGE_EXT, SCRIPT_EXT)]


def _disable(sims_dir: Path, state: dict, unit_list: list[str]) -> None:
    mods = sims_dir / "Mods"
    for u in unit_list:
        for f in _files_of(mods, u):
            off = f.with_name(f.name + "OFF")
            f.rename(off)
            state["renamed"].append(str(off))


def _enable_all(state: dict) -> None:
    for off in state["renamed"]:
        p = Path(off)
        if p.exists():
            p.rename(p.with_name(p.name[:-3]))
    state["renamed"] = []


def _split(state: dict, sims_dir: Path) -> str:
    s = state["suspects"]
    half = len(s) // 2
    state["testing_on"], state["testing_off"] = s[:half] or s, s[half:] if half else []
    _disable(sims_dir, state, state["testing_off"])
    state["round"] += 1
    _save(sims_dir, state)
    return (
        f"Round {state['round']}: {len(s)} suspect mod(s) left. Turned OFF {len(state['testing_off'])}, "
        f"kept ON {len(state['testing_on'])}.\nStart the game and check for the problem, then run\n"
        "  bettersims bisect broken   (problem still happens)\n  bettersims bisect fixed    (problem is gone)"
    )


def start(sims_dir: Path) -> str:
    if load_state(sims_dir):
        raise SystemExit("A bisect is already running. Use 'bettersims bisect reset' to stop it first.")
    u = units(sims_dir / "Mods")
    if len(u) < 2:
        raise SystemExit("You need at least 2 mods to bisect.")
    state = {"suspects": u, "renamed": [], "round": 0}
    return _split(state, sims_dir)


def answer(sims_dir: Path, still_broken: bool) -> str:
    state = load_state(sims_dir)
    if not state:
        raise SystemExit("No bisect running. Start one with 'bettersims bisect start'.")
    state["suspects"] = state["testing_on"] if still_broken else state["testing_off"]
    _enable_all(state)
    if len(state["suspects"]) == 1:
        culprit = state["suspects"][0]
        _state_path(sims_dir).unlink()
        return (f"Found it: {culprit}\n(If the problem still happens with only this mod off, it isn't caused by a mod.)\nAll your mods are turned back on. To remove it safely, run\n"
                f'  bettersims quarantine "{culprit}"')
    return _split(state, sims_dir)


def reset(sims_dir: Path) -> str:
    state = load_state(sims_dir)
    if not state:
        return "No bisect running."
    _enable_all(state)
    _state_path(sims_dir).unlink()
    return "Bisect stopped. All mods are turned back on."
