# OPEN rating engine

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/openpickleball/engine/badge)](https://scorecard.dev/viewer/?uri=github.com/openpickleball/engine)

The reference rating engine for [OPEN](https://github.com/openpickleball), the Open Pickleball Evaluation Network.

> **Project status:** This engine is a demonstration. Its methods have not been validated on real match data, its outputs are not OPEN ratings, and its API may change without notice. Do not use it to rate real players. OPEN is in early development, and no OPEN rating is live.

## Technology

The engine is a Python package (`openpickleball-engine`, imported as `open_engine`) with no runtime dependencies. It requires Python 3.11 or later.

A standard external rating system is the intended source of imported ratings, which seed a player's starting rating. An imported reliability is shown separately from OPEN-earned reliability. The engine uses none of that system's code or internals, and it is not a reproduction of any proprietary rating algorithm.

[`docs/model.md`](https://github.com/openpickleball/engine/blob/main/docs/model.md) states the rating model: every equation the engine uses to move a rating, compute reliability, and predict and evaluate matches, with the function each comes from and a table of the `Config` parameters.

[`docs/behavior.md`](https://github.com/openpickleball/engine/blob/main/docs/behavior.md) holds the published behavior notes: the engine's edge cases (input types, errors, ledger order, duplicates, late matches, snapshots, and the rest) and the requirements the backend must meet.

## Install

A dev release is on TestPyPI, the Python test index. It is not on PyPI. Install it with:

```bash
pip install -i https://test.pypi.org/simple/ openpickleball-engine==0.3.0.dev0
```

A dev release is for trying the package. The engine is still a demonstration, and its outputs are not OPEN ratings.

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
from open_engine import Match, MatchType, RatingEngine, RatingFormat

engine = RatingEngine()
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

`result.reason` is `None` when the match counted and a `RejectionReason` when it did not. Authoritative ratings come from `replay(ledger, as_of=...)`. Live updates between replays are provisional. See [`examples/`](https://github.com/openpickleball/engine/tree/main/examples) for a fuller example.

The rules behind each step are in [`docs/behavior.md`](https://github.com/openpickleball/engine/blob/main/docs/behavior.md). The main ones to know before you try the engine:

- **Late matches.** Live recording applies matches in arrival order. Check `result.late` and, for a category match, `result.parent.late`. Schedule a replay when either is `True`: only a replay gives play order. See [Late live matches](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#late-live-matches).
- **Match ids.** A `match_id` names one match. So a corrected resubmission needs a new `match_id`, and the rejected row stays in the ledger as it was. Resubmit only after a rejection that comes from the match itself: `GAME_FORMAT_NOT_ACCEPTED`, `SELF_REPORTED_UNCONFIRMED`, or `MATCH_NOT_COMPLETED`. See [Match ids, rejections, and resubmission](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#match-ids-rejections-and-resubmission).
- **Possible duplicates.** Check `result.possible_duplicate_of` too. It names an earlier counted match with the same date, the same two teams, and the same game scores, under another `match_id`, so the same match may have been reported twice. See [Likely duplicates](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#likely-duplicates).
- **Player summaries.** `player_summary` returns a `PlayerSummary`. On a live engine that was never replayed, pass `as_of` or it raises `ReferenceDateError`. Its `reliability` is always OPEN-earned. An imported reliability is shown separately, in `imported_reliability`. `established` says whether the rating has reached the reliability threshold and enough verified matches, and `connectivity` says whether the player's pool connects to the wider network. See [Player summaries](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#player-summaries), [Established](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#established), and [Network connectivity](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#network-connectivity).
- **Calibration.** Fit the expectation scale with `fit_scale` and the win probability calibration with `fit_win_calibration`, and check whether a fit is identified. See [Calibration fits](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#calibration-fits).
- **Backend.** The engine is not a persistence layer, and it cannot check who sent a match. A service that runs it must meet the [Backend requirements](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#backend-requirements), for example rejecting a match dated after the day it was submitted.

## Public API

<!-- public-api:start -->
These are the public names, all importable from `open_engine`. The API may change in any 0.x release.

Names not listed here are internal, including everything reached only through `open_engine.engine` or `open_engine.winprob`. They can change or disappear in any release.

Rating:

- `RatingEngine`: holds players and matches, records matches, and predicts outcomes. Its `predict_point_share` and `predict_win_probability` methods give team A's expected point share and calibrated match win probability. `to_snapshot` saves its whole state as JSON-safe data, and `from_snapshot` loads it back (see "Snapshots and checkpoints"). Its `possible_duplicates` attribute maps the `match_id` of each match flagged or rejected as a possible duplicate to the `match_id` it repeats, live and after a replay. An entry never clears: a counted copy's `match_id` is processed, and a rejected copy's `match_id` is reserved, so neither can count again.
- `replay`: rebuilds authoritative ratings from seeds and the match ledger as of a date. With `from_snapshot`, it resumes from a checkpoint instead of starting over. Add `whole_ledger=True` when passing the whole ledger, so resume also catches a row voided on or before the checkpoint date.
- `ledger_order`: the sort key that sets the order of play in a ledger.
- `reliability`: OPEN-earned reliability (0 to 100) for one rating state, from RD and partner and opponent variety. `PlayerSummary.reliability` also applies the network connectivity cap when `Config.connectivity_reliability_cap` is set.
- `expected_point_share`: team A's expected share of points from two team ratings and a scale.

Inputs:

- `Config`: every rating parameter, with a fingerprint for provenance. `params_version` labels a published parameter set (`None` or `v<N>`), and `parameter_fingerprint()` hashes the parameter values without `version` and `params_version`, so it stays the same across releases that change no parameter. Its mapping fields are read-only copies, and a config is hashable. Equal configs have the same fingerprint and hash. `Config.to_dict()` and `Config.from_dict()` store and load one with the same fingerprint.
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
- `PlayerSummary`: a player's rating, reliability, established flag, network connectivity, and provenance in one format.
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

## Ledger order

`replay` and `prequential_predictions` sort the ledger the same way whatever order the rows arrive in: by date, with untimed matches first on each date, then timed matches by the moment they were played. Rows that repeat a `match_id` count once when they are the same match, and any other repeat raises `LedgerConflictError`. See [Ledger order](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#ledger-order).

## Match performance

A match moves ratings by how the result compared with the expectation. By default, performance is a team's share of all points across the match's games. So a team can win and still lose rating, for example when it wins by less than expected. There is no floor on a winner's change, other than the rating range. See [Match performance](https://github.com/openpickleball/engine/blob/main/docs/behavior.md#match-performance) and the [model](https://github.com/openpickleball/engine/blob/main/docs/model.md#match-performance).

## What a rating means

A rating gap sets the share of points each team is expected to win. Under the default scale, a team rating gap of 0.5 means the higher team is expected to win about 59.8% of the points, and a gap of 1.0 about 68.9%. The default scale is an uncalibrated placeholder, so these figures can change. A new player with no seed starts at 3.5, and a rating places a player relative to the players they have played. So in singles or overall doubles, a pool where every player starts new keeps its mean near 3.5 whatever its skill, as long as its total imbalance is small. Ratings from pools that share no players are not comparable. An imported external rating is a seed only: it sets a starting state (rating, RD, and a date that freezes the player in matches on or before it), and no setting is chosen to move OPEN ratings toward an external rating. See [What a rating means](https://github.com/openpickleball/engine/blob/main/docs/model.md#what-a-rating-means).

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
