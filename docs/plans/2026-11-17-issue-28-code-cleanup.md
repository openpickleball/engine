# Plan: Code cleanup (issue #28)

Risk tier: Low. Two items remain: a docs sentence and argument checks on the two calibration fits. No rating, snapshot, replay, or `Config` path changes. Every rule is unit-testable.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-celery, django-allauth, python-fastapi, React and Expo skills: not applicable because this is a standard-library-only Python package with no framework, API layer, or UI.

## Status of each item on main (3207f18)

- `norm` inside `fingerprint` untyped: done. `def norm(value: object) -> object` (a09c0c4, PR #29).
- Bare `tuple` and `dict` types: done. No bare `tuple`, `dict`, `list`, or `set` annotation is left in `src/` (PR #29 and later).
- mypy errors on `initial_source_date`, `primary_updates`, `parent_format`: done. `mypy --strict src` passes (PR #29).
- `evaluate()` does not reject played seeds: done. `prequential_predictions` (so `evaluate`) checks every seed through `_add_seed`, which applies the seed rules, `matches_played must be 0` included (243ebb2 and 89e3170, PR #46; identity rules in PR #75).
- `evaluate()` calls private `_peek_state`: closed with a reason. `RatingEngine.state` is the public read (read-only since PR #64). The remaining call is `_is_established`, a private helper in the same module, which needs a read that does not raise for a player with no record yet (`state` raises `UnknownPlayerError`). That is engine-internal code, not an outside caller of a private name.
- Module docstring and README statement about the external rating source: open. The module docstring already says "standard external rating system"; the README does not say that none of its code or internals are used.
- Public classes and enums lack docstrings: done. `RatingFormat`, `MatchType`, `ScoringType`, and every public method have docstrings, and a test checks them (50518c0, PR #39).
- `fit_scale()` does not validate `low < high` or `tolerance > 0`: open.

## Acceptance criteria

1. `fit_scale` raises `EvaluationError` before any prediction work when `low` or `high` is not finite, when `low >= high`, or when `tolerance` is not finite or not greater than 0. Today `tolerance <= 0` hangs in the golden-section loop and `low >= high` returns a meaningless fit.
2. `fit_win_calibration` applies the same checks (same defect, same search code).
2a. Added after review: both fits also reject a bound too large to double (`2 * low` or `2 * high` not finite), since the search takes widths and midpoints of points in the range and each is at most twice the larger bound in size, and both golden-section loops end when the bracket stops shrinking, so a positive tolerance below the float spacing near the optimum no longer hangs. The break fires only when the bracket width does not decrease, which never happens before the tolerance stops a normal search, so valid fits are unchanged.
3. Valid arguments give exactly the same fits as before.
4. The README states that a standard external rating system is the intended source of seed ratings and that the engine uses none of its code or internals. It makes no broader claim about when an import can replace a rating (see issue #79).
5. A synthetic ledger's `replay` output, `to_snapshot()` JSON, and `Config().fingerprint()` are byte-identical before and after the change.
6. `mypy --strict src` passes, plus the full gate.
7. Changelog entry under `## [Unreleased]` `### Fixed`.

## Files that change

- `src/open_engine/engine.py`: a private `_check_search_bounds(low, high, tolerance)` helper, called first in `fit_scale` and `fit_win_calibration`; docstrings name the new raises.
- `tests/test_calibration.py`: parametrized tests for both fits.
- `README.md`: one statement in "Technology"; the `EvaluationError` line names the fit arguments.
- `CHANGELOG.md`: a Fixed entry.

## Order of work

1. Capture the baseline digest (replay results, snapshot, fingerprint) on this branch before any code change.
2. Red tests for criteria 1 and 2 (`low >= high` cases fail on main by returning a fit; the tolerance cases hang on main, proven with a timeout run below).
3. The helper and the two calls.
4. README, changelog.
5. Recompute the digest and compare. Full gate.

## Risks

- A check that rejects a valid call. Only bounds that are not finite or too large to double, `low >= high`, and a tolerance that is not finite and greater than 0 are rejected. `low <= 0` is left to `Config`, which already rejects a non-positive scale; `fit_win_calibration` allows any range within those checks.
- The checks run before `prequential_predictions`, so a call with both bad bounds and a bad ledger now reports the bounds first.

## Proof

- Criteria 1 and 2: `tests/test_calibration.py` parametrized tests with `pytest.raises(EvaluationError, match=...)`.
- Criterion 3: the existing calibration tests stay green.
- Criterion 5: the digest script in the PR body, run before and after.

Measured on main (3207f18), with `prequential_predictions` patched to 10 synthetic predictions and a synthetic MSE for `fit_scale`, each call under `timeout 10`:

- `fit_win_calibration(low=3.0, high=0.5)` returns `a=1.661, at_bound=True`; `low=high=2.0` returns `a=2.0`; `tolerance=0.0` returns `a=0.2`; `tolerance=-1.0` times out (exit 124).
- `fit_scale(low=3.0, high=0.5)` returns `scale=1.839`; `low=high=2.0` returns `scale=2.0`; `tolerance=0.0` and `tolerance=-1.0` time out (exit 124).
