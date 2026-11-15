"""Check that a wheel holds only the open_engine package and its dist-info.

Usage: python scripts/check_wheel.py dist/*.whl

Exits 1, naming each problem, when a wheel has:
- no openpickleball_engine-<version>.dist-info directory with a non-empty version,
  or more than one;
- a member name that appears more than once;
- a member that is neither an allowed open_engine file nor a file under that
  dist-info directory, including one with a '.', '..', or empty path segment;
- a directory entry other than open_engine/ and the dist-info directory itself;
- a missing allowed open_engine file (py.typed among them), or a dist-info
  without METADATA, WHEEL, or RECORD.
Exits 1 when given no paths. Exits 0 otherwise. Standard library only.

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
# Files the wheel spec requires in every dist-info directory.
DIST_INFO_FILES: frozenset[str] = frozenset({"METADATA", "WHEEL", "RECORD"})


def _is_dist_info(first: str) -> bool:
    return (
        first.startswith(DIST_INFO_PREFIX)
        and first.endswith(DIST_INFO_SUFFIX)
        and len(first) > len(DIST_INFO_PREFIX) + len(DIST_INFO_SUFFIX)
    )


def problems(names: list[str]) -> list[str]:
    """Return problem descriptions for wheel member names, empty when clean.

    Duplicate members come first, then unexpected members, then missing package
    files, then missing dist-info files, each group sorted. A missing or
    repeated dist-info directory is reported alone, since members cannot be
    judged without it.
    """
    dist_infos = sorted(
        {
            first
            for first, sep, _ in (name.partition("/") for name in names)
            if sep and _is_dist_info(first)
        }
    )
    if not dist_infos:
        return [f"no dist-info directory starting with {DIST_INFO_PREFIX!r}"]
    if len(dist_infos) > 1:
        return [f"more than one dist-info directory: {', '.join(dist_infos)}"]
    dist_info = dist_infos[0]
    seen: set[str] = set()
    duplicates: set[str] = set()
    unexpected: set[str] = set()
    present: set[str] = set()
    dist_info_present: set[str] = set()
    for name in names:
        if name in seen:
            duplicates.add(name)
            continue
        seen.add(name)
        if name.endswith("/"):
            if name not in (f"{PACKAGE}/", f"{dist_info}/"):
                unexpected.add(name)
            continue
        parts = name.split("/")
        if any(part in ("", ".", "..") for part in parts):
            unexpected.add(name)
        elif parts[0] == dist_info and len(parts) > 1:
            dist_info_present.add("/".join(parts[1:]))
        elif parts[0] == PACKAGE and len(parts) == 2 and parts[1] in PACKAGE_FILES:
            present.add(parts[1])
        else:
            unexpected.add(name)
    found = [f"duplicate member: {name}" for name in sorted(duplicates)]
    found.extend(f"unexpected member: {name}" for name in sorted(unexpected))
    found.extend(
        f"missing required member: {PACKAGE}/{file}" for file in sorted(PACKAGE_FILES - present)
    )
    found.extend(
        f"missing required member: {dist_info}/{file}"
        for file in sorted(DIST_INFO_FILES - dist_info_present)
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
