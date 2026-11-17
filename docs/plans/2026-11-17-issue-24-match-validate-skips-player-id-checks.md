# Plan: Match.validate skips player ID checks, so a bad ID leaves a partial write (issue #24)

Risk tier: Medium. The change tightens `Match.validate`, which every `record` and every ledger row goes through, so it alters existing behavior: ledgers that replayed before can now raise. It is small, standard-library only, and fully unit-testable.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-celery, django-allauth, python-fastapi, React and Expo skills: not applicable because this is a standard-library-only Python package with no framework, API layer, or UI.

## What main does today

Measured on `origin/main` (fe6f4ff) with synthetic ids:

- `team_a=("b", "")` and `team_a=("b", " ")`: `record` raises `InvalidMatchError("Player ids must be non-empty strings")` and the engine has no players and no processed ids. The player id part of the issue is fixed (CHANGELOG, Fixed: blank player ids).
- `played_on=datetime(2026, 1, 5, 10)`: `record` counts it. A later `record` with a plain `date` raises `TypeError: can't compare datetime.datetime to datetime.date`, and `replay` of the two rows raises the same `TypeError`. A `str` `played_on` raises `TypeError` in `replay`. Not fixed.
- `games=((11, True),)`: `record` counts it. `tests/test_content_key_scores.py::test_a_bool_score_has_the_same_content_as_its_int` pins that. Not fixed.

## Acceptance criteria

1. Invalid player ids (empty, whitespace-only, not a str) fail in `validate()`, and a failed `record()` leaves the engine unchanged: `to_snapshot()` and `players` are equal before and after. A test covers both teams and both positions.
2. `Match.validate` raises `InvalidMatchError` for a `played_on` that is not a plain `date` (a `datetime`, a `str`, `None`), before anything changes. `record` and `replay` raise `InvalidMatchError`, not `TypeError`, for such a row, wherever it sits in the ledger.
3. `Match.validate` raises `InvalidMatchError("Game scores must be integers")` for a `bool` game score, in either position. `record` leaves the engine unchanged after it.
4. No rating changes for any ledger that still validates.
5. `CHANGELOG.md` records the change as **Breaking** under `## [Unreleased]`, and the README's ledger-order validation note mentions the new rejections only if it lists such cases.

## Files that change

- `src/open_engine/engine.py`, `Match.validate`: before the `played_at` checks, `if not _is_plain_date(self.played_on): raise InvalidMatchError(...)`. In the score loop, reject `bool` with the existing integer message. `_is_plain_date` is defined later in the module; it is a module-level function, so calling it at run time is fine.
- `tests/test_match_field_types.py` (new): criteria 1 to 3, with `pytest.raises(InvalidMatchError, match=...)`.
- `tests/test_content_key_scores.py`: the bool-score test pins behavior this issue removes. Replace it with a test that a bool score is rejected before the content key is computed.
- `CHANGELOG.md`: a **Breaking** entry under `### Changed` (or `### Fixed`, matching the blank-id entry), citing issue #24.

## Order of work

1. Commit this plan.
2. Write the new tests; run them on the branch and record the failures.
3. Change `Match.validate`; update the bool content-key test; run the gate.
4. Changelog entry.

## Risks

- Ledgers in the backend that send `bool` scores or `datetime` dates now raise. That is the point, and the error is an `InvalidMatchError`.
- `_canonical` treats `bool` and `int` as different types; with bools rejected earlier, that branch is unreachable for scores, which is fine.

## Out of scope

Other `Match` fields with no type check (`sequence`, `best_of`, `rating_format`, `match_type`, `team_a`/`team_b` containers, `confirmed_by`) raise `TypeError` or `AttributeError` or are accepted. None of them writes state before raising. Filed as a separate issue.

## Proof

- Criterion 1: `test_blank_player_id_changes_nothing` (passes on main; regression guard).
- Criterion 2: `test_non_date_played_on_is_rejected` (record and replay, parametrized over datetime, str, None).
- Criterion 3: `test_bool_score_is_rejected`.
- Criterion 4: the existing suite.
