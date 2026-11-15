"""Check that a source distribution holds only the expected top-level entries.

Usage: python scripts/check_sdist.py dist/*.tar.gz

Exits 1, naming each problem, when an sdist has a top-level entry outside the
allowed set, a member outside its single root directory, or a missing required
entry. Exits 1 when given no paths. Exits 0 otherwise. Standard library only.
"""

from __future__ import annotations

import sys
import tarfile

# Mirrors `only-include` in pyproject.toml's [tool.hatch.build.targets.sdist],
# plus PKG-INFO and .gitignore, which hatchling always adds.
ALLOWED_TOP_LEVEL: frozenset[str] = frozenset(
    {
        "src",
        "tests",
        "examples",
        "README.md",
        "LICENSE",
        "CHANGELOG.md",
        "pyproject.toml",
        "PKG-INFO",
        ".gitignore",
    }
)
REQUIRED_TOP_LEVEL: frozenset[str] = frozenset({"src", "pyproject.toml", "PKG-INFO"})


def problems(names: list[str]) -> list[str]:
    """Return sorted problem descriptions for tar member names, empty when clean."""
    if not names:
        return ["archive is empty"]
    root = names[0].split("/", 1)[0]
    found: list[str] = []
    top_level: set[str] = set()
    for name in names:
        parts = name.split("/")
        if parts[0] != root:
            found.append(f"member outside root {root!r}: {name}")
            continue
        if len(parts) < 2 or parts[1] == "":
            continue
        top_level.add(parts[1])
    for entry in top_level - ALLOWED_TOP_LEVEL:
        found.append(f"unexpected top-level entry: {entry}")
    for entry in REQUIRED_TOP_LEVEL - top_level:
        found.append(f"missing required entry: {entry}")
    return sorted(set(found))


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
