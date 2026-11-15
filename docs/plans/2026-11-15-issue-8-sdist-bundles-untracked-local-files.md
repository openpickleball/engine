# Plan: Source distribution bundles untracked local files (issue #8)
Risk tier: Medium. Build and CI config plus a small stdlib check script. It changes existing behavior (what the sdist ships), and the real proof is a build from a clean CI checkout, which a unit test cannot fully exercise. No engine code or rating behavior changes.
Domain skills: test-driven-development: invoke (the check script gets tests written first). All others not applicable because the change has no framework, UI, API, data, auth, payments, or email surface.

## Acceptance criteria
1. `pyproject.toml` has `[tool.hatch.build.targets.sdist]` with `only-include = ["src", "tests", "scripts", "examples", "README.md", "LICENSE", "CHANGELOG.md", "pyproject.toml"]`. The wheel target is unchanged.
2. With untracked `uv.lock`, `.superpowers/review.diff`, and `notes.txt` present in the checkout, `python -m build --sdist` produces a tarball whose `tar tzf` listing has exactly these top-level entries under `openpickleball_engine-<version>/`: `src/`, `tests/`, `scripts/`, `examples/`, `README.md`, `LICENSE`, `CHANGELOG.md`, `pyproject.toml`, `PKG-INFO`, `.gitignore` (hatchling always adds the last two). `CLAUDE.md`, `docs/`, and `.github/` are no longer shipped.
3. `.gitignore` ignores `.superpowers/` (`git check-ignore .superpowers/x` succeeds). `uv.lock`: see "Open decision" below.
4. `scripts/check_sdist.py PATH...` (standard library only, typed, Python 3.10 compatible) opens each sdist tarball and exits non-zero, naming each offending path, when no first path component starts with `openpickleball_engine-` and is a directory prefix of some member (the root is the first such, in member order), when a member is not under that root (a different first component, or any `.` or `..` segment), when a member has an empty path segment after the root (a trailing slash on a directory entry is allowed), when any top-level entry under the root is outside the allowed set in criterion 2 (the entry is named, then each offending member path under it, sorted), or when a required entry (`src/`, `pyproject.toml`, `PKG-INFO`) is missing. It exits 0 and prints the checked file otherwise. It also exits non-zero when given no paths.
5. `.github/workflows/test.yml` gains a `package` job (one Python version, 3.12) that checks out the repo, installs `build`, runs `python -m build`, then `python scripts/check_sdist.py dist/*.tar.gz`. Actions are pinned to the same SHAs, with version comments, as the `test` job. The existing `test` job is unchanged.
6. `tests/test_check_sdist.py` covers criterion 4 with synthetic tarballs built in `tmp_path`: allowed set passes; each of `uv.lock`, `.superpowers/review.diff`, `CLAUDE.md`, `docs/plans/x.md` fails and is named in the output; a member outside the root fails; a missing `PKG-INFO` fails; no arguments fails.
7. `CHANGELOG.md` has an Unreleased entry for the sdist change. The gate passes: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest --cov`.

## Open decision
`uv.lock`: decided by the user at plan approval. It is gitignored (no lock is tracked while CI installs with pip). Criterion 3 therefore also requires `git check-ignore uv.lock` to succeed.

## Files that change
- `pyproject.toml`: add the sdist target table above the wheel table.
- `.gitignore`: add `.superpowers/` and `/uv.lock` in a "Local tooling" group.
- `scripts/check_sdist.py`: new.
- `tests/test_check_sdist.py`: new. Loads the script with `importlib.util.spec_from_file_location` from `Path(__file__).parents[1] / "scripts" / "check_sdist.py"` (`scripts/` is not a package; it ships in the sdist so the shipped tests can load it).
- `.github/workflows/test.yml`: new `package` job.
- `CHANGELOG.md`, and `CLAUDE.md`/`README.md` only if their text becomes wrong or a contributor needs a new rule to keep the change working (Phase 8). Phase 8 outcome: `CLAUDE.md` gains a file-list entry for `scripts/check_sdist.py`, the check command, and the rule that a new shipped top-level path goes in both `only-include` and `ALLOWED_TOP_LEVEL`, since the two lists can drift. `README.md` is unchanged.

## Order of work
1. Write `tests/test_check_sdist.py` against the interface below; run it and see it fail (script missing). Commit the red test.
2. Write `scripts/check_sdist.py`:
   - `ALLOWED_TOP_LEVEL: frozenset[str]` = the ten names in criterion 2 (directories without the slash), with a comment that it mirrors `only-include` in `pyproject.toml` plus the two hatchling adds.
   - `REQUIRED_TOP_LEVEL = frozenset({"src", "pyproject.toml", "PKG-INFO"})`.
   - `problems(names: list[str]) -> list[str]`: pure function over tar member names; returns human-readable problem strings, empty when clean. The root is the first first path component, in member order, that starts with `openpickleball_engine-` and is a directory prefix of some member (`name.startswith(root + "/")`), so a stray top-level file is never taken as the root. Output order: path problems (outside the root, `.` or `..` segments, empty segments) sorted, then each unexpected top-level entry followed by its offending member paths sorted, then missing required entries sorted.
   - `main(argv: list[str] | None = None) -> int`: for each path, `tarfile.open(path, "r:gz")`, `getnames()`, print problems to stderr; return 1 if any path had problems or no paths were given, else 0.
   - `if __name__ == "__main__": raise SystemExit(main())`.
   Run the tests green. Commit.
3. Edit `pyproject.toml` and `.gitignore`. Commit.
4. Add the `package` job to `test.yml`. Commit.
5. Live drive (below). Then the gate.

## Risks
- `only-include` drops anything not listed. Checked: `PKG-INFO`, the readme, and the license are still in the sdist, and the wheel is unchanged (measured below).
- An untracked file inside an included directory (for example `src/open_engine/scratch.py`) would still ship in both sdist and wheel. Not in scope: the issue's criterion is top-level paths. `.gitignore` still filters inside included directories.
- The allowed set lives in two places (`pyproject.toml` and the script). The CI job catches drift in one direction (a new top-level path), and a removed required entry fails the check.
- Review at implementation time tightened criterion 4: the root name is validated, `.`, `..`, and empty segments are rejected, and each offending member path is named, not only its top-level entry.
- `scripts/` ships in the sdist: the user chose this at implementation time, because the shipped `tests/test_check_sdist.py` loads the script and pytest from an extracted sdist otherwise errors.
- Riskiest step: the CI job, since it only runs on GitHub. The live drive runs the same two commands locally from a clean `git archive` export.
- Not doing: a release or publish workflow ("build release artifacts only in CI" needs a publish job, which does not exist yet; worth its own issue), checking wheel contents, type-checking `scripts/` in the gate (CI runs `mypy src`; the script is checked with `mypy --strict scripts/check_sdist.py` during implementation but the gate command is not changed).

## Proof
- Criterion 1, 3, 5: inspect the diff; `git check-ignore -v .superpowers/x`.
- Criterion 2 and the live drive: from a `git archive HEAD` export with `git init`, add `uv.lock`, `.superpowers/review.diff`, `notes.txt`; run `python -m build`; show `tar tzf dist/*.tar.gz`, then `python scripts/check_sdist.py dist/*.tar.gz` (exit 0). Then run the check script against an sdist built from `origin/main` the same way (exit 1, naming the junk).
- Criteria 4, 6: `pytest tests/test_check_sdist.py -v`.
- Criterion 7: the gate in CI order.
- Measured on origin/main (926ed9e), Python 3.12, hatchling via `python -m build`:
  - With untracked `uv.lock`, `.superpowers/review.diff`, `notes.txt` in the worktree, `tar tzf` of the sdist includes `notes.txt`, `uv.lock`, `.superpowers/review.diff`, plus `CLAUDE.md`, `.github/**`, and `docs/plans/**`.
  - With the criterion 1 table applied in a scratch `git archive` copy and the same junk plus `src/open_engine/__pycache__/a.pyc`, the sdist lists only: `CHANGELOG.md`, `examples/basic_match.py`, `src/open_engine/{__init__,engine,winprob}.py`, `src/open_engine/py.typed`, `tests/test_{calibration,engine,winprob}.py`, `tests/fixtures/README.md`, `.gitignore`, `LICENSE`, `README.md`, `pyproject.toml`, `PKG-INFO`. The wheel holds `open_engine/*` and `dist-info` (METADATA, WHEEL, licenses/LICENSE, RECORD), the same as before.
