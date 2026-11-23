"""Game-side problems: crash logs, Options.ini, caches, outdated script mods."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from ..model import Action, FileEntry, Finding, Severity
from ..scanner import Inventory

LOG_NAME_RE = re.compile(r"(lastexception|lastuiexception|betterexception|be_report|mc_lastexception)", re.I)
TS4SCRIPT_RE = re.compile(r"([^\\/\"'<>\s:]+\.ts4script)", re.I)
PACKAGE_RE = re.compile(r"([^\\/\"'<>\s:]+\.package)", re.I)
PYFILE_RE = re.compile(r"[\\/]([A-Za-z_][\w]*)[\\/][\w\\/]*?\.pyc?\b")


def find_logs(inv: Inventory) -> list[Path]:
    logs: list[Path] = []
    for d in (inv.sims_dir, inv.mods_dir):
        if not d.is_dir():
            continue
        it = d.iterdir() if d == inv.sims_dir else d.rglob("*")
        for p in it:
            if p.is_file() and LOG_NAME_RE.search(p.name) and p.suffix.lower() in (".txt", ".html", ".log", ".xml"):
                logs.append(p)
    return sorted(set(logs))


def _top_modules(inv: Inventory) -> dict[str, FileEntry]:
    """Top-level Python package name -> the script mod that ships it."""
    index: dict[str, FileEntry] = {}
    for f in inv.scripts():
        info = inv.script(f)
        if isinstance(info, Exception):
            continue
        for m in info.members:
            if m.endswith((".py", ".pyc")) and "/" in m:
                index.setdefault(m.split("/", 1)[0].lower(), f)
    return index


def check_crash_logs(inv: Inventory) -> list[Finding]:
    logs = find_logs(inv)
    if not logs:
        return []
    by_name: dict[str, FileEntry] = {f.path.name.lower(): f for f in inv.mods_files}
    modules = _top_modules(inv)
    blame: Counter[Path] = Counter()
    for log in logs:
        text = log.read_text(errors="replace")
        hits: set[Path] = set()
        for m in TS4SCRIPT_RE.findall(text) + PACKAGE_RE.findall(text):
            f = by_name.get(m.lower())
            if f:
                hits.add(f.path)
        for m in PYFILE_RE.findall(text):
            f = modules.get(m.lower())
            if f:
                hits.add(f.path)
        blame.update(hits)

    out: list[Finding] = []
    for path, n in blame.most_common():
        out.append(
            Finding(
                "crash-culprit",
                Severity.CRITICAL,
                f"Error logs blame {path.name}",
                f"{n} of {len(logs)} error log(s) mention this mod in the crash/error trace. It's broken, "
                "outdated for the current game patch, or missing a mod it depends on. Update it from "
                "the creator, or quarantine it.",
                [path],
                fix_label="Quarantine it",
                actions=[Action("quarantine", path)],
                auto=False,
            )
        )
    out.append(
        Finding(
            "error-logs",
            Severity.INFO if blame else Severity.WARNING,
            f"{len(logs)} game error log(s) found",
            "The game writes lastException files when something goes wrong. "
            + ("BetterSims matched them to the mods above. " if blame else
               "None of them name a specific mod. Install Better Exceptions (TwistedMexi) for clearer "
               "reports, or use 'bettersims bisect' to find the culprit by elimination. ")
            + "Archiving them after you fix things makes new errors easy to spot.",
            logs,
            fix_label="Archive the logs (quarantine)",
            actions=[Action("quarantine", p) for p in logs],
        )
    )
    return out


def _read_ini(path: Path) -> str:
    return path.read_text(errors="replace")


def check_options_ini(inv: Inventory) -> list[Finding]:
    ini = inv.sims_dir / "Options.ini"
    if not ini.is_file():
        return []
    text = _read_ini(ini)
    new = text
    problems = []
    if re.search(r"^\s*modsdisabled\s*=\s*1\s*$", text, re.M | re.I):
        problems.append("all custom content is turned off")
        new = re.sub(r"^(\s*modsdisabled\s*=\s*)1(\s*)$", r"\g<1>0\g<2>", new, flags=re.M | re.I)
    if re.search(r"^\s*scriptmodsenabled\s*=\s*0\s*$", text, re.M | re.I):
        problems.append("script mods are turned off")
        new = re.sub(r"^(\s*scriptmodsenabled\s*=\s*)0(\s*)$", r"\g<1>1\g<2>", new, flags=re.M | re.I)
    if not problems:
        return []
    return [
        Finding(
            "options-ini",
            Severity.CRITICAL,
            "Mods are switched off in the game settings",
            f"In Options.ini, {' and '.join(problems)}. Game patches do this automatically, "
            "which is why mods 'stop working' after an update.",
            [ini],
            fix_label="Turn mods and script mods back on (backup kept). Close the game first.",
            actions=[Action("write", ini, content=new)],
        )
    ]


CACHE_FILES = ["localthumbcache.package", "avatarcache.package", "localsimtexturecache.package"]
CACHE_DIRS = ["cache", "cachestr", "onlinethumbnailcache"]


def check_caches(inv: Inventory) -> list[Finding]:
    items: list[Path] = [inv.sims_dir / n for n in CACHE_FILES if (inv.sims_dir / n).is_file()]
    for d in CACHE_DIRS:
        p = inv.sims_dir / d
        if p.is_dir():
            items += [c for c in p.rglob("*") if c.is_file()]
    if not items:
        return []
    size = sum(p.stat().st_size for p in items)
    return [
        Finding(
            "cache",
            Severity.INFO,
            f"Game cache files ({size / 1_048_576:.1f} MB)",
            "Old caches cause wrong thumbnails, missing CC in CAS and slow loading after you add or "
            "remove mods. The game rebuilds them, so clearing is always safe.",
            items,
            fix_label="Clear them (quarantine)",
            actions=[Action("quarantine", p) for p in items],
        )
    ]


RULES = [check_options_ini, check_crash_logs, check_caches]
