# Plan: prequential_predictions does not check that score_from and anchor are plain dates (issue #90)

Risk tier: Medium. It changes existing behavior (a breaking change for an empty ledger with a bad date, which used to succeed), but the change is a pure argument check, fully unit-testable, in one function.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-allauth, django-celery, python-fastapi, React and Expo skills: not applicable because this is a standard-library Python package with no web, UI, or framework code.

Decision from the maintainer (counts as plan approval): at the top of `prequential_predictions`, before the ledger is read, check `score_from` and `anchor` (each when not None) with `_is_plain_date`, the helper `replay` uses for `as_of` since #66, and raise `EvaluationError` naming the argument. The three forwarding functions raise it too.

Finding from Phase 1: `EvaluationError` is the existing subclass for bad evaluation arguments (`mode`, `low`, `high`, `tolerance`). `evaluate`, `fit_scale`, and `fit_win_calibration` never read `score_from` or `anchor` themselves; they only forward them. `fit_scale` and `fit_win_calibration` check `low`, `high`, and `tolerance` first, and that order stays.

## Acceptance criteria

1. `prequential_predictions`, `evaluate`, `fit_scale`, and `fit_win_calibration` raise `EvaluationError` whose message names the argument (`score_from` or `anchor`) for a value that is not None and not a plain date: a str, a naive datetime, an aware datetime, an int. This holds for an empty ledger and a one-row ledger, in `"live"` and `"scheduled"` modes.
2. In `prequential_predictions` the check runs before the ledger is read: a ledger holding an invalid row still raises the date `EvaluationError`, not `InvalidMatchError`.
3. A `date` subclass that is not a datetime is still accepted, and `None` is still accepted.
4. Docstrings of the four functions say so. The README `EvaluationError` line names the new case.
5. `CHANGELOG.md`: a `**Breaking:**` entry under `### Changed` with a Migration note.

## Files that change

- `src/open_engine/engine.py`: the check at the top of `prequential_predictions`; four docstrings.
- `tests/test_evaluation_date_types.py` (new): criteria 1 to 3.
- `README.md`, `CHANGELOG.md`.

## Order of work

1. Red tests, committed alone.
2. The check.
3. Docs and changelog.
4. Gate: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.

## Risks

- A caller passing a datetime to `score_from` on an empty ledger got `[]` and now gets an error. Called out in the Migration note.
- Found in review: live mode never reads `anchor`, so a live call with any `anchor` used to run normally and now raises. A `score_from` that no row was compared against (rows all filtered out by `scoring`) was accepted too. The changelog entry names these cases.

## Proof

On main (f75cb03): `pp([m], score_from=datetime(...))`, `pp([m], score_from="2026-03-01")`, and `pp([m], mode="scheduled", anchor=datetime(...))` raise bare `TypeError`; `pp([], score_from="x")` returns `[]`. `tests/test_evaluation_date_types.py` covers criteria 1 to 3. No live drive beyond the test suite: the package has no app, and the tests call the public functions directly.
