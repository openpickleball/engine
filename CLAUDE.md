# CLAUDE.md

This file gives Claude Code guidance for working in this repository.

## What this repository is

The reference rating engine for OPEN, the Open Pickleball Evaluation Network: a free, open-source, community-governed pickleball rating network. It is a Python package (`openpickleball-engine`, imported as `open_engine`) that holds the rating math: match expectations, rating updates, reliability, deterministic replay of a match ledger, and calibration (`fit_scale`, `fit_win_calibration`, `evaluate`).

The services and APIs that store players and matches and serve ratings live in the separate `backend` repository. The backend calls this package; it never reimplements the math.

## Current status

The engine is a demonstration. Its methods have not been validated on real match data, its outputs are not OPEN ratings, and its API may change without notice. No OPEN rating is live. Keep that labeling in the README, the package description, the module docstrings, and the changelog.

- `src/open_engine/engine.py`: enums and game formats, `Config`, `Match`, `Player`, `RatingEngine`, `replay`, evaluation and calibration. Its module docstring lists the published behavior notes and the production requirements the backend must meet.
- `src/open_engine/winprob.py`: game and match win probability and its calibration.
- `src/open_engine/__init__.py`: the public API (`__all__`) and `__version__`.
- `tests/`: pytest suite. `tests/fixtures/` holds synthetic fixtures only (see its README).
- `examples/basic_match.py`: a runnable example, linted with the rest of the code.
- `docs/plans/`: plans recorded for issues before their work lands.

## Stack

- Python 3.10 or later (`requires-python = ">=3.10"`, ruff `target-version = "py310"`). CI tests 3.10, 3.11, 3.12, and 3.13, so do not use syntax or standard library features newer than 3.10. Modules use `from __future__ import annotations`.
- No runtime dependencies (`dependencies = []`). Keep it that way: the standard library only.
- Build backend hatchling. The version is read from `__version__` in `src/open_engine/__init__.py`. The package ships `py.typed`.
- Dev tools: pytest, ruff (lint rules `E`, `F`, `W`, `I`, `B`, `UP`, line length 100), mypy in strict mode, build.

## Engine rules

- **Determinism.** `replay(ledger, as_of=...)` must give the same result for the same ledger, config, and date, whatever order the matches arrive in. Ordering goes through `ledger_order`. Never add randomness, wall-clock reads, or iteration over unordered sets or dicts to a code path that affects ratings.
- **Config provenance.** `Config.fingerprint()` hashes every parameter, and each replay's `ReplayInfo` stores `as_of`, the config version, and the fingerprint. The fingerprint walks every dataclass field, so a new `Config` field is included automatically, but its value must be a type that `norm()` turns into stable JSON (add a case there if not). Validate a new field in `__post_init__` when it has a valid range. A change to a default value or to rating behavior changes published ratings: record it in `CHANGELOG.md`.
- **Versions.** `Config.version` and `__version__` move together, and only in a release. The backend pins the engine as a git dependency on a release tag.
- **Zero-sum exceptions.** Team average movement is zero-sum except for the two documented cases, `clamped` and `frozen`, both reported on every `MatchResult`. A change that adds another exception must report it the same way and document it in the module docstring.
- **Provisional versus authoritative.** Authoritative ratings come from scheduled replays. Live updates between replays are provisional and labeled with the count since the last recalculation. Keep that labeling.
- **External ratings are a seed only.** An imported rating initializes a player. It can never overwrite an OPEN-earned rating once OPEN matches exist. Imported reliability is shown separately from OPEN-earned reliability, and `reliability` is never a conversion of an external score.
- **Calibration honesty.** A fit that lands on a search edge (`at_bound`) or has a flat error profile is not identified. Keep `ScaleFit.profile` and `at_bound` reporting it, and do not hide or round away signals that make an unidentified fit visible.
- **Public API.** Anything exported from `open_engine` is listed in `__all__` in `__init__.py`. Adding, removing, or changing the signature of an export is a changelog entry, and a breaking change is called out as one (minor versions may break before 1.0.0).

## Commands

Set up from a local checkout (the package is not on PyPI yet):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"      # or: uv venv && uv pip install -e ".[dev]"
```

The gate, the same commands CI runs (`.github/workflows/test.yml`) on every supported Python version:

```bash
ruff check .
ruff format --check .
mypy src
pytest
```

Other commands:

```bash
ruff format .                # apply formatting
python examples/basic_match.py
python -m build              # wheel and source distribution
```

CI also runs `.github/workflows/dco.yml` (the `sign-off` check) on every pull request and the OpenSSF Scorecard workflow. GitHub Actions are pinned by commit SHA with the version in a comment; keep that form when Dependabot or a contributor bumps one.

## Tests

- Tests use pytest with plain asserts and fixtures, no unittest classes. Prefer `pytest.mark.parametrize` for rule tables, and assert the `ValueError` message with `pytest.raises(..., match=...)` when a test exercises a validation branch.
- Every behavior change comes with a test. A bug fix comes with a test that fails without the fix.
- Fixtures and test data are synthetic, with invented player identifiers. Never add real match results, even with names removed: a sequence of opponents, dates, and scores can still point to a specific person.

## Documentation

- `README.md`: public overview, setup, quick start, and the demonstration status.
- `CHANGELOG.md`: Keep a Changelog format, Semantic Versioning. Every change to behavior, the public API, or `Config` adds an entry under `## [Unreleased]` in the same pull request.
- `docs/plans/`: one plan per issue, named `YYYY-MM-DD-issue-<N>-<slug>.md`, committed before the work it describes.
- Organization-wide policy lives in the `openpickleball/.github` repository: charter, governance, roadmap, contributing guide, security policy, licensing. A local checkout may exist at `../OPEN-pickleball`.
- The backend may be checked out at `../OPEN-backend`. Its `CONTEXT.md` holds the domain language (Match, Ledger, Replay, Rating Format, Reliability). Use the same terms here.

## Hard rules

1. **No real player data, anywhere.** Not in code, fixtures, examples, docs, logs, issues, pull requests, or screenshots. Use made-up or properly de-identified data. `.gitignore` excludes local data directories and database files; keep it that way.
2. **This repository is the package only.** Evaluation or validation runs on real match data do not belong here, and neither do their results.
3. **Keep the demonstration label** until the governance process decides otherwise. Do not describe engine output as an OPEN rating.
4. **No runtime dependencies.**
5. **Do not name competing rating systems** in code, docs, issues, or comments. Say "standard external rating system" or "external rating". The engine is not a reproduction of any proprietary rating algorithm.
6. **Charter limits.** No paid tiers, premium features, or anything that conflicts with the [mission charter](https://github.com/openpickleball/.github/blob/main/CHARTER.md).
7. **Security-sensitive areas:** anything that lets one party change another player's rating, the handling of imported external ratings, and the replay's determinism and provenance. Treat bypasses here as security bugs, not feature gaps.

## Contribution conventions

- Every commit needs a DCO sign-off (`git commit -s`). CI (`.github/workflows/dco.yml`) rejects pull requests with unsigned commits. That check runs only on pull requests, so commits pushed straight to `main` must be signed off by hand.
- Founding phase: repository admins may push directly to `main` while the project is getting established. Everyone else, and admins once this phase ends, uses one change per pull request, linked to an issue. Add or update tests, docs, and the changelog when behavior changes.
- Commit messages: imperative mood, subject under 72 characters, body explains why.
- License: AGPL-3.0-or-later.
- Before proposing a feature, ask whether it makes the standard more accurate, more trusted, or easier to adopt. If not, it probably belongs in a partner's product.

## Git exception

This section overrides a contributor's global no-commit rule for the `/work-issue` workflow only.

- The workflow may commit (`git commit -s`, with explicit paths) and push, but only on the `issue-<N>-*` branch it creates or resumes.
- After a rebase it may push that branch with `--force-with-lease`. It never force-pushes any other branch.
- It may open a pull request against `main`, edit its description, label it (creating the ready label if it does not exist), and reply to review comments on it.
- It never commits or pushes to `main`, never merges, and never enables auto-merge. A human reviews and merges every pull request.

## Writing style

Docs in this project use plain, short sentences. No emojis and no em dashes. Prefer "player", "match", and "organizer" over generic words like "user" or "entity".
