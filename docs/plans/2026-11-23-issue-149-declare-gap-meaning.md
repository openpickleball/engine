# Plan: Declare what a rating gap means and state what the level means (issue #149, rescoped)

Risk tier: Low. Documentation only: no code, `Config` field, default, fingerprint, parameter file, or snapshot changes. The risk is a figure or claim in the docs that the engine does not give.

Domain skills: none apply (standard-library Python package; a documentation change). No framework, UI, API, data, auth, payments, or email skill is relevant.

## Scope decision (rescope)

The issue's "Done when" asked that `Config` defaults reflect the declared scale. The maintainer decided on 2026-11-23 not to change the defaults. The evaluation's default-change rule, fixed before it ran, did not pass its gain bar for v1 against `Config()` (it passed only the non-inferiority bar); PARAMETERS.md records the figures. So the declared unit applies to the published parameter set `v1` (`params/v1.json`: scale 1.0 for side-out and rally), and the defaults are revisited with the same rule on new data after the evaluation's pre-registered future-play test. The earlier plan, `2026-11-22-issue-149-rating-gap-and-level-meaning.md`, did the level and no-tuning text; this plan finishes the gap part.

Not done here: any `Config` default change, any edit to `params/v1.json`, and the "demonstration" wording change (issue #49).

## Acceptance criteria

1. `docs/model.md`, "What a rating means", states the gap meaning for parameter set `v1` as expected point share, not win probability: at scale 1.0, a team gap of 0.5 gives the stronger team about 76.0% of the points, and a gap of 1.0 about 90.9%, with the `expected_point_share` calls that give them.
2. The same section says the meaning is fixed by declaring the scale, not by fitting it, and is recorded in the parameter version, so a later change of meaning is a visible, versioned decision.
3. The same section says plainly that the `Config()` defaults are placeholders and do not carry this meaning, and that a caller loads `v1` with `Config.from_parameter_set` for it. It says why the defaults stay (the default-change rule, linking PARAMETERS.md) and that they are checked again on new data. The sentence calling the declared scale an open question (issue #149) goes.
4. The level statement covers: every player starts at 3.5 (a new player with no seed, and V1 does not seed); a rating places a player relative to the players they have played, directly or through others; pools that share no players are not comparable; external ratings are only a seed, shown separately, never a conversion; the evaluation's seeding test (GeoBradDev/open-engine-evaluation#24) did not pass, so V1 does not seed.
5. No tuning toward an external rating is stated (already in `docs/model.md`; kept, and in the README).
6. `README.md` "What a rating means" summarizes criteria 1 to 5 in plain language for players and organizers, with the link to the `docs/model.md` section.
7. `CHANGELOG.md` has a docs-only entry under `## [Unreleased]`, `### Changed`, saying no rating, default, fingerprint, or parameter set changed.
8. No competing rating system is named, no real match data or evaluation result appears beyond a citation by repository and issue, no emojis or em dashes.
9. The gate passes: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest -n auto`.

## Files that change

- `docs/model.md`: the gap and external-rating paragraphs of "What a rating means".
- `README.md`: the "What a rating means" section.
- `CHANGELOG.md`: one entry.
- This plan.

No test pins the changed text: the only test that pins gap figures (`tests/test_model_worked_example.py`, `GAP_FIGURES`) pins the "Win probability" worked example under `Config()`, which does not change.

## Order of work

1. Compute the figures with the engine (Proof).
2. Edit `docs/model.md`, then `README.md`, then `CHANGELOG.md`.
3. Run the gate. Commit as one docs commit, signed off.

## Risks

- Stating a figure the engine does not give: both are computed below and given to one decimal, as the engine rounds them.
- Implying v1 is an OPEN rating or the default: keep the demonstration label and say `Config()` is unchanged.

## Proof

Measured on the branch at `1851143` (origin/main), before any edit:

`uv run python -c "import json; from open_engine import expected_point_share as e, Config; c=Config.from_parameter_set(json.load(open('params/v1.json'))); print(dict(c.scale_by_scoring)); print(e(3.75,3.25,1.0), e(4.0,3.0,1.0), e(3.75,3.25,2.9), e(4.0,3.0,2.9))"`

printed side-out 1.0 and rally 1.0, then `0.7597469266479578 0.9090909090909091 0.5979660470796391 0.6886884601202378`. So v1: 76.0% and 90.9% (exactly 10/11); `Config()`: 59.8% and 68.9%.

Criteria 1 to 8 are checked by reading the diff; criterion 9 by the gate output. No live drive: no code path changes.
