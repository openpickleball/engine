# Plan: Report rating changes lost to float precision as clamped (issue #129)

Risk tier: Medium. It changes what `MatchResult.clamped` reports, which the backend reads to explain `imbalance`, and the published claim about `imbalance`. It changes no rating.

Domain skills: superpowers:test-driven-development, invoke. Framework skills: not applicable, a standard-library Python package.

## Decision

Keep the public API: `clamped` keeps its name and type. Its meaning widens from "hit the rating floor or ceiling" to "part of the change was not applied": the rating range cut it, or float rounding dropped a part visible at the reported precision. Today's docs already describe `clamped` as players "whose change was not fully applied", so this makes the code match that wording. A new field would be an API change for a case that needs ratings of about 2**36 in magnitude or more.

Revised after review: the first version compared the applied change with the raw change at 1e-12, which flags ordinary rounding at every rating of 2**14 or more in magnitude, so `clamped` became noise and hid which player the range cut. The maintainer chose to keep main's range check unchanged and add a second check at half a unit in the fifth reported place (`_VISIBLE_LOSS`, 5e-6). A smaller rounding loss goes unlisted, and the docs say so.

## Acceptance criteria

1. In `RatingEngine._compute` (`src/open_engine/_engine.py`), a player who is not frozen is listed in `clamped` when either main's range check holds, `abs(state.rating - (old_rating + raw_delta)) > _CLAMP_TOLERANCE` (unchanged), or the lost amount is visible at the reported precision, `abs((state.rating - old_rating) - raw_delta) >= _VISIBLE_LOSS`, a new `_math.py` constant equal to `0.5 * 10**-_RESULT_PLACES` (5e-6).
2. The issue's reproduction (`rating=1e17` in a `0.0..1e18` range) lists the absorbed player in `clamped`, with `imbalance` still 0.6. Ratings, deltas, `imbalance`, and `net_rating_created` do not change for any input.
3. Under any rating range where the change is never absorbed, `clamped` is the same as on `main`: a player at the floor or ceiling is listed, others are not. In particular, under the default `Config` the results of every `record` call are unchanged: the range check is main's, and a rounding loss of 5e-6 needs a rating of at least 2**34 in magnitude.
4. Docs say what `clamped` now means and that float precision is a source of imbalance: the `MatchResult` docstring and its field comment (`src/open_engine/_model.py`), the `_CLAMP_TOLERANCE` comment (`_math.py`), `docs/behavior.md` ("K and imbalance", item 2, and the line that says imbalance is 0.0 when nothing is clamped or frozen), `docs/model.md` (the clamp rule and the imbalance paragraph that names float rounding as a separate case), and `CLAUDE.md` ("Own K and imbalance"). With this change, the claim "imbalance is 0.0 when both teams have the same mean K and nothing is clamped or frozen" holds in exact arithmetic up to the 5-place rounding; check it against the other sources of nonzero imbalance before keeping it, and narrow it if needed.
5. `CHANGELOG.md`, `## [Unreleased]`, `### Fixed`.

## Files that change

- `src/open_engine/_engine.py`: the clamp check in `_compute` only.
- `src/open_engine/_model.py`, `src/open_engine/_math.py`: comments and docstrings.
- New test files: `tests/test_clamped_precision.py`, `tests/test_clamped_visible_loss.py`, and `tests/test_clamped_loss_boundary.py` (a loss on each side of the 5e-6 line).
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
