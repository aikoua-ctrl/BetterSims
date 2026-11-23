import json
from pathlib import Path

import pytest

from bettersims import bisect, demo, fixer
from bettersims.cli import main, run_scan, select
from bettersims.dbpf import DbpfError, ResourceKey, read_package, write_package
from bettersims.rules.packages import base_name


@pytest.fixture
def sims(tmp_path):
    return demo.build(tmp_path / "The Sims 4")


def rules_of(findings):
    return {f.rule for f in findings}


def snapshot(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if "BetterSims" not in p.parts)


def test_dbpf_roundtrip(tmp_path):
    k = ResourceKey(0x034AEECB, 0x80000000, 0x1234_5678_9ABC_DEF0)
    write_package(tmp_path / "a.package", [(k, b"hello")])
    pkg = read_package(tmp_path / "a.package")
    assert [e.key for e in pkg.entries] == [k]
    assert pkg.entries[0].mem_size == 5


def test_dbpf_rejects_garbage(tmp_path):
    p = tmp_path / "bad.package"
    p.write_bytes(b"<html>404</html>" * 10)
    with pytest.raises(DbpfError):
        read_package(p)


@pytest.mark.parametrize("name,base", [
    ("MCCC_v2024.6.1", "mccc"), ("Hair (1)", "hair"), ("Sofa - Copy", "sofa"),
    ("BetterBuild_v1.3", "betterbuild"), ("MoreTraits_old", "moretraits"), ("Mod_2025-01-15", "mod"), ("Plain", "plain"),
])
def test_base_name(name, base):
    assert base_name(name) == base


def test_scan_finds_every_planted_problem(sims):
    _, findings = run_scan(sims)
    expected = {
        "archive", "corrupt-package", "crash-culprit", "options-ini", "script-conflict", "too-deep",
        "wrong-python", "conflict", "dangerous-file", "duplicate", "empty-file", "mods-in-tray",
        "old-version", "resource-cfg", "tray-in-mods", "cache", "disabled", "empty-folders",
        "error-logs", "junk",
    }
    assert expected <= rules_of(findings)
    # The two BetterBuild versions are one "old-version" finding, not also a "conflict".
    conflicts = [f for f in findings if f.rule == "conflict"]
    assert len(conflicts) == 1 and "FasterAging" in conflicts[0].title
    # Crash log blames the right mod.
    culprit = next(f for f in findings if f.rule == "crash-culprit")
    assert culprit.files[0].name == "CrashyCareers.ts4script"


def test_safe_fix_then_rescan_is_clean(sims):
    _, findings = run_scan(sims)
    res = fixer.Fixer(sims).apply(select(findings, sims, None, False), log=lambda *_: None)
    assert not res.errors
    _, after = run_scan(sims)
    left = rules_of(after)
    # Left over are only the fixes that need a human decision.
    assert left <= {"conflict", "crash-culprit", "disabled", "cache", "wrong-python", "error-logs"}
    mods = sims / "Mods"
    assert (mods / "NiceLamp" / "NiceLamp.package").exists()
    assert (mods / "Scripts" / "partymod.ts4script").exists()
    assert (sims / "Tray" / "StarterHome.trayitem").exists()
    assert "modsdisabled = 0" in (sims / "Options.ini").read_text()
    assert not (mods / "Old").exists()  # emptied folder tidied
    assert not (mods / "Thumbs.db").exists()


def test_nothing_is_deleted_and_undo_restores_everything(sims):
    before = snapshot(sims)
    _, findings = run_scan(sims)
    res = fixer.Fixer(sims).apply(select(findings, sims, None, True), log=lambda *_: None)
    q = sims / "BetterSims" / "Quarantine" / res.session
    assert (q / "Mods" / "broken_download.package").exists()
    assert fixer.undo(sims, res.session, log=lambda *_: None) == []
    assert snapshot(sims) == before
    with pytest.raises(fixer.FixError):
        fixer.undo(sims, res.session)


def test_refuses_paths_outside_sims_folder(sims, tmp_path):
    outside = tmp_path / "precious.txt"
    outside.write_text("x")
    from bettersims.model import Action
    with pytest.raises(fixer.FixError):
        fixer.Fixer(sims).apply_action(Action("quarantine", outside))
    assert outside.exists()


def test_purge_needs_confirmation_and_blocks_undo(sims, capsys):
    main(["quarantine", "--sims-dir", str(sims), "empty.package"])
    assert main(["purge", "--sims-dir", str(sims), "--all"]) == 1  # no --yes
    assert (sims / "BetterSims" / "Quarantine").exists()
    assert main(["purge", "--sims-dir", str(sims), "--all", "--yes"]) == 0
    s = fixer.list_sessions(sims)[-1]
    with pytest.raises(fixer.FixError):
        fixer.undo(sims, s["session"])


def test_bisect_finds_culprit_and_restores(sims):
    culprit = "CrashyCareers.ts4script"
    before = snapshot(sims)
    msg = bisect.start(sims)
    for _ in range(20):
        st = bisect.load_state(sims)
        if st is None:
            break
        msg = bisect.answer(sims, still_broken=culprit in st["testing_on"])
    assert f"Found it: {culprit}" in msg
    assert snapshot(sims) == before


def test_corrupt_script_detected(sims):
    (sims / "Mods" / "bad.ts4script").write_bytes(b"PK\x03\x04garbage")
    _, findings = run_scan(sims)
    assert any(f.rule == "corrupt-script" and "bad.ts4script" in f.title for f in findings)


def test_cli_scan_json(sims, capsys):
    main(["scan", "--json", "--sims-dir", str(sims)])
    data = json.loads(capsys.readouterr().out)
    assert any(d["rule"] == "too-deep" for d in data)


def test_dry_run_changes_nothing(sims):
    before = snapshot(sims)
    main(["fix", "--sims-dir", str(sims)])
    assert snapshot(sims) == before


def test_script_conflict_between_different_mods_is_not_auto(sims):
    from bettersims.demo import pyc, write_script
    write_script(sims / "Mods" / "ModA.ts4script", {"injector.pyc": pyc("a")})
    write_script(sims / "Mods" / "ModB.ts4script", {"injector.pyc": pyc("b")})
    _, findings = run_scan(sims)
    f = next(f for f in findings if f.rule == "script-conflict" and "ModA" in f.title)
    assert not f.auto
    same = next(f for f in findings if f.rule == "script-conflict" and "MoreTraits" in f.title)
    assert same.auto


def test_wrong_python_follows_majority(sims):
    from bettersims.demo import PY39, pyc, write_script
    # If most script mods are built for a newer Python, the game must have upgraded:
    # then the 3.7 ones are the odd ones out, not the 3.9 ones.
    for i in range(6):
        write_script(sims / "Mods" / f"New{i}.ts4script", {f"new{i}/m.pyc": pyc("x", PY39)})
    _, findings = run_scan(sims)
    flagged = {f.files[0].name for f in findings if f.rule == "wrong-python"}
    assert "NotForSims.ts4script" not in flagged
    assert "CrashyCareers.ts4script" in flagged


def test_identical_content_is_not_a_conflict(tmp_path):
    from bettersims.scanner import scan
    from bettersims.rules.packages import check_resource_conflicts
    sims = tmp_path / "S"
    k = ResourceKey(0x03B33DDF, 0, 42)
    write_package(sims / "Mods" / "a.package", [(k, b"same tuning")])
    write_package(sims / "Mods" / "b.package", [(k, b"same tuning"), (ResourceKey(1, 0, 1), b"x")])
    assert check_resource_conflicts(scan(sims)) == []


def test_deleted_records_are_ignored(tmp_path):
    import struct
    p = tmp_path / "d.package"
    write_package(p, [(ResourceKey(1, 0, 1), b"a"), (ResourceKey(2, 0, 2), b"b")])
    data = bytearray(p.read_bytes())
    idx = struct.unpack_from("<I", data, 64)[0]
    struct.pack_into("<H", data, idx + 4 + 32 + 28, 0xFFE0)  # mark 2nd record deleted
    p.write_bytes(bytes(data))
    assert [e.key.type for e in read_package(p).entries] == [1]


def test_rejects_wrong_folders(tmp_path):
    from bettersims.paths import check_sims_dir
    assert check_sims_dir(tmp_path / "nope")
    assert "doesn't look like" in check_sims_dir(tmp_path)
    (tmp_path / "Game" / "Bin").mkdir(parents=True)
    assert "installed" in check_sims_dir(tmp_path)
    with pytest.raises(SystemExit):
        main(["fix", "--apply", "--sims-dir", str(tmp_path)])
    assert not (tmp_path / "Mods").exists()


def test_bisect_opens_big_folders(tmp_path):
    sims = tmp_path / "S"
    for i in range(8):
        write_package(sims / "Mods" / "CC" / f"item{i}.package", [(ResourceKey(1, 0, i), b"x")])
    write_package(sims / "Mods" / "other.package", [(ResourceKey(1, 0, 99), b"x")])
    before = snapshot(sims)
    culprit = "item5.package"
    bisect.start(sims)
    for _ in range(30):
        st = bisect.load_state(sims)
        if st is None:
            break
        on_files = [f for lbl in st["testing_on"] for f in st["units"][lbl]]
        msg = bisect.answer(sims, still_broken=any(culprit in f for f in on_files))
    assert "Found it: item5.package" in msg
    assert snapshot(sims) == before


def test_package_and_script_of_one_mod_stay_together(tmp_path):
    from bettersims.demo import pyc, write_script
    mods = tmp_path / "S" / "Mods"
    write_package(mods / "CoolMod.package", [(ResourceKey(1, 0, 1), b"x")])
    write_script(mods / "CoolMod.ts4script", {"coolmod/a.pyc": pyc("a")})
    write_package(mods / "Other.package", [(ResourceKey(1, 0, 2), b"x")])
    u = bisect.units(mods)
    assert sorted(len(v) for v in u.values()) == [1, 2]


def test_locked_or_missing_files_do_not_stop_other_fixes(sims):
    from bettersims.model import Action, Finding, Severity
    gone = Finding("x", Severity.INFO, "t", "", [], [Action("quarantine", sims / "Mods" / "missing.package")])
    real = Finding("x", Severity.INFO, "t", "", [], [Action("quarantine", sims / "Mods" / "empty.package")])
    res = fixer.Fixer(sims).apply([gone, real], log=lambda *_: None)
    assert not res.errors and len(res.done) == 1 and len(res.skipped) == 1


def test_unreadable_file_is_reported_not_removed(sims, monkeypatch):
    import bettersims.scanner as sc
    real = sc.dbpf.read_package

    def fake(path):
        if path.name == "BetterBuild_v1.3.package":
            raise PermissionError(13, "Permission denied")
        return real(path)

    monkeypatch.setattr(sc.dbpf, "read_package", fake)
    _, findings = run_scan(sims)
    f = next(f for f in findings if f.rule == "unreadable")
    assert f.files[0].name == "BetterBuild_v1.3.package" and not f.actions
    assert not any(f.rule == "corrupt-package" and "BetterBuild" in f.title for f in findings)


def test_options_ini_bytes_preserved(sims):
    ini = sims / "Options.ini"
    ini.write_bytes(b"[options]\nname = Caf\xe9\nmodsdisabled = 1\n")  # a non-UTF-8 byte
    _, findings = run_scan(sims)
    fixer.Fixer(sims).apply([f for f in findings if f.rule == "options-ini"], log=lambda *_: None)
    assert ini.read_bytes() == b"[options]\nname = Caf\xe9\nmodsdisabled = 0\n"


# ---- automatic 50/50 -------------------------------------------------------
from bettersims import autobisect  # noqa: E402


def test_auto_bisect_finds_culprit_and_restores(sims):
    before = snapshot(sims)
    lines = []
    res = autobisect.run_auto(sims, autobisect.DemoRunner(seconds=0), say=lines.append)
    assert res.found == "CrashyCareers.ts4script"
    assert "Confirmed" in res.message
    assert snapshot(sims) == before
    assert any("Check with all mods on" in line for line in lines)


def test_auto_bisect_stops_when_baseline_is_clean(sims):
    before = snapshot(sims)
    res = autobisect.run_auto(sims, autobisect.DemoRunner(culprit="nothing-matches", seconds=0), say=lambda *_: None)
    assert res.found is None and "manual" in res.message
    assert snapshot(sims) == before


def test_auto_bisect_two_culprits_reports_honestly(sims):
    # Broken while EITHER of two mods is on: one is found, and the double-check says more are involved.
    class Two:
        def run(self, sims_dir, stop):
            names = {p.name for p in (sims_dir / "Mods").rglob("*")}
            bad = {"CrashyCareers.ts4script", "MoreTraits.ts4script"} & names
            return autobisect.Outcome(bool(bad), "x")
    before = snapshot(sims)
    res = autobisect.run_auto(sims, Two(), say=lambda *_: None)
    assert res.found in ("CrashyCareers.ts4script", "MoreTraits.ts4script")
    assert "more than one mod" in res.message
    assert snapshot(sims) == before


def test_auto_bisect_failed_launch_aborts_and_restores(sims):
    calls = []

    class Flaky:
        def run(self, sims_dir, stop):
            calls.append(1)
            if len(calls) == 1:
                return autobisect.Outcome(True, "baseline broken")
            return autobisect.Outcome(False, "game didn't start", failed=True)
    before = snapshot(sims)
    with pytest.raises(SystemExit):
        autobisect.run_auto(sims, Flaky(), say=lambda *_: None)
    assert snapshot(sims) == before and bisect.load_state(sims) is None


def test_auto_bisect_stop_button(sims):
    import threading
    stop = threading.Event()

    class StopAfterTwo:
        n = 0

        def run(self, sims_dir, stop_ev):
            self.n += 1
            if self.n == 2:
                stop.set()
            return autobisect.Outcome(True, "x")
    before = snapshot(sims)
    res = autobisect.run_auto(sims, StopAfterTwo(), stop=stop, say=lambda *_: None)
    assert res.found is None and "Stopped" in res.message
    assert snapshot(sims) == before


def test_new_error_log_detection(sims):
    before = autobisect.log_snapshot(sims)
    assert autobisect.new_errors(sims, before) == []
    import os, time
    log = sims / "lastException_demo.txt"
    t = time.time() + 5
    os.utime(log, (t, t))
    assert autobisect.new_errors(sims, before) == [log]
    assert autobisect.new_errors(sims, before, match="career_track") == [log]
    assert autobisect.new_errors(sims, before, match="nope") == []
    (sims / "lastUIException_1.txt").write_text("new")
    assert len(autobisect.new_errors(sims, before)) == 2


def test_cli_auto_demo(sims, capsys):
    assert main(["bisect", "auto", "--demo", "--sims-dir", str(sims)]) == 0
    assert "Bad mod: CrashyCareers.ts4script" in capsys.readouterr().out


@pytest.mark.parametrize("scenario,expect_broken,expect_failed", [
    ("crash", True, False), ("errorlog", True, False), ("clean", False, False), ("nostart", False, True),
])
def test_game_runner_watching(sims, monkeypatch, scenario, expect_broken, expect_failed):
    import threading
    import time as _time
    clock = {"t": 1000.0}
    monkeypatch.setattr(autobisect.time, "time", lambda: clock["t"])
    monkeypatch.setattr(autobisect.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(autobisect.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(autobisect.subprocess, "run", lambda *a, **k: None)
    state = {"running": False, "polls": 0}

    def running():
        state["polls"] += 1
        if scenario == "nostart":
            return False
        if state["polls"] == 2:
            state["running"] = True
        if scenario == "crash" and state["polls"] > 6:
            state["running"] = False
        if scenario == "errorlog" and state["polls"] == 5:
            (sims / "lastException_new.txt").write_text("boom")
        return state["running"]

    monkeypatch.setattr(autobisect, "game_running", running)
    r = autobisect.GameRunner(Path("TS4_x64.exe"), minutes=1, startup_minutes=1, log=lambda *_: None)
    o = r.run(sims, threading.Event())
    assert (o.broken, o.failed) == (expect_broken, expect_failed), o.reason


def test_every_problem_type_has_a_plain_explanation():
    import re
    from bettersims import explain
    src = "".join(p.read_text() for p in (Path(__file__).parent.parent / "bettersims" / "rules").glob("*.py"))
    rules = set(re.findall(r'Finding\(\s*"([a-z-]+)"', src))
    assert rules, "found no rules"
    missing = rules - set(explain._TEXT) - {"known-issues"}
    assert not missing, f"no plain explanation for {missing}"


def test_explanations_render_for_demo(sims):
    from bettersims.explain import explain, summary
    _, findings = run_scan(sims)
    for f in findings:
        e = explain(f)
        assert e.wrong and summary(f)
        assert e.options and e.options[-1][0] == "Leave it"
        if f.fixable:
            assert e.fix and e.options[0][0] == "Fix"
