"""Check that a release tag matches the built distribution files.

Usage: python scripts/check_release.py TAG dist/*

Looks at file names only, not archive contents. Exits 1, naming each problem, when
the dist paths do not hold exactly one sdist openpickleball_engine-<version>.tar.gz
and exactly one wheel openpickleball_engine-<version>-py3-none-any.whl, when any
other file is present, when the sdist and wheel versions differ, when the tag does
not start with 'v', or when the tag is not exactly 'v' followed by the version (so a
tag that is not in canonical form, such as v0.3.0-dev1 for 0.3.0.dev1, fails). Exits
1 when given no tag or no dist paths. Exits 0 otherwise. Standard library only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SDIST = re.compile(r"openpickleball_engine-(?P<version>[^-/]+)\.tar\.gz")
WHEEL = re.compile(r"openpickleball_engine-(?P<version>[^-/]+)-py3-none-any\.whl")


def problems(tag: str, paths: list[str]) -> list[str]:
    """Return problem descriptions for a tag and dist paths, empty when they match."""
    if not paths:
        return ["no dist files given"]
    sdists: list[str] = []
    wheels: list[str] = []
    found: list[str] = []
    for name in sorted(Path(path).name for path in paths):
        if match := SDIST.fullmatch(name):
            sdists.append(match["version"])
        elif match := WHEEL.fullmatch(name):
            wheels.append(match["version"])
        else:
            found.append(f"unexpected file: {name}")
    if len(sdists) != 1:
        found.append(f"expected exactly one sdist, found {len(sdists)}")
    if len(wheels) != 1:
        found.append(f"expected exactly one wheel, found {len(wheels)}")
    versions = sorted(set(sdists + wheels))
    if len(sdists) == 1 and len(wheels) == 1 and sdists[0] != wheels[0]:
        found.append(f"sdist version {sdists[0]!r} differs from wheel version {wheels[0]!r}")
    if not tag.startswith("v"):
        found.append(f"tag {tag!r} does not start with 'v'")
    elif len(versions) == 1 and tag != f"v{versions[0]}":
        found.append(f"tag {tag!r} does not match version {versions[0]!r}")
    return found


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("usage: check_release.py TAG DIST_FILE [...]", file=sys.stderr)
        return 1
    tag, paths = args[0], args[1:]
    found = problems(tag, paths)
    if found:
        for problem in found:
            print(f"{tag}: {problem}", file=sys.stderr)
        return 1
    print(f"{tag}: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
