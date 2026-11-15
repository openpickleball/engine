# OPEN rating engine

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/openpickleball/engine/badge)](https://scorecard.dev/viewer/?uri=github.com/openpickleball/engine)

The reference rating engine for [OPEN](https://github.com/openpickleball), the Open Pickleball Evaluation Network.

> **Project status:** This engine is a demonstration. Its methods have not been validated on real match data, its outputs are not OPEN ratings, and its API may change without notice. Do not use it to rate real players. OPEN is in early development, and no OPEN rating is live.

## Technology

The engine is a Python package (`openpickleball-engine`, imported as `open_engine`) with no runtime dependencies. It requires Python 3.11 or later.

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

summary = engine.player_summary("alice", RatingFormat.DOUBLES)
print(summary.rating, summary.reliability, summary.reliability_basis)
```

`result.reason` is `None` when the match counted and a `RejectionReason` when it did not. `player_summary` returns a `PlayerSummary`.

Authoritative ratings come from `replay(ledger, as_of=...)`. Calibrate the expectation scale on match history with `fit_scale(...)`. The expectation scale and the K factor together set how sharp predictions are, and in a closed pool they also set the rating spread. Run `fit_scale` with `mode="scheduled"` and a `score_from` date that skips immature ratings, then check `at_bound` and `profile` on the result: a fit that sits on a search edge or has a flat error profile is not identified. `RatingEngine.predict_win_probability` assumes points are independent, which tends to make match win probabilities overconfident. Calibrate them with `fit_win_calibration` and store the result in `Config.win_calibration_by_scoring`.

See [`examples/`](examples) for a fuller example.

## Public API

<!-- public-api:start -->
These are the public names, all importable from `open_engine`. The API may change in any 0.x release.

Names not listed here are internal, including everything reached only through `open_engine.engine` or `open_engine.winprob`. They can change or disappear in any release.

Rating:

- `RatingEngine`: holds players and matches, records matches, and predicts outcomes. Its `predict_point_share` and `predict_win_probability` methods give team A's expected point share and calibrated match win probability.
- `replay`: rebuilds authoritative ratings from seeds and the match ledger as of a date.
- `ledger_order`: the sort key that sets the order of play in a ledger.
- `reliability`: OPEN-earned reliability (0 to 100) for one rating state.
- `expected_point_share`: team A's expected share of points from two team ratings and a scale.

Inputs:

- `Config`: every rating parameter, with a fingerprint for provenance.
- `Match`: one completed match with its teams, game scores, date, and format.
- `GameFormat`: how each game is scored (scoring type, points to, win by, cap).
- `RatingFormat`: the rating pool (singles, doubles, and the doubles categories).
- `MatchType`: how a match was organized and verified, which sets its weight.
- `ScoringType`: side-out or rally scoring.
- `Player`: a player record with ratings by format and linked identities.
- `RatingState`: one player's rating in one format, with uncertainty and provenance.
- `ExternalRatingSnapshot`: a verified external rating, used only as a seed.

Results:

- `MatchResult`: what `record` did with a match, including the rating changes.
- `RejectionReason`: why a match was not counted.
- `PlayerSummary`: a player's rating, reliability, and provenance in one format.
- `ReliabilityBasis`: whether a displayed reliability is OPEN-earned or imported.
- `ReplayInfo`: the date, config version, and config fingerprint of a replay.

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

Errors (every class is a `ValueError`):

- `OpenEngineError`: base class for every error the engine raises on purpose.
- `ConfigError`: a `Config` or `GameFormat` value is not valid.
- `InvalidMatchError`: a match, its teams, its scores, or its `played_at` is not valid.
- `InvalidSeedError`: an external or replay seed cannot initialize a rating.
- `PlayerError`: a player identity, account link, or external identity is not valid.
- `EvaluationError`: an evaluation, fit, or win probability cannot be computed.
- `LedgerConflictError`: ledger rows share a `match_id` but differ. Its `match_ids` lists them.
<!-- public-api:end -->

## Ledger order

`replay` and `prequential_predictions` sort the ledger the same way whatever order the rows arrive in.

- Matches sort by `played_on` first, the calendar date at the venue. The rules below apply within one `played_on` date.
- On one day, matches without a `played_at` come first, by `sequence` and then `match_id`.
- Matches with a `played_at` follow, by the moment they were played in UTC, then `sequence`, then `match_id`. A match at 21:00 at UTC-5 comes after one at 22:00 UTC on the same day.
- `played_at` must include a time zone and fall on `played_on` in that zone.
- Every row passed in is validated, including rows after `as_of`. An invalid row, such as one with a `played_at` without a time zone, raises `ValueError`.
- Rows that repeat a `match_id` count once when they are the same match: every field matches in type and value (so `3` and `3.0` differ), with `played_at` at the same moment and the same UTC offset. Any other repeat raises `LedgerConflictError`, a `ValueError` whose `match_ids` lists every conflicting id. This covers rows after `as_of` too.

## Test data

Use only made-up or properly de-identified data. Do not put real player names, contact details, or match histories in this repository, including in test fixtures and screenshots. See [`tests/fixtures/README.md`](tests/fixtures/README.md).

## Contributing

See the [contributing guide](https://github.com/openpickleball/.github/blob/main/CONTRIBUTING.md). Every commit must be signed off. Please report security problems privately, as described in the [security policy](https://github.com/openpickleball/.github/blob/main/SECURITY.md).

## License

This repository is licensed under [AGPL-3.0-or-later](LICENSE). See [how OPEN is licensed](https://github.com/openpickleball/.github/blob/main/LICENSING.md).
