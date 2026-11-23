"""Finding the Sims 4 user folder (Documents/Electronic Arts/The Sims 4)."""
from __future__ import annotations

import os
from pathlib import Path


def _documents_dirs() -> list[Path]:
    home = Path.home()
    dirs: list[Path] = []
    # Windows "Documents" may be redirected by OneDrive or the registry.
    if os.name == "nt":
        try:
            import winreg  # type: ignore

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as k:
                val, _ = winreg.QueryValueEx(k, "Personal")
                dirs.append(Path(os.path.expandvars(val)))
        except OSError:
            pass
    for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(env):
            dirs.append(Path(os.environ[env]) / "Documents")
    dirs += [home / "Documents", home / "OneDrive" / "Documents"]
    seen, out = set(), []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


SIMS_MARKERS = ("Mods", "Tray", "Options.ini", "saves", "GameVersion.txt")


def looks_like_sims_dir(p: Path) -> bool:
    """True for the Documents/Electronic Arts/The Sims 4 user folder (not the game install folder)."""
    return any((p / m).exists() for m in SIMS_MARKERS)


def check_sims_dir(p: Path) -> str | None:
    """None if p is usable, otherwise a plain-English explanation of what's wrong."""
    if not p.is_dir():
        return f"Folder not found: {p}"
    if (p / "Game" / "Bin").is_dir() or (p / "Data" / "Client").is_dir():
        return ("That's where the game is installed, not where your mods live. Pick "
                "Documents\\Electronic Arts\\The Sims 4 instead.")
    if not looks_like_sims_dir(p):
        return ("That doesn't look like a Sims 4 folder (no Mods, Tray, saves or Options.ini inside). "
                "Pick Documents\\Electronic Arts\\The Sims 4.")
    return None


def find_sims_dirs() -> list[Path]:
    """All plausible Sims 4 user folders, including localized names ("Die Sims 4" etc)."""
    found: list[Path] = []
    for docs in _documents_dirs():
        ea = docs / "Electronic Arts"
        if not ea.is_dir():
            continue
        for child in ea.iterdir():
            if child.is_dir() and ((child / "Mods").is_dir() or (child / "Options.ini").is_file()):
                found.append(child)
    # Prefer the English default name, then whichever has a Mods folder.
    found.sort(key=lambda c: (c.name != "The Sims 4", not (c / "Mods").is_dir()))
    return found


def resolve_sims_dir(given: str | None) -> Path:
    if given:
        p = Path(given).expanduser()
        # Accept either the Sims 4 folder or the Mods folder itself.
        if p.name.lower() == "mods" and p.is_dir():
            p = p.parent
        problem = check_sims_dir(p)
        if problem:
            raise SystemExit(problem)
        return p
    found = find_sims_dirs()
    if not found:
        raise SystemExit(
            "Couldn't find your Sims 4 folder automatically. Pass it with --sims-dir, e.g.\n"
            '  bettersims scan --sims-dir "C:\\Users\\you\\Documents\\Electronic Arts\\The Sims 4"'
        )
    return found[0]


GAME_EXES = ("TS4_x64.exe", "TS4_DX9_x64.exe", "TS4.exe")


def game_running() -> bool:
    """True if The Sims 4 is running (Windows only; elsewhere we can't tell and say no)."""
    if os.name != "nt":
        return False
    import subprocess

    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                             timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    low = out.lower()
    return any(f'"{e.lower()}"' in low for e in GAME_EXES)


def app_dir(sims_dir: Path) -> Path:
    """Where BetterSims keeps quarantine, journals and state. Outside Mods, so the game ignores it."""
    d = sims_dir / "BetterSims"
    d.mkdir(exist_ok=True)
    return d
