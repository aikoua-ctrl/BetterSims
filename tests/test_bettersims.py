import json
import zipfile
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
    ("BetterBuild_v1.3", "betterbuild"), ("Mod_2025-01-15", "mod"), ("Plain", "plain"),
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
    assert left <= {"conflict", "crash-culprit", "disabled"}
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
