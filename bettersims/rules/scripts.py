"""Script mod (.ts4script) problems."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ..model import Action, FileEntry, Finding, Severity
from ..scanner import Inventory

# The Sims 4 runs Python 3.7. Its .pyc files start with this magic number.
GAME_PYC_MAGIC = (3394).to_bytes(2, "little") + b"\r\n"


def _pyc_version(magic: bytes) -> str:
    n = int.from_bytes(magic[:2], "little")
    table = [(3394, "3.7"), (3413, "3.8"), (3425, "3.9"), (3439, "3.10"), (3495, "3.11"), (3531, "3.12"), (3571, "3.13")]
    best = "unknown"
    for num, ver in table:
        if n >= num:
            best = ver
    if n < 3394:
        best = "older than 3.7"
    return best


def check_scripts(inv: Inventory) -> list[Finding]:
    out: list[Finding] = []
    scripts = [f for f in inv.scripts() if f.size > 0]
    wrong_python: list[tuple[FileEntry, str]] = []
    for f in scripts:
        info = inv.script(f)
        if isinstance(info, Exception):
            out.append(
                Finding(
                    "corrupt-script",
                    Severity.CRITICAL,
                    f"Broken script mod: {f.path.name}",
                    f"The .ts4script archive is damaged ({info}). The game will throw errors loading it. "
                    "Re-download it from the creator.",
                    [f.path],
                    fix_label="Quarantine it",
                    actions=[Action("quarantine", f.path)],
                )
            )
            continue
        if not any(m.endswith((".py", ".pyc")) for m in info.members):
            out.append(
                Finding(
                    "corrupt-script",
                    Severity.WARNING,
                    f"Script mod has no Python code: {f.path.name}",
                    "It contains no .py or .pyc files, so it can't do anything. It may be a renamed "
                    "zip of something else.",
                    [f.path],
                    fix_label="Quarantine it",
                    actions=[Action("quarantine", f.path)],
                )
            )
            continue
        bad = {v for v in info.pyc_magics.values() if v[:4] != GAME_PYC_MAGIC}
        if info.pyc_magics and len(bad) and all(v[:4] != GAME_PYC_MAGIC for v in info.pyc_magics.values()):
            wrong_python.append((f, _pyc_version(next(iter(bad)))))
        if info.risky:
            out.append(
                Finding(
                    "risky-script",
                    Severity.INFO,
                    f"Script mod uses system access: {f.path.name}",
                    f"It references {', '.join(info.risky)}. Popular mods sometimes do this for "
                    "legitimate reasons, but only keep it if it's from a creator you trust.",
                    [f.path],
                    fix_label="Quarantine it",
                    actions=[Action("quarantine", f.path)],
                    auto=False,
                )
            )
    # If most scripts "mismatch", the game itself probably moved to a newer Python: don't flag.
    if wrong_python and len(wrong_python) <= max(1, len(scripts) // 2):
        for f, ver in wrong_python:
            out.append(
                Finding(
                    "wrong-python",
                    Severity.CRITICAL,
                    f"Script mod built for the wrong Python: {f.path.name}",
                    f"It was compiled for Python {ver}, but The Sims 4 runs Python 3.7, so the game can't "
                    "load it. This usually means the mod is broken or isn't really for Sims 4.",
                    [f.path],
                    fix_label="Quarantine it",
                    actions=[Action("quarantine", f.path)],
                )
            )
    out += _module_conflicts(inv, scripts)
    return out


def _module_conflicts(inv: Inventory, scripts: list[FileEntry]) -> list[Finding]:
    owners: dict[str, list[tuple[FileEntry, int]]] = defaultdict(list)
    for f in scripts:
        info = inv.script(f)
        if isinstance(info, Exception):
            continue
        for name, crc in info.members.items():
            if name.endswith((".py", ".pyc")):
                owners[name.rsplit(".", 1)[0]].append((f, crc))
    groups: dict[frozenset[Path], list[str]] = defaultdict(list)
    entries: dict[Path, FileEntry] = {}
    for mod, owners_ in owners.items():
        files = {f.path for f, _ in owners_}
        if len(files) < 2 or len({crc for _, crc in owners_}) < 2:
            continue
        if mod.rsplit("/", 1)[-1] == "__init__" and "/" not in mod:
            continue
        for f, _ in owners_:
            entries[f.path] = f
        groups[frozenset(files)].append(mod)
    out = []
    for paths, mods in groups.items():
        files = [entries[p] for p in sorted(paths)]
        keep = sorted(files, key=lambda f: -f.mtime)[0]
        rest = [f for f in files if f is not keep]
        out.append(
            Finding(
                "script-conflict",
                Severity.CRITICAL,
                f"Script mods overwrite each other: {' vs '.join(f.path.name for f in files)}",
                f"They ship {len(mods)} Python module(s) with the same name but different code "
                f"(e.g. {', '.join(sorted(mods)[:3])}). Only one copy gets loaded, which usually breaks "
                "the other. This is almost always two versions of the same mod.",
                [f.path for f in files],
                fix_label=f"Keep the newest ({keep.path.name}), quarantine the other {len(rest)}",
                actions=[Action("quarantine", f.path) for f in rest],
            )
        )
    return out


RULES = [check_scripts]
