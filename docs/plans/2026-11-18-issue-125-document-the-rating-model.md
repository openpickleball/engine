# Plan: Document the rating model and name its constants (issue #125)

Risk tier: Low. Documentation, docstrings, and renaming literals as module constants with the same values. No change to behavior, `Config`, the fingerprint, the snapshot schema, or any output.

Domain skills: superpowers:test-driven-development, invoke (there is no new behavior, so the proof is an output-equality check against main, below). Django, FastAPI, React, and the other listed framework skills: not applicable because this is a standard-library Python package with no web, UI, data store, auth, payments, or email code.

## Acceptance criteria

1. `docs/model.md` exists and states every equation the engine uses to move a rating or compute reliability, and each one matches the code: the team rating, the expected point share (with its clamp), match performance (point share blended by `win_weight`), the evidence weight (match weight, self-reported repeat factor, information multiplier), RD growth for inactivity, K from RD, the per-player rating update with its clamp to the rating range, the RD shrink, frozen players, `imbalance`, the starting states (new player, imported seed, category derived from overall doubles), category matches feeding overall doubles, the eligibility checks that stop an update, reliability, game and match win probability and its calibration, and the evaluation and fits (`prequential_predictions`, `evaluate`, `fit_scale`, `fit_win_calibration`).
2. `docs/model.md` lists each `Config` parameter the math reads, with its default and its role, and says which defaults are uncalibrated placeholders or declared choices. It carries the demonstration label.
3. Every "always" or "never" claim in `docs/model.md` holds for every input its stated conditions allow (CLAUDE.md, Writing style). Where unsure, the doc says "can" and names the case, or points to the behavior note in the `engine.py` module docstring.
4. `player_k`, `information_multiplier`, `_actual_point_share`, `_rolling_unique_count`, `_clone_state`, and `clone_player` have docstrings that state what they compute. `expected_point_share`, `inflated_rd`, `player_k`, `information_multiplier`, `_actual_point_share`, and `reliability` point to `docs/model.md`.
5. These literals on the rating, prediction, and reliability paths become named module constants with a comment saying why each has its value, with the same values:
   - the expected-share exponent clamp `12.0` in `expected_point_share`;
   - the reliability blend `0.60` and `0.40` in `_reliability`;
   - the clamp detection tolerance `1e-12` in `_compute`;
   - the rounding places: 5 for `MatchResult` numbers (`deltas`, `expected_a`, `actual_a`, `surprise`, `effective_weight`, `imbalance`) and `_scored_pairs`, which mirrors them; 3 for `PlayerSummary.rating`; 4 for `PlayerSummary.rd`; 1 for reliability (`_reliability`, the connectivity cap display including its `- 0.1` step, and `imported_reliability`).
6. The README links `docs/model.md`.
7. Every output is unchanged: the gate passes with no test edited, and the output-equality check below gives identical results on `origin/main` and the branch.

## Files that change

- `docs/model.md` (new).
- `src/open_engine/engine.py`: docstrings for the functions in criterion 4; the constants in criterion 5, defined near the code that uses them (the math helpers section for the share clamp, reliability blend, and clamp tolerance; next to `MatchResult` and `PlayerSummary` for the rounding places, or one block in the math helpers section if that reads better).
- `README.md`: one sentence linking `docs/model.md`, near the top (for example at the end of "Technology") or in a short "How ratings move" section.
- `CHANGELOG.md`: an entry under `## [Unreleased]`, `### Added`: `docs/model.md`, with a note that no behavior changed.

## Order of work

1. Read the code paths in full: `expected_point_share`, `inflated_rd`, `player_k`, `information_multiplier`, `_actual_point_share`, `_reliability`, `RatingEngine._new_state`, `_consider`, `_compute`, `player_summary`, `seed_external_rating` (the state it stores), `add_player` (seed rules), `winprob.py`, `_prequential`, `_predict_match`, `_is_established`, `_scored_pairs`, `_report_from_predictions`, `fit_scale`, `fit_win_calibration`. Also the module docstring's behavior notes, which `docs/model.md` must not contradict.
2. Name the constants (criterion 5). Run the gate.
3. Add the docstrings (criterion 4).
4. Write `docs/model.md`. Suggested outline: Status; Notation; Rating state; Starting states; Expected point share; Match performance; Evidence weight; Inactivity; K; The update (with clamp, RD shrink, frozen players, imbalance); Categories and overall doubles; When an update does not count (pointing to `RejectionReason`); Reliability (and the labels `established` and connectivity cap, which change no rating); Win probability; Evaluation and calibration; Parameters (table: field, default, role, status); What the model does not do (no rating drift for inactivity, no rating for non-completed matches, team movement not zero-sum). Write equations in plain text or Markdown code blocks, not LaTeX, so they read on GitHub and in a terminal. Name the function each equation comes from.
   As built: "When a match does not count" moved before the team rating, since the checks run before any rating math, so the page follows a match in processing order. A short worked example follows "Categories and overall doubles". The cap display step is its own constant, `_RELIABILITY_STEP`. The docs pass also added one descriptive line for `docs/model.md` to the Documentation section of `CLAUDE.md`, which lists the project's docs. At the maintainer's request after review, that line also requires a pull request that changes the model's math, a `Config` field or default, or a named model constant to update `docs/model.md` in the same pull request.
5. README link and changelog entry.
6. Output-equality check, then the full gate.

## Risks

- A doc claim that is false for some input. Mitigation: each equation names its function; check edge cases (clamps, frozen players, the win_weight blend, RD bounds, an rd_as_of of None) against the code, and prefer "can" with the case named where a claim is not universal.
- A constant rename that changes a value or a type (for example `12` vs `12.0`, or the cap display step). Mitigation: same literal values and types; output-equality check.
- Docstrings that repeat the module docstring's long edge-case lists. Keep the new ones short; issue #126 restructures the rest.

Not done here (other issues): moving the behavior notes (#126), splitting the module (#127), breaking up `_compute` (#128). Evaluation report rounding (6 and 4 places) and the fit result rounding (3 places) stay as they are: they are not on the rating, prediction, or reliability path.

## Proof

- Criterion 7: the full gate (`ruff check .`, `ruff format --check .`, `mypy src`, `pytest`) with no test file changed (`git diff --stat origin/main -- tests` is empty).
- Criterion 7: an output-equality script in the scratch directory (not committed) that builds a synthetic ledger with invented ids (singles, doubles, and a mixed doubles category; side-out and rally; best of 1 and 3; tournament, league, rec_verified, and confirmed self-reported matches; one imported seed; dates spanning a long gap; a lopsided match that hits the rating floor or ceiling under a narrow rating range), replays it, and prints a SHA-256 of `json.dumps(engine.to_snapshot(), sort_keys=True)`, every `MatchResult` from live `record` calls, every `PlayerSummary`, `predict_win_probability` for a few pairs, and an `evaluate` report. Run with `origin/main`'s `src` and the branch's `src` on `PYTHONPATH`; the outputs must be byte-identical. Also `python examples/basic_match.py` output identical on both.
- Criteria 1 to 3: the review and verify phases check each equation in `docs/model.md` against the named function.
- Criterion 4 and 5: `grep` for the old literals on those paths returns nothing; an AST check that the six functions have docstrings.
