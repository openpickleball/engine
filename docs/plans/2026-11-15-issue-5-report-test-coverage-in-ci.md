# Plan: Report test coverage in CI (issue #5)
Risk tier: Low. Dev tooling and CI config only; no engine code or runtime behavior changes, and the CI step is a command the local gate runs verbatim.
Domain skills: test-driven-development: not applicable because no behavior changes; the proof is the coverage report the new command prints (see Proof). All others not applicable because the change has no framework, UI, API, data, auth, payments, or email surface.

## Acceptance criteria
1. `pyproject.toml`'s `dev` extra lists `pytest-cov` with a lower bound (`pytest-cov>=5`).
2. `pyproject.toml` has `[tool.coverage.run]` with `branch = true` and `source = ["src/open_engine"]`, and `[tool.coverage.report]` with `show_missing = true`.
3. The `Test` step in `.github/workflows/test.yml` runs `pytest --cov`, on every Python version in the matrix. No other step changes.
4. From the repo root, `pytest --cov` passes and prints a terminal report that lists `src/open_engine/__init__.py`, `src/open_engine/engine.py`, and `src/open_engine/winprob.py`, has `Branch`, `BrPart`, and `Missing` columns, and a `TOTAL` line. No file outside `src/open_engine` (for example under `tests/`) appears in it.
5. No `fail_under` is set; the PR body states the current total for a maintainer to pick the minimum.
6. Plain `pytest` (no `--cov`) still runs without coverage, so local runs keep their speed; no `addopts` is added.
7. `src/` and `tests/` are unchanged; `ruff check .`, `ruff format --check .`, `mypy src`, and `pytest` pass.

## Files that change
- `pyproject.toml`: add `"pytest-cov>=5"` to the `dev` list; add the two coverage tables after `[tool.pytest.ini_options]`.
- `.github/workflows/test.yml`: change `run: pytest` to `run: pytest --cov` in the `Test` step.
- `README.md` and `CHANGELOG.md`: Phase 8 (coordinator) decides. Likely a `pytest --cov` line in the README's test commands and an Unreleased entry, following the mypy-in-CI precedent.

## Order of work
1. Edit `pyproject.toml`:
   ```toml
   dev = ["pytest>=8", "pytest-cov>=5", "ruff>=0.6", "mypy>=1.13", "build"]
   ...
   [tool.pytest.ini_options]
   testpaths = ["tests"]

   [tool.coverage.run]
   branch = true
   source = ["src/open_engine"]

   [tool.coverage.report]
   show_missing = true
   ```
2. Edit `.github/workflows/test.yml`: `run: pytest` becomes `run: pytest --cov`. Bare `--cov` takes its source from the config, so the report scope lives in one place and a local `pytest --cov` matches CI.
3. Reinstall the dev extra (`uv pip install --python .venv/bin/python -e ".[dev]"`) so `pytest-cov` comes from the extra, not a manual install.
4. Run the gate in CI order, then `pytest --cov` and check criterion 4 against its output. Delete the generated `.coverage` file afterwards (it is gitignored, but keep the tree clean).
5. Confirm `git diff --stat src/ tests/` is empty.

## Risks
- Python 3.10 has no `tomllib`; coverage reads `pyproject.toml` there only with `tomli`. pytest-cov 7.1.0 requires `coverage[toml]>=7.10.6`, so it is pulled in. Measured below on 3.10.
- `source` as a path relies on the editable install mapping `open_engine` to `src/open_engine`, which CI's `pip install -e ".[dev]"` does. A non-editable install would report nothing; not in scope.
- Riskiest step: none is risky at runtime; the main failure mode is a misconfigured scope (tests counted, or engine missing), which criterion 4 checks.
- Not doing: `fail_under` (the issue leaves it to a maintainer), HTML or XML reports, coverage upload services, `addopts`.

## Proof
- Criteria 1, 2, 3, 6: inspect the diff.
- Criterion 4: output of `pytest --cov` from the repo root.
- Criterion 5: inspect the diff for `fail_under`; the total goes in the PR body.
- Criterion 7: the gate, in CI order: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`, plus `git diff --stat src/ tests/`.
- Live drive: the local `pytest --cov` run is the drive; the CI run on the PR shows the report on all four matrix versions.
- Measured on origin/main (8e89dab) before any change, with the planned config in a scratch file passed via `--cov-config` and pytest-cov 7.1.0, coverage 7.17.3:
  - Python 3.12.3: 99 passed. Report lists the three `src/open_engine` files with Branch, BrPart, and Missing columns and nothing from `tests/`. `TOTAL 766 77 296 62 85%` (engine.py 84%, winprob.py 98%, `__init__.py` 100%).
  - Python 3.10.12: 99 passed, same `TOTAL 766 77 296 62 85%`.
