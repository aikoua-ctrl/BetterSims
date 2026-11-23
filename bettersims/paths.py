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
    return found


def resolve_sims_dir(given: str | None) -> Path:
    if given:
        p = Path(given).expanduser()
        # Accept either the Sims 4 folder or the Mods folder itself.
        if p.name.lower() == "mods" and p.is_dir():
            p = p.parent
        if not p.is_dir():
            raise SystemExit(f"Folder not found: {p}")
        return p
    found = find_sims_dirs()
    if not found:
        raise SystemExit(
            "Couldn't find your Sims 4 folder automatically. Pass it with --sims-dir, e.g.\n"
            '  bettersims scan --sims-dir "C:\\Users\\you\\Documents\\Electronic Arts\\The Sims 4"'
        )
    return found[0]


def app_dir(sims_dir: Path) -> Path:
    """Where BetterSims keeps quarantine, journals and state. Outside Mods, so the game ignores it."""
    d = sims_dir / "BetterSims"
    d.mkdir(exist_ok=True)
    return d
