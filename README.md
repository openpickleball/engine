# OPEN rating engine

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/openpickleball/engine/badge)](https://scorecard.dev/viewer/?uri=github.com/openpickleball/engine)

The reference rating engine for [OPEN](https://github.com/openpickleball), the Open Pickleball Evaluation Network.

> **Project status:** This engine is the reference implementation of the OPEN rating method. Its outputs are not OPEN ratings. Do not use it to rate real players. OPEN is in early development, and no OPEN rating is live.
>
> The engine was evaluated on one club's closed pool: 1,601 doubles matches among 189 players. A forthcoming report describes the evaluation. Parameter set `v1` did not pass the gain bar of the rule for changing defaults, so the `Config()` defaults remain placeholders, and `v1` is loaded explicitly ([parameters](https://github.com/openpickleball/engine/blob/main/docs/parameters.md)).
>
> Ratings are provisional. They come from one club's closed pool. Ratings from pools that share no players are not comparable. Parameters are retuned on a published [schedule](https://github.com/openpickleball/engine/blob/main/docs/parameters.md#schedule) (issue #48).
>
> The engine has known weaknesses, such as regular partners sharing one rating, no check for deliberate losing, and no limit on made-up league matches between colluding accounts. Read [Known limitations](#known-limitations) before relying on it.

## Known limitations

The engine can be gamed, and it rates fixed partnerships poorly. The OPEN evaluation found the problems below by simulating leagues of invented players whose true skill was known, then comparing their ratings with that skill. They show what the engine does in each situation, not how often it happens in real play. Each finding comes from 20 simulated runs of one fixed setup, on a pre-1.0 build of the engine (0.3.0.dev0).

Every rating amount below is on one common scale, the one `Config()` uses, so the two configurations can be compared. To read an amount as a `v1` rating, divide it by 2.9.

- **Regular partners end up with the same rating.** Two players who always play together get the same updates, so they hold the same rating, and the engine cannot tell who is carrying whom. When a weaker and a stronger player (true gap 1.6) always partnered, the weaker one ended about 0.8 too high and the stronger one about 0.8 too low: both landed in the middle. Reliability gave no warning. The partner gap filter (`Config.max_doubles_partner_gap`) cannot catch this, because the two hold the same rating. Set to a gap of 1.0 (0.345 in a `v1` config), it rejected 29% of the honest matches in that league under `v1` and 6% under `Config()`, and the weaker partner stayed about as overrated.
- **Losing on purpose is not detected.** An established player who threw 15 matches, losing every game 11-0, dropped by about 1.9 under `v1` and 0.8 under `Config()`. After 60 more honest matches, the player was still more than 0.25 below their earlier place relative to the pool in most runs (12 of 20 under `v1`, 16 of 20 under `Config()`). Uncertainty and reliability did not change, so nothing flagged it.
- **Colluding accounts can inflate one player.** Made-up league matches count without limit. Four accounts that recorded 40 made-up league matches among themselves, each won 11-0 by the same player, raised that player by about 3.5 under `v1` and 1.8 under `Config()`. The other three accounts lost what the target gained, and the target's reliability did not drop. Self-reported matches are limited by the repeat rule, which held the gain from the same 40 matches to about 0.4 under `v1` and 0.15 under `Config()`, but a smaller gain is still a gain.
- **A closed group can be labeled established.** New players who play only each other stay near the starting rating, whatever their skill. A group of weaker newcomers who played only each other for 20 nights ended level with the main league, about 0.8 above their true place, and every member was labeled established. The connectivity cap meant for this case (`Config.connectivity_reliability_cap`) is off in `Config()` and in `v1`.
- **`v1` makes gaming pay more.** `v1` moves ratings faster than `Config()`, so the sandbagging and collusion effects were 1.9 to 2.6 times as large under `v1`. Regular partners and closed groups ended about as far off under both.
- **`v1` is not shown to predict better.** On held-out matches, `v1` showed no measurable gain in accuracy over the `Config()` defaults ([PARAMETERS.md](https://github.com/openpickleball/engine/blob/main/PARAMETERS.md)). A test on matches played after the evaluation, registered in advance, is still pending.

The model's [Assumptions](https://github.com/openpickleball/engine/blob/main/docs/model.md#assumptions) explain why: a team's rating is the mean of its players' ratings, with no term for a pair, and the engine takes recorded results as given.

## Technology

The engine is a Python package (`openpickleball-engine`, imported as `open_engine`) with no runtime dependencies. It requires Python 3.11 or later.

A standard external rating system is the intended source of imported ratings, which seed a player's starting rating. An imported reliability is shown separately from OPEN-earned reliability. The engine uses none of that system's code or internals, and it is not a reproduction of any proprietary rating algorithm.

[`docs/model.md`](https://github.com/openpickleball/engine/blob/main/docs/model.md) states the rating model: every equation the engine uses to move a rating, compute reliability, and predict and evaluate matches, with the function each comes from and a table of the `Config` parameters.

[`docs/behavior.md`](https://github.com/openpickleball/engine/blob/main/docs/behavior.md) holds the published behavior notes: the engine's edge cases (input types, errors, ledger order, duplicates, late matches, snapshots, and the rest) and the requirements the backend must meet.

## Install

The package is on PyPI. Install it with:

```bash
pip install openpickleball-engine
```

To reproduce a result, such as an evaluation, pin the exact version (`openpickleball-engine==1.0.0`). The package's outputs are not OPEN ratings.

## Setup and tests

To work on the engine, install it from a local checkout:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the tests, linter, formatter, and type checker:

```bash
pytest
ruff check .
ruff format .
mypy src
```

Build a wheel and source distribution:

```bash
python -m build
```

## Quick start

```python
from datetime import date
from open_engine import Config, Match, MatchType, RatingEngine, RatingFormat

engine = RatingEngine(Config.published("v1"))
result = engine.record(
    Match(
        match_id="league-2026-10-29-court-3-match-7",
        team_a=("alice", "carol"),
        team_b=("bob", "dave"),
        games=((11, 8), (9, 11), (11, 7)),
        played_on=date(2026, 10, 29),
        rating_format=RatingFormat.DOUBLES,
        match_type=MatchType.LEAGUE,
        best_of=3,
    )
)
print(result.counted, result.reason, result.deltas)

summary = engine.player_summary("alice", RatingFormat.DOUBLES, as_of=date(2026, 10, 29))
print(summary.rating, summary.reliability, summary.imported_reliability)
```

`RatingEngine()` with no config uses the `Config()` placeholders, which carry no declared gap meaning, and `v1` is the published set (see [What a rating means](#what-a-rating-means) and [PARAMETERS.md](https://github.com/openpickleball/engine/blob/main/PARAMETERS.md)).

`result.reason` is `None` when the match counted and a `RejectionReason` when it did not. Authoritative ratings come from `replay(ledger, cfg=Config.published("v1"), as_of=...)`, with the same config as the live engine. Live updates between replays are provisional. See [`examples/`](https://github.com/openpickleball/engine/tree/main/examples) for a fuller example.

The rules behind each step are in [`docs/behavior.md`](https://github.com/openpickleball/engine/blob/main/docs/behavior.md). The main ones to know before you try the engine:

- **Late matches.** Live recording applies matches in arrival order. Check `result.late` and, for a category match, `result.parent.late`. Schedule a replay when either is `True`: only a replay gives play order. See [Late live matches](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#late-live-matches).
- **Match ids.** A `match_id` names one match. So a corrected resubmission needs a new `match_id`, and the rejected row stays in the ledger as it was. Resubmit only after a rejection that comes from the match itself: `GAME_FORMAT_NOT_ACCEPTED`, `SELF_REPORTED_UNCONFIRMED`, or `MATCH_NOT_COMPLETED`. See [Match ids, rejections, and resubmission](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#match-ids-rejections-and-resubmission).
- **Possible duplicates.** Check `result.possible_duplicate_of` too. It names an earlier counted match with the same date, the same two teams, and the same game scores, under another `match_id`, so the same match may have been reported twice. See [Likely duplicates](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#likely-duplicates).
- **Player summaries.** `player_summary` returns a `PlayerSummary`. On a live engine that was never replayed, pass `as_of` or it raises `ReferenceDateError`. Its `reliability` is always OPEN-earned. An imported reliability is shown separately, in `imported_reliability`. `established` says whether the rating has reached the reliability threshold and enough verified matches, and `connectivity` says whether the player's pool connects to the wider network. See [Player summaries](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#player-summaries), [Established](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#established), and [Network connectivity](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#network-connectivity).
- **Calibration.** Fit the expectation scale with `fit_scale` and the win probability calibration with `fit_win_calibration`, and check whether a fit is identified. See [Calibration fits](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#calibration-fits).
- **Backend.** The engine is not a persistence layer, and it cannot check who sent a match. A service that runs it must meet the [Backend requirements](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#backend-requirements), for example rejecting a match dated after the day it was submitted.

## Public API

<!-- public-api:start -->
These are the public names, all importable from `open_engine`.

Names not listed here are internal, including everything reached only through `open_engine.engine` or `open_engine.winprob`. They can change or disappear in any release.

Since 1.0.0, the public API follows strict Semantic Versioning: a breaking change needs a new major version. The promise covers the names below, the parameters and defaults of every public function and method (other than `Config` defaults), the fields of each dataclass, the members and values of each enum, the error classes and which one each documented failure raises, and the `RatingEngine` attributes `cfg`, `players`, `processed_match_ids`, `possible_duplicates`, `calculated_as_of`, and `replay_info`, which are for reading only. It also covers the ledger rules: the order `ledger_order` gives, the duplicate and conflict rules, and that resuming equals a full replay or raises, within the limits in [Replay and resume](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#replay-and-resume). It does not cover snapshots (a minor release may raise `snapshot_version`, an older snapshot raises `SnapshotError`, and a full replay rebuilds it), rating values and default `Config` values (retuned in minor releases and recorded in the changelog), additions in a minor release, stored `Config.to_dict()` output, error message text, internal modules, or pickles made by another version. `RatingEngine.player()` returns the engine's own `Player` record, not a copy, and `RatingEngine.state()` returns the engine's own stored `RatingState` (a starting state for a format with no stored state is a new copy). A later update replaces a stored `RatingState` with a new object, so one returned earlier keeps the old values. Changing a `Player` or `RatingState` in place is unsupported and outside this promise. A `MatchResult` is new output for each call. A published parameter set's values never change: retuning publishes a new label, such as `v2`, and a change to the `Config()` defaults comes in a minor release. [Stability](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#stability) has the full list.

Rating:

- `RatingEngine`: holds players and matches, records matches, and predicts outcomes. Its `predict_point_share` and `predict_win_probability` methods give team A's expected point share and calibrated match win probability. `to_snapshot` saves its whole state as JSON-safe data, and `from_snapshot` loads it back (see "Snapshots and checkpoints"). Its `possible_duplicates` attribute maps the `match_id` of each match flagged or rejected as a possible duplicate to the `match_id` it repeats, live and after a replay. An entry never clears: a counted copy's `match_id` is processed, and a rejected copy's `match_id` is reserved, so neither can count again.
- `replay`: rebuilds authoritative ratings from seeds and the match ledger as of a date. With `from_snapshot`, it resumes from a checkpoint instead of starting over. Add `whole_ledger=True` when passing the whole ledger, so resume also catches a row voided on or before the checkpoint date.
- `ledger_order`: the sort key that sets the order of play in a ledger.
- `reliability`: OPEN-earned reliability (0 to 100) for one rating state, from RD and partner and opponent variety. `PlayerSummary.reliability` also applies the network connectivity cap when `Config.connectivity_reliability_cap` is set.
- `expected_point_share`: team A's expected share of points from two team ratings and a scale.

Inputs:

- `Config`: every rating parameter, with a fingerprint for provenance. `params_version` labels a published parameter set (`None` or `v<N>`), and `parameter_fingerprint()` hashes the parameter values without `version` and `params_version`, so it stays the same across releases that change no parameter value (a release that adds, removes, or renames a `Config` field, or changes how a value is written in the payload, changes it). Its mapping fields are read-only copies, and a config is hashable. Equal configs have the same fingerprint and hash. `Config.to_dict()` and `Config.from_dict()` store and load one with the same fingerprint. `Config.published(label)` loads a published parameter set, such as `v1`, from the installed package, and `Config.published_sets()` lists the labels this release publishes. `Config.from_parameter_set()` takes the parsed JSON of a parameter set file and loads it only when its label and values match a set this release publishes; `published` goes through it.
- `Match`: one match with its teams, game scores, date, format, and status. Only a completed match can count.
- `MatchStatus`: how a match ended: completed, forfeit, retired, or incomplete (see "Match status").
- `GameFormat`: how each game is scored (scoring type, points to, win by, cap).
- `RatingFormat`: the rating pool (singles, doubles, and the doubles categories).
- `MatchType`: how a match was organized and verified, which sets its weight.
- `ScoringType`: side-out or rally scoring.
- `Player`: a player record with ratings by format and linked identities.
- `RatingState`: one player's rating in one format, with uncertainty and provenance.
- `ExternalRatingSnapshot`: a verified external rating, used only as a seed.

Results:

- `MatchResult`: what `record` did with a match, including the rating changes.
- `RejectionReason`: why a match was not counted. `MATCH_NOT_COMPLETED` covers every status other than completed.
- `PlayerSummary`: a player's rating, reliability, established flag, network connectivity, and provenance in one format, including the config's `params_version` and parameter fingerprint.
- `NetworkConnectivity`: whether a player's pool connects to the main network in one format, with the pool size, fringe, outside players and dates, and the size of the player's group behind it.
- `ReplayInfo`: the date, config version, config fingerprint, `params_version`, parameter fingerprint, and match counts of a replay, and `net_rating_created`, the net rating its counted updates added to each rating format's pool.

Evaluation and calibration:

- `prequential_predictions`: predicts each match from ratings that have not seen it.
- `PrequentialPrediction`: one such prediction.
- `evaluate`: scores prequential predictions of point share.
- `EvaluationReport`: error and calibration bins from `evaluate`.
- `fit_scale`: fits the expectation scale for one scoring type.
- `ScaleFit`: the result of `fit_scale`.
- `fit_win_calibration`: fits the win probability calibration for one scoring type.
- `WinCalibrationFit`: the result of `fit_win_calibration`.
- `game_win_probability`: probability of winning a game from a point win probability.
- `match_win_probability`: probability of winning a best-of-N match from a point win probability.
- `calibrate_win_probability`: applies a fitted calibration to a win probability.

Exact input types: every public function and method takes exactly the types it names for each argument it checks. A subclass, or an object whose `__class__` only claims the type, raises the entry point's error before any state changes. A few inputs, such as `Config` numbers, are read once and converted to plain values. See [Input types](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#input-types) for the full rules and the carve-outs.

Errors (every class is a `ValueError`). [Errors](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#errors) lists what raises each one.

- `OpenEngineError`: base class for every error the engine raises on purpose.
- `ConfigError`: a `Config` value is not valid, a `cfg` is not `None` or exactly a `Config` holding only its fields (`reliability` takes no `None`), or a `GameFormat` checked outside a match is not valid.
- `InvalidMatchError`: a match, its teams, its scores, its `played_at`, or its `GameFormat` is not valid.
- `InvalidSeedError`: an external or replay seed cannot initialize a rating.
- `PlayerError`: a player identity, account link, or external identity is not valid.
- `UnknownPlayerError`: a `PlayerError` for a player id with no player record.
- `EvaluationError`: an evaluation, fit, or win probability cannot be computed.
- `ReferenceDateError`: no usable reference date to measure inactivity and reliability at.
- `LedgerConflictError`: ledger rows share a `match_id` but differ. Its `match_ids` lists them.
- `SnapshotError`: a snapshot cannot be loaded or resumed from.
<!-- public-api:end -->

## Config values

`Config` holds every rating parameter. It checks every field when it is built and raises `ConfigError` for any value it rejects, such as a NaN, a value out of range, or a missing mapping key. Compare an engine's config with `==` or `fingerprint()`, not `is`. See [Config validation](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#config-validation) for every rule, and the [parameter table](https://github.com/openpickleball/engine/blob/main/docs/model.md#parameters) for what each one does.

`Config()` is not a published parameter set. The published sets, such as `v1`, ship in the package (`src/open_engine/params/` in the repository) and are listed in [PARAMETERS.md](https://github.com/openpickleball/engine/blob/main/PARAMETERS.md). Load one with `Config.published("v1")`, which reads the packaged file and checks it against the sets this release publishes. `Config.published_sets()` lists the labels. A new set needs an engine release that adds it. See [Maintenance and parameter versioning](https://github.com/openpickleball/engine/blob/main/docs/parameters.md) for the file format, the existing-ratings rule, and the retuning rules. Engine output under a published set is still not an OPEN rating.

## Ledger order

`replay` and `prequential_predictions` sort the ledger the same way whatever order the rows arrive in: by date, with untimed matches first on each date, then timed matches by the moment they were played. Rows that repeat a `match_id` count once when they are the same match, and any other repeat raises `LedgerConflictError`. See [Ledger order](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#ledger-order).

## Match performance

A match moves ratings by how the result compared with the expectation. By default, performance is a team's share of all points across the match's games. So a team can win and still lose rating, for example when it wins by less than expected. There is no floor on a winner's change, other than the rating range. See [Match performance](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#match-performance) and the [model](https://github.com/openpickleball/engine/blob/main/docs/model.md#match-performance).

## What a rating means

A rating gap sets the share of points each team is expected to win, not the chance of winning the match. Under the published parameter set `v1`, a team rating gap of 0.5 means the stronger team is expected to win about 76.0% of the points, and a gap of 1.0 about 90.9%. That meaning is declared, not fitted, and `v1` records it, so changing it takes a changed published parameter set that anyone can see. The `Config()` defaults are placeholders and do not carry this meaning (under them the same gaps give about 59.8% and 68.9%). Load `v1` with `Config.published("v1")` to use it.

A new player with no seed starts at 3.5. A rating places a player relative to the players they have played, directly or through others. So in singles or overall doubles, a pool where every player starts new keeps its mean near 3.5 whatever its skill, as long as its total imbalance is small. Ratings from pools that share no players are not comparable. An imported external rating is only a seed, never a conversion: it sets a starting state (rating, RD, and a date that freezes the player in matches on or before it), and its reliability is shown separately. The evaluation's seeding test did not pass, so OPEN's first release does not seed: every player starts as a new player, at 3.5 in singles and overall doubles. No setting is chosen to move OPEN ratings toward an external rating. See [What a rating means](https://github.com/openpickleball/engine/blob/main/docs/model.md#what-a-rating-means).

## Match status

`Match.status` says how a match ended. It is a `MatchStatus` and defaults to `COMPLETED`. A match that is not completed (a forfeit, a retirement, or an incomplete match) is never rated, but `replay` accepts the row. A status keeps a match from moving any rating, so the backend should verify a status as it verifies scores. See [Match status](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#match-status) and [Match fields](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#match-fields).

## Snapshots and checkpoints

`RatingEngine.to_snapshot()` returns the engine's whole state as plain JSON data, and `RatingEngine.from_snapshot(snapshot, cfg)` builds the engine back. A snapshot of a replay is a checkpoint: `replay(..., from_snapshot=snapshot)` resumes from it. Resume gives the same result as a full replay of the ledger and seeds its rows come from. It detects every row it is given that disagrees with the checkpoint. A void, an edit, or a late row on or before the checkpoint date, and a seed withdrawn or corrected after the checkpoint, each need a full replay. Take the checkpoint snapshot right after the replay, before any other call on the engine. A stored snapshot sets every rating when it is loaded. Give it the same write protection as the ratings themselves. See [Snapshots and checkpoints](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#snapshots-and-checkpoints), [Replay and resume](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#replay-and-resume), and the [snapshot schema](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#snapshot-schema).

## Test data

Use only made-up or properly de-identified data. Do not put real player names, contact details, or match histories in this repository, including in test fixtures and screenshots. See [`tests/fixtures/README.md`](https://github.com/openpickleball/engine/blob/main/tests/fixtures/README.md).

## Contributing

See the [contributing guide](https://github.com/openpickleball/.github/blob/main/CONTRIBUTING.md). Every commit must be signed off. Please report security problems privately, as described in the [security policy](https://github.com/openpickleball/.github/blob/main/SECURITY.md).

## License

This repository is licensed under [AGPL-3.0-or-later](https://github.com/openpickleball/engine/blob/main/LICENSE). See [how OPEN is licensed](https://github.com/openpickleball/.github/blob/main/LICENSING.md).
