# Plan: Replay's recency decay shrinks old matches' updates, and evaluate() skips it (issue #13)

Risk tier: High. It removes a public `Config` field (a breaking API change), changes the update rule behind every published rating, and touches replay, which CLAUDE.md lists as security-sensitive. Each raise is well covered by unit tests, but the tier rounds up.

Domain skills: superpowers:test-driven-development, invoke. Django, Ninja, Celery, allauth, FastAPI, React and Expo skills: not applicable because this is a standard-library Python package with no web, data store, or UI layer.

## Decision

Decided with the maintainer on 2026-11-15:

- Remove step-size recency entirely: `Config.recency_half_life_days` and `recency_multiplier` go away. Staleness is handled only by RD inflation (`inflated_rd`), which already exists. Old results are forgotten the Elo way: later updates move the rating away from them.
- The issue's last criterion, "Re-run the evaluation and record the change", is out of scope for this repository. The real-data evaluation lives in a separate private repository, and CLAUDE.md forbids evaluation results here. This PR records the behavior change in `CHANGELOG.md` and proves it with synthetic tests. The PR body says the evaluation re-run happens elsewhere.

## Acceptance criteria

1. Replaying a ledger and recording the same ledger live, in ledger order, in a fresh engine give the same `rating`, `rd`, `matches_played`, `last_played`, `rd_as_of`, `partners`, and `opponents` for every player and format (exactly equal floats). Only `live_updates_since_replay` differs.
2. `replay()` and `record()` (and so `evaluate()`, `fit_scale()`, `fit_win_calibration()`, and `prequential_predictions()` in both modes) use one update rule: no step uses the replay date. `evaluate(mode="live")` and `evaluate(mode="scheduled")` give equal reports on a ledger spanning years.
3. A player whose only matches are years before `as_of` does not regress toward the default rating: replay gives that player the same rating and RD as live recording. For the issue's repro (one 11-2 doubles match three years before `as_of`, fresh players), each winner moves +0.15577 in replay and RD is 0.752, not +0.01947 and 0.794.
4. Replaying the same ledger at two different `as_of` dates on or after its last match gives the same ratings and RDs. A recalculation with no new matches no longer shifts ratings.
5. `Config` has no `recency_half_life_days` field, and `open_engine.engine` has no `recency_multiplier`. `Config(recency_half_life_days=365)` raises `TypeError`.
6. Inactivity is still reflected in uncertainty: `player_summary(..., as_of=later)` reports an RD inflated by `inflated_rd` (existing tests keep passing).
7. Docs: the module docstring's note that "every recalculation shifts ratings slightly even with no new matches" is removed and replaced by the rule in criterion 1. `CHANGELOG.md` records the removal as **Breaking** under `### Changed` (published ratings and `Config.fingerprint()` change) and the bug under `### Fixed`. The existing Unreleased line that lists `recency_multiplier` among unexported helpers is corrected, since the helper no longer exists.

## Files that change

- `src/open_engine/engine.py`
  - `Config`: delete the `recency_half_life_days` field, its comment, and its `__post_init__` check.
  - Delete `recency_multiplier`.
  - `RatingEngine._compute`: drop the recency factor from `effective_weight`, and drop the now-unused `as_of` parameter from `_compute` and its two call sites in `record`.
  - `RatingEngine.record`: keep its `as_of` keyword. It still marks the update as a replay (not live) and still rejects `as_of` before `played_on`. Update its docstring if it implies `as_of` changes the update; it should say `as_of` only labels the update and that the update itself does not depend on it.
  - Module docstring: replace the "Because recency decay depends on the replay date..." sentence with: a replay gives the same ratings as recording the same ledger live in ledger order; inactivity widens RD (`inflated_rd`) but never shrinks or discounts an old match's update.
- `tests/test_api.py`: remove `"recency_multiplier"` from `HIDDEN_HELPERS`. That test asserts the helper still exists in `open_engine.engine`; the helper is removed on purpose, so the row goes. This is the only edit to an existing test.
- New `tests/test_replay_live_parity.py`: the tests under Proof.
- `CHANGELOG.md`: entries per criterion 7.
- `README.md`: no change expected (grep finds no recency or decay text). Recheck in Phase 8.

## Order of work

1. Write `tests/test_replay_live_parity.py` with every test below. Run it on the unmodified engine and record which fail (expected: the parity, repro, as_of-invariance, evaluate-equality, and field-absence tests). Commit the red tests alone.
2. Change `engine.py` as above. Run the new tests green.
3. Update `tests/test_api.py` `HIDDEN_HELPERS`.
4. Run the full gate. If any other existing test fails, do not edit it: report it. A failure there means an expectation depended on decay, and that needs a decision.
5. `CHANGELOG.md` and the module docstring.
6. Run the full gate again, plus `python examples/basic_match.py`.

## Risks

- Every published rating changes, and `Config.fingerprint()` changes for the default config. That is intended and goes in the changelog as breaking. `Config.version` and `__version__` do not move (only a release moves them).
- The private evaluation repository constructs `Config(recency_half_life_days=...)` in its sensitivity grid and tests. Those break until that repository is updated. Out of scope here; the PR body says so.
- Riskiest step: removing `as_of` from `_compute`. If any other caller passes it, mypy or the tests catch it. `record`'s `as_of` stays, so no public signature besides `Config` changes.
- A test of "behaves alike" could pass trivially if the ledger is too short or too recent. The parity tests use ledgers spanning about three years with `as_of` well after the last match, and mixed doubles and singles, so decay on main would show (measured below: 0.485 rating difference).
- Not done: recalibrating `rd_growth_per_day` or other parameters to compensate. Calibration is the evaluation's job.

## Proof

Tests in `tests/test_replay_live_parity.py`, all on synthetic ids:

- `test_replay_matches_live_recording` (criterion 1): a seeded synthetic ledger of 120 or more matches spanning about three years, doubles and singles, with a category-doubles format so parent updates are exercised. Record live in `_ledger` order in a fresh engine; replay with `as_of` a year after the last match. Compare every field in criterion 1 for every player and format, exact equality; assert `live_updates_since_replay` is positive live and zero in replay.
- `test_old_match_moves_ratings_as_much_in_replay` (criterion 3): the issue's repro. Replay delta for each winner equals the live delta (0.15577) and RD equals live RD (0.752, to 4 places).
- `test_player_with_only_old_matches_keeps_their_rating` (criterion 3): a player who wins many matches only in the first year of a three-year span ends replay at the same rating as live, and at least 0.3 above the default.
- `test_replay_does_not_depend_on_as_of` (criterion 4): replay the same ledger at its last match date and at dates 1, 7, and 730 days later; ratings and RDs are equal.
- `test_live_and_scheduled_evaluation_agree` (criterion 2): `evaluate(ledger, mode="live")` equals `evaluate(ledger, mode="scheduled")` on the multi-year ledger (whole `EvaluationReport` equal), and `prequential_predictions` in both modes give the same expected values.
- `test_recency_option_is_gone` (criterion 5): `"recency_half_life_days"` not in `{f.name for f in dataclasses.fields(Config)}`; `pytest.raises(TypeError)` on `Config(recency_half_life_days=365)`; `not hasattr(open_engine.engine, "recency_multiplier")`.
- Criterion 6: existing `tests/test_engine.py` inactivity tests (`player_summary` with later `as_of`) stay green.
- Criterion 7: reviewed in Phase 8.

No live drive beyond `python examples/basic_match.py`: this is a library, and the tests drive the changed path directly.

Measured on `origin/main` (7e6bc3c) with `/tmp/.../scratchpad/measure.py` (a 120-match doubles ledger from 2023-01-01, every 8 days, `as_of` 2026-11-01):

- Issue repro: live delta for a winner 0.15577, RD 0.752; replay delta 0.01947, RD 0.794. Reproduces the issue.
- Default config (half-life 365): max |live - replay| over rating and RD = 0.48515. Live vs scheduled `evaluate` MSE: 0.068866 vs 0.067491.
- `Config(recency_half_life_days=None)`: max |live - replay| = 0.0, and live vs scheduled MSE 0.068866 vs 0.068866. So with decay gone, the "behave alike" tests hold exactly on the current update rule.
