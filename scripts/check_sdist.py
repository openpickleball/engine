"""Check that a source distribution holds only the expected top-level entries.

Usage: python scripts/check_sdist.py dist/*.tar.gz

Exits 1, naming each problem, when an sdist has no openpickleball_engine-<version>
root directory, a member outside that root (including one with a '.' or '..'
segment), a member with an empty path segment, a top-level entry outside the
allowed set (with each offending member path), or a missing required entry.
Exits 1 when given no paths. Exits 0 otherwise. Standard library only.
"""

from __future__ import annotations

import sys
import tarfile

# Mirrors `only-include` in pyproject.toml's [tool.hatch.build.targets.sdist],
# plus PKG-INFO and .gitignore, which hatchling always adds. scripts/ ships because
# tests/test_check_sdist.py loads scripts/check_sdist.py, and PARAMETERS.md
# because tests/test_parameter_sets.py reads it. The parameter set files ship
# under src/open_engine/params/, so a top-level params/ is not allowed.
ALLOWED_TOP_LEVEL: frozenset[str] = frozenset(
    {
        "src",
        "tests",
        "scripts",
        "examples",
        "README.md",
        "PARAMETERS.md",
        "LICENSE",
        "CHANGELOG.md",
        "pyproject.toml",
        "PKG-INFO",
        ".gitignore",
    }
)
REQUIRED_TOP_LEVEL: frozenset[str] = frozenset({"src", "pyproject.toml", "PKG-INFO"})
# Hatchling names the sdist root directory openpickleball_engine-<version>.
ROOT_PREFIX = "openpickleball_engine-"


def problems(names: list[str]) -> list[str]:
    """Return problem descriptions for tar member names, empty when clean.

    The root is the first first path component, in member order, that starts with
    ROOT_PREFIX and is a directory prefix of some member. Path problems come
    first, sorted. Then each unexpected top-level entry is followed by its
    offending member paths, sorted. Missing required entries come last.
    """
    if not names:
        return ["archive is empty"]
    root = next(
        (
            first
            for first in (name.split("/", 1)[0] for name in names)
            if first.startswith(ROOT_PREFIX) and any(name.startswith(f"{first}/") for name in names)
        ),
        None,
    )
    if root is None:
        return [f"no root directory starting with {ROOT_PREFIX!r}"]
    path_problems: set[str] = set()
    members: dict[str, set[str]] = {}
    for name in names:
        parts = name.split("/")
        if parts[0] != root:
            path_problems.add(f"member outside root {root!r}: {name}")
            continue
        rest = parts[1:]
        if rest and rest[-1] == "":
            rest = rest[:-1]  # a trailing slash marks a directory entry
        if not rest:
            continue  # the root directory entry itself
        if any(part in (".", "..") for part in rest):
            path_problems.add(f"member not under root {root!r} ('.' or '..' segment): {name}")
            continue
        if "" in rest:
            path_problems.add(f"member has an empty path segment: {name}")
            continue
        members.setdefault(rest[0], set()).add("/".join(rest))
    found = sorted(path_problems)
    for entry in sorted(set(members) - ALLOWED_TOP_LEVEL):
        found.append(f"unexpected top-level entry: {entry}")
        found.extend(f"  unexpected member: {member}" for member in sorted(members[entry]))
    for entry in sorted(REQUIRED_TOP_LEVEL - set(members)):
        found.append(f"missing required entry: {entry}")
    return found


def main(argv: list[str] | None = None) -> int:
    paths = sys.argv[1:] if argv is None else argv
    if not paths:
        print("usage: check_sdist.py SDIST.tar.gz [...]", file=sys.stderr)
        return 1
    failed = False
    for path in paths:
        with tarfile.open(path, "r:gz") as tar:
            names = tar.getnames()
        found = problems(names)
        if found:
            failed = True
            for problem in found:
                print(f"{path}: {problem}", file=sys.stderr)
        else:
            print(f"{path}: ok")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
