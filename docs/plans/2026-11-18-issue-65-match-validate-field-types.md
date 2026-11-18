# Plan: Match.validate type-checks sequence, best_of, rating_format, match_type, teams, and confirmed_by (issues #65 and #83)

Risk tier: Medium. Validation only: no rating math, Config, or snapshot schema change, and every rule is unit-testable. Raised from Low because it changes the public input contract (rows that used to be accepted now raise) and alters existing behavior. `match_type` is security-sensitive input (it sets evidence weight and `established`), but the change only narrows what is accepted.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-celery, django-allauth, python-fastapi, React and Expo skills: not applicable because this is a standard-library-only Python package with no framework, API layer, or UI.

Issue #83 (`match_type`) is one row of #65's table. One branch, one PR, closing both.

## Decision (recorded on #65 and #83)

Reject. `Match.validate` raises `InvalidMatchError` when `match_type` is not a `MatchType` or `rating_format` is not a `RatingFormat`, including a str equal to a member's value. This matches the existing `status` check. The "str vs enum" case in `tests/test_snapshot.py::test_digest_separates_rows_canonical_separates` becomes an invalid-match test. Breaking: changelog entry.

## What main does today

Measured on `origin/main` (ce911f2), base `Match("m1", ("b", "c"), ("d", "e"), ((11, 5),), date(2026, 1, 5), RatingFormat.DOUBLES)`, `RatingEngine().record(...)` with one field changed:

| Field | Value | Result on main |
| --- | --- | --- |
| `sequence` | `"1"` | `TypeError` |
| `sequence` | `True`, `1.5` | accepted, counted |
| `best_of` | `"3"` | `TypeError` |
| `best_of` | `True`, `1.0` | accepted, counted |
| `rating_format` | `"doubles"` | `AttributeError` |
| `match_type` | `"tournament"` | accepted, counted |
| `match_type` | `"bogus"` | `KeyError` |
| `team_a` | `["b", "c"]` | `TypeError` |
| `confirmed_by` | `"b"`, `["b"]` | accepted, counted |
| `game_format` | `"x"` | `AttributeError` (also fixed) |
| `played_at` | `"2026-01-05"` | `AttributeError` (also fixed) |

## Design

In `Match.validate` (`src/open_engine/engine.py`), add type checks that raise `InvalidMatchError` with a message naming the field and the type given, in the form of the existing status check (`"<field> must be a <Type>, not <type name>"`). Each check runs before the first use of the field it guards:

- `rating_format`: `isinstance(..., RatingFormat)`, before `self.rating_format.team_size`.
- `match_type`: `isinstance(..., MatchType)`. A str equal to a member's value is rejected.
- `team_a`, `team_b`: each must be a `tuple` (the annotated type). A list is rejected, so `team_a + team_b` can never mix types. No test or example on main passes a list team (measured: a tuple-only probe check ran the full suite green). `games` keeps accepting a tuple or list; it is not part of this issue.
- `confirmed_by`: a `set` or `frozenset`, checked before `set(self.confirmed_by)`. Its members are already required to be participants, which are non-blank strs.
- `sequence`, `best_of`: an `int` and not a `bool` (an int subclass is accepted, as for game scores), checked before their range checks. A float such as `1.0` or `1.5` is rejected.
- Also fixed (same function, a few lines): `game_format` must be a `GameFormat` before `self.game_format.validate()`, and `played_at` must be `None` or a `datetime` before `_utc_instant`.

`record`, `replay` (through `_ledger`), and `prequential_predictions` already call `validate` before any state change, so each raises before anything changes and wherever the row sits in a ledger.

No other code changes. `record`'s equality comparison of `match_type` (from #59) stays; it now only ever sees members.

## Acceptance criteria

1. Each case in the table above (including the also-fixed rows) raises `InvalidMatchError` from `Match.validate()`, with a message naming the field.
2. `RatingEngine.record` raises `InvalidMatchError` for each case and leaves the engine unchanged (`to_snapshot()` equal before and after, no player created).
3. `replay` raises `InvalidMatchError` for such a row wherever it sits: first, middle, last, and after `as_of`. `prequential_predictions` raises it too.
4. A str equal to a member's value (`match_type="self_reported"`, `"tournament"`, `rating_format="doubles"`) is rejected.
5. Valid rows are unchanged: enum members, tuple teams, set or frozenset `confirmed_by`, int (and int subclass) `sequence` and `best_of`, `played_at` `None` or an aware datetime all validate as before. The full suite stays green apart from the tests listed under "Tests that change".
6. Every new raise is `InvalidMatchError`, an `OpenEngineError`; tests assert the exact type with `pytest.raises(InvalidMatchError, match=...)`.
7. CHANGELOG has a `**Breaking:**` entry under `### Changed` for #65 and #83, with a migration note (pass enum members, tuples, a set or frozenset, and ints). The #59 Fixed entry that says `Match.validate` accepts a str `match_type` is updated so it no longer contradicts the code. README states the new rule near the `Match.status` paragraph.

## Files that change

- `src/open_engine/engine.py`: `Match.validate`, and the `Match` docstring if it states field types.
- `tests/test_match_field_types.py` (new): parametrized rule table for criteria 1 to 6.
- `tests/test_snapshot.py`: the "str vs enum" and "int vs float best_of" params of `test_digest_separates_rows_canonical_separates` move to a new test asserting `InvalidMatchError` from `validate()` for those rows.
- `tests/test_engine.py`: `LOOSE_PAIRS` drops the `best_of` (3 vs 3.0) and `match_type` (enum vs str) pairs; a new test asserts `replay` and `prequential_predictions` raise `InvalidMatchError` for a ledger holding the loose row, in either order. The `points_to` pair stays (GameFormat field types are out of scope).
- `tests/test_str_match_type.py`: its premise (validate accepts a str `match_type`) is reversed by the decision. Replace its tests with ones asserting that a str `match_type` (`"self_reported"`, `"tournament"`) raises `InvalidMatchError` from `validate`, `record` (engine unchanged), and `replay`. Update its module docstring.
- `CHANGELOG.md`, `README.md`.

## Order of work

1. Write `tests/test_match_field_types.py`; confirm it fails on the current code for the expected reasons (wrong exception type, or accepted). Commit it red alone.
2. Add the checks to `Match.validate`; the new tests pass.
3. Update the existing tests listed above, with the reason in the commit body.
4. CHANGELOG and README.
5. Full gate.

## Risks

- Breaking for callers that pass str enum values, lists for teams, a list or str for `confirmed_by`, or float or bool `best_of` or `sequence`. A stored ledger with such rows now raises on replay, and a resumable snapshot taken from one cannot be resumed with the same rows: the rows must be corrected and replayed in full. Changelog says so.
- Check order: a check placed after the first use of its field would still let a `TypeError` or `AttributeError` escape. Tests use one bad field at a time on an otherwise valid match, so each would catch that.
- Not done: type checks on `GameFormat` fields (`points_to`, `win_by`, `cap`, `scoring`), which `GameFormat.validate` uses and which raise `TypeError` for a str. Separate function and wider effect; filed as a follow-up if no issue exists.

## Proof

- Criteria 1, 4, 6: `test_match_field_types.py` validate table.
- Criterion 2: `test_match_field_types.py` record test with `to_snapshot()` comparison.
- Criterion 3: `test_match_field_types.py` replay positions test and prequential test; `test_engine.py` loose-row test.
- Criterion 5: positive table in `test_match_field_types.py` and the existing suite.
- Criterion 7: changelog and README review.
- Live drive: the repo has no app. Run the issue's repro script against the branch and show each row raises `InvalidMatchError`.
