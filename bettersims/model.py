"""Core data types: the scanned inventory, findings, and fix actions."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

PACKAGE_EXT = ".package"
SCRIPT_EXT = ".ts4script"
DISABLED_SUFFIX = "off"  # Sims 4 Mod Manager convention: foo.packageOFF
TRAY_EXTS = {".trayitem", ".blueprint", ".bpi", ".hhi", ".sgi", ".householdbinary", ".room", ".rmi"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z"}
JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
JUNK_DIRS = {"__macosx"}
# Executables and loose scripts never belong in Mods (SimSolution's safety idea).
DANGEROUS_EXTS = {".exe", ".dll", ".bat", ".cmd", ".ps1", ".vbs", ".scr", ".msi", ".com", ".jar"}
DOC_EXTS = {".txt", ".md", ".html", ".htm", ".pdf", ".url", ".png", ".jpg", ".jpeg", ".gif", ".webp"}


class Severity(str, Enum):
    CRITICAL = "critical"  # breaks the game or stops mods loading
    WARNING = "warning"  # likely causes bugs
    INFO = "info"  # worth knowing, usually harmless

    @property
    def rank(self) -> int:
        return {"critical": 0, "warning": 1, "info": 2}[self.value]


@dataclass
class FileEntry:
    path: Path  # absolute
    rel: Path  # relative to Mods (or Tray for tray files)
    root: str  # "mods" or "tray"
    size: int
    mtime: float
    _hash: str | None = None

    @property
    def ext(self) -> str:
        return self.path.suffix.lower()

    @property
    def depth(self) -> int:
        """Number of folders between Mods and the file. Mods/a.package = 0."""
        return len(self.rel.parts) - 1

    @property
    def is_junk(self) -> bool:
        return self.path.name.lower() in JUNK_NAMES or any(p.lower() in JUNK_DIRS for p in self.rel.parts[:-1])

    @property
    def disabled(self) -> bool:
        return self.ext in (PACKAGE_EXT + DISABLED_SUFFIX, SCRIPT_EXT + DISABLED_SUFFIX)

    def sha1(self) -> str:
        if self._hash is None:
            h = hashlib.sha1()
            with open(self.path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            self._hash = h.hexdigest()
        return self._hash


@dataclass
class Action:
    """One reversible file operation. kind: move | quarantine | write | disable | extract."""

    kind: str
    src: Path
    dst: Path | None = None
    content: str | None = None  # for "write"

    def describe(self, base: Path) -> str:
        def r(p: Path | None) -> str:
            if p is None:
                return ""
            try:
                return str(p.relative_to(base))
            except ValueError:
                return str(p)

        if self.kind == "move":
            return f"Move {r(self.src)} -> {r(self.dst)}"
        if self.kind == "quarantine":
            return f"Quarantine {r(self.src)}"
        if self.kind == "disable":
            return f"Disable {r(self.src)} (rename to {self.dst.name if self.dst else ''})"
        if self.kind == "write":
            return f"Rewrite {r(self.src)} (backup kept)"
        if self.kind == "rmdir":
            return f"Remove empty folder {r(self.src)}"
        if self.kind == "extract":
            return f"Extract {r(self.src)} into {r(self.dst)}, then quarantine the archive"
        return f"{self.kind} {r(self.src)}"


@dataclass
class Finding:
    rule: str
    severity: Severity
    title: str
    detail: str
    files: list[Path] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    fix_label: str = ""  # short description of the proposed fix
    auto: bool = True  # included in "fix all safe"; False = needs the user to pick it

    @property
    def fixable(self) -> bool:
        return bool(self.actions)

    def id(self, base: Path) -> str:
        h = hashlib.sha1((self.rule + "|" + "|".join(sorted(str(p) for p in self.files))).encode())
        return f"{self.rule}-{h.hexdigest()[:8]}"
