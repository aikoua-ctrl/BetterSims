# BetterSims

A Windows app that scans your Sims 4 Mods folder, finds everything that breaks mods, and fixes it safely.

**Nothing is ever deleted unless you ask.** Every fix moves files into `The Sims 4/BetterSims/Quarantine/` and records each step, so one click (or `bettersims undo`) puts everything back exactly as it was.

## What it finds and fixes

| Problem | Fix |
|---|---|
| Script mods (`.ts4script`) more than 1 folder deep, packages more than 5 deep (the game silently ignores them) | Moves them up to a depth the game loads |
| Mods still inside a `.zip` | Unzips just the mod files, quarantines the zip (`.rar`/`.7z`: flagged) |
| Broken packages (bad header, truncated, 0 bytes, failed downloads) | Quarantine |
| Broken or empty script mods, and script mods compiled for the wrong Python (Sims 4 runs 3.7) | Quarantine |
| Exact duplicate files, even with different names | Keeps the best copy, quarantines the rest |
| Old and new versions of the same mod side by side | Keeps the newest |
| Two script mods shipping the same Python modules with different code | Keeps the newest |
| **Package conflicts**: two packages overriding the same game resource (tuning, objects, CAS, skintones, sliders) with different content | Shows exactly what clashes. You choose: keep the newest, or leave it |
| Error logs (`lastException`, Better Exceptions) | Reads the stack traces and names the mod that crashed. Archives old logs |
| Mods or script mods switched off in `Options.ini` (patches do this) | Turns them back on |
| Missing or broken `Resource.cfg` | Writes the standard one |
| Tray files in Mods, mods in Tray | Moves them where they belong |
| Junk (`Thumbs.db`, `__MACOSX`), empty folders, programs (`.exe`, `.dll`) hiding in Mods | Quarantine / remove |
| Stale caches (`localthumbcache.package` etc.) | Clears them (the game rebuilds them) |
| Your own list of known-bad mods (`BetterSims/known_issues.json`) | Flags or quarantines them |

When nothing points at a specific mod, the **50/50 helper** finds it for you: it turns half your mods off (renaming to `.packageOFF`, same as Sims 4 Mod Manager), you test the game and click "still broken" or "problem gone", and a few rounds later it names the bad mod and turns everything back on.

## Using it

**Desktop app:** double-click `BetterSims.exe`. It finds your Sims 4 folder (including OneDrive Documents), scans, and lists everything with safe fixes pre-ticked. Click **Fix checked**. Use **Undo last fix** if anything looks wrong.

No game installed yet? Click **Try a demo folder** to make a fake, deliberately broken Sims 4 folder and see what it does.

**Command line:**

```
bettersims scan --open                 # scan and open the HTML report
bettersims fix                         # preview the safe fixes (changes nothing)
bettersims fix --apply                 # apply them
bettersims fix --apply --only conflict-1a2b3c4d   # apply a specific one from the report
bettersims undo                        # undo the last fix session
bettersims history                     # list fix sessions
bettersims quarantine "SomeMod.package"           # remove a mod safely
bettersims purge --all --yes           # permanently delete quarantined files
bettersims bisect start | broken | fixed | reset  # 50/50 helper
bettersims demo "C:\Temp\Demo Sims 4"  # build a broken demo folder
```

Close The Sims 4 before applying fixes.

## Building from source

Requires Python 3.10+ (no other dependencies).

```
pip install -e ".[dev]"
pytest
pyinstaller --onefile --windowed --name BetterSims --collect-submodules bettersims run_bettersims.py
```

GitHub Actions runs the tests on Windows and Linux and builds `BetterSims.exe` on every push (see the run's artifacts).

## How it works

- `scanner.py` walks the folder once. `dbpf.py` reads only each package's header and resource index, so thousands of mods scan quickly.
- Each detector in `bettersims/rules/` is a small function that returns findings with proposed actions. Adding a new check means adding one function.
- `fixer.py` applies actions through a journal (`BetterSims/journal/*.json`) and refuses to touch anything outside your Sims 4 folder.

Credits for ported code and ideas are in [NOTICE.md](NOTICE.md). The Sims is a trademark of Electronic Arts. This is an unofficial fan tool.
