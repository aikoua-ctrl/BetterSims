"""Command line interface. Run `bettersims --help`."""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

from . import __version__, bisect, demo, fixer, report
from .model import Action, Finding, Severity
from .paths import app_dir, resolve_sims_dir
from .rules import run_rules
from .scanner import scan

ICON = {Severity.CRITICAL: "[!!]", Severity.WARNING: "[! ]", Severity.INFO: "[i ]"}


def run_scan(sims_dir: Path, progress=None):
    inv = scan(sims_dir)
    return inv, run_rules(inv, progress)


def select(findings: list[Finding], sims_dir: Path, only: list[str] | None, include_all: bool) -> list[Finding]:
    fixable = [f for f in findings if f.fixable]
    if only:
        wanted = set(only)
        return [f for f in fixable if f.id(sims_dir) in wanted or f.rule in wanted]
    return fixable if include_all else [f for f in fixable if f.auto]


def cmd_scan(a) -> int:
    sims = resolve_sims_dir(a.sims_dir)
    inv, findings = run_scan(sims)
    if a.json:
        print(json.dumps([
            {"id": f.id(sims), "rule": f.rule, "severity": f.severity.value, "title": f.title,
             "detail": f.detail, "files": [str(p) for p in f.files], "fix": f.fix_label, "auto": f.auto}
            for f in findings], indent=1))
        return 0
    print(f"Scanned {sims}\n  {len(inv.packages())} packages, {len(inv.scripts())} script mods\n")
    for f in findings:
        print(f"{ICON[f.severity]} {f.title}   ({f.id(sims)})")
        if a.verbose:
            print(f"      {f.detail}")
            for p in f.files[:10]:
                print(f"      - {p}")
        if f.fixable:
            print(f"      fix: {f.fix_label}{'' if f.auto else '  [pick with --only]'}")
    if not findings:
        print("No problems found.")
    out = Path(a.report) if a.report else app_dir(sims) / "report.html"
    report.write(inv, findings, out)
    print(f"\nFull report: {out}")
    if a.open:
        webbrowser.open(out.as_uri())
    n_auto = len(select(findings, sims, None, False))
    if n_auto:
        print(f"Run 'bettersims fix' to preview {n_auto} safe fix(es), then 'bettersims fix --apply'.")
    return 1 if any(f.severity == Severity.CRITICAL for f in findings) else 0


def cmd_fix(a) -> int:
    sims = resolve_sims_dir(a.sims_dir)
    _, findings = run_scan(sims)
    chosen = select(findings, sims, a.only, a.all)
    if not chosen:
        print("Nothing to fix." if not a.only else "No fixable problem matches --only.")
        return 0
    print(f"{'Applying' if a.apply else 'Plan (dry run, nothing changed)'}: {len(chosen)} fix(es)\n")
    if not a.apply:
        for f in chosen:
            print(f"{ICON[f.severity]} {f.title}\n      {f.fix_label}")
            for act in f.actions[:8]:
                print(f"        - {act.describe(sims)}")
            if len(f.actions) > 8:
                print(f"        - ... {len(f.actions) - 8} more")
        skipped = [f for f in findings if f.fixable and not f.auto and f not in chosen]
        if skipped:
            print(f"\n{len(skipped)} fix(es) need you to choose (conflicts, crash suspects). Add them with "
                  "--only <id> or use --all.")
        print("\nRun again with --apply to do it. Close The Sims 4 first.")
        return 0
    res = fixer.Fixer(sims).apply(chosen)
    print(f"\nDone: {len(res.done)} change(s), {len(res.skipped)} skipped, {len(res.errors)} error(s).")
    if res.done:
        print(f"Undo any time with: bettersims undo {res.session}")
    return 1 if res.errors else 0


def cmd_undo(a) -> int:
    sims = resolve_sims_dir(a.sims_dir)
    sessions = [s for s in fixer.list_sessions(sims) if not s.get("undone") and not s.get("purged")]
    if not sessions:
        print("Nothing to undo.")
        return 0
    session = a.session or sessions[-1]["session"]
    problems = fixer.undo(sims, session)
    print(f"Undid session {session}." + (f" {len(problems)} item(s) couldn't be restored:" if problems else ""))
    for p in problems:
        print("  -", p)
    return 1 if problems else 0


def cmd_history(a) -> int:
    sims = resolve_sims_dir(a.sims_dir)
    for s in fixer.list_sessions(sims):
        state = "undone" if s.get("undone") else "purged" if s.get("purged") else "active"
        print(f"{s['session']}  {len(s['ops']):4d} step(s)  {state:7s}  {', '.join(s.get('findings', [])[:3])}")
    return 0


def cmd_purge(a) -> int:
    sims = resolve_sims_dir(a.sims_dir)
    if not a.yes:
        print("This PERMANENTLY deletes quarantined files, so they can't be undone.\n"
              "Run again with --yes to confirm.")
        return 1
    removed = fixer.purge(sims, None if a.all else a.session)
    print(f"Permanently deleted quarantine for {len(removed)} session(s).")
    return 0


def cmd_quarantine(a) -> int:
    sims = resolve_sims_dir(a.sims_dir)
    targets = []
    for t in a.paths:
        p = Path(t)
        if not p.is_absolute():
            p = sims / "Mods" / t
        if not p.exists():
            print(f"Not found: {p}")
            return 1
        targets.append(p)
    f = Finding("manual", Severity.INFO, "Manual removal", "", targets,
                [Action("quarantine", p) for p in targets], "Quarantine")
    res = fixer.Fixer(sims).apply([f])
    print(f"Quarantined {len(res.done)} item(s). Undo with: bettersims undo {res.session}")
    return 0


def cmd_bisect(a) -> int:
    sims = resolve_sims_dir(a.sims_dir)
    if a.step == "start":
        print(bisect.start(sims))
    elif a.step in ("broken", "fixed"):
        print(bisect.answer(sims, still_broken=a.step == "broken"))
    elif a.step == "reset":
        print(bisect.reset(sims))
    else:
        st = bisect.load_state(sims)
        print("No bisect running." if not st else
              f"Round {st['round']}, {len(st['suspects'])} suspects. OFF now: {', '.join(st['testing_off'])}")
    return 0


def cmd_demo(a) -> int:
    root = demo.build(Path(a.path))
    print(f"Built a broken demo Sims 4 folder at {root}\nTry: bettersims scan --sims-dir \"{root}\"")
    return 0


def cmd_gui(a) -> int:
    from .gui import main as gui_main

    gui_main(a.sims_dir)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bettersims", description="Find and fix broken, duplicate and conflicting Sims 4 mods.")
    p.add_argument("--version", action="version", version=f"bettersims {__version__}")
    sub = p.add_subparsers(dest="cmd")

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--sims-dir", help="Your 'The Sims 4' folder (found automatically if left out)")
        sp.set_defaults(fn=fn)
        return sp

    s = add("scan", cmd_scan, "Scan for problems and write an HTML report")
    s.add_argument("--report", help="Where to write the HTML report")
    s.add_argument("--open", action="store_true", help="Open the report in your browser")
    s.add_argument("--json", action="store_true", help="Print findings as JSON")
    s.add_argument("-v", "--verbose", action="store_true")
    f = add("fix", cmd_fix, "Preview fixes, or apply them with --apply")
    f.add_argument("--apply", action="store_true", help="Actually make the changes")
    f.add_argument("--all", action="store_true", help="Include fixes that normally need you to choose")
    f.add_argument("--only", nargs="+", metavar="ID", help="Only these finding ids or rule names")
    u = add("undo", cmd_undo, "Undo a fix session (the last one by default)")
    u.add_argument("session", nargs="?")
    add("history", cmd_history, "List fix sessions")
    pg = add("purge", cmd_purge, "Permanently delete quarantined files")
    pg.add_argument("session", nargs="?")
    pg.add_argument("--all", action="store_true")
    pg.add_argument("--yes", action="store_true")
    q = add("quarantine", cmd_quarantine, "Remove specific mods safely (into quarantine)")
    q.add_argument("paths", nargs="+", help="Paths, or names relative to Mods")
    b = add("bisect", cmd_bisect, "Find a bad mod by turning half off at a time")
    b.add_argument("step", choices=["start", "broken", "fixed", "reset", "status"])
    d = sub.add_parser("demo", help="Create a fake broken Sims 4 folder to try BetterSims on")
    d.add_argument("path")
    d.set_defaults(fn=cmd_demo)
    add("gui", cmd_gui, "Open the desktop window")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        # Double-clicked exe: open the window.
        return cmd_gui(argparse.Namespace(sims_dir=None))
    a = build_parser().parse_args(argv)
    if not getattr(a, "fn", None):
        build_parser().print_help()
        return 0
    return a.fn(a)
