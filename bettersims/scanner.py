"""Walks the Sims 4 folder once and caches everything the rules need."""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import dbpf
from .model import PACKAGE_EXT, SCRIPT_EXT, FileEntry


@dataclass
class ScriptInfo:
    members: dict[str, int]  # member path -> CRC32
    pyc_magics: dict[str, bytes]  # member path -> first 4 bytes of .pyc
    risky: list[str]  # risky API names found


RISKY_MARKERS = [b"subprocess", b"os.system", b"ctypes", b"winreg", b"shutil.rmtree", b"socket"]


@dataclass
class Inventory:
    sims_dir: Path
    mods_dir: Path
    tray_dir: Path
    mods_files: list[FileEntry] = field(default_factory=list)
    tray_files: list[FileEntry] = field(default_factory=list)
    mods_dirs: list[Path] = field(default_factory=list)
    _packages: dict[Path, dbpf.Package | Exception] = field(default_factory=dict)
    _scripts: dict[Path, ScriptInfo | Exception] = field(default_factory=dict)

    # --- convenience views -------------------------------------------------
    def packages(self) -> list[FileEntry]:
        return [f for f in self.mods_files if f.ext == PACKAGE_EXT and not f.is_junk]

    def scripts(self) -> list[FileEntry]:
        return [f for f in self.mods_files if f.ext == SCRIPT_EXT and not f.is_junk]

    def package(self, entry: FileEntry) -> dbpf.Package | Exception:
        if entry.path not in self._packages:
            try:
                self._packages[entry.path] = dbpf.read_package(entry.path)
            except Exception as e:  # noqa: BLE001 - any unreadable package is "broken", never a crash
                self._packages[entry.path] = e
        return self._packages[entry.path]

    def script(self, entry: FileEntry) -> ScriptInfo | Exception:
        if entry.path not in self._scripts:
            try:
                self._scripts[entry.path] = _read_script(entry.path)
            except Exception as e:  # noqa: BLE001 - bad zip, unsupported compression, encryption...
                self._scripts[entry.path] = e
        return self._scripts[entry.path]


def _read_script(path: Path) -> ScriptInfo:
    members: dict[str, int] = {}
    magics: dict[str, bytes] = {}
    risky: set[str] = set()
    with zipfile.ZipFile(path) as z:
        bad = z.testzip()
        if bad is not None:
            raise zipfile.BadZipFile(f"damaged entry {bad}")
        for info in z.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            members[name] = info.CRC
            if name.endswith((".pyc", ".py")):
                data = z.read(info)
                if name.endswith(".pyc"):
                    magics[name] = data[:4]
                for m in RISKY_MARKERS:
                    if m in data:
                        risky.add(m.decode())
    return ScriptInfo(members, magics, sorted(risky))


def scan(sims_dir: Path) -> Inventory:
    sims_dir = Path(sims_dir)
    inv = Inventory(sims_dir, sims_dir / "Mods", sims_dir / "Tray")
    for root_name, root, bucket in (("mods", inv.mods_dir, inv.mods_files), ("tray", inv.tray_dir, inv.tray_files)):
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_dir():
                if root_name == "mods":
                    inv.mods_dirs.append(p)
                continue
            if not p.is_file():
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            bucket.append(FileEntry(p, p.relative_to(root), root_name, st.st_size, st.st_mtime))
    return inv
