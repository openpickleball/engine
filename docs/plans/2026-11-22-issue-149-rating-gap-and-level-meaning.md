# Plan: State what a rating gap and the rating level mean (issue #149, docs subset)

Risk tier: Low. Documentation only: no code, `Config` field, default, fingerprint, or snapshot changes. The risk is a claim in the docs that does not hold for every input its conditions allow.

Domain skills: none apply (standard-library Python package; a documentation change). No framework, UI, API, data, auth, payments, or email skill is relevant.

## Scope decision

Issue #149 part 1 proposed fixing the side-out scale by declaration and retuning K, if a pre-registered out-of-sample test showed no loss of accuracy. Part 1 (a declared scale, new `scale_by_scoring` and K defaults) is not done here. It waits on evaluation outside this repository and stays open on #149. The maintainer chose to do the documentation parts now: part 2 (what the level means), part 3 (no tuning toward an external rating), and a statement of what a gap means under the current defaults, labeled as depending on a placeholder scale.

No evaluation result on real match data goes into this repository (CLAUDE.md hard rule 2). The docs state only what follows from the code and the default `Config`.

## Acceptance criteria

1. `docs/model.md` has a new section, "What a rating means", listed in the contents, placed right after "Expected point share" (so it can use `E` and `scale`). It states:
   - **The gap.** The expected point share depends only on the gap between the two team ratings, divided by the scale (from the equation, no new claim). Under the default scales (2.9 for side-out and rally), a team rating gap of 0.5 means the higher team is expected to win about 59.8% of points, and a gap of 1.0 about 68.9%. These figures are computed from `expected_point_share` and match it to the stated precision.
   - The meaning is stated as a point share, not a win probability, because the win probability also depends on the game model and its calibration (`win_calibration_by_scoring`), which link to the "Win probability" section.
   - The default scale is a placeholder: a different `scale_by_scoring` changes what a gap means. `Config.fingerprint()` records the scale, so the meaning behind any replay is on record. Fixing the meaning of a gap by declaring the scale is an open question (issue #149).
   - **The level.** A new player with no seed starts at `cfg.default_rating` (3.5). A rating places a player relative to the players they have played, directly or through others. In singles or overall doubles, in a pool of players who all start at the default rating with no seeds, the pool's mean rating moves only through imbalance (linked), so it stays near 3.5 whatever the pool's skill when imbalance is small. A category pool is excluded: a category state can start from the player's overall doubles rating, so its total does not follow `net_rating_created` (found by the Proof scratch check). Two such pools are comparable only through players who play in both.
   - **External ratings.** An imported rating is a seed only: it sets a starting rating, the imported reliability is shown separately from OPEN-earned reliability and is never converted into RD or OPEN-earned reliability, and OPEN matches then move the rating like any other. It cannot replace a state once a counted match included the player in that format (link to "External seeds" in `docs/behavior.md`, which holds the full rules).
   - **No tuning toward an external rating.** No `Config` value is chosen to bring OPEN ratings closer to an external rating. A default that changes a rating or a prediction changes only when out-of-sample prediction supports it (or for an integrity reason, as the engine rules say).
2. The parameter table's lead-in in `docs/model.md` ("Parameters") states the same no-tuning-toward-an-external-rating rule in one sentence, or links to it.
3. `README.md` has a short "What a rating means" section (a few sentences) that summarizes the gap and level statements and links the new `docs/model.md` section with an absolute GitHub URL, like the README's other links. It keeps the demonstration label and names no external rating system. The README stays under about 20 KB.
4. `CLAUDE.md` "Engine rules", under "External ratings are a seed only", gains one sentence: no `Config` value is chosen to move OPEN ratings toward an external rating.
5. `CHANGELOG.md` gets an entry under `## [Unreleased]`, `### Changed`: documentation only, naming the new section, saying no rating, default, or fingerprint changed, and that the declared scale part of #149 stays open.
6. Every always/never claim in the new text holds under its stated conditions (see Proof). No competing rating system is named. No real-data result appears.
7. The gate passes: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.

## Files that change

- `docs/model.md`: new section, contents entry, one sentence in "Parameters".
- `README.md`: new short section.
- `CLAUDE.md`: one sentence in "Engine rules".
- `CHANGELOG.md`: one entry.
- `docs/plans/2026-11-22-issue-149-rating-gap-and-level-meaning.md`: this plan.

No code or test file changes. There is no behavior change, so no new test; the existing README and doc tests must still pass.

## Order of work

1. Compute the point shares with the engine (see Proof) and keep the command and output.
2. Write the `docs/model.md` section and contents entry; add the "Parameters" sentence.
3. Write the README section.
4. Add the `CLAUDE.md` sentence and the changelog entry.
5. Run the claim checks in Proof, then the gate.
6. Commit as one docs commit, signed off.

## Risks

- A claim that overreaches. The pool-level claim is the riskiest: seeds, unequal K, clamping at the bounds, and frozen players all move a pool's mean. Scope it to unseeded pools and say the mean moves only through imbalance, which is true by construction (`imbalance` is the net change per match, and `ReplayInfo.net_rating_created` totals it).
- "Depends only on the gap": true of the equation `x = (R_B - R_A) / scale`. Float subtraction of shifted ratings can differ in the last bits; the docs state the math, not bit equality, so avoid "exactly the same output for shifted inputs".
- The README section repeating too much of `docs/model.md`: keep it a summary with a link, as #144 set.
- Accidentally implying the 2.9 scale is calibrated or that a gap's meaning is fixed. Keep "placeholder" next to the figures.

Chose not to do: change `scale_by_scoring` or K defaults (part 1 waits on evaluation outside this repository); add any new `Config` field; mention the evaluation's figures.

## Proof

- Criterion 1 figures: run on the default branch and record the output:
  `python -c "from open_engine import expected_point_share as e; print(e(3.75,3.25,2.9), e(4.0,3.0,2.9))"`, expecting about 0.598 and 0.689. Also check the signature of `expected_point_share` first and adapt the call to it.
- Criterion 6, pool level: the claim "the pool's mean moves only through imbalance" is checked by a scratch run (not committed): replay a synthetic unseeded ledger of a few players, then compare `fsum(final ratings) - n * 3.5` with `net_rating_created` from `ReplayInfo`, which already totals the unrounded per-player changes (the sum of `team_size` times the unrounded imbalance), so no extra weighting is needed. Run per rating format, including mixed doubles.
- Criteria 3 and 7: the gate output, and `wc -c README.md`.
- Live drive: none beyond the scratch checks above; nothing runs that this change touches.

Measured on the default branch (`e62468c`) before the plan was committed: `python -c "from open_engine import expected_point_share as e; print(e(3.75,3.25,2.9), e(4.0,3.0,2.9))"` printed `0.5979660470796391 0.6886884601202378`. The signature is `expected_point_share(team_a_rating, team_b_rating, scale)`.
