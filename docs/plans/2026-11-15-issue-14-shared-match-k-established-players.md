# Plan: Shared match K makes established players absorb a new player's uncertainty (issue #14)

Risk tier: High. It changes the core rating update and a published behavior rule (zero-sum
team-average movement), so every rating a replay produces can change.

Domain skills: none apply. This is a standard-library math package with no web, data, auth,
payments, or email code.

## Decision

The maintainer chose "own K, not zero-sum". Each player moves by their own K:

```
delta_i = sign * K_i * effective_weight * surprise      (before the rating clamp)
```

`K_i` is `player_k` on the player's working state, after `inflated_rd`, as today. The shared
`match_k` (the mean of every participant's K) and the within-team share
`team_size * match_k * K_i / sum(team K)` are removed. When every participant has the same K,
the new formula gives exactly the old deltas, because `team_size * k * k / (team_size * k) = k`.

Team-average movement is then zero-sum only when both teams have the same mean K and no clamp
or freeze applies. Every `MatchResult` reports the net as a new field, `imbalance`:

```
imbalance = mean(deltas of team_a) + mean(deltas of team_b)
```

It is computed from the applied, rounded `deltas` (frozen players count as 0.0), rounded to 5
places. It is 0.0 for a rejected match. It covers all three sources: unequal K, `clamped`, and
`frozen`. The parent (overall doubles) `MatchResult` of a category match gets its own value.

The issue's third criterion, "Backtest shows no regression in Brier score", is out of scope for
this repository: evaluation on real match data lives in a separate private repository (CLAUDE.md
hard rule 2). The pull request says the backtest is pending and uses `Refs #14`, not `Closes #14`,
so the issue stays open until that backtest is done.

## Acceptance criteria

1. An established player's step against a new opponent is no larger than against an established
   opponent with the same expectation. Tested as a parametrized property in singles and doubles,
   over a grid of opponent RD values (rd_min to rd_max) and both match outcomes: the established
   player's absolute delta never exceeds the delta against an opponent with the same RD as the
   established player. In doubles this also covers a new player on the opposing team (one new,
   one established opponent).
2. Each player who is not frozen and not clamped moves by exactly
   `round(sign * K_i * effective_weight * surprise, 5)`, whatever the K of their partner or
   opponents.
3. When every participant has the same K, deltas are identical to `origin/main` (values measured
   below).
4. `MatchResult.imbalance` equals `mean(team_a deltas) + mean(team_b deltas)` (within 1e-5) on
   every counted match, is 0.0 when K is equal and nothing is clamped or frozen, is nonzero with
   the expected sign when K differs (the higher-K team moves more), and is 0.0 on a rejected
   match. A category match's `parent` result carries its own `imbalance`.
5. A frozen player's teammate moves by their own K only (unchanged documented `frozen` behavior:
   the team moves less).
6. The conservation rule is documented: the module docstring's "Published behavior notes", the
   `Config` K comment, the `MatchResult` docstring, the `CLAUDE.md` "Zero-sum exceptions" engine
   rule, and a `CHANGELOG.md` entry under `## [Unreleased]` (a rating behavior change and a new
   public field on `MatchResult`).
7. The full gate passes: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.

## Files that change

- `src/open_engine/engine.py`: `_compute` (the update loop), `MatchResult` (new `imbalance`
  field and docstring), the `Config` K comment, and the module docstring's zero-sum note.
- `tests/test_engine.py` or a new `tests/test_own_k.py`: the tests listed under Proof. A new file
  is preferred so the issue's tests stay together.
- `CHANGELOG.md`: `## [Unreleased]` entry under Changed (own K, no longer zero-sum) and Added
  (`MatchResult.imbalance`).
- `CLAUDE.md`: rewrite the "Zero-sum exceptions" engine rule.
- `README.md`: only if a line becomes false. The current text does not mention zero-sum, so
  probably no change.

## Order of work

1. Write the failing tests (criteria 1, 2, 4, 5) and the equal-K regression test (criterion 3).
   Confirm 1, 2, 4 fail on the current code for the right reason. Commit them as the red tests.
2. Change `_compute`: drop `match_k`, `team_ks`, `team_k_total`; compute `K_i` per non-frozen
   player after RD inflation; apply `sign * K_i * effective_weight * surprise`. Keep the clamp,
   RD shrink, and bookkeeping exactly as they are.
3. Add `imbalance: float = 0.0` to `MatchResult` after `clamped` (keep `parent` last), compute it
   in `_compute` from the rounded deltas, and update the docstring.
4. Update the module docstring, the `Config` comment, `CLAUDE.md`, and `CHANGELOG.md`.
5. Run the full gate and `python examples/basic_match.py`.

## Risks

- Rating pool drift. Without zero-sum, a pool with many new players can inflate or deflate. That
  is the accepted cost; `imbalance` makes it visible per match. Riskiest step: the update loop
  in step 2.
- Existing tests that pin exact deltas from mixed-K matches will change. Any such test that
  fails must be examined: update it only when the old value encoded the shared-K behavior this
  issue removes, and say so in the commit message. Never loosen a determinism or parity test.
- `tests/test_replay_live_parity.py` and replay determinism must stay green unchanged: the
  formula is still a pure function of ordered state.
- `MatchResult` is a public dataclass. A new field with a default is additive, but positional
  construction would break if the field went before an existing one, so it goes after `clamped`
  and before `parent`. Check that `test_api.py` does not pin the field list; if it does, update
  it and note it in the changelog.
- Not chosen: discounting by opponent uncertainty (a further factor on each player's step). It
  adds a new parameter that needs calibration; it can be a later issue.

## Proof

- Criterion 1: parametrized property test, singles and doubles, opponent RD grid, win and loss.
- Criterion 2: a test with mixed K (one seeded established player, one new player) asserting
  each delta equals `round(sign * player_k(state) * effective_weight * surprise, 5)`, with K read
  from the post-inflation state before the match.
- Criterion 3: equal-K regression test pinning the values measured on main below.
- Criterion 4: tests on `imbalance` for equal K, mixed K, a clamp, a rejected match, and a
  category match's parent.
- Criterion 5: a frozen-teammate test.
- Live drive: run the measurement script below after the change. It must show singles
  est +0.02856 against both the new and the established opponent, new player -0.05000, and the
  equal-K rows unchanged. (An earlier draft said -0.05049. That used the established player's K
  before inactivity inflation, 0.3394. The K the update uses is the inflated one, about 0.3427,
  so a K 0.6 player moves 0.05000, the same step as in the all-new doubles row.)

Measured on `origin/main` (f6d57f3). Players seeded at 4.0 with `seed_external_rating`
(snapshot 2026-10-01, reliability 85; K 0.3394) or `seed_provisional_rating` (K 0.6), all
ratings forced to 4.0, one game 11-7, doubles format default league weight, played 2026-10-29:

```
singles est vs new        K .3394/.6000            est +0.03928  new -0.03928
singles est vs est2       K .3394/.3394            est +0.02856  est2 -0.02856
doubles e1,e2 vs e3,e4    all K .3394              e1,e2 +0.02856  e3,e4 -0.02856
doubles e1,e2 vs n1,e4    K .3394,.3394 / .6,.3394 e1,e2 +0.03392  n1 -0.04318  e4 -0.02466
doubles n1,n2 vs n3,n4    all K .6                 n1,n2 +0.05  n3,n4 -0.05
```

Command: the scratch script `measure.py` (reproduced in the criterion 3 test), run with the
worktree venv on `origin/main`. Rows 2, 3 and 5 are the equal-K "behave alike" cases for
criterion 3. Row 1 reproduces the issue: 0.03928 / 0.02856 = 1.375.
