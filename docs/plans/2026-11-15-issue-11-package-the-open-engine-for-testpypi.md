# Plan: Package the OPEN engine for TestPyPI (issue #11)

Risk tier: Medium. Packaging, CI matrix, and version plumbing across about ten files. It changes a public contract (dropping Python 3.10 is breaking for installers on 3.10) and moves where the version is defined, but rating behavior and every rating output stay the same. The real TestPyPI install can only be checked after merge and tag, so a unit test cannot fully cover it.

Domain skills: test-driven-development: invoke (the wheel check script, the README quickstart test, and the version source test are written first). All others not applicable because the change has no framework, UI, API, data, auth, payments, or email surface.

Decisions made with the maintainer before this plan:
- This PR is also the release PR for the first TestPyPI upload: version `0.3.0.dev0`. Only tag `v0.1.0` exists, `0.2.0` was never tagged, and `main` has breaking changes after the `0.2.0` CHANGELOG entry, so publishing `main` as `0.2.0` would mislabel it.
- The PR says `Refs #11`, not `Closes #11`. The maintainer closes #11 after the tagged upload installs from TestPyPI.

Decisions made in this plan:
- Single source: the version lives in a new `src/open_engine/_version.py` (`__version__ = "0.3.0.dev0"`). `__init__.py` re-exports it, `engine.py` uses it as the `Config.version` default, and hatchling reads it (`[tool.hatch.version] path`). `Config.version` stays a field (it is in the fingerprint and `ReplayInfo`); only its default's source moves. `engine.py` cannot import from `__init__.py` (circular), which is why the new module exists.
- Python 3.10 is end of life (October 2026). Drop it as the issue asks.
- Ruff with `target-version = "py311"` reports UP042 (`str, Enum` to `StrEnum`) on `RatingFormat`, `MatchType`, `ScoringType`. `StrEnum` changes `str()` and `format()` of members, which can change printed output and anything that stringifies an enum. Do not convert. Add `ignore = ["UP042"]` to `[tool.ruff.lint]` with a comment saying why. UP017 (`timezone.utc` to `datetime.UTC`) is the same object: apply the fix in `engine.py` and `tests/test_engine.py`.
- Dev extras: add `twine` so contributors can run the same metadata check as CI. Do not add `hypothesis`: no test uses it, and an unused tool in the extras is noise. Note this in the PR.
- The README gets an install section pointing to TestPyPI, pinned to the version. Contributor setup from a local checkout stays.

## Acceptance criteria

1. Version decided and single-sourced. `src/open_engine/_version.py` holds `__version__ = "0.3.0.dev0"` and is the only file in `src/` that contains the literal `0.3.0.dev0` (and no other version literal). `open_engine.__version__`, `Config().version`, and the built sdist and wheel file names all read `0.3.0.dev0`. `[tool.hatch.version] path` is `src/open_engine/_version.py`. `_version` is not added to `__all__`.
2. `requires-python = ">=3.11"`; the 3.10 classifier is removed (3.11, 3.12, 3.13 remain); ruff `target-version = "py311"`; the `test.yml` matrix is `["3.11", "3.12", "3.13"]`; the `release.yml` gate comment says 3.11 to 3.13; `ruff check .` passes with only UP042 ignored.
3. The sdist and wheel contain only intended files, and `py.typed` is in the wheel. A new `scripts/check_wheel.py WHEEL...` (standard library only, typed) exits 0 and prints `<path>: ok` when every member of each wheel is either `open_engine/<name>` from the allowed set (`__init__.py`, `_version.py`, `engine.py`, `winprob.py`, `py.typed`) or a file under one `openpickleball_engine-<version>.dist-info/` directory with a non-empty version; it exits 1 naming each problem when a member is outside both, when a member name appears more than once, when a directory entry is anything but `open_engine/` or the dist-info directory itself, when `open_engine/py.typed` or any allowed module is missing, when the dist-info lacks `METADATA`, `WHEEL`, or `RECORD`, when there is no such dist-info directory or more than one, or when given no paths. CI's `package` job and the release `build` job run it on `dist/*.whl` right after `check_sdist.py`. `scripts/check_sdist.py` is unchanged (`_version.py` is under `src/`).
4. `twine check --strict dist/*` passes on a local build (already enforced in CI; recorded as evidence).
5. TestPyPI install: proven before merge by installing the built wheel into a new, empty venv (no repo on `sys.path`) and importing it. After merge the maintainer tags `v0.3.0.dev0` and runs `pip install -i https://test.pypi.org/simple/ openpickleball-engine==0.3.0.dev0` in a clean venv; that step is in the PR body as a post-merge check, and #11 stays open until it passes.
6. The README quickstart runs unchanged against the installed package. A new test, `tests/test_readme.py`, extracts the first ```` ```python ```` block under `## Quick start` in `README.md` and runs it with `exec`, asserting it raises nothing. The live drive runs the same block in the clean venv from criterion 5.
7. `README.md`: an "Install" section with `pip install -i https://test.pypi.org/simple/ openpickleball-engine==0.3.0.dev0`, a sentence that it is a dev release on the test index (not PyPI) and that the engine is still a demonstration; "It requires Python 3.11 or later"; the demonstration warning stays where it is. The contributor setup still installs from a local checkout. `tests/test_readme.py` asserts the install line pins exactly `open_engine.__version__`, so a version bump that misses the README fails the gate.
8. `CHANGELOG.md`: the current `## [Unreleased]` entries move under `## [0.3.0.dev0] - 2026-11-15`, plus new entries: Python 3.11 or later required (**Breaking**), version single-sourced in `_version.py`, `check_wheel.py` in CI and release, `twine` in the dev extra, and `Config.version` and `__version__` are `0.3.0.dev0`. `## [Unreleased]` is left empty. Link references updated: `[Unreleased]` compares `v0.3.0.dev0...HEAD`, `[0.3.0.dev0]` compares `v0.1.0...v0.3.0.dev0`, because `v0.2.0` was never tagged. The `0.2.0` heading is unlinked (`## 0.2.0 - 2026-11-15`), its link reference is removed, and one line under it says 0.2.0 was never tagged or published.
9. `docs/releasing.md` step 1 names one place to set the version, `src/open_engine/_version.py`, says the README install line is updated to the same version and that `tests/test_readme.py` checks it, and says every release, dev releases included, moves the `## [Unreleased]` entries under a new version section and updates the compare links. "What the workflow does" lists the `check_wheel.py` step. `CLAUDE.md` Dev tools names `twine`. `CLAUDE.md` Stack and Versions bullets say Python 3.11 or later, CI tests 3.11 to 3.13, and that the version lives in `_version.py`; the file list names `scripts/check_wheel.py`.
10. The gate passes: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.

## Files that change

- `src/open_engine/_version.py`: new, docstring plus `__version__`.
- `src/open_engine/__init__.py`: `from ._version import __version__`, remove the literal.
- `src/open_engine/engine.py`: `from ._version import __version__`; `version: str = __version__`; UP017 fix.
- `pyproject.toml`: `requires-python`, classifiers, ruff target and UP042 ignore, hatch version path, `twine` in `dev`.
- `.github/workflows/test.yml`: matrix; `package` job step `python scripts/check_wheel.py dist/*.whl`.
- `.github/workflows/release.yml`: gate comment; `build` job step running `check_wheel.py` after `check_sdist.py`.
- `scripts/check_wheel.py`: new.
- `tests/test_check_wheel.py`: new.
- `tests/test_readme.py`: new.
- `tests/test_engine.py`: UP017 fix; new test that `_version.__version__` is what `__init__` and `Config` read.
- `README.md`, `CHANGELOG.md`, `docs/releasing.md`, `CLAUDE.md`.

## Order of work

1. Plan commit (this file).
2. Python 3.11: `pyproject.toml` (requires-python, classifiers, ruff target, UP042 ignore with comment), UP017 fixes, `test.yml` matrix, `release.yml` comment. Gate. Commit.
3. TDD: `tests/test_check_wheel.py` (synthetic wheels via `zipfile` in `tmp_path`, mirroring `tests/test_check_sdist.py`'s loading of the script by path). Red. Write `scripts/check_wheel.py`. Green. Add the step to both workflows. Commit.
4. TDD: the version source test (`open_engine._version.__version__ == open_engine.__version__ == Config().version`) fails on import of `_version`. Add `_version.py`, wire `__init__`, `engine`, hatch path, bump to `0.3.0.dev0`. Commit.
5. TDD: `tests/test_readme.py` red on a deliberately absent section is not useful; instead write it against the current README (it passes, which is the point: it guards the "unchanged" criterion) and confirm it fails when the block is broken by temporarily corrupting a copy (point the helper at a `tmp_path` README in a second test that asserts a broken block raises). Commit.
6. `twine` in dev extra. README, CHANGELOG, `docs/releasing.md`, `CLAUDE.md`. Commit.
7. Full gate, build (`python -m build --installer uv` locally, because this machine lacks `ensurepip`; CI uses `--no-isolation`), `check_sdist.py`, `check_wheel.py`, `twine check --strict`, live drive.

## Risks

- `Config.version` default now comes from an import. Its value is the same string as `__version__`, so fingerprints for the same version are unchanged; the bump to `0.3.0.dev0` does change `Config.version` and so every fingerprint, which is expected for a release and recorded in the CHANGELOG.
- Dropping 3.10 is breaking for anyone installing on 3.10. Acceptable before 1.0.0 and called out as **Breaking**.
- The UP042 ignore hides a lint suggestion forever. The comment explains why; converting to `StrEnum` is a separate decision with its own changelog entry.
- The README install command is wrong until the tag is pushed and the upload succeeds. The window is merge to tag, which the maintainer controls.
- `check_wheel.py`'s allowed module list must be updated when a module is added to `open_engine`. The script's docstring and `CLAUDE.md` say so, as with `ALLOWED_TOP_LEVEL` in `check_sdist.py`.
- Riskiest step: the version move, because `hatchling` must still find `__version__` by regex in the new path. The build in step 7 proves it.

## Proof

- Criterion 1: `tests/test_engine.py` version source test; existing `test_config_version_matches_package_version`; built file names in step 7; `grep -rn "0\.3\.0\.dev0\|0\.2\.0" src` shows one hit.
- Criterion 2: `grep` of `pyproject.toml` and workflows; `ruff check .`.
- Criterion 3: `tests/test_check_wheel.py`; `check_wheel.py` and `check_sdist.py` on the real build.
- Criterion 4: `twine check --strict` output.
- Criterion 5 and 6 (live drive): new empty venv outside the repo, `pip install` the built wheel, `python -c "import open_engine, sys; print(open_engine.__version__, open_engine.__file__)"` shows `0.3.0.dev0` and a `site-packages` path, then run the README quickstart block extracted to a file, from a directory outside the repo. Edge path: the same venv with Python 3.10 refuses to install (`requires-python`), if a 3.10 interpreter is available.
- Criterion 6: `tests/test_readme.py`.
- Criteria 7 to 10: diff review and the gate output.

Measured on `main` (1a624ab): the README quickstart block, extracted and run with `exec`, prints the deltas and the player summary with no error (`config_version` `0.2.0`). The wheel built from `main` holds `open_engine/__init__.py`, `engine.py`, `py.typed`, `winprob.py` and the dist-info files `METADATA`, `WHEEL`, `licenses/LICENSE`, `RECORD`. `ruff check --target-version py311 .` on `main` reports exactly 3 UP042 and 3 UP017.
