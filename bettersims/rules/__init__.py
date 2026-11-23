"""All detectors. Each rule is a function Inventory -> list[Finding]."""
from __future__ import annotations

import fnmatch
import json

from ..model import Action, Finding, Severity
from ..scanner import Inventory
from . import folders, game, packages, scripts


def check_known_issues(inv: Inventory) -> list[Finding]:
    """User-editable list of mods known to be broken: BetterSims/known_issues.json.

    Format: [{"pattern": "*OldMod*.ts4script", "reason": "Broken since patch X", "remove": true}]
    """
    path = inv.sims_dir / "BetterSims" / "known_issues.json"
    if not path.is_file():
        return []
    try:
        entries = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return [Finding("known-issues", Severity.WARNING, "known_issues.json can't be read",
                        "Fix the JSON syntax in BetterSims/known_issues.json.", [path])]
    out = []
    for e in entries:
        pat = str(e.get("pattern", "")).lower()
        hits = [f for f in inv.mods_files if pat and fnmatch.fnmatch(f.path.name.lower(), pat)]
        for f in hits:
            out.append(Finding(
                "known-issue", Severity.CRITICAL if e.get("remove") else Severity.WARNING,
                f"Known problem mod: {f.path.name}", str(e.get("reason", "Listed in known_issues.json.")),
                [f.path], fix_label="Quarantine it",
                actions=[Action("quarantine", f.path)], auto=bool(e.get("remove")),
            ))
    return out


ALL_RULES = folders.RULES + packages.RULES + scripts.RULES + game.RULES + [check_known_issues]


def run_rules(inv: Inventory, progress=None) -> list[Finding]:
    findings: list[Finding] = []
    for i, rule in enumerate(ALL_RULES):
        if progress:
            progress(i, len(ALL_RULES), rule.__name__)
        findings += rule(inv)
    # A "conflict" between files already reported as versions/duplicates of one mod is the same problem.
    covered = [set(f.files) for f in findings if f.rule in ("old-version", "duplicate")]
    findings = [f for f in findings if not (f.rule == "conflict" and any(set(f.files) <= c for c in covered))]
    findings.sort(key=lambda f: (f.severity.rank, f.rule, f.title))
    return findings
