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

When nothing points at a specific mod, the **50/50 helper** finds it for you: it turns half your mods off (renaming to `.packageOFF`, same as Sims 4 Mod Manager), you test the game and click "still broken" or "problem gone", and a few rounds later it names the bad mod and turns everything back on. A mod's `.package` and `.ts4script` are always switched together, and when the search narrows down to one big folder (like a "CC" folder) it carries on inside it.

### Automatic 50/50

Switch the 50/50 finder to **Automatic** and BetterSims runs the rounds itself: it turns half the suspects off, starts The Sims 4, and watches for a crash or a new error log (lastException, Better Exceptions, MCCC). If neither happens within the time you pick (3, 5 or 10 minutes), that half is clean. It closes the game and continues until one mod is left, then double-checks by running once with only that mod off.

- It first runs with all mods on. If the problem doesn't show up automatically there, it stops instead of guessing.
- "Only count errors mentioning" narrows it to your specific error. "Launches per round" helps with problems that don't happen every time.
- It can only see crashes and errors while the game loads. Problems that need you to load a save and play (broken interactions, invisible CC, visual glitches) still need the manual mode.
- Stop works at any time, and every mod is always turned back on at the end.
- Command line: `bettersims bisect auto [--minutes 5] [--match TEXT] [--tries 2] [--game-exe PATH]`, or `--demo` to simulate the game on the demo folder.

### Auto Repair (all-in-one)

The **Auto Repair** page does everything in one go and keeps going until the game runs clean:

1. Scans your Mods folder and applies every safe fix (plus clearing the game's cache, which you can switch off).
2. Starts the game and watches it. If there's no crash and no new error log, it's done.
3. The first time the game has a problem, it runs once more with every mod off. If the problem is still there, it isn't a mod, and it stops and tells you so instead of removing innocent mods.
4. Runs the automatic 50/50 search to find the mod that breaks the game, and moves it to quarantine.
5. Goes back to step 2, until the game runs clean or it has removed the most mods you allowed (3, 5 or 10).

Everything it does goes into one fix session, so a single **Undo** in History puts every file back. It has the same limit as the automatic 50/50: it sees crashes and errors while the game loads, not problems that only show up once you're playing.

Command line: `bettersims repair [--max-removals 5] [--minutes 5] [--tries 2] [--keep-cache] [--game-exe PATH]`, or `--demo` to simulate the game on the demo folder.

### What gets fixed automatically and what waits for you

Pre-ticked (safe): folder depth, zipped mods, broken and empty files, exact duplicates, old versions of the same mod, two versions of the same script mod, Options.ini, Resource.cfg, misplaced/junk/program files.

Your call (not pre-ticked): package conflicts between different mods (one may be a patch for the other), script mods from different creators that clash, mods blamed by error logs, mods built for a different Python version, clearing caches and archiving error logs.

BetterSims refuses to apply fixes while The Sims 4 is running, and won't touch anything outside your Sims 4 folder. Files it can't open (locked, too-long path) are reported, never removed.

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
bettersims bisect auto                 # automatic 50/50 (launches the game itself)
bettersims repair                      # Auto Repair: fix, test, remove bad mods, repeat
bettersims demo "C:\Temp\Demo Sims 4"  # build a broken demo folder
```

Close The Sims 4 before applying fixes.

## Building from source

Requires Python 3.10+. The desktop app uses [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) for its dark theme; the scanner itself has no dependencies.

```
pip install -e ".[dev]"
pytest
pyinstaller --onefile --windowed --name BetterSims --collect-submodules bettersims --collect-data customtkinter run_bettersims.py
```

GitHub Actions runs the tests on Windows and Linux and builds `BetterSims.exe` on every push (see the run's artifacts).

## How it works

- `scanner.py` walks the folder once. `dbpf.py` reads only each package's header and resource index, so thousands of mods scan quickly.
- Each detector in `bettersims/rules/` is a small function that returns findings with proposed actions. Adding a new check means adding one function.
- `fixer.py` applies actions through a journal (`BetterSims/journal/*.json`) and refuses to touch anything outside your Sims 4 folder.

Credits for ported code and ideas are in [NOTICE.md](NOTICE.md). The Sims is a trademark of Electronic Arts. This is an unofficial fan tool.
