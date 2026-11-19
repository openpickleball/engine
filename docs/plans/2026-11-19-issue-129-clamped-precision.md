# Plan: Report rating changes lost to float precision as clamped (issue #129)

Risk tier: Medium. It changes what `MatchResult.clamped` reports, which the backend reads to explain `imbalance`, and the published claim about `imbalance`. It changes no rating.

Domain skills: superpowers:test-driven-development, invoke. Framework skills: not applicable, a standard-library Python package.

## Decision

Keep the public API: `clamped` keeps its name and type. Its meaning widens from "hit the rating floor or ceiling" to "part of the change was not applied": the rating range or float precision dropped part of it. Today's docs already describe `clamped` as players "whose change was not fully applied", so this makes the code match that wording. A new field would be an API change for a case that needs a rating range far wider than the default.

## Acceptance criteria

1. In `RatingEngine._compute` (`src/open_engine/_engine.py`), a player who is not frozen is listed in `clamped` when the applied change differs from the raw change by more than `_CLAMP_TOLERANCE`: `abs((state.rating - old_rating) - raw_delta) > _CLAMP_TOLERANCE`, replacing the comparison of `state.rating` with the already rounded `old_rating + raw_delta`.
2. The issue's reproduction (`rating=1e17` in a `0.0..1e18` range) lists the absorbed player in `clamped`, with `imbalance` still 0.6. Ratings, deltas, `imbalance`, and `net_rating_created` do not change for any input.
3. Under any rating range where the change is never absorbed, `clamped` is the same as on `main`: a player at the floor or ceiling is listed, others are not. In particular, under the default `Config` the results of every `record` call are unchanged, except possibly where the range cuts a change by almost exactly 1e-12: the old comparison rounds `old_rating + raw_delta` to the rating's float spacing (about 1e-15 there), so the two comparisons can fall on different sides of the tolerance. (Plan edit during implementation: the original wording said "every", which a boundary case breaks.)
4. Docs say what `clamped` now means and that float precision is a source of imbalance: the `MatchResult` docstring and its field comment (`src/open_engine/_model.py`), the `_CLAMP_TOLERANCE` comment (`_math.py`), `docs/behavior.md` ("K and imbalance", item 2, and the line that says imbalance is 0.0 when nothing is clamped or frozen), `docs/model.md` (the clamp rule and the imbalance paragraph that names float rounding as a separate case), and `CLAUDE.md` ("Own K and imbalance"). With this change, the claim "imbalance is 0.0 when both teams have the same mean K and nothing is clamped or frozen" holds in exact arithmetic up to the 5-place rounding; check it against the other sources of nonzero imbalance before keeping it, and narrow it if needed.
5. `CHANGELOG.md`, `## [Unreleased]`, `### Fixed`.

## Files that change

- `src/open_engine/_engine.py`: the clamp check in `_compute` only.
- `src/open_engine/_model.py`, `src/open_engine/_math.py`: comments and docstrings.
- A new test file, `tests/test_clamped_precision.py`.
- `docs/behavior.md`, `docs/model.md`, `CLAUDE.md`, `CHANGELOG.md`.

## Order of work

1. Red test, committed alone: the issue's reproduction (`big` listed in `clamped`; deltas, imbalance unchanged); a doubles match where one player's change is absorbed; a player at the floor and at the ceiling (listed, as on `main`); an ordinary match under the default config (nothing listed).
2. Change the comparison. Keep `_CLAMP_TOLERANCE` and its value.
3. Docs and changelog. Full gate.

## Risks

- The new comparison flags a player on `main`'s normal paths. Mitigation: a scratch fuzz runs `main` and the branch over many random matches (default config and a few narrow and wide rating ranges inside the default scale of magnitudes) and shows `clamped`, deltas, and imbalance are identical everywhere except where a change is absorbed. Record the counts in the PR.

## Proof

- Measured on `main`: the issue's reproduction gives `deltas={'big': 0.0, 'small': 0.6}`, `clamped=()`, `imbalance=0.6`.
- The red test and the fuzz comparison.
