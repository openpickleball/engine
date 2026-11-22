# Plan: Finish the engine method specification (issue #47)

Risk tier: Low. Documentation plus one new test file. No engine code, `Config` field, default, fingerprint, or snapshot changes.

Domain skills: none apply (standard-library Python package; docs and a pytest test). No framework, UI, API, data, auth, payments, or email skill is relevant.

## Context

Issue #47 asked for `docs/method.md`. `docs/model.md` (issue #125) now holds that specification: every equation with the function it comes from, starting states, K, team rating and the partner gap filter, match performance and `win_weight`, win probability and calibration, reliability, clamping and freezing, and, since #151, what a rating gap and the level mean. No new file is needed; the issue closes on `docs/model.md`. Three items remain:

1. No test checks the worked example against the engine, so the example can drift from the code.
2. The issue's assumption list is only partly stated: "matches independent given ratings", one model for singles and doubles, no partner effect, and a closed pool.
3. What a one-point team gap means as a win probability for an 11-point side-out game.

## Acceptance criteria

1. **Worked example, complete.** `docs/model.md` "A worked example" carries the second match (league best of 3, 11-9, 8-11, 11-6, sixty days later) through to its result: `E`, `P` (30 / 56), surprise, the deltas, imbalance, and each player's rating and RD after it. Figures measured on the default branch (see Proof): `E = 0.52644`, `P = 0.53571`, surprise `0.00927`, delta `+0.00580` for team A and `-0.00580` for team B (written to 5 places like the rest of the block, so the test's text check pins the last place; `MatchResult` reports `0.0058`), imbalance `0.0`, rating after `3.57246` (team A) and `3.42754` (team B), `rd_new = 0.7418463 * (1 - 0.08 * 1.116) = 0.67561`. The first match's text gains its after ratings (`3.56667` and `3.43333`) if not already shown. Round as the existing block does.
2. **Worked example test.** A new `tests/test_model_worked_example.py` records both matches with `RatingEngine.record` under `Config()` (invented ids `a1`, `a2`, `b1`, `b2`, dates as the doc gives or any fixed date sixty days apart). For each figure the doc's worked example states, it asserts both: the engine's value rounds to the stated figure, and the stated figure's text appears in the "A worked example" section of `docs/model.md` (read the file, cut the section from its heading to the next `## `). So a change to either the engine or the doc fails the test. Use `pytest.mark.parametrize` over a table of (label, stated text, engine value getter) where it reads clearly.
3. **Assumptions.** `docs/model.md` gets an "Assumptions" section, listed in the contents, right before "What the model does not do". It states, each checked against the code:
   - Matches are independent given the ratings before them: each update reads only the match and the stored states, never other matches' results (no batch or retroactive fit inside a replay).
   - One model for singles and doubles: the same expectation, performance, K, and update for every rating format, with a separate state per format (and the category link to overall doubles as "Categories and overall doubles" states).
   - No partner effect: a team rating is the mean of its players' ratings, with no term for a pair or for how well partners play together.
   - A closed pool: a rating is measured against the players a player has met, directly or through others (link "What a rating means").
   - The win probability's own assumptions stay in "Win probability" (link to them, do not repeat).
   Any claim that does not hold for every input its conditions allow is narrowed (CLAUDE.md "Writing style").
4. **Gap as win probability.** "Win probability" gains a short example: under the default config (scale 2.9, `a = 1.0`), for one side-out game to 11, win by 2, no cap (`GameFormat()`), best of 1, a team rating gap of 0.5 gives team A about 83.1% to win and a gap of 1.0 about 97.1%. Say these rest on a placeholder scale and calibration and on the point independence assumption, which only approximates side-out scoring. The test from criterion 2 (or a second test in the same file) checks these two figures against `RatingEngine.predict_win_probability` and their text in the "Win probability" section.
5. **External seed bullet** (scope addition approved by the maintainer during the work). In "What the model does not do", the bullet "No conversion of external scores" said an imported rating seeds a starting rating only. A seed also sets RD to `cfg.external_seed_rd` and an `initial_source_date` that freezes the player in matches on or before it (`_store_external_seed`, `_frozen_players`). Reword it to say an imported rating is a seed only that sets a starting state (link "Starting states"), and keep the rest of the bullet. No test.
6. `CHANGELOG.md` `## [Unreleased]` `### Changed`: documentation and a test only, no rating change (issue #47).
7. Gate passes: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.

## Files that change

- `docs/model.md`: worked example, a new "Assumptions" section with its contents entry, the "Win probability" example, the external seed bullet in "What the model does not do".
- `tests/test_model_worked_example.py`: new.
- `CHANGELOG.md`: one entry.
- This plan.

## Order of work

1. Write `tests/test_model_worked_example.py` against the doc figures above. Run it: it fails, since the doc does not yet state the second match's result or the win probability figures (record the failing output in the commit or the return).
2. Edit `docs/model.md` (worked example, Win probability example). Rerun the test green.
3. Add the Assumptions section and contents entry, checking each claim against `src/open_engine/` (`_engine.py` `_compute` and the team mean, `_replay.py`).
4. Changelog entry, then the full gate.

## Risks

- Text-match brittleness: the test should search for each figure as a plain substring of its section, not a whole line, so rewording the prose around a number does not fail it.
- Rounding: assert `round(engine_value, places) == stated` with the places the doc shows, not float equality of unrounded values.
- "Matches independent given ratings" can read as a claim about prediction quality. State it as how the update works.
- Win probability figures look strong (97% for a 1.0 gap). Keep the placeholder and side-out caveats beside them so nobody reads them as calibrated.

Chose not to do: a new `docs/method.md` (model.md is the spec); any change to the math.

## Proof

Measured on the default branch (`origin/main` at the time of planning) in this worktree:

- Worked example through the engine (`RatingEngine(Config())`, `record` of both matches): first match `expected_a=0.5, actual_a=0.61111, surprise=0.11111, effective_weight=1.0`, deltas `+/-0.06667`, `imbalance=0.0`, a1 after `3.566666666666667`, RD `0.7360000000000001`. Second match `expected_a=0.52644, actual_a=0.53571, surprise=0.00927, effective_weight=1.116`, deltas `+/-0.0058`, `imbalance=0.0`, a1 after `3.5724628717102878`, b1 after `3.4275371282897122`, RD `0.6756143035701527`.
- Win probability: `expected_point_share(4.0, 3.5, 2.9) = 0.5979660470796391`, `game_win_probability(that, GameFormat()) = 0.8313833503363468`; for a 1.0 gap `0.6886884601202378` and `0.970835058757726`. `GameFormat()` is side-out, to 11, win by 2, no cap.
- Criterion 2 and 4: the new test. Criterion 6: gate output.
