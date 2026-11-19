# CLAUDE.md

This file gives Claude Code guidance for working in this repository.

## What this repository is

The reference rating engine for OPEN, the Open Pickleball Evaluation Network: a free, open-source, community-governed pickleball rating network. It is a Python package (`openpickleball-engine`, imported as `open_engine`) that holds the rating math: match expectations, rating updates, reliability, deterministic replay of a match ledger, and calibration (`fit_scale`, `fit_win_calibration`, `evaluate`).

The services and APIs that store players and matches and serve ratings live in the separate `backend` repository. The backend calls this package; it never reimplements the math.

## Current status

The engine is a demonstration. Its methods have not been validated on real match data, its outputs are not OPEN ratings, and its API may change without notice. No OPEN rating is live. Keep that labeling in the README, the package description, the module docstrings, `docs/behavior.md`, and the changelog.

- `src/open_engine/engine.py`: the module docstring and the re-exports. It defines nothing: every public name of the engine lives in a private module and is re-exported here, so `from open_engine.engine import X` works. Private names are not re-exported, except `_FrozenMapping`, which pickles made before issue #133 name there.
- The engine's private modules, in import order (each imports from the package only `_errors`, `_version`, and the modules above it; `tests/test_module_layout.py` checks this): `_validation.py` (the type and number predicates, with a comment on which to use when), `_types.py` (the enums and game formats, `_is_member`), `winprob.py`, `_config.py` (`Config`), `_model.py` (`Match`, `Player`, the rating state, and the result records), `_ledger.py` (ledger order, `_ledger`, match digests, content keys), `_math.py` (the rating math, reliability, and the named model constants), `_connectivity.py`, `_seeds.py` (seed validation), `_snapshot.py` (the snapshot schema, writers, and readers), `_engine.py` (`RatingEngine` with its snapshots, `to_snapshot` and `from_snapshot`), `_replay.py` (`replay` and resume from a snapshot), and `_evaluation.py` (evaluation and calibration). Their docstrings state each public call's contract, and many name the `docs/behavior.md` section that holds their edge cases. Each class reports the module that defines it. Match digests tag every value a validated `Match` can hold, other than `played_at` (digested untagged, as its UTC instant and offset when set), with a fixed string for its exact type from `_DIGEST_TAGS` in `_ledger.py`, not from `__module__`, so moving a class does not change stored digests (only a type outside the table, which a validated match never holds, falls back to its module and name). A new type a `Match` can hold (a new field or a new accepted container) needs an entry there with a new string; `tests/test_digest_tags.py` fails when a `Match` or `GameFormat` field's type has none. Never change an existing string: that changes stored digests. `engine.py` keeps re-exporting every public class and `_FrozenMapping` (its one private re-export), since pickles made before issue #133 name them as `open_engine.engine.<Class>`; `tests/test_public_class_identity.py` checks that they load. A test that monkeypatches a private name patches it in every module whose code calls it.
- `src/open_engine/winprob.py`: game and match win probability and its calibration.
- `src/open_engine/_errors.py`: the exception hierarchy, `OpenEngineError` and its subclasses. `engine.py` re-exports them.
- `src/open_engine/__init__.py`: the public API (`__all__`). It re-exports `__version__`.
- `src/open_engine/_version.py`: `__version__`, the one place a release sets the version.
- `tests/`: pytest suite. `tests/fixtures/` holds synthetic fixtures only (see its README).
- `examples/basic_match.py`: a runnable example, linted with the rest of the code. `tests/test_examples.py` runs every script in `examples/`.
- `scripts/check_sdist.py`: checks a built source distribution's layout: one `openpickleball_engine-<version>` root, clean member paths, only allowed top-level entries, and the required ones present. CI runs it in the `package` job.
- `scripts/check_wheel.py`: checks a built wheel's members: only the `open_engine` modules in `PACKAGE_FILES`, all of them present (`py.typed` included), and one `openpickleball_engine-<version>.dist-info` directory. CI's `package` job and the release build run it. A new module in `src/open_engine/` goes in `PACKAGE_FILES`.
- `scripts/check_release.py`: checks that a release tag is `v` plus the version of the built sdist and wheel, and that `dist/` holds nothing else. The release workflow runs it.
- `docs/behavior.md`: the published behavior notes (input types, errors, ledger order, duplicates, late matches, imbalance, snapshots, and the rest) and the production requirements the backend must meet.
- `docs/releasing.md`: the release workflow, its one-time setup, and the steps for each release.
- `docs/plans/`: plans recorded for issues before their work lands.

## Stack

- Python 3.11 or later (`requires-python = ">=3.11"`, ruff `target-version = "py311"`). CI tests 3.11, 3.12, and 3.13, so do not use syntax or standard library features newer than 3.11. The public string enums subclass a private `_StrEnum` (a `StrEnum`), so `str()` and f-strings give the value. Modules use `from __future__ import annotations`.
- No runtime dependencies (`dependencies = []`). Keep it that way: the standard library only.
- Build backend hatchling. The version is read from `__version__` in `src/open_engine/_version.py`. The package ships `py.typed`.
- Dev tools: pytest, ruff (lint rules `E`, `F`, `W`, `I`, `B`, `UP`, line length 100), mypy in strict mode, build, twine.

## Engine rules

- **Changes must improve predictions.** A change to how ratings move must make predictions more accurate, measured out of sample against a baseline set before the test. Tuning and scoring on the same matches does not count. How a result feels to players is not a reason to change the math. Never add a special-case override that discards evidence, such as a floor that stops winners from losing rating. Express a model choice as a declared `Config` parameter, so the fingerprint records it. Changes for integrity or security (manipulation resistance, determinism, provenance, input validation) are the exception. They must not cost accuracy without a stated reason. Evaluation on real match data happens outside this repository.
- **Determinism.** `replay(ledger, as_of=...)` must give the same result for the same ledger, config, and date, whatever order the matches arrive in. A code path that consumes a ledger goes through `_ledger`. It validates every row, collapses or rejects repeated `match_id`s, and sorts by `ledger_order`. Sorting by `ledger_order` alone skips validation and the duplicate check. Rows that repeat a `match_id` collapse only when they are interchangeable for replay (`_canonical`). Any other repeat raises `LedgerConflictError`. Never compare repeated rows with `Match` equality (`==`). Never add randomness, wall-clock reads, or iteration over unordered sets or dicts to a code path that affects ratings. To add up an iterable of floats on any path that affects ratings, predictions, or evaluation, use `math.fsum`, not the built-in `sum()`: `sum()` of floats rounds differently on Python 3.11 than on 3.12 and later. Integer sums can use `sum()`, and a fixed-order `+` or `+=` loop is fine.
- **Config provenance.** `Config.fingerprint()` hashes every parameter, and each replay's `ReplayInfo` stores `as_of`, the config version, and the fingerprint. The fingerprint walks every dataclass field, so a new `Config` field is included automatically, but its value must be a type that `norm()` turns into stable JSON (add a case there if not). A new mapping field goes in `_CONFIG_MAPPING_FIELDS`, which `__post_init__` stores as read-only copies, and a field whose `to_dict()` form is not its own type (an enum, a tuple, a set, an int key) needs a conversion in `Config.from_dict`. Validate a new field in `__post_init__` when it has a valid range. A change to a default value or to rating behavior changes published ratings: record it in `CHANGELOG.md`.
- **Versions.** `Config.version` and `__version__` move together, and only in a release. Both come from `src/open_engine/_version.py`, so set the version there only. A dev release such as `0.3.0.dev1` counts as a release. A test fails if the two differ. The backend pins the engine as a git dependency on a release tag.
- **Own K and imbalance.** Each player moves by their own K, so a partner's or opponent's uncertainty never changes a player's step. Team average movement is not always zero-sum. Every `MatchResult` reports the net as `imbalance`. When both teams have the same mean K and nothing is `clamped` or `frozen`, imbalance is 0.0 except for float rounding, which shows only at very large ratings or steps. `clamped` lists a player when the clamped rating differs from the rounded sum old rating + raw delta by more than 1e-12, or the applied change differs from the raw delta by at least 5e-6; a loss that meets neither rule is not listed, including a range cut that rounding of that sum absorbs first. Imbalance can be nonzero for unequal K, `clamped`, `frozen`, or that rounding, and a 0.0 does not mean nothing was clamped or frozen. A change that adds another source of imbalance must show up in that field and be documented in `docs/behavior.md` ("K and imbalance"). High-K and low-K players can add or remove rating from the pool, so colluding accounts can create rating; the backend should total and monitor `imbalance`.
- **Provisional versus authoritative.** Authoritative ratings come from scheduled replays. Live updates between replays are provisional and labeled with the count since the last recalculation. Keep that labeling.
- **External ratings are a seed only.** An imported rating initializes a player. It can never overwrite an OPEN-earned rating once OPEN matches exist. Imported reliability is shown separately from OPEN-earned reliability, and `reliability` is never a conversion of an external score.
- **Calibration honesty.** A fit that lands on a search edge (`at_bound`) or has a flat error profile is not identified. Keep `ScaleFit.profile` and `at_bound` reporting it, and do not hide or round away signals that make an unidentified fit visible.
- **Public API.** Anything exported from `open_engine` is listed in `__all__` in `__init__.py` and in the README's "Public API" section, and has its own docstring, as do the public methods of exported classes. Tests check all three. Everything else, including names reached only through `open_engine.engine` or `open_engine.winprob`, is internal. Adding, removing, or changing the signature of an export is a changelog entry, and a breaking change is called out as one (minor versions may break before 1.0.0).
- **Exact input types.** A public entry point takes exactly the types it names, checked before any state change, and raises its own `OpenEngineError` subclass for anything else (issues #116, #118, #119). The README lists three carve-outs. `Config(...)` numbers, mappings, and `allowed_game_formats`, seed state numbers, `ExternalRatingSnapshot` numbers, and snapshot numbers are read once and converted to plain values (#107). `matches` and `seed_players` may be any iterable, read once into a list before any row is checked. Output dataclasses are not inputs. Outside those, check a type with `type(x) is T` (or `_validation._type_is`). Do not use `isinstance`, which a `__class__` property fools, or `type(x) in (...)`, which runs a metaclass `__eq__`. Check an enum by member identity (`_types._is_member`), since an object of the enum's own type can exist without being a member. Check each dict key's type before hashing or looking it up. Do not call a method on a caller's object, its tzinfo or offset included, before its type has passed. A rejection message for a caller's input names a wrong-typed value's type with `_validation._type_name`, not its `repr` or `str`.
- **Errors.** Raise only `OpenEngineError` subclasses from `_errors.py`, all of them `ValueError`s; a test scans every module for other raises. A rejected match is not an error: `record` returns a `MatchResult` with a `RejectionReason`. A new rejection gets a new `RejectionReason` member.
- **Snapshots.** `to_snapshot` must capture every piece of engine state that can change a rating or a later replay, and `from_snapshot` must accept every state the engine's own methods can produce. When you add a snapshot field, or a rule that links fields, give its validation every rule the engine keeps that the snapshot alone can check, including rules that depend on the config or hold only in a resumable snapshot. A new engine attribute or a new field of `Player`, `RatingState`, or `ReplayInfo` goes into the snapshot schema, its validation, and the README schema section, with a `snapshot_version` bump and a changelog entry (a bump makes older stored snapshots fail to load). The config is covered by its version and fingerprint. Tests in `tests/test_snapshot.py` fail when such an attribute or field appears, until the schema (and, for an engine attribute, the test's attribute list) is updated; no test checks the version bump. A private cache derived only from state the snapshot already holds, and cleared by `_changed`, is not engine state: keep it out of the snapshot and list it in `test_snapshot_covers_every_engine_attribute` with the reason. A method that changes engine state must clear the resumable mark (through `_changed`, `_put_player`, or `_put_state`) before or as it changes state, so resume refuses a snapshot taken after it. Resume must equal a full replay of the same ledger and seeds or raise, except for the limits the README names.
- **Validate before committing.** `record` validates the whole match, every player id included, before it changes any state, so a failed call leaves ratings untouched and a retry cannot move them again.

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
python scripts/check_sdist.py dist/*.tar.gz   # CI's package job; clear dist/ first
python scripts/check_wheel.py dist/*.whl
twine check --strict dist/*
```

The sdist ships only the paths in `only-include` under `[tool.hatch.build.targets.sdist]` in `pyproject.toml`, plus the `PKG-INFO` and `.gitignore` that hatchling adds. A new top-level path that must ship goes in that list and in `ALLOWED_TOP_LEVEL` in `scripts/check_sdist.py`. If it is in `only-include` but not in `ALLOWED_TOP_LEVEL`, the `package` job fails.

CI also runs `.github/workflows/dco.yml` (the `sign-off` check) on every pull request and the OpenSSF Scorecard workflow. GitHub Actions are pinned by commit SHA with the version in a comment; keep that form when Dependabot or a contributor bumps one.

Pushing a `v*` tag runs `.github/workflows/release.yml`, which calls `test.yml` as its gate and publishes to TestPyPI (see `docs/releasing.md`). The `package` job and the release build install their tools from `.github/requirements/release.txt`, a hash-pinned lock. Never edit it by hand: regenerate it with the command in its header, on Linux (see `docs/releasing.md`).

## Tests

- Tests use pytest with plain asserts and fixtures, no unittest classes. Prefer `pytest.mark.parametrize` for rule tables, and assert the error and its message with `pytest.raises(..., match=...)` when a test exercises a validation branch. New tests name the specific `OpenEngineError` subclass; older tests that expect `ValueError` stay valid, since every engine error is one.
- Every behavior change comes with a test. A bug fix comes with a test that fails without the fix.
- Fixtures and test data are synthetic, with invented player identifiers. Never add real match results, even with names removed: a sequence of opponents, dates, and scores can still point to a specific person.

## Documentation

- `README.md`: public overview, setup, quick start, and the demonstration status.
- `CHANGELOG.md`: Keep a Changelog format, Semantic Versioning. Every change to behavior, the public API, or `Config` adds an entry under `## [Unreleased]` in the same pull request.
- `docs/model.md`: the rating model, each equation naming the function it comes from, and the `Config` parameter table. Keep it true of the code: a pull request that changes the math of ratings, reliability, win probability, evaluation, or calibration fits, adds or changes a `Config` field or default, or changes a named model constant in `_math.py` or `winprob.py` updates `docs/model.md` in the same pull request.
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

A claim about engine behavior in the docs or docstrings that something always or never happens must hold for every input its stated conditions allow, not only the tested case. Before writing one, try to break it with other rating formats, game formats, game counts, dates, RDs, match types, match histories, and `Config` values (the rating range included). Prefer one condition that keeps everything else the same over a list of conditions that can miss one. For example, the match performance note scopes its claim to one engine state where only the match's game scores change, with the same number of games and the same result. If the claim does not hold, narrow it, or say "can" and name the case that breaks it.

A docstring or doc that states the order in which errors are checked must match the code's order. Confirm each pair of adjacent checks with an input that fails both.
