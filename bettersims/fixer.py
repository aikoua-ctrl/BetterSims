"""Applies fixes safely. Nothing is deleted: files go to BetterSims/Quarantine/<session>/,
and every step is written to a journal so the whole session can be undone."""
from __future__ import annotations

import json
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .model import PACKAGE_EXT, SCRIPT_EXT, TRAY_EXTS, Action, Finding
from .paths import app_dir


class FixError(Exception):
    pass


@dataclass
class FixResult:
    session: str
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _inside(p: Path, base: Path) -> bool:
    try:
        p.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _free(target: Path) -> Path:
    cand, n = target, 2
    while cand.exists():
        cand = target.with_name(f"{target.stem} ({n}){target.suffix}")
        n += 1
    return cand


class Journal:
    def __init__(self, sims_dir: Path, session: str | None = None):
        self.sims_dir = sims_dir
        self.session = session or time.strftime("%Y%m%d-%H%M%S")
        self.dir = app_dir(sims_dir) / "journal"
        self.dir.mkdir(exist_ok=True)
        self.path = self.dir / f"{self.session}.json"
        n = 2
        while self.path.exists() and session is None:
            self.session = f"{time.strftime('%Y%m%d-%H%M%S')}-{n}"
            self.path = self.dir / f"{self.session}.json"
            n += 1
        self.data = {"session": self.session, "created": time.time(), "ops": [], "undone": False, "purged": False}

    @property
    def quarantine_dir(self) -> Path:
        return app_dir(self.sims_dir) / "Quarantine" / self.session

    def record(self, **op) -> None:
        self.data["ops"].append({k: str(v) if isinstance(v, Path) else v for k, v in op.items()})
        self.save()

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=1))

    @classmethod
    def load(cls, sims_dir: Path, session: str) -> "Journal":
        j = cls(sims_dir, session)
        j.data = json.loads(j.path.read_text())
        return j


def list_sessions(sims_dir: Path) -> list[dict]:
    d = app_dir(sims_dir) / "journal"
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            continue
    return out


class Fixer:
    def __init__(self, sims_dir: Path):
        self.sims_dir = Path(sims_dir)
        self.journal = Journal(self.sims_dir)

    # -- primitive operations, each journaled --------------------------------
    def _mkdirs(self, d: Path) -> None:
        missing = []
        p = d
        while not p.exists():
            missing.append(p)
            p = p.parent
        for m in reversed(missing):
            m.mkdir()
            self.journal.record(op="mkdir", path=m)

    def _move(self, src: Path, dst: Path) -> Path:
        dst = _free(dst)
        self._mkdirs(dst.parent)
        shutil.move(str(src), str(dst))
        self.journal.record(op="move", src=src, dst=dst)
        return dst

    def _quarantine(self, src: Path) -> Path:
        rel = src.resolve().relative_to(self.sims_dir.resolve())
        return self._move(src, self.journal.quarantine_dir / rel)

    def _create(self, path: Path, data: bytes) -> None:
        self._mkdirs(path.parent)
        path.write_bytes(data)
        self.journal.record(op="create", path=path)

    # -- actions -------------------------------------------------------------
    def apply_action(self, a: Action) -> str:
        if not _inside(a.src, self.sims_dir):
            raise FixError(f"refusing to touch a file outside the Sims 4 folder: {a.src}")
        if a.kind == "write":
            new = (a.content or "").encode("utf-8", "surrogateescape")
            if a.src.exists():
                if a.src.read_bytes() == new:
                    return "already correct"
                self._quarantine(a.src)
            self._create(a.src, new)
            return "written"
        if a.kind == "rmdir":
            if not a.src.is_dir():
                return "already gone"
            if any(a.src.iterdir()):
                return "skipped (folder no longer empty)"
            a.src.rmdir()
            self.journal.record(op="rmdir", path=a.src)
            return "removed"
        if not a.src.exists():
            return "already handled"
        if a.kind == "quarantine":
            self._quarantine(a.src)
            return "quarantined"
        if a.kind == "move":
            assert a.dst is not None
            if not _inside(a.dst, self.sims_dir):
                raise FixError(f"refusing to move outside the Sims 4 folder: {a.dst}")
            self._move(a.src, a.dst)
            return "moved"
        if a.kind == "disable":
            dst = a.dst or a.src.with_name(a.src.name + "OFF")
            self._move(a.src, dst)
            return "disabled"
        if a.kind == "extract":
            assert a.dst is not None
            self._extract(a.src, a.dst)
            self._quarantine(a.src)
            return "extracted"
        raise FixError(f"unknown action {a.kind}")

    def _extract(self, archive: Path, dest: Path) -> None:
        """Unzip only real mod files, flattened one level so nothing ends up too deep."""
        tray = self.sims_dir / "Tray"
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                name = Path(info.filename.replace("\\", "/"))
                ext = name.suffix.lower()
                if info.is_dir() or "__MACOSX" in name.parts:
                    continue
                if ext in (PACKAGE_EXT, SCRIPT_EXT):
                    target = dest / name.name
                elif ext in TRAY_EXTS:
                    target = tray / name.name
                else:
                    continue  # readmes, images, executables: left out on purpose
                self._create(_free(target), z.read(info))

    def _tidy_empty_dirs(self) -> None:
        """Remove folders inside Mods/Tray that our moves left empty (journaled, so undo recreates them)."""
        roots = [(self.sims_dir / "Mods").resolve(), (self.sims_dir / "Tray").resolve()]
        parents = {Path(op["src"]).parent for op in self.journal.data["ops"] if op["op"] == "move"}
        for d in sorted(parents, key=lambda p: len(p.parts), reverse=True):
            while d.resolve() not in roots and any(_inside(d, r) for r in roots):
                if not d.is_dir() or any(d.iterdir()):
                    break
                d.rmdir()
                self.journal.record(op="rmdir", path=d)
                d = d.parent

    def apply(self, findings: list[Finding], log=print) -> FixResult:
        res = FixResult(self.journal.session)
        self.journal.data.setdefault("findings", []).extend(f.title for f in findings)
        self.journal.save()
        for f in findings:
            for a in f.actions:
                desc = a.describe(self.sims_dir)
                try:
                    outcome = self.apply_action(a)
                except Exception as e:  # noqa: BLE001 - report and carry on with the other fixes
                    if isinstance(e, PermissionError):
                        e = "file is in use or read-only (is The Sims 4 still running?)"
                    res.errors.append(f"{desc}: {e}")
                    log(f"  ERROR  {desc}: {e}")
                    continue
                if outcome in ("already handled", "already gone", "already correct") or outcome.startswith("skipped"):
                    res.skipped.append(f"{desc}: {outcome}")
                else:
                    res.done.append(desc)
                    log(f"  OK     {desc}")
        self._tidy_empty_dirs()
        if not self.journal.data["ops"]:
            self.journal.path.unlink(missing_ok=True)
        return res


def undo(sims_dir: Path, session: str, log=print) -> list[str]:
    j = Journal.load(Path(sims_dir), session)
    if j.data.get("purged"):
        raise FixError("this session's quarantine was permanently deleted, so it can't be undone")
    if j.data.get("undone"):
        raise FixError("this session was already undone")
    problems: list[str] = []
    for op in reversed(j.data["ops"]):
        kind = op["op"]
        try:
            if kind == "move":
                src, dst = Path(op["src"]), Path(op["dst"])
                if not dst.exists():
                    problems.append(f"missing {dst}")
                    continue
                if src.exists():
                    problems.append(f"{src} exists again, restored copy kept at {dst}")
                    continue
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dst), str(src))
                log(f"  restored {src}")
            elif kind == "create":
                Path(op["path"]).unlink(missing_ok=True)
            elif kind == "mkdir":
                p = Path(op["path"])
                if p.is_dir() and not any(p.iterdir()):
                    p.rmdir()
            elif kind == "rmdir":
                Path(op["path"]).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            problems.append(f"{kind} {op}: {e}")
    j.data["undone"] = True
    j.save()
    return problems


def purge(sims_dir: Path, session: str | None = None) -> list[str]:
    """Permanently delete quarantined files (one session, or all). This cannot be undone."""
    removed = []
    for s in list_sessions(Path(sims_dir)):
        if session and s["session"] != session:
            continue
        j = Journal.load(Path(sims_dir), s["session"])
        if j.quarantine_dir.exists():
            shutil.rmtree(j.quarantine_dir)
            removed.append(j.session)
        j.data["purged"] = True
        j.save()
    return removed
