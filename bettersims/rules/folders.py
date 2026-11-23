"""Folder structure problems: depth limits, misplaced files, junk, Resource.cfg."""
from __future__ import annotations

import zipfile
from pathlib import Path

from ..model import (
    ARCHIVE_EXTS,
    DANGEROUS_EXTS,
    PACKAGE_EXT,
    SCRIPT_EXT,
    TRAY_EXTS,
    Action,
    Finding,
    Severity,
)
from ..scanner import Inventory

MAX_SCRIPT_DEPTH = 1  # Mods/Folder/mod.ts4script is the deepest the game loads
MAX_PACKAGE_DEPTH = 5  # Mods/1/2/3/4/5/mod.package

DEFAULT_RESOURCE_CFG = """Priority 500
PackedFile *.package
PackedFile */*.package
PackedFile */*/*.package
PackedFile */*/*/*.package
PackedFile */*/*/*/*.package
PackedFile */*/*/*/*/*.package
"""


def free_path(target: Path, taken: set[Path]) -> Path:
    """target, or target with ' (2)', ' (3)'... if something already lives there."""
    cand, n = target, 2
    while cand.exists() or cand in taken:
        cand = target.with_name(f"{target.stem} ({n}){target.suffix}")
        n += 1
    taken.add(cand)
    return cand


def check_mods_folder(inv: Inventory) -> list[Finding]:
    if inv.mods_dir.is_dir():
        return []
    return [
        Finding(
            "mods-folder-missing",
            Severity.CRITICAL,
            "Mods folder is missing",
            "The game only loads mods from a folder called Mods inside your Sims 4 folder.",
            [inv.mods_dir],
            fix_label="Create the Mods folder with a default Resource.cfg",
            actions=[Action("write", inv.mods_dir / "Resource.cfg", content=DEFAULT_RESOURCE_CFG)],
        )
    ]


def check_resource_cfg(inv: Inventory) -> list[Finding]:
    if not inv.mods_dir.is_dir():
        return []
    cfg = inv.mods_dir / "Resource.cfg"
    if not cfg.exists():
        return [
            Finding(
                "resource-cfg",
                Severity.WARNING,
                "Resource.cfg is missing",
                "Resource.cfg tells the game which subfolders of Mods to load packages from. "
                "Without it, packages in subfolders may not load.",
                [cfg],
                fix_label="Write the standard Resource.cfg",
                actions=[Action("write", cfg, content=DEFAULT_RESOURCE_CFG)],
            )
        ]
    text = cfg.read_text(errors="replace").lower()
    if "packedfile" not in text:
        return [
            Finding(
                "resource-cfg",
                Severity.WARNING,
                "Resource.cfg looks broken",
                "It has no PackedFile lines, so the game won't load packages listed through it.",
                [cfg],
                fix_label="Replace with the standard Resource.cfg (old one backed up)",
                actions=[Action("write", cfg, content=DEFAULT_RESOURCE_CFG)],
            )
        ]
    return []


def check_depth(inv: Inventory) -> list[Finding]:
    out: list[Finding] = []
    taken: set[Path] = set()
    for f in inv.mods_files:
        if f.is_junk:
            continue
        if f.ext == SCRIPT_EXT and f.depth > MAX_SCRIPT_DEPTH:
            limit, kind = MAX_SCRIPT_DEPTH, "Script mod"
        elif f.ext == PACKAGE_EXT and f.depth > MAX_PACKAGE_DEPTH:
            limit, kind = MAX_PACKAGE_DEPTH, "Package"
        else:
            continue
        target = free_path(inv.mods_dir / f.rel.parts[0] / f.path.name, taken)
        out.append(
            Finding(
                "too-deep",
                Severity.CRITICAL,
                f"{kind} is too deep to load: {f.path.name}",
                f"It's {f.depth} folders inside Mods. The game ignores {kind.lower()}s deeper than "
                f"{limit} folder{'s' if limit > 1 else ''}, so this mod is silently not running.",
                [f.path],
                fix_label=f"Move it up to Mods/{f.rel.parts[0]}/",
                actions=[Action("move", f.path, target)],
            )
        )
    return out


def check_misplaced(inv: Inventory) -> list[Finding]:
    out: list[Finding] = []
    taken: set[Path] = set()
    tray_in_mods = [f for f in inv.mods_files if f.ext in TRAY_EXTS]
    if tray_in_mods:
        out.append(
            Finding(
                "tray-in-mods",
                Severity.WARNING,
                f"{len(tray_in_mods)} Tray file(s) in the Mods folder",
                "Saved lots, rooms and households (.trayitem, .blueprint, ...) only show up in the "
                "gallery when they're in the Tray folder.",
                [f.path for f in tray_in_mods],
                fix_label="Move them to the Tray folder",
                actions=[Action("move", f.path, free_path(inv.tray_dir / f.path.name, taken)) for f in tray_in_mods],
            )
        )
    mods_in_tray = [f for f in inv.tray_files if f.ext in (PACKAGE_EXT, SCRIPT_EXT)]
    if mods_in_tray:
        out.append(
            Finding(
                "mods-in-tray",
                Severity.WARNING,
                f"{len(mods_in_tray)} mod file(s) in the Tray folder",
                "Packages and script mods in Tray are never loaded.",
                [f.path for f in mods_in_tray],
                fix_label="Move them to the Mods folder",
                actions=[Action("move", f.path, free_path(inv.mods_dir / f.path.name, taken)) for f in mods_in_tray],
            )
        )
    for f in inv.mods_files:
        if f.ext not in ARCHIVE_EXTS:
            continue
        if f.ext == ".zip":
            try:
                with zipfile.ZipFile(f.path) as z:
                    names = [n for n in z.namelist() if not n.endswith("/")]
            except zipfile.BadZipFile:
                out.append(
                    Finding(
                        "archive",
                        Severity.WARNING,
                        f"Damaged archive: {f.path.name}",
                        "This zip can't be opened, so it's an incomplete download. Re-download it.",
                        [f.path],
                        fix_label="Quarantine it",
                        actions=[Action("quarantine", f.path)],
                    )
                )
                continue
            useful = [n for n in names if Path(n).suffix.lower() in (PACKAGE_EXT, SCRIPT_EXT) or Path(n).suffix.lower() in TRAY_EXTS]
            if useful:
                dest = free_path(f.path.parent / f.path.stem, taken)
                out.append(
                    Finding(
                        "archive",
                        Severity.CRITICAL,
                        f"Mod still zipped: {f.path.name}",
                        f"The game can't read mods inside a .zip. It contains {len(useful)} mod file(s).",
                        [f.path],
                        fix_label=f"Unzip into {dest.name}/ and quarantine the zip",
                        actions=[Action("extract", f.path, dest)],
                    )
                )
                continue
        out.append(
            Finding(
                "archive",
                Severity.WARNING,
                f"Archive in Mods: {f.path.name}",
                f"The game can't read {f.ext} archives. Extract it with 7-Zip and put the .package / "
                ".ts4script files in Mods, then quarantine the archive.",
                [f.path],
                fix_label="Quarantine the archive (extract it yourself first)",
                actions=[Action("quarantine", f.path)],
            )
        )
    return out


def check_junk(inv: Inventory) -> list[Finding]:
    out: list[Finding] = []
    junk = [f for f in inv.mods_files if f.is_junk]
    if junk:
        out.append(
            Finding(
                "junk",
                Severity.INFO,
                f"{len(junk)} junk file(s) in Mods",
                "System files like Thumbs.db, desktop.ini and __MACOSX folders do nothing but slow scanning down.",
                [f.path for f in junk],
                fix_label="Quarantine them",
                actions=[Action("quarantine", f.path) for f in junk],
            )
        )
    danger = [f for f in inv.mods_files if f.ext in DANGEROUS_EXTS]
    if danger:
        out.append(
            Finding(
                "dangerous-file",
                Severity.WARNING,
                f"{len(danger)} program file(s) in Mods",
                "Programs (.exe, .dll, .bat...) are never part of a Sims 4 mod and the game ignores them. "
                "They may be malware bundled with a download. Don't run them.",
                [f.path for f in danger],
                fix_label="Quarantine them",
                actions=[Action("quarantine", f.path) for f in danger],
            )
        )
    empty = [f for f in inv.mods_files if f.size == 0 and f.ext in (PACKAGE_EXT, SCRIPT_EXT)]
    if empty:
        out.append(
            Finding(
                "empty-file",
                Severity.WARNING,
                f"{len(empty)} empty mod file(s)",
                "These are 0 bytes: failed downloads. The game may log errors loading them.",
                [f.path for f in empty],
                fix_label="Quarantine them",
                actions=[Action("quarantine", f.path) for f in empty],
            )
        )
    return out


def check_empty_dirs(inv: Inventory) -> list[Finding]:
    files_under: set[Path] = set()
    for f in inv.mods_files:
        for parent in f.path.parents:
            files_under.add(parent)
    empty = [d for d in inv.mods_dirs if d not in files_under]
    if not empty:
        return []
    # Remove deepest first so parents become removable.
    empty.sort(key=lambda p: len(p.parts), reverse=True)
    return [
        Finding(
            "empty-folders",
            Severity.INFO,
            f"{len(empty)} empty folder(s) in Mods",
            "Empty folders are harmless but clutter the Mods folder.",
            empty,
            fix_label="Remove them",
            actions=[Action("rmdir", d) for d in empty],
        )
    ]


def check_disabled(inv: Inventory) -> list[Finding]:
    off = [f for f in inv.mods_files if f.disabled]
    if not off:
        return []
    return [
        Finding(
            "disabled",
            Severity.INFO,
            f"{len(off)} mod file(s) are turned off",
            "These end in OFF (e.g. .packageOFF), the way Sims 4 Mod Manager and BetterSims disable mods. "
            "The game skips them.",
            [f.path for f in off],
        )
    ]


RULES = [check_mods_folder, check_resource_cfg, check_depth, check_misplaced, check_junk, check_empty_dirs, check_disabled]
