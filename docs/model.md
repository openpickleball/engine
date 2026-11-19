# The OPEN rating model

> **Status:** This engine is a demonstration. Its methods have not been validated on real match data, its outputs are not OPEN ratings, and its API may change without notice. No OPEN rating is live. Each default parameter value is an uncalibrated placeholder, a declared choice, or a label that changes no rating, as the [parameter table](#parameters) marks it.

This page states every equation the engine uses to move a rating, to report reliability, and to predict and evaluate matches. It follows a match through the engine in the order the code processes it. Each equation names the function in `src/open_engine/` that computes it (most are in `_math.py`, `_engine.py`, `_evaluation.py`, and `winprob.py`), so you can check one against the other.

The engine's edge cases (input types, ledger order, late matches, duplicates, snapshots, and the rest) are in the published behavior notes in [`docs/behavior.md`](behavior.md). This page does not repeat them. Where a rule here has an edge case, it links to the note.

In short: a rating is a performance-based Elo rating. Each match compares the share of points a team won with the share its rating predicted. Each player moves by their own K, which grows with their rating uncertainty (RD). Once a state has an `rd_as_of` (from its first counted match, an imported capture date, or, for a category state derived from overall doubles, the doubles state's `rd_as_of`), RD can grow with inactivity, up to `rd_max`. It can shrink with each counted match, down to `rd_min`. A frozen player's RD does not change. Reliability, a 0 to 100 label, combines RD with the variety of a player's partners and opponents.

## Contents

1. [Notation](#notation)
2. [Rating state](#rating-state)
3. [Starting states](#starting-states)
4. [When a match does not count](#when-a-match-does-not-count)
5. [Team rating](#team-rating)
6. [Expected point share](#expected-point-share)
7. [Match performance](#match-performance)
8. [Evidence weight](#evidence-weight)
9. [Inactivity](#inactivity)
10. [K](#k)
11. [The update](#the-update)
12. [Categories and overall doubles](#categories-and-overall-doubles)
13. [A worked example](#a-worked-example)
14. [Reliability](#reliability)
15. [Win probability](#win-probability)
16. [Evaluation and calibration](#evaluation-and-calibration)
17. [Parameters](#parameters)
18. [What the model does not do](#what-the-model-does-not-do)

## Notation

Equations are plain text. `cfg.x` is the `Config` field `x`. `clamp(x, lo, hi)` is `max(lo, min(hi, x))`. `mean` is the arithmetic mean, computed with `math.fsum`. Team A and team B are the two sides of a match as recorded. The rating math reads dates, not times: `played_at` only orders matches within a day (see the ledger order note). The engine never reads the wall clock.

## Rating state

Each player has a separate `RatingState` in each rating format: singles, overall doubles, and the three doubles categories (mixed, men's, and women's doubles). The fields the math reads are:

| Field | Meaning |
| --- | --- |
| `rating` | The rating, between `cfg.min_rating` and `cfg.max_rating`. |
| `rd` | Rating deviation, OPEN's internal uncertainty, between `cfg.rd_min` and `cfg.rd_max`. |
| `rd_as_of` | The date inactivity is measured from. A counted match moves it forward to the match date, never back, so a match dated before the state's own `rd_as_of` leaves it as it was. Each player's date moves on its own, so a match flagged `late` because of one player can still move another player's `rd_as_of` forward. A frozen player's state is not changed. An imported seed sets it to its capture date. A category state derived from overall doubles keeps the doubles `rd_as_of`, though its RD is raised to at least `cfg.category_seed_rd`. `None` for a new player. |
| `initial_source_date` | The capture date of an imported rating, else `None`. Matches on or before it are frozen for this player. |
| `matches_played`, `verified_matches_played` | Counted matches, and counted tournament, league, and recreational verified matches. |
| `last_played` | The date of the player's latest counted match in this format. |
| `partners`, `opponents` | The latest date this player played with or against each other player. |

A counted match can move the rating. A frozen player gets 0.0, and a player at a rating bound can stay put. A new seed can also replace an unplayed state: `seed_external_rating(..., replace_unplayed_seed=True)` can replace an unplayed seed, and under `cfg.category_feeds_parent` a new overall doubles seed replaces each stored, unplayed category state derived from doubles (`initial_source` `open_parent:doubles`). `seed_external_rating` refuses in several cases, including a derived `open_parent:` state, a format where a counted match included the player (even frozen, so `matches_played` can still be 0), and a format with a rejected update a seed could change. [External seeds](behavior.md#external-seeds) lists the full rules.

## Starting states

A seed given to `RatingEngine.add_player` or `replay` sets a state directly, under the seed rules listed in `add_player`. It can carry any rating in range, including an `open_provisional` one other than `cfg.default_rating`. Otherwise the engine builds one of these three states itself.

**New player** (`RatingEngine._new_state`):

```
rating   = cfg.default_rating
rd       = cfg.new_player_rd
rd_as_of = None
```

Because `rd_as_of` is `None`, RD does not grow before the player's first match, however late it comes.

**Imported seed** (`RatingEngine.seed_external_rating`):

```
rating              = snapshot.rating
rd                  = cfg.external_seed_rd
rd_as_of            = snapshot.captured_on
initial_source_date = snapshot.captured_on
```

An imported rating is a seed only. It sets the starting state above, including `initial_source_date`, which freezes the player in matches on or before it. The imported reliability is never converted: it is stored for display (`PlayerSummary.imported_reliability`) and never sets RD or OPEN-earned reliability. RD grows from the capture date, so an older import starts with more uncertainty. `seed_external_rating` refuses to replace a rating in a format where a counted OPEN match included the player.

**Category derived from overall doubles** (`RatingEngine._new_state`). When `cfg.category_feeds_parent` is on, the player has no stored state in the category, and the player's overall doubles state has a counted match or an `initial_source_date` (an imported baseline), the category state starts from it:

```
rating              = doubles.rating
rd                  = max(doubles.rd, cfg.category_seed_rd)
rd_as_of            = doubles.rd_as_of
initial_source_date = doubles.initial_source_date
```

`doubles.rd` is the stored RD, before any inactivity growth. Growth then runs from the doubles `rd_as_of` at the category match. Otherwise the category starts as a new player. Until the player has a stored category state, each category match derives it again from the current doubles state.

## When a match does not count

`RatingEngine.record` (through `_record`, `_consider`, and `_compute`) checks a match in this order. The first check that fails stops it, and the result names a `RejectionReason`. A match that does not count changes no rating.

1. The `match_id` was already processed or reserved: `DUPLICATE_MATCH_ID`.
2. The match is not completed (a forfeit, retirement, or unfinished match): `MATCH_NOT_COMPLETED`.
3. Its game format (scoring type, points to) is not in `cfg.allowed_game_formats`: `GAME_FORMAT_NOT_ACCEPTED`.
4. It has the same content as an earlier counted match and `cfg.reject_content_duplicates` is on: `POSSIBLE_DUPLICATE`.
5. It is self-reported, `cfg.self_reported_requires_both_teams` is on, and `confirmed_by` does not name a player from each team: `SELF_REPORTED_UNCONFIRMED`.
6. It is self-reported and the same two teams already have at least `cfg.self_reported_max_repeats` counted self-reported matches in this format within the repeat window: `SELF_REPORTED_REPEAT_LIMIT` (see [Evidence weight](#evidence-weight)).

Then, for each rating format the match updates (`_compute`):

7. Every player in it is frozen, that is, the match is on or before each player's `initial_source_date`: `BEFORE_IMPORTED_BASELINE`.
8. In singles, with `cfg.max_singles_rating_gap` set, `abs(R_A - R_B)` is above it: `SINGLES_RATING_GAP`.
9. In any doubles format, with `cfg.max_doubles_partner_gap` set, the two ratings on either team differ by more than it: `DOUBLES_PARTNER_GAP`.

The behavior notes on [duplicates](behavior.md#likely-duplicates), [match status](behavior.md#match-status), and [resubmission](behavior.md#match-ids-rejections-and-resubmission) cover what each rejection reserves and when a corrected match can be sent again.

## Team rating

`RatingEngine._compute`. A team's rating is the mean of its players' ratings in the format being updated, before the match:

```
R_A = mean(rating of each player on team A)
R_B = mean(rating of each player on team B)
```

A frozen player's rating counts in the mean, though that player does not move.

## Expected point share

`expected_point_share`. Team A's expected share of all points in the match:

```
x = clamp((R_B - R_A) / scale, -12, 12)
E = 1 / (1 + 10**x)
```

`scale` is `cfg.scale_by_scoring[scoring]`, one value for side-out and one for rally scoring. The scale must be greater than 0: `Config` raises `ConfigError` for a scale of 0 or less, and `expected_point_share` raises `EvaluationError` for one. A rating gap equal to `scale` gives 10:1 expected point-share odds. The clamp keeps `10**x` finite for any rating range and scale. Under the default rating range and scales, `abs(x)` is at most 6 / 2.9, about 2.1, so the clamp never applies there.

## Match performance

`_actual_point_share`. Team A's performance in the match:

```
P = points_A / (points_A + points_B)        summed over every game
W = 1 if team A won more games, else 0
S = (1 - cfg.win_weight) * P + cfg.win_weight * W
surprise = S - E
```

The default `win_weight` is 0.0, so `S` is the pooled point share. A team can win and still lose rating when it wins by less than its expected share, or wins a best of 3 with fewer total points. The [match performance note](behavior.md#match-performance) states the full policy, including what holds and what does not across game formats and game counts.

## Evidence weight

`_compute`, `information_multiplier`, and `_consider`. How much a match counts:

```
weight = cfg.match_weights[match_type] * repeat_factor * M
M      = min(cfg.max_information_multiplier,
             cfg.game_length_weights[points_to] * (1 + cfg.game_count_bonus * (games - 1)))
```

`games` is the number of games recorded. Under the default weights, longer games and more games carry more evidence, up to the cap. With `cfg.game_count_bonus` at 0, or game length weights that do not rise with length, they need not.

`repeat_factor` is 1.0 except for a self-reported match (`_consider`):

```
prior         = earlier counted self-reported matches between the same two teams,
                in the same rating format, dated from
                (played_on - cfg.self_reported_repeat_window_days) to played_on
repeat_factor = cfg.self_reported_repeat_factor ** prior
```

The two teams are compared as sets of players, so either side may be team A. A match with `prior` at or above `cfg.self_reported_max_repeats` is rejected. A category match's overall doubles update uses the same `repeat_factor` as the category update.

## Inactivity

`inflated_rd`. Before the update, each player who is not frozen has their RD grown for the days since `rd_as_of`:

```
if rd_as_of is None or played_on <= rd_as_of:
    rd' = rd
else:
    days = played_on - rd_as_of
    rd'  = min(cfg.rd_max, sqrt(rd**2 + cfg.rd_growth_per_day**2 * days))
```

Inactivity can widen RD, which can raise K and lower reliability, but by itself it never moves a rating. The rating does not drift, and an old match's update is never shrunk or discounted. A match dated before a player's `rd_as_of` (a late live match) grows no RD.

## K

`player_k`. Each player's K comes from their own RD after inactivity:

```
f = clamp((rd' - cfg.rd_min) / (cfg.rd_max - cfg.rd_min), 0, 1)
K = cfg.k_min + (cfg.k_max - cfg.k_min) * f
```

A partner's or opponent's RD never changes a player's K.

## The update

`RatingEngine._compute`. For each player who is not frozen:

```
sign       = +1 for team A, -1 for team B
raw_delta  = sign * K * weight * surprise
rating_new = clamp(rating + raw_delta, cfg.min_rating, cfg.max_rating)
rd_new     = max(cfg.rd_min, rd' * (1 - cfg.rd_shrink_per_match * weight))
```

The player is listed in `MatchResult.clamped` when either holds:

```
abs(rating_new - (rating + raw_delta)) > 1e-12      the clamp cut the change
abs((rating_new - rating) - raw_delta) >= 5e-6      rounding dropped part of it
```

Both are evaluated in floating point. The first is the range check; an unclamped `rating_new` equals the rounded sum exactly. The second catches a change that float rounding of `rating_new` drops, at half a unit in the fifth place, where the loss can show in the reported delta and imbalance (`_VISIBLE_LOSS`). A smaller rounding loss is not listed. Rounding drops that much only at very large magnitudes: with the old and new rating both below 2**34 (about 1.7e10) in magnitude it cannot, and in a measured sweep with steps up to 1 the first such loss came at ratings of 2**36 (about 6.9e10). The RD update starts from the inflated `rd'`, not the stored `rd`, and never goes below `cfg.rd_min`. The update also sets:

```
rd_as_of     = max(rd_as_of, played_on)        played_on when rd_as_of is None
last_played  = max(last_played, played_on)     played_on when last_played is None
matches_played          += 1
verified_matches_played += 1                   for a tournament, league, or rec_verified match
partners[p], opponents[p] = the later of the stored date and played_on
```

Dates never move back, so a late live match counts but does not rewind inactivity (see the [late match note](behavior.md#late-live-matches)).

**Frozen players.** A player is frozen when `played_on <= initial_source_date`: the imported rating already reflects the match. A frozen player's rating counts in the team rating, but their change is 0.0 and their state is not written. Their teammates move by their own K.

**Imbalance.** Each player moves by their own K, so a match need not move the two teams by equal and opposite amounts:

```
applied_i = rating_new_i - rating_i            0.0 for a frozen player
imbalance = mean(applied_i on team A) + mean(applied_i on team B)
```

The mean divides by the full team size, frozen players included. `MatchResult.imbalance` is this value rounded to 5 places. A match adds `team_size * imbalance`, with the unrounded imbalance, to the pool's total rating. `ReplayInfo.net_rating_created` totals it per rating format: `_replay` takes `math.fsum` of the unrounded `applied_i` of each counted update and adds those sums with `+=` in ledger order. In exact arithmetic the changes cancel when the two teams have the same mean K and no player is clamped or frozen. Unequal K, a clamped player, or a frozen player can make imbalance nonzero. A rounding loss of 5e-6 or more lists the player in `clamped`. With nothing clamped or frozen and the same mean K, float rounding is left: unlisted losses under 5e-6 each can add up to plus or minus 1e-5 at ratings near 2**36 in magnitude, and rounding of the steps themselves can show at the fifth place for steps far larger than the default config gives. The [K and imbalance notes](behavior.md#k-and-imbalance) cover the rest, including why the backend should monitor the total.

`MatchResult` reports `expected_a = E`, `actual_a = S`, `surprise`, `effective_weight = weight`, and each player's delta, each rounded to 5 places. The stored rating keeps full precision.

## Categories and overall doubles

A mixed, men's, or women's doubles match updates its category. When `cfg.category_feeds_parent` is on (the default) and the category update counts, the same match also updates overall doubles (`_consider`). The overall doubles update runs every step above on the players' overall doubles states, with the same match, performance, and `repeat_factor`. It is reported in `MatchResult.parent`. Both updates are computed from the states before the match.

A rejection of the overall doubles update (a frozen baseline or a gap filter) does not stop the category update. A rejected category update sends nothing to overall doubles.

## A worked example

Four new doubles players, default config, one tournament game of side-out scoring to 11, won 11-7 by team A. Every player starts at rating 3.5 and RD 0.80, with no `rd_as_of`.

```
R_A = R_B = 3.5            E = 1 / (1 + 10**0) = 0.5
P = 11 / 18 = 0.61111      S = P (win_weight 0)       surprise = 0.11111
M = 1.00 * (1 + 0.12 * 0) = 1.0                       weight = 1.00 * 1.0 * 1.0 = 1.0
rd' = 0.80 (no rd_as_of)   f = 1                      K = 0.60
team A delta = +0.60 * 1.0 * 0.11111 = +0.06667       team B delta = -0.06667
rd_new = max(0.10, 0.80 * (1 - 0.08 * 1.0)) = 0.736   imbalance = 0.0
```

Sixty days later the same four players meet in a league best of 3 (11-9, 8-11, 11-6). Each RD first grows: `rd' = sqrt(0.736**2 + 0.012**2 * 60) = 0.7418463`, so `K = 0.12 + 0.48 * (rd' - 0.10) / 0.70 = 0.56012`, computed from the unrounded `rd'`. The weight is `0.90 * 1.0 * min(1.30, 1.00 * (1 + 0.12 * 2)) = 1.116`.

## Reliability

`reliability` and `_reliability`. OPEN-earned reliability, from 0 to 100, at a reference date `as_of`:

```
rd_ref    = inflated_rd(state, as_of)
certainty = 1 - (rd_ref - cfg.rd_min) / (cfg.rd_max - cfg.rd_min)
count     = partners last seen on or after (as_of - cfg.diversity_window_days)
          + opponents last seen on or after (as_of - cfg.diversity_window_days)
variety   = min(1, count / cfg.diversity_target)
score     = 100 * certainty * (0.60 + 0.40 * variety)
reliability = round(clamp(score, 0, 100), 1)
```

A player with no variety keeps 60 percent of their certainty. Partners and opponents are counted separately, so a player who was both counts twice. Reliability is never a conversion of an external score.

`player_summary` (`RatingEngine.player_summary`) adds three labels on top. None of them changes a rating, an RD, K, a prediction, or an evaluation:

- With `cfg.connectivity_reliability_cap` set, a player who is not connected has reliability at most the cap, shown rounded down to 1 place. The [network connectivity note](behavior.md#network-connectivity) defines connected and its limits.
- `established` is true when the displayed reliability is at least `cfg.established_reliability_threshold` and `verified_matches_played` is at least `cfg.established_min_verified_matches`.
- `imported_reliability` shows the imported value, rounded to 1 place, while it is carried. The rules for carrying it are in [`PlayerSummary` fields](behavior.md#playersummary-fields).

The summary also reports `rating` rounded to 3 places and `rd` (RD inflated to the reference date) rounded to 4. The notes on [reference dates](behavior.md#reference-dates) and on [`established`](behavior.md#established) explain which date a summary is measured at.

## Win probability

`winprob.py`. The expected point share becomes a probability that team A wins the match.

**Assumptions.** Points are independent, and team A wins each point with the same probability `p`, the expected point share `E`. Games are independent, each with the same game win probability. The result is exact under these assumptions. They suit rally scoring and only approximate side-out scoring, where only the serving side can score.

**Game** (`game_win_probability`, `q = 1 - p`, `n = points_to`):

```
win_by 1, no cap:  G = sum over k = 0 .. n-1 of C(n-1+k, k) * p**n * q**k
win_by 2, no cap:  G = sum over k = 0 .. n-2 of C(n-1+k, k) * p**n * q**k
                     + C(2(n-1), n-1) * (p*q)**(n-1) * p**2 / (p**2 + q**2)
with a cap:        a dynamic program over scores (_capped); a game ends when a side
                   has at least n points and leads by win_by, or reaches the cap
```

A `win_by` other than 1 or 2 has no modeled win probability.

**Match** (`match_win_probability`, best of `N`, `w = N // 2 + 1` games to win):

```
P_match = sum over k = 0 .. w-1 of C(w-1+k, k) * G**w * (1 - G)**k
```

**Calibration** (`calibrate_win_probability`), with `a = cfg.win_calibration_by_scoring[scoring]`:

```
p_c = clamp(P_match, 1e-6, 1 - 1e-6)
P   = 1 / (1 + exp(-a * ln(p_c / (1 - p_c))))
```

`a = 1.0`, the default, leaves the probability unchanged apart from the clip. `RatingEngine.predict_win_probability` returns the calibrated value. Win probability is a prediction only: no rating reads it.

## Evaluation and calibration

**Prequential predictions** (`prequential_predictions`, `_prequential`, `_predict_match`). Each match is predicted from ratings that have not seen it, then recorded. A match is predicted when it is completed, every player's state in the format (stored, or the starting state `record` would use) has an `initial_source_date` or at least `burn_in_matches` counted matches, its scoring matches `scoring` when given, and it is on or after `score_from` when given. A prediction is kept only when `record` then counts the match. This eligibility rule is separate from `PlayerSummary.established`.

- `"live"` mode records every match in ledger order in one engine.
- `"scheduled"` mode mirrors production. Replay dates are `anchor + k * cfg.replay_interval_days`. A match is predicted from a replay to the latest replay date strictly before its own date, and the matches in that window are then predicted and recorded live in ledger order.

**Evaluation report** (`evaluate`, `_scored_pairs`, `_report_from_predictions`). Each prediction gives a pair, both rounded to 5 places as in `MatchResult`:

```
expected = E
actual   = (1 - cfg.win_weight) * P + cfg.win_weight * W
error    = actual - expected
mse  = mean(error**2)        mae = mean(abs(error))        bias = mean(error)
```

`mse`, `mae`, and `bias` are reported to 6 places. The calibration table has ten bins, `lower = index / 10` and `upper = (index + 1) / 10` for index 0 to 9. A pair is in a bin when `lower <= expected < upper`, and the last bin also takes `expected == 1.0`. One bin's upper edge is the same float as the next bin's lower edge, so the bins partition `[0, 1]`: every pair is in exactly one bin, and the counts add up to `matches_scored`. Only bins with at least one pair are listed. Each listed bin gives its lower bound, mean expected, mean actual (4 places), and count.

**Scale fit** (`fit_scale`). For one scoring type, the scale that minimizes the unrounded prequential MSE on `[low, high]`. Each trial scale reruns the whole prequential pass, since the ratings depend on the scale. The search evaluates a grid of 15 points (14 intervals), then a golden-section search within one grid step of the best point until the bracket is no wider than `tolerance`, or stops shrinking at float spacing. The result is the bracket midpoint rounded to 3 places. Trial scales that round to the same 6 places share one result.

**Win calibration fit** (`fit_win_calibration`). For one scoring type, the exponent `a` that minimizes the Brier score over predictions that have a modeled win probability:

```
brier(a) = mean((calibrated(P_match, a) - won)**2)        won = 1 or 0
```

`a` is evaluated rounded to 6 places. The predictions are made once, with the config's scale, so fit the scale first. The search is the same as for the scale, with the same two stopping rules, and the result is rounded to 3 places.

**Identification.** A fit within `tolerance` of `low` or `high` has `at_bound` set. `ScaleFit.profile` lists every scale `fit_scale` ran, as its 6-place cache key, with its unrounded MSE, sorted by scale. That includes the grid, the search, and the runs at the fitted scale and at the config's default scale. Either of those two can lie outside `[low, high]`: the default scale is whatever the config holds, and the fitted scale is rounded to 3 places, which can move it slightly past a bound. A fit at a bound, or with a flat profile, is not identified: the data do not pin down the value. Fits on real match data run outside this repository.

## Parameters

Every `Config` field the math reads, with its default. Status says what the default is:

- **Placeholder**: an uncalibrated placeholder. It needs fitting or out-of-sample evaluation on real match data.
- **Declared**: a declared choice (OPEN policy or a modeling choice), not a calibrated one. It changes only when out-of-sample evaluation supports another value.
- **Label**: a label or display rule only. It changes no rating, prediction, or evaluation.

`Config.fingerprint()` hashes every field, and every replay records the fingerprint in its `ReplayInfo`, so the exact values behind a replay's ratings are on record.

| Field | Default | Role | Status |
| --- | --- | --- | --- |
| `min_rating`, `max_rating` | 2.0, 8.0 | The rating range. Every rating is clamped to it. | Declared |
| `default_rating` | 3.5 | A new player's rating. | Declared |
| `scale_by_scoring` | side-out 2.9, rally 2.9 | `scale` in the expected point share. | Placeholder (fit with `fit_scale`) |
| `win_calibration_by_scoring` | side-out 1.0, rally 1.0 | `a` in win probability calibration. | Placeholder (fit with `fit_win_calibration`) |
| `k_min`, `k_max` | 0.12, 0.60 | K at `rd_min` and at `rd_max`. | Placeholder |
| `rd_min`, `rd_max` | 0.10, 0.80 | The RD range. | Placeholder |
| `new_player_rd` | 0.80 | A new player's RD. | Placeholder |
| `external_seed_rd` | 0.42 | An imported seed's RD. | Placeholder |
| `category_seed_rd` | 0.50 | The least RD of a category state derived from overall doubles. | Placeholder |
| `rd_shrink_per_match` | 0.08 | RD shrink per unit of weight. | Placeholder |
| `rd_growth_per_day` | 0.012 | RD growth per day of inactivity, in quadrature. | Placeholder |
| `win_weight` | 0.0 | Blend of the match result into performance. | Declared |
| `match_weights` | tournament 1.00, league 0.90, rec_verified 0.75, self_reported 0.40 | Weight by match type. | Declared |
| `allowed_game_formats` | side-out and rally, to 11, 15, and 21 | Game formats that can count. | Declared |
| `game_length_weights` | 11: 1.00, 15: 1.10, 21: 1.20 | Information multiplier by game length. | Placeholder |
| `game_count_bonus` | 0.12 | Information multiplier added per extra game. | Placeholder |
| `max_information_multiplier` | 1.30 | Cap on the information multiplier. | Placeholder |
| `self_reported_requires_both_teams` | True | A self-reported match needs a confirmation from each team. | Declared |
| `self_reported_repeat_window_days` | 90 | Window for counting repeat self-reported matches. | Declared |
| `self_reported_repeat_factor` | 0.5 | Weight factor per earlier repeat. | Declared |
| `self_reported_max_repeats` | 4 | Earlier repeats at which a self-reported match is rejected. | Declared |
| `reject_content_duplicates` | False | Reject, rather than flag, a likely duplicate. | Declared |
| `category_feeds_parent` | True | Category matches also update overall doubles, and categories start from it. | Declared |
| `max_singles_rating_gap` | None (off) | Reject a singles match with a larger rating gap. | Declared |
| `max_doubles_partner_gap` | None (off) | Reject a doubles match with a larger gap between partners. | Declared |
| `replay_interval_days` | 7 | The published replay schedule, and the window in scheduled evaluation. | Declared |
| `diversity_window_days` | 365 | Window for reliability variety and connectivity. | Label |
| `diversity_target` | 12 | Partners plus opponents for full variety. | Label |
| `established_reliability_threshold` | 60.0 | Reliability needed for `established`. | Label |
| `established_min_verified_matches` | 3 | Verified matches needed for `established`. | Label |
| `import_reliability_carry_matches` | 10 | Matches after which imported reliability is no longer shown. | Label |
| `import_reliability_max_age_days` | 365 | Age after which imported reliability is no longer shown. | Label |
| `connectivity_reliability_cap` | None (off) | Reliability cap for a player who is not connected. | Label |
| `connectivity_min_shared_contacts` | 4 | Shared contacts for a tie. | Label |
| `connectivity_min_contact_days` | 3 | Distinct dates for a tie or for outside contact. | Label |
| `connectivity_min_outside_players` | 8 | Outside players for a pool to be connected. | Label |

`version` names the release and is in the fingerprint. The valid range of each field is in [Config validation](behavior.md#config-validation).

The engine also has fixed constants in `_math.py`, each with a comment saying why it has its value: the expected-share exponent clamp (12), the reliability blend (0.60 and 0.40), the clamp detection tolerance (1e-12), the visible rounding loss (5e-6), and the reported decimal places (5 for `MatchResult` numbers, 3 for `PlayerSummary.rating`, 4 for `PlayerSummary.rd`, 1 for reliability). The win probability clip (1e-6) is `PROBABILITY_CLIP` in `winprob.py`. They are not `Config` fields, so the fingerprint does not record them. A change to one changes the engine itself, so it needs a release and a changelog entry.

## What the model does not do

- **No rating drift for inactivity.** Inactivity can widen RD, which can raise K and lower reliability. It never moves a rating.
- **No rating for a match that is not completed.** A forfeit, retirement, or unfinished match changes no rating. Rating a retirement on its finished games would need a declared `Config` parameter backed by out-of-sample evaluation.
- **No zero-sum guarantee.** Each player moves by their own K, so a match can add rating to the pool or remove it. `imbalance` and `ReplayInfo.net_rating_created` report it, and the engine does not cap it.
- **No floor on a winner's change** other than the rating range. The match performance note gives the cases.
- **No conversion of external scores.** An imported rating seeds a starting rating only, and an imported reliability is never turned into RD or OPEN reliability.
- **No use of game order in the update.** From the game scores, the rating update reads only each team's total points, the number of games, and which team won more games. The likely-duplicate check does compare scores in order, so under `cfg.reject_content_duplicates` a copy with its games reordered can count where a copy in the same order is rejected.
- **No dependence on the replay date, for the rows it counts.** `replay(as_of=d)` records only the rows dated on or before `d`, and gives the same ratings as recording those rows live in ledger order. The replay date decides which rows are recorded and labels the result, but changes no update of a row it records.
