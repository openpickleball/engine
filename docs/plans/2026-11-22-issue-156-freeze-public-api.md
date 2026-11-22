# Plan: Freeze the public API and ledger semantics for 1.0.0 (issue #156)

Risk tier: Medium. The change renames a public-looking `RatingEngine` attribute (a breaking change in 0.x), adds a test that pins the public surface, and changes the README, `docs/behavior.md`, docstrings, and the changelog. It changes no rating math, no snapshot schema, and no validation. Plan approval was delegated by the repo owner for this run, so the tier is recorded and work proceeds.

Domain skills: django, django-ninja-api, django-allauth, django-celery, python-fastapi: not applicable because this is a standard-library-only Python package with no web framework. Frontend, React, Expo, design, and data visualization skills: not applicable because there is no UI. superpowers:test-driven-development: invoke. No payments, auth, or email code is touched.

## Problem

1.0.0 needs a written promise the backend can pin: what is frozen under strict Semantic Versioning, and what is not. The review on the issue (https://github.com/openpickleball/engine/issues/156#issuecomment-7085930821) lists both. Today only the list of names in `__all__` is tested. Signatures, fields, enum values, and the `RatingEngine` attributes are not, and `RatingEngine.self_reported_log` is an unprefixed attribute whose keys are an internal type (`MatchupKey`).

Out of scope: the demonstration status wording (issue #49, the release step) and `params/v1.json` / `PARAMETERS.md` (issue #48). The existing "demonstration" sentences, including "its API may change without notice", stay as they are. The promise is worded as taking effect at 1.0.0, so it does not contradict them.

## Acceptance criteria

1. `RatingEngine().self_reported_log` no longer exists; the engine keeps the log as `_self_reported_log`. The snapshot key stays `self_reported_log`, `snapshot_version` stays 9, and a snapshot round trip and resume still pass.
2. `tests/test_api_surface.py` renders the public surface to text and compares it with `tests/api_surface.txt`: for every name in `__all__`, its kind; for functions and every public method of an exported class, the `inspect.signature` text; for dataclasses, the frozen flag and each field's name, annotation, and default in order; for enums, each member's name and value in order; for exceptions, the base classes; for `RatingEngine`, its public instance attributes on a fresh engine. The test fails with a diff when they differ, and its message says how to regenerate the file and that a changelog entry is needed.
3. The rendering is deterministic across Python 3.11, 3.12, and 3.13 (no set ordering, no memory addresses in reprs).
4. README "Public API" section states the 1.0.0 promise: what is frozen, the read attributes, and what is outside it (snapshots, rating values and default `Config` values, additions in minor releases, error message text, internal modules, cross-version pickles). The existing sentence "The API may change in any 0.x release." stays.
5. `docs/behavior.md` gains a "Stability" section with the same promise and the ledger semantics under it (`ledger_order`'s order, validation of every row, the duplicate and conflict rules, independence from arrival order, resume equals a full replay or raises). It links to the existing sections instead of restating them.
6. The `ledger_order` docstring says the order is the promise, not the key's shape. The `RatingEngine` class docstring lists the read attributes and says writing to them is unsupported.
7. `CHANGELOG.md` under `## [Unreleased]`: an Added entry for the stability promise and the surface test, and a **Breaking** Changed entry for the rename.
8. `CLAUDE.md` "Public API" rule mentions the surface file so future editors update it with an export change.
9. The full gate passes: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest -n auto`.

## Files that change

- `src/open_engine/_engine.py`: rename the attribute (definition, its uses, `to_snapshot`, the snapshot loader that sets it); class docstring.
- `src/open_engine/_ledger.py`: `ledger_order` docstring.
- Tests that read the engine attribute: `tests/test_snapshot.py` (the attribute set in `test_snapshot_covers_every_engine_attribute` and the engine reads) and any other `eng.self_reported_log` style access. Error-message strings such as `snapshot.self_reported_log[0]` and snapshot keys stay.
- New: `tests/test_api_surface.py`, `tests/api_surface.txt`.
- `README.md`, `docs/behavior.md`, `CHANGELOG.md`, `CLAUDE.md`.

## Order of work

1. Write `tests/test_api_surface.py` and generate `tests/api_surface.txt` from `main`'s code; it passes.
2. Rename the attribute in tests first (red), then in `_engine.py` (green). Regenerate the surface file: the only diff is the `RatingEngine` attribute line.
3. Docstrings, docs, changelog, CLAUDE.md.
4. Full gate.

## Risks

- A pickle of a `RatingEngine` made before the rename loads, but lacks `_self_reported_log`. Cross-version pickles are not promised; snapshots are the supported way to save state. Stated in the promise and the changelog.
- Signature text can differ across Python versions (for example enum default reprs). The test runs on 3.11 to 3.13 in CI; the renderer uses `inspect.signature` strings, whose format is stable across those versions, and enum members print as `Enum.MEMBER`.
- The evaluation repository reads `engine.players` only, not `self_reported_log`, so the rename does not break it.

## Proof

- Criterion 1: `tests/test_snapshot.py::test_snapshot_covers_every_engine_attribute`, the snapshot round trip and resume tests, and a new assert in `tests/test_api_surface.py` that `self_reported_log` is not an attribute.
- Criterion 2 and 3: `tests/test_api_surface.py` on all CI Python versions; a check that the test fails when the file is edited.
- Criteria 4 to 8: read the diff; `tests/test_api.py` README checks still pass.
- No live drive: the change has no runtime path beyond what the suite covers (a library, no UI).
