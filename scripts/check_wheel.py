"""Check that a wheel holds only the open_engine package and its dist-info.

Usage: python scripts/check_wheel.py dist/*.whl

Exits 1, naming each problem, when a wheel has a member that is neither an
allowed open_engine file nor under the one openpickleball_engine-<version>.dist-info
directory (including a member with a '.', '..', or empty path segment), is missing
an allowed open_engine file (py.typed among them), or has no such dist-info
directory or more than one. Exits 1 when given no paths. Exits 0 otherwise.
Standard library only.

Add a new module in src/open_engine/ to PACKAGE_FILES, or this check fails.
"""

from __future__ import annotations

import sys
import zipfile

PACKAGE = "open_engine"
# Every file the wheel ships from src/open_engine/. All of them are required.
PACKAGE_FILES: frozenset[str] = frozenset(
    {"__init__.py", "_version.py", "engine.py", "winprob.py", "py.typed"}
)
# Hatchling names the metadata directory openpickleball_engine-<version>.dist-info.
DIST_INFO_PREFIX = "openpickleball_engine-"
DIST_INFO_SUFFIX = ".dist-info"


def problems(names: list[str]) -> list[str]:
    """Return problem descriptions for wheel member names, empty when clean.

    Directory entries (a trailing slash) are skipped. Unexpected members come
    first, sorted, then missing package files, sorted. A missing or repeated
    dist-info directory is reported alone, since members cannot be judged
    without it.
    """
    dist_infos = sorted(
        {
            first
            for first, sep, _ in (name.partition("/") for name in names)
            if sep and first.startswith(DIST_INFO_PREFIX) and first.endswith(DIST_INFO_SUFFIX)
        }
    )
    if not dist_infos:
        return [f"no dist-info directory starting with {DIST_INFO_PREFIX!r}"]
    if len(dist_infos) > 1:
        return [f"more than one dist-info directory: {', '.join(dist_infos)}"]
    dist_info = dist_infos[0]
    unexpected: set[str] = set()
    present: set[str] = set()
    for name in names:
        parts = name.split("/")
        if len(parts) > 1 and parts[-1] == "":
            parts = parts[:-1]  # a trailing slash marks a directory entry
            if parts[0] in (PACKAGE, dist_info) and all(
                part not in ("", ".", "..") for part in parts
            ):
                continue
        if any(part in ("", ".", "..") for part in parts):
            unexpected.add(name)
        elif parts[0] == dist_info and len(parts) > 1:
            continue
        elif parts[0] == PACKAGE and len(parts) == 2 and parts[1] in PACKAGE_FILES:
            present.add(parts[1])
        else:
            unexpected.add(name)
    found = [f"unexpected member: {name}" for name in sorted(unexpected)]
    found.extend(
        f"missing required member: {PACKAGE}/{file}" for file in sorted(PACKAGE_FILES - present)
    )
    return found


def main(argv: list[str] | None = None) -> int:
    paths = sys.argv[1:] if argv is None else argv
    if not paths:
        print("usage: check_wheel.py WHEEL.whl [...]", file=sys.stderr)
        return 1
    failed = False
    for path in paths:
        with zipfile.ZipFile(path) as wheel:
            names = wheel.namelist()
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
