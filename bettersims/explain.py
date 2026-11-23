"""Plain-language explanations for every kind of problem.

For each finding: what's wrong (in everyday words), what the fix will do, how safe it is,
and what each choice the user has will actually do.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import PACKAGE_EXT, SCRIPT_EXT, Finding

UNDO = "You can put it back any time from History."


@dataclass
class Explanation:
    wrong: str  # what's broken, in simple words
    fix: str  # what pressing Fix does
    safety: str  # one short line: how risky the fix is
    options: list[tuple[str, str]] = field(default_factory=list)  # (choice, what happens)


def _names(f: Finding, n: int = 2) -> str:
    names = [p.name for p in f.files]
    if not names:
        return "these files"
    return ", ".join(names[:n]) + (f" and {len(names) - n} more" if len(names) > n else "")


def _first(f: Finding) -> str:
    return f.files[0].name if f.files else "this mod"


# rule -> function(finding) -> (wrong, fix, safety, leave-it consequence)
_TEXT = {
    "mods-folder-missing": lambda f: (
        "There's no Mods folder, so the game has nowhere to look for mods.",
        "Creates the Mods folder and the small settings file the game needs inside it.",
        "Safe. Nothing of yours is touched.",
        "No mods will load at all."),
    "resource-cfg": lambda f: (
        "A small settings file in your Mods folder (Resource.cfg) is missing or broken. It tells the game to "
        "look inside your subfolders, so without it some mods in folders may not load.",
        "Writes a fresh, standard copy of that file. If there was an old one, it's kept in quarantine.",
        f"Safe. {UNDO}",
        "Mods inside subfolders might not show up in the game."),
    "too-deep": lambda f: (
        f"{_first(f)} is buried in too many folders. The game stops looking after a certain depth "
        "(1 folder for script mods, 5 for other mods), so this mod is silently not running.",
        "Moves the file up into a folder the game does look in. Nothing is deleted.",
        f"Safe. {UNDO}",
        "The mod stays installed but keeps doing nothing in the game."),
    "tray-in-mods": lambda f: (
        "Saved houses, rooms or Sims (Tray files) are sitting in your Mods folder. The game only shows them in "
        "the gallery when they're in the Tray folder.",
        "Moves them into the Tray folder, where the gallery can find them.",
        f"Safe. {UNDO}",
        "Those lots, rooms or Sims won't appear in your library."),
    "mods-in-tray": lambda f: (
        "Some mods ended up in the Tray folder by mistake. The game never loads mods from there.",
        "Moves them into the Mods folder so they start working.",
        f"Safe. {UNDO}",
        "Those mods stay switched off without you knowing."),
    "archive": lambda f: (
        f"{_first(f)} is still a compressed download (a .zip, .rar or .7z). The game can't open those, so "
        "whatever is inside isn't being used.",
        "For .zip files: takes the mod files out, puts them in a folder next to it, and puts the zip in quarantine. "
        "For .rar/.7z: puts it in quarantine, and you'll need to extract it yourself with 7-Zip.",
        f"Safe. {UNDO}",
        "The mod inside never loads."),
    "junk": lambda f: (
        "Leftover system files (like Thumbs.db or __MACOSX folders) are mixed in with your mods. They do nothing "
        "except make loading a little slower, and the Mac ones can look like broken mods.",
        "Moves them to quarantine.",
        f"Safe. {UNDO}",
        "Nothing bad, just clutter."),
    "dangerous-file": lambda f: (
        f"There's a program file ({_names(f)}) in your Mods folder. Real Sims 4 mods are never programs, so this "
        "probably came bundled with a download and could be harmful. Don't open it.",
        "Moves it to quarantine so it's out of the way. It is not run.",
        f"Safe. {UNDO}",
        "It stays there. The game ignores it, but it's a risk if someone opens it."),
    "empty-file": lambda f: (
        f"{_names(f)} is empty (0 bytes), which means the download failed.",
        "Moves the empty file to quarantine. Download the mod again from the creator to get a working copy.",
        f"Safe. {UNDO}",
        "The game may show errors while trying to load it."),
    "empty-folders": lambda f: (
        "Some folders in Mods are empty.",
        "Deletes the empty folders. They have nothing in them, so nothing is lost.",
        "Safe. Undo recreates them.",
        "Nothing bad, just clutter."),
    "disabled": lambda f: (
        "These mods are switched off (their names end in OFF). That's normal if you or a mod manager turned them off.",
        "", "", "They stay off."),
    "corrupt-package": lambda f: (
        f"{_first(f)} is damaged or isn't really a Sims 4 mod file. Broken mods like this can stop the game "
        "loading or make items invisible.",
        "Moves the broken file to quarantine. Download it again from the creator if you want the mod.",
        f"Safe. {UNDO}",
        "It can cause loading errors, crashes or missing items."),
    "unreadable": lambda f: (
        f"BetterSims couldn't open {_first(f)}. It might be fine, but something is blocking it: it may be open "
        "in another program, held by OneDrive or antivirus, or its folder path is too long.",
        "", "", "Nothing changes. Try closing other programs, or move it to a shorter folder, then scan again."),
    "duplicate": lambda f: (
        f"The exact same mod is installed more than once ({_names(f, 3)}). The game loads it twice, which "
        "slows loading, and for script mods can cause errors.",
        "Keeps one copy and moves the extra copies to quarantine.",
        f"Safe. The copies are identical, so you lose nothing. {UNDO}",
        "Slower loading, and possible errors from the mod running twice."),
    "old-version": lambda f: (
        f"You have more than one version of the same mod ({_names(f, 3)}). Old and new versions together are "
        "one of the most common reasons mods break after an update.",
        "Keeps the newest version and moves the older ones to quarantine.",
        f"Safe. {UNDO}",
        "The old and new versions fight each other, which often causes errors."),
    "conflict": lambda f: (
        f"These mods ({_names(f, 3)}) both change the same thing in the game, in different ways. Only one of "
        "them can win, so the other one is partly broken. Sometimes that's on purpose, for example when one is a "
        "patch made for the other.",
        "Keeps the newest one and moves the others to quarantine.",
        "Your call. Check the mods' download pages first if you're not sure which one you want.",
        "Both stay installed. One of them quietly overrides parts of the other."),
    "corrupt-script": lambda f: (
        f"The script mod {_first(f)} is damaged or empty inside, so it can't run. The game will show errors "
        "about it.",
        "Moves it to quarantine. Download it again from the creator if you want it.",
        f"Safe. {UNDO}",
        "The game keeps throwing errors about this mod."),
    "risky-script": lambda f: (
        f"{_first(f)} is a script mod that can reach outside the game (for example, run programs or use the "
        "internet). Popular mods sometimes do this for good reasons, like checking for updates.",
        "Moves it to quarantine.",
        "Your call. Keep it if it's from a creator you trust.",
        "Nothing changes. It keeps working as before."),
    "wrong-python": lambda f: (
        f"{_first(f)} was made for a different version of the game's scripting system, so the game can't run it. "
        "It's probably outdated or not made for Sims 4.",
        "Moves it to quarantine. Look for an updated version on the creator's page.",
        f"Your call, but it isn't working anyway. {UNDO}",
        "It stays installed and does nothing, or causes errors."),
    "script-conflict": lambda f: (
        f"These script mods ({_names(f, 3)}) contain pieces of code with the same name. Only one copy can load, "
        "so one of the mods breaks. If they're two versions of the same mod, the older one should go.",
        "Keeps the newest one and moves the others to quarantine.",
        ("Safe when they're two versions of the same mod. " if f.auto else
         "Your call: these look like different mods, so decide which one you'd rather keep. ") + UNDO,
        "One of them keeps breaking the other, usually with error pop-ups."),
    "crash-culprit": lambda f: (
        f"The game's own error reports point at {_first(f)}. It's probably broken by a game update, outdated, or "
        "missing another mod it needs.",
        "Moves the mod to quarantine so the game stops loading it. Check the creator's page for an update or a "
        "required mod.",
        f"Your call. {UNDO}",
        "The errors or crashes are likely to keep happening."),
    "error-logs": lambda f: (
        "The game saved error reports (lastException files). They don't hurt anything, they're just a record of "
        "what went wrong.",
        "Moves the old reports to quarantine, so next time it's easy to tell whether there are new errors.",
        f"Safe. {UNDO}",
        "Nothing bad. Old reports stay and can make new ones harder to spot."),
    "options-ini": lambda f: (
        "Mods are switched off in the game's own settings. Game updates do this automatically, which is why mods "
        "often 'stop working' after a patch.",
        "Switches custom content and script mods back on in the settings file. Your other settings stay the same. "
        "Close the game first.",
        f"Safe. The old settings file is kept. {UNDO}",
        "None of your mods will load until you switch them on in Game Options."),
    "cache": lambda f: (
        "These are temporary files the game makes to load faster. Old ones can cause wrong thumbnails, missing CC "
        "in Create a Sim, and slow loading after you add or remove mods.",
        "Moves them to quarantine. The game makes fresh ones the next time it starts.",
        "Safe. The game rebuilds them by itself.",
        "Nothing changes. Usually fine unless you're seeing odd thumbnails or missing CC."),
    "known-issue": lambda f: (
        f"{_first(f)} is on your list of known problem mods.",
        "Moves it to quarantine.",
        f"Safe. {UNDO}",
        "It stays installed."),
}


def explain(f: Finding) -> Explanation:
    fn = _TEXT.get(f.rule)
    if fn is None:
        return Explanation(f.detail, f.fix_label, "", [])
    wrong, fix, safety, leave = fn(f)
    options: list[tuple[str, str]] = []
    if f.fixable and fix:
        options.append(("Fix", fix))
    has_mod_files = any(p.suffix.lower() in (PACKAGE_EXT, SCRIPT_EXT, ".packageoff", ".ts4scriptoff")
                        for p in f.files)
    if has_mod_files and f.rule not in ("too-deep", "mods-in-tray"):
        options.append(("Remove mod files", "Moves every mod file listed here to quarantine, so the game stops "
                        f"loading them. {UNDO}"))
    options.append(("Leave it", leave))
    return Explanation(wrong, fix, safety, options)


def summary(f: Finding) -> str:
    """One short sentence for the problem card."""
    e = explain(f)
    first = e.wrong.split(". ")[0].rstrip(".") + "."
    return first
