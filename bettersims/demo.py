"""Builds a fake, deliberately broken Sims 4 folder for trying BetterSims without the game.

Every file is in the real format (DBPF packages, zip-based .ts4script with .pyc files),
so the scanner treats it exactly like a real Mods folder.
"""
from __future__ import annotations

import io
import os
import time
import zipfile
from pathlib import Path

from .dbpf import ResourceKey, write_package

PY37 = (3394).to_bytes(2, "little") + b"\r\n"
PY39 = (3425).to_bytes(2, "little") + b"\r\n"

CAS, TUNING, OBJ, THUMB = 0x034AEECB, 0x03B33DDF, 0x319E4F1D, 0x3C1AF1F2


def pyc(code: str, magic: bytes = PY37) -> bytes:
    return magic + b"\0" * 12 + code.encode()


def write_script(path: Path, members: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    path.write_bytes(buf.getvalue())


def keys(t: int, start: int, n: int, group: int = 0) -> list[ResourceKey]:
    return [ResourceKey(t, group, 0x1000_0000_0000_0000 + start + i) for i in range(n)]


def build(root: Path) -> Path:
    root = Path(root)
    mods, tray = root / "Mods", root / "Tray"
    mods.mkdir(parents=True, exist_ok=True)
    tray.mkdir(exist_ok=True)

    # Healthy CC, plus an exact duplicate of it in another folder.
    hair = [(k, b"hair mesh %d" % i) for i, k in enumerate(keys(CAS, 0, 4))] + [(keys(THUMB, 0, 1)[0], b"thumb")]
    write_package(mods / "CoolHair" / "SimmerX_LongHair.package", hair)
    write_package(mods / "Downloads" / "SimmerX_LongHair - Copy.package", hair)

    # Two versions of the same mod.
    write_package(mods / "BetterBuild_v1.2.package", [(k, b"old") for k in keys(OBJ, 100, 6)])
    write_package(mods / "BetterBuild_v1.3.package", [(k, b"new") for k in keys(OBJ, 100, 6)])

    # Two different gameplay mods overriding the same tuning.
    write_package(mods / "Gameplay" / "FasterAging.package", [(k, b"aging A") for k in keys(TUNING, 500, 3)])
    write_package(mods / "Gameplay" / "RealisticLife.package",
                  [(k, b"aging B") for k in keys(TUNING, 500, 2)] + [(k, b"x") for k in keys(TUNING, 900, 5)])

    # Too deep.
    write_package(mods / "a" / "b" / "c" / "d" / "e" / "f" / "DeepSofa.package", [(keys(OBJ, 700, 1)[0], b"sofa")])
    write_script(mods / "Scripts" / "Creator" / "PartyMod" / "partymod.ts4script",
                 {"partymod/__init__.pyc": pyc("init"), "partymod/main.pyc": pyc("party")})

    # Broken / empty files.
    (mods / "broken_download.package").write_bytes(b"<html>Error 404</html>")
    (mods / "empty.package").write_bytes(b"")

    # Script mod versions fighting over the same modules.
    write_script(mods / "MoreTraits.ts4script", {"moretraits/core.pyc": pyc("v2 code")})
    write_script(mods / "Old" / "MoreTraits_old.ts4script", {"moretraits/core.pyc": pyc("v1 code")})
    # Built for the wrong Python.
    write_script(mods / "NotForSims.ts4script", {"notforsims/main.pyc": pyc("x", PY39)})
    # A script that crashes (named in the exception log below).
    write_script(mods / "CrashyCareers.ts4script", {"crashycareers/careers.pyc": pyc("boom")})

    # Still zipped.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        tmp = root / "_tmp.package"
        write_package(tmp, [(keys(OBJ, 800, 1)[0], b"lamp")])
        z.write(tmp, "NiceLamp/NiceLamp.package")
        z.writestr("NiceLamp/readme.txt", "Thanks for downloading!")
        tmp.unlink()
    (mods / "NiceLamp.zip").write_bytes(buf.getvalue())

    # Misplaced, junk and dangerous files.
    (mods / "StarterHome.trayitem").write_bytes(b"tray")
    write_package(tray / "WrongPlace.package", [(keys(CAS, 50, 1)[0], b"shirt")])
    (mods / "Thumbs.db").write_bytes(b"junk")
    (mods / "__MACOSX").mkdir(exist_ok=True)
    (mods / "__MACOSX" / "._SimmerX_LongHair.package").write_bytes(b"junk")
    (mods / "FreeCC_Installer.exe").write_bytes(b"MZ not really a program")
    (mods / "Empty Folder").mkdir(exist_ok=True)
    write_package(mods / "TurnedOff.packageOFF", [(keys(CAS, 60, 1)[0], b"off")])

    # Game files.
    (root / "Options.ini").write_text("[options]\nversion = 1\nmodsdisabled = 1\nscriptmodsenabled = 0\n")
    (root / "localthumbcache.package").write_bytes(b"\0" * 2048)
    (root / "lastException_demo.txt").write_text(
        '<?xml version="1.0"?><root><report><desyncdata>Traceback (most recent call last):\n'
        '  File "C:\\Users\\Simmer\\Documents\\Electronic Arts\\The Sims 4\\Mods\\CrashyCareers.ts4script'
        '\\crashycareers\\careers.py", line 42, in on_load\nAttributeError: object has no attribute '
        "'career_track'</desyncdata></report></root>"
    )
    # Make the "new" versions newer.
    now = time.time()
    os.utime(mods / "BetterBuild_v1.2.package", (now - 86400 * 30, now - 86400 * 30))
    os.utime(mods / "Old" / "MoreTraits_old.ts4script", (now - 86400 * 30, now - 86400 * 30))
    os.utime(mods / "Downloads" / "SimmerX_LongHair - Copy.package", (now - 86400, now - 86400))
    return root
