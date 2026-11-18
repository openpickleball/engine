# Plan: replay checks that as_of is a plain date (issue #66)

Risk tier: Medium. Input validation only: no rating math, Config, or snapshot schema change, and every rule is unit-testable. Raised from Low because it changes the public input contract (an empty ledger with a bad `as_of` used to replay) and alters existing behavior.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-celery, django-allauth, python-fastapi, React and Expo skills: not applicable because this is a standard-library-only Python package with no framework, API layer, or UI.

## Decision (given with the issue, counts as plan approval)

`replay` rejects an `as_of` that is not a plain `date`. A `datetime` is a `date` subclass, so it is rejected explicitly (`_is_plain_date` already does this). The error is `ReferenceDateError`, the `OpenEngineError` subclass that already covers "an as_of that is not a plain date" for `player_summary`. This breaks the empty-ledger case, which used to succeed: a `**Breaking:**` entry under `### Changed` with a migration note.

## What main does today

Measured on `origin/main` (457bbf1), synthetic match `Match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 5),), date(2026, 3, 2), RatingFormat.DOUBLES)`:

| Call | Result on main |
| --- | --- |
| `replay([m], as_of=datetime(2026, 3, 31))` | bare `TypeError` ('>' not supported between date and datetime) |
| `replay([], as_of=datetime(2026, 3, 31))` | succeeds, `calculated_as_of` is a datetime |
| `replay([], as_of="2026-03-31")` | succeeds, `calculated_as_of` is a str |

## Acceptance criteria

1. `replay` raises `ReferenceDateError` (an `OpenEngineError`) for an `as_of` that is a str, a naive datetime, an aware datetime, `None`, or an int, for an empty and a non-empty ledger. The message names `as_of`.
2. The check runs first: before the `whole_ledger` checks, before a `from_snapshot` is loaded (so a resume with a datetime `as_of` raises `ReferenceDateError`, not a bare `TypeError` from the checkpoint comparison), and before any ledger row or seed is validated (so a bad `as_of` with an invalid row raises `ReferenceDateError`).
3. A plain `date` `as_of` behaves as before; a `date` subclass that is not a `datetime` is accepted, as `_is_plain_date` already allows. The full suite stays green.
4. Tests assert the exact type with `pytest.raises(ReferenceDateError, match="as_of")`.
5. CHANGELOG: a `**Breaking:**` entry under `### Changed` with a "Migration:" note. The `ReferenceDateError` docstring, the module docstring error list, the `replay` docstring, and the README `ReferenceDateError` line name `replay` too.

## Files that change

- `src/open_engine/engine.py`: at the top of `replay`, `if not _is_plain_date(as_of): raise ReferenceDateError("replay as_of must be a date, not a str, datetime, or other type")`. Update the `replay` docstring (a Raises line) and the module docstring line describing `ReferenceDateError`.
- `src/open_engine/_errors.py`: `ReferenceDateError` docstring names `replay`.
- `tests/test_replay_as_of_type.py` (new): parametrized over bad values, empty and non-empty ledger, resume from a snapshot, a bad `as_of` with an invalid row, `whole_ledger=True` without a snapshot.
- `README.md`: the `ReferenceDateError` bullet.
- `CHANGELOG.md`: Breaking entry under `### Changed`.

## Order of work

1. Write `tests/test_replay_as_of_type.py`; confirm it fails on main (TypeError or no raise). Commit red.
2. Add the check in `replay`; tests green.
3. Docs and changelog.
4. Full gate.

## Risks

- A caller in the package that passes a non-date to `replay`: none. `prequential_predictions` calls `_replay` directly with dates it computes.
- Order: the new check precedes the `whole_ledger` type check, so `replay([], as_of="x", whole_ledger=1)` now raises `ReferenceDateError` instead of `SnapshotError`. Both are usage errors; documented as "checked first".

## Not done here

`prequential_predictions` (and so `evaluate`, `fit_scale`, `fit_win_calibration`) does not check `score_from` and `anchor`: a datetime or str raises a bare `TypeError`, and with an empty ledger a bad `score_from` is accepted. That is four public functions, not a few lines in `replay`, so it goes to a follow-up issue.

## Proof

- Criteria 1, 2, 4: `tests/test_replay_as_of_type.py`.
- Criterion 3: the existing suite.
- No live drive beyond the gate: a library with no app; the tests call the public function directly.
