# Plan: A match winner can lose rating under pure point share (issue #20)

Risk tier: Low. Docs and tests only. No rating math, default, `Config` field, or public API changes, and the automated gate fully covers the new tests.

Domain skills: none applicable. This is a standard-library Python package with no web framework, UI, API, data store, authentication, payments, or email.

## Decision

The owner chose the policy on 2026-11-16: keep `Config.win_weight = 0.0` (pure pooled point share) as the default and publish it. Reasons, from `CLAUDE.md` "Engine rules":

- A change to how ratings move must improve out-of-sample predictions. The private evaluation's v2 plan tests `win_weight` 0.0 and 0.25. Until such evidence supports another value, the default stays.
- A rule that a winner's delta is never negative is a special-case override that discards evidence. It is ruled out.
- Per-game scoring would be a new declared `Config` option with its own evaluation. It is out of scope here.

## Acceptance criteria

1. The policy is published in the `engine.py` module docstring ("Published behavior notes"), next to the `win_weight` field in `Config`, and in a short README section. It states: match performance is team A's share of all points across games, blended with the match result by `win_weight` (default 0.0); a team can win and lose rating when it wins by less than expected, and it can also happen when it wins a best-of-3 with fewer total points; there is no floor on a winner's change other than the rating range (`min_rating` to `max_rating`); among matches with the same players, the same ratings and RD at match time, and the same game format, number of games, match type, and result, a higher point share never gives a team a worse change, but longer games and more games carry more weight, so across formats a slightly wider winning margin can still cost more rating; the default changes only when out-of-sample evaluation supports it; `win_weight = 1.0` scores the result only, so a winner never loses; values between 0 and 1 make a loss less likely, and only values close to 1 rule it out, depending on the rating range and scale.
2. The default config reflects the policy: `Config().win_weight == 0.0`, pinned by a test.
3. Property tests:
   a. At the default, the issue's repro: team A rated 5.0 beats team B rated 4.0 11-9 in doubles (all RD 0.42, no inactivity) and each team A player loses 0.03531; each team B player gains 0.03531.
   b. At the default, at equal ratings, team A wins 2-1 with 11-9, 0-11, 11-9 (fewer total points, actual 0.43137) and each team A player loses rating.
   c. For each `win_weight` in 0.0, 0.25, 0.5, 1.0, a team's change is monotonic in its margin: over the listed one-game side-out scores to 11 (up to 13-11), ordered by team A's point share, team A's delta never decreases and team B's never increases. For `win_weight < 1` team A's delta strictly increases.
   d. With `win_weight = 1.0`, the match winner never loses rating, across rating gaps where the winner is the much higher-rated team.
4. `CHANGELOG.md` records the published policy under `## [Unreleased]`, saying it changes no rating.

## Files that change

- `src/open_engine/engine.py`: one new published behavior note in the module docstring; a fuller comment above `win_weight` in `Config`. No code.
- `README.md`: a short `## Match performance` section after `## Ledger order`.
- `CHANGELOG.md`: one `### Added` entry under `## [Unreleased]`.
- `tests/test_match_performance.py`: new, the tests for criteria 2 and 3.

## Order of work

1. Write `tests/test_match_performance.py`. These tests pin current behavior, so they pass on the unchanged code; no red step applies (no defect is fixed). Commit.
2. Docs: engine docstring note, `Config` comment, README section, changelog entry. Commit.
3. Gate: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.

Test helpers: build an engine with `RatingEngine(Config(win_weight=w))`, seed each player with `seed_provisional_rating(pid, RatingFormat.DOUBLES)` and set `rating`, `rd = 0.42`, `rd_as_of = ON` on the returned state (the pattern in `tests/test_own_k.py`), then `record(Match("m", ("a1", "a2"), ("b1", "b2"), games, ON, RatingFormat.DOUBLES, best_of=...))`. Use `pytest.mark.parametrize` for the `win_weight` values and rating gaps. Use `pytest.approx` with `abs=1e-5` for the 0.03531 values.

The listed one-game side-out scores to 11 (up to 13-11) for criterion 3c: team A losing `(p, 11)` for p in 0..9, `(10, 12)`, `(11, 13)`; team A winning the mirror images. Sort by `a / (a + b)`.

## Risks

- Docs could claim more than the code does. Each claim in criterion 1 maps to a test in criterion 3 or to `_actual_point_share` in `engine.py`.
- Calling the default "validated" would break the demonstration label. The docs say the default is a declared choice awaiting evaluation, not a validated one.
- Hard rule 5: the docs must not name any competing rating system. Say "margin-based systems" at most.
- The new docstring note must not contradict the existing notes on own K, `imbalance`, and clamping. With RD 0.42 everywhere and ratings inside bounds, nothing is clamped in the tests.

## Proof

- Criterion 1: read in review (docs). Criterion 2: `test_default_win_weight_is_point_share`. Criterion 3a: `test_higher_rated_winner_can_lose_rating`. 3b: `test_best_of_three_winner_with_fewer_points_loses_rating`. 3c: `test_delta_is_monotonic_in_margin`. 3d: `test_full_win_weight_winner_never_loses`. Criterion 4: read in review.
- No live drive: no behavior changes, and the gate covers the tests.

Measured on `origin/main` (a1c101b), with a scratch script using the helper above:

- 5.0 vs 4.0, 11-9, `win_weight` 0.0: expected 0.68869, actual 0.55, team A -0.03531 each, team B +0.03531 each.
- 4.0 vs 4.0, (11-9, 0-11, 11-9), best of 3: expected 0.5, actual 0.43137, team A -0.02166 each.
- 5.0 vs 4.0, 11-9, `win_weight` 1.0: actual 1.0, team A +0.07925 each.
- 5.0 vs 4.0, 11-9, `win_weight` 0.25: actual 0.6625, team A -0.00667 each, so 0.25 does not stop a winner from losing.
- Team A delta for 11-0 through 11-9, then 12-10 and 13-11, at `win_weight` 0.0: 0.07925, 0.05804, 0.04009, 0.0247, 0.01137, -0.0003, -0.0106, -0.01975, -0.02794, -0.03531, -0.03646, -0.03743. Strictly decreasing as the margin narrows.
