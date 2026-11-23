# Plan: Ship published parameter sets in the wheel with a packaged loader (issue #165)

Risk tier: Medium. It adds public API (a contract frozen at 1.0.0) and changes packaging, which unit tests cannot fully exercise: only a build and a clean install prove the wheel ships the file. Recorded without a human gate: the owner delegated plan approval for this run.

Domain skills: none applicable. This is a standard library Python package with no framework, UI, API server, data store, auth, payments, or email.

## Acceptance criteria

1. A clean install of the built wheel (not the repository) can run `Config.published("v1").parameter_fingerprint()` and get `f9149f3c38a01efc`. CI checks this against the built wheel.
2. An unknown label raises `ConfigError`, the error type `from_parameter_set()` uses for an unpublished label, with the same "is not a parameter set this release publishes" wording.
3. The packaged file and the published fingerprint table cannot disagree without a test failing.
4. `from_parameter_set()` keeps working unchanged: no change to its checks, order, messages, or result.
5. `Config.published_sets()` returns the labels this release publishes, in table order, so callers do not hard-code them.
6. One copy of each parameter set file exists in the repository. No parameter value, the v1 fingerprint, or the package version changes.
7. `tests/api_surface.txt`, README, `docs/parameters.md`, `docs/behavior.md`, `docs/model.md`, `PARAMETERS.md`, `CLAUDE.md`, and `CHANGELOG.md` are true of the code.

## Files that change

- `params/v1.json` moves to `src/open_engine/params/v1.json` (`git mv`, content unchanged). The repository root `params/` goes away, so there is one source of truth.
- `src/open_engine/_config.py`: two classmethods on `Config`.
  - `published(label: str) -> Config`. Checks: label not exactly a str raises `ConfigError` (names the type only); a label not in `_PUBLISHED_PARAMETER_SETS` raises `ConfigError` "... is not a parameter set this release publishes (it publishes v1)", without echoing the label. Only a label in the table is turned into a path, so no caller string reaches the file system. Then it reads `open_engine/params/<label>.json` with `importlib.resources`, parses it with `json.loads`, and returns `from_parameter_set(data)`, so the label and fingerprint checks run on the packaged file too. A missing or unreadable file (`OSError`) or invalid JSON (`ValueError`) raises `ConfigError` naming the label, since every engine error is an `OpenEngineError`.
  - `published_sets() -> tuple[str, ...]`: the labels in `_PUBLISHED_PARAMETER_SETS`, in order.
  - Docstring of `from_parameter_set` and the comments near `_PUBLISHED_PARAMETER_SETS` and `_PARAMS_VERSION` updated for the new path.
- `pyproject.toml`: drop `params` from the sdist `only-include` (it ships under `src`). The wheel already ships every file under `src/open_engine`.
- `scripts/check_sdist.py`: drop `params` from `ALLOWED_TOP_LEVEL`; update the comment.
- `scripts/check_wheel.py`: `PACKAGE_FILES` gains `params/v1.json`; members may sit in a subdirectory listed in `PACKAGE_FILES`, and a directory entry for such a subdirectory is allowed.
- New `scripts/check_installed_wheel.py`: run with the clean venv's interpreter under `-I`. Fails unless `open_engine` is imported from outside the checkout, `Config.published("v1").parameter_fingerprint()` is `f9149f3c38a01efc`, and every label in `Config.published_sets()` loads with its table fingerprint.
- `.github/workflows/test.yml`, `package` job: after the wheel check, a step makes a venv in `$RUNNER_TEMP`, installs `dist/*.whl` with `--no-index --no-deps`, and runs the new script with that venv's Python under `-I`.
- Tests:
  - `tests/test_parameter_set_binding.py`, `tests/test_parameter_sets.py`, `tests/test_parameter_set_check_order.py`: read from `src/open_engine/params`.
  - New `tests/test_published_parameter_sets.py`: `published("v1")` equals `from_parameter_set` of the file and has fingerprint `f9149f3c38a01efc`; `published_sets()` equals the table labels and the file names; every published label loads; unknown, malformed, and non-str labels raise `ConfigError` with the shared wording and without echoing the label; a table label with no file and a file with invalid JSON raise `ConfigError`.
  - `tests/test_check_wheel.py`: the allowed set gains `open_engine/params/v1.json`; a test that `PACKAGE_FILES` equals the files under `src/open_engine` (no `__pycache__`), so a new set file cannot be left out of the wheel check; the params directory entry is allowed.
  - `tests/test_check_sdist.py`: `params/v1.json` at the top level is no longer allowed.
  - New `tests/test_check_installed_wheel.py`: the script reports no problem for the current install when the checkout bound is elsewhere, and reports an import from the checkout.
  - `tests/api_surface.txt`: regenerated with `python tests/test_api_surface.py`.
- Docs: README ("Public API" `Config` line and "Config values"), `docs/parameters.md` (intro, file format, loader), `docs/behavior.md` (Config validation: `published` checks), `docs/model.md` (parameter labels paragraph), `PARAMETERS.md` (intro and the `File:` line, with the test that pins it), `CLAUDE.md` (`params/` entry, scripts, provenance rule), `docs/releasing.md` if it describes the package job, `CHANGELOG.md` under `[Unreleased]` (Added: the two classmethods and the wheel shipping the sets; Changed: the file moved).

## Order of work

1. Plan commit.
2. Red tests: new loader tests and the check_wheel and check_sdist test changes.
3. `git mv` the file, update path-reading tests.
4. Implement the two classmethods, the scripts, pyproject, CI step.
5. Regenerate `tests/api_surface.txt`.
6. Docs and changelog.
7. Gate: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest -n auto`, then `python -m build`, `check_sdist`, `check_wheel`, `twine check --strict`, and the clean venv install step run locally.

## Risks

- The wheel or sdist silently dropping the JSON file: covered by `check_wheel.py` requiring it and the installed wheel step loading it.
- A label used as a path: only table labels form a path.
- A second copy drifting: the root `params/` is removed, and the sdist check rejects a top-level `params`.
- Changing `from_parameter_set`: only its docstring changes. Its tests stay as they are, apart from the file path.

## Proof

- Criterion 1: `scripts/check_installed_wheel.py` in the CI `package` job, plus a local run of the same commands.
- Criterion 2: `tests/test_published_parameter_sets.py` unknown label tests.
- Criterion 3: `test_the_published_table_matches_the_params_files` (now reading the packaged directory), the new `published_sets` versus files test, and `check_wheel.py` versus the package files test.
- Criterion 4: the existing `from_parameter_set` tests pass unchanged apart from the path.
- Criterion 5: `published_sets` test.
- Criterion 6: `git diff -M` shows a pure rename with 100% similarity; `_version.py` untouched.
- Criterion 7: `tests/test_api_surface.py`, `tests/test_api.py`, the PARAMETERS.md register test, and a doc review.
