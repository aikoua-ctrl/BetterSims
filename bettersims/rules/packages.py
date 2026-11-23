"""Package problems: corrupt files, duplicates, old versions, resource conflicts."""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from .. import dbpf
from ..model import Action, FileEntry, Finding, Severity
from ..scanner import Inventory


def check_corrupt_packages(inv: Inventory) -> list[Finding]:
    out: list[Finding] = []
    for f in inv.packages():
        if f.size == 0:
            continue  # reported by empty-file rule
        pkg = inv.package(f)
        if isinstance(pkg, OSError):
            out.append(unreadable(f, pkg))
        elif isinstance(pkg, Exception):
            out.append(
                Finding(
                    "corrupt-package",
                    Severity.CRITICAL,
                    f"Broken package: {f.path.name}",
                    f"It can't be read ({pkg}). Broken packages can stop the game loading or cause "
                    "missing/invisible items. Re-download it from the creator.",
                    [f.path],
                    fix_label="Quarantine it",
                    actions=[Action("quarantine", f.path)],
                )
            )
        elif not pkg.entries:
            out.append(
                Finding(
                    "corrupt-package",
                    Severity.INFO,
                    f"Empty package: {f.path.name}",
                    "It contains no resources, so it does nothing.",
                    [f.path],
                    fix_label="Quarantine it",
                    actions=[Action("quarantine", f.path)],
                )
            )
    return out


def unreadable(f: FileEntry, err: OSError) -> Finding:
    """A file we couldn't open is not necessarily broken, so it is never auto-removed."""
    hint = ("Its full path is very long; Windows may refuse paths over 260 characters. Move it to a "
            "shorter folder." if len(str(f.path)) > 240 else
            "It may be open in another program, or blocked by antivirus or OneDrive syncing.")
    return Finding(
        "unreadable",
        Severity.WARNING,
        f"Can't open {f.path.name}",
        f"BetterSims couldn't read this file ({err.strerror or err}). {hint}",
        [f.path],
    )


def names_title(files: list[FileEntry]) -> str:
    names = [f.path.name for f in files]
    return " vs ".join(names) if len(names) <= 3 else f"{' vs '.join(names[:2])} and {len(names) - 2} more"


def _keeper(files: list[FileEntry]) -> FileEntry:
    """Which copy to keep: newest, then the one in the shallowest folder, then the shortest name."""
    return sorted(files, key=lambda f: (-f.mtime, f.depth, len(f.path.name), str(f.path)))[0]


def check_exact_duplicates(inv: Inventory) -> list[Finding]:
    by_size: dict[tuple[str, int], list[FileEntry]] = defaultdict(list)
    for f in inv.mods_files:
        if f.size > 0 and f.ext in (".package", ".ts4script") and not f.is_junk:
            by_size[(f.ext, f.size)].append(f)
    out: list[Finding] = []
    for group in by_size.values():
        if len(group) < 2:
            continue
        by_hash: dict[str, list[FileEntry]] = defaultdict(list)
        for f in group:
            by_hash[f.sha1()].append(f)
        for dupes in by_hash.values():
            if len(dupes) < 2:
                continue
            keep = _keeper(dupes)
            rest = [f for f in dupes if f is not keep]
            out.append(
                Finding(
                    "duplicate",
                    Severity.WARNING,
                    f"{len(dupes)} identical copies of {keep.path.name}",
                    "The same file is installed more than once. Duplicates waste load time and "
                    "duplicate script mods can run twice and cause errors.",
                    [f.path for f in dupes],
                    fix_label=f"Keep {keep.rel}, quarantine the other {len(rest)}",
                    actions=[Action("quarantine", f.path) for f in rest],
                )
            )
    return out


_VERSION_RE = re.compile(
    r"""(
        [\s_\-.]*\(\d+\)$            |   # "mod (1)"
        [\s_\-.]*-?\s*copy(\s*\d+)?$ |   # "mod - Copy"
        [\s_\-.]+(old|new|backup|bak|latest|updated)$ |   # "mod_old"
        [\s_\-.]*v?\d+([._]\d+)+[a-z]?$ |  # "mod_v1.2.3", "mod 2024.6"
        [\s_\-.]*v\d+[a-z]?$          |   # "mod_v3"
        [\s_\-.]*(19|20)\d{2}[\-_.]?\d{2}([\-_.]?\d{2})?$  # dates
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def base_name(stem: str) -> str:
    prev = None
    s = stem.strip()
    while prev != s:
        prev = s
        s = _VERSION_RE.sub("", s).strip()
    return re.sub(r"[\s_\-.]+", "", s).lower()


def _key_set(inv: Inventory, f: FileEntry) -> set[dbpf.ResourceKey]:
    pkg = inv.package(f)
    return set() if isinstance(pkg, Exception) else {e.key for e in pkg.entries}


def _module_set(inv: Inventory, f: FileEntry) -> set[str]:
    s = inv.script(f)
    return set() if isinstance(s, Exception) else {m.rsplit(".", 1)[0] for m in s.members}


def check_old_versions(inv: Inventory) -> list[Finding]:
    groups: dict[tuple[str, str], list[FileEntry]] = defaultdict(list)
    for f in inv.mods_files:
        # Script versions are caught by the script-conflict rule, with a better explanation.
        if f.ext == ".package" and f.size > 0 and not f.is_junk:
            b = base_name(f.path.stem)
            if b:
                groups[(f.ext, b)].append(f)
    out: list[Finding] = []
    for (ext, _), files in groups.items():
        hashes = {f.sha1() for f in files}
        if len(files) < 2 or len(hashes) < 2:
            continue  # identical copies are reported as duplicates
        # Only call it "another version" when the files really overlap in content.
        sets = [(_key_set(inv, f) if ext == ".package" else _module_set(inv, f)) for f in files]
        nonempty = [s for s in sets if s]
        if len(nonempty) < 2:
            continue
        common = set.intersection(*nonempty)
        if len(common) < 0.5 * min(len(s) for s in nonempty):
            continue
        keep = _keeper(files)
        rest = [f for f in files if f is not keep]
        out.append(
            Finding(
                "old-version",
                Severity.WARNING,
                f"{len(files)} versions of the same mod: {keep.path.name}",
                "These look like different versions of one mod (same name, overlapping content). "
                "Having old and new versions together is a top cause of errors after updates.",
                [f.path for f in files],
                fix_label=f"Keep the newest ({keep.rel}), quarantine {len(rest)} older",
                actions=[Action("quarantine", f.path) for f in rest],
            )
        )
    return out


CATEGORY_LABEL = {
    "object": "objects/build-buy",
    "skintone": "skintones",
    "cas": "CAS (clothes, hair, accessories)",
    "slider": "sliders",
    "tuning": "gameplay tuning",
    "other": "other resources",
}


def check_resource_conflicts(inv: Inventory) -> list[Finding]:
    owners: dict[dbpf.ResourceKey, list[FileEntry]] = defaultdict(list)
    for f in inv.packages():
        pkg = inv.package(f)
        if isinstance(pkg, Exception):
            continue
        seen: set[dbpf.ResourceKey] = set()
        for e in pkg.entries:
            if e.key.type in dbpf.NOISE_TYPES or e.key in seen:
                continue
            seen.add(e.key)
            owners[e.key].append(f)

    # Group clashing keys by the exact set of files involved.
    clashes: dict[frozenset[Path], dict[str, list[dbpf.ResourceKey]]] = defaultdict(lambda: defaultdict(list))
    file_by_path: dict[Path, FileEntry] = {}
    for key, files in owners.items():
        if len(files) < 2:
            continue
        # Identical bytes in every file = harmless shared resource, not a conflict.
        hashes = set()
        for f in files:
            pkg = inv.package(f)
            hashes.add(pkg.chunk_hash(pkg.entry(key)))  # type: ignore[union-attr]
        if len(hashes) == 1:
            continue
        for f in files:
            file_by_path[f.path] = f
        cat = dbpf.CATEGORY_BY_TYPE.get(key.type, "other")
        clashes[frozenset(f.path for f in files)][cat].append(key)

    out: list[Finding] = []
    for paths, by_cat in clashes.items():
        files = [file_by_path[p] for p in sorted(paths)]
        serious = [c for c in by_cat if c != "other"]
        total = sum(len(v) for v in by_cat.values())
        summary = ", ".join(f"{len(v)} {CATEGORY_LABEL[c]}" for c, v in sorted(by_cat.items()))
        examples = []
        for c, keys in by_cat.items():
            for k in keys[:3]:
                examples.append(f"{dbpf.type_name(k.type)} {k}")
        keep = _keeper(files)
        rest = [f for f in files if f is not keep]
        out.append(
            Finding(
                "conflict",
                Severity.WARNING if serious else Severity.INFO,
                f"Conflict: {names_title(files)}",
                f"These packages change the same {total} game resource(s) ({summary}) in different ways, "
                "so only one version wins and the other mod is partly broken. Sometimes this is on "
                "purpose (a patch or add-on for another mod). Check the creators' pages if unsure. "
                f"Examples: {'; '.join(examples[:5])}.",
                [f.path for f in files],
                fix_label=f"Keep the newest ({keep.path.name}), quarantine the other {len(rest)}",
                actions=[Action("quarantine", f.path) for f in rest],
                auto=False,
            )
        )
    return out


RULES = [check_corrupt_packages, check_exact_duplicates, check_old_versions, check_resource_conflicts]
