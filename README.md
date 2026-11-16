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

summary = engine.player_summary("alice", RatingFormat.DOUBLES, as_of=date(2026, 10, 29))
print(summary.rating, summary.reliability, summary.imported_reliability)
```

`result.reason` is `None` when the match counted and a `RejectionReason` when it did not. Live recording applies matches in arrival order. A match dated before a player's last match in that rating format, or before the date that player's rating in that format already reflects (for a category rating copied from overall doubles), still counts, but no stored date moves back, and it is late. Check `result.late` and, for a category match, `result.parent.late`. Schedule a replay when either is `True`: only a replay gives play order. Dates are compared, not times, so a same-day match is never late. Reject a match whose `played_on` is after the date it was submitted. The engine never reads the clock, so a future-dated match would hold its players' dates at that future date until the next replay: their RD would stop growing and their reliability would stay high. `player_summary` returns a `PlayerSummary`. Its `reliability` is always OPEN-earned. `imported_reliability` is an imported external reliability while it is carried, shown separately, and `None` otherwise. `player_summary` measures inactivity and reliability at `as_of`. With no `as_of`, it uses the later of the last replay date and the player's last match, so a stale player is measured at the replay date and a player with live matches since at their last match. On a live engine that was never replayed, pass `as_of` or it raises `ReferenceDateError`. It also raises `ReferenceDateError` for an `as_of` that is not a plain date. An `as_of` earlier than the player's last match does not rebuild past state: RD and partner variety stay as of the stored state. The engine never reads the wall clock. `established` is `True` when `reliability` (as displayed, rounded to 1 place) is at least `Config.established_reliability_threshold`, 60 by default. Imported reliability never counts toward it, and it lapses with inactivity like `reliability`. Under the defaults, a new player with no imported rating, playing one match a week with varied partners and opponents, becomes established after about 10 to 13 tournament, league, or recreational verified matches (10 tournament, 11 league, or 13 recreational verified matches of one game, fewer for best of 3), and after about twice as many against the same players. Self-reported matches need about 20 to 25 with varied players and never get there against the same players, because repeat matches are discounted and then rejected. A player seeded from a recently captured imported rating starts at a lower RD (`Config.external_seed_rd`) and is established after about 3 to 4 tournament, league, or recreational verified matches. RD grows from the capture date, so an older import gives less of a head start, and a very old one none. The imported rating and its reliability value do not change the count. A rating that is not established is what external rating systems usually call provisional, which is different from OPEN's provisional live updates between replays and the `open_provisional` source. The threshold is a label only: it changes no rating and no `evaluate` output. Evaluation's eligibility rule (every participant has an imported baseline or at least `burn_in_matches` OPEN matches) is a separate rule from `established`.

Authoritative ratings come from `replay(ledger, as_of=...)`. Calibrate the expectation scale on match history with `fit_scale(...)`. The expectation scale and the K factor together set how sharp predictions are, and in a closed pool they also set the rating spread. Run `fit_scale` with `mode="scheduled"` and a `score_from` date that skips immature ratings, then check `at_bound` and `profile` on the result: a fit that sits on a search edge or has a flat error profile is not identified. `RatingEngine.predict_win_probability` assumes points are independent, which tends to make match win probabilities overconfident. Calibrate them with `fit_win_calibration` and store the result in `Config.win_calibration_by_scoring`.

See [`examples/`](examples) for a fuller example.

## Public API

<!-- public-api:start -->
These are the public names, all importable from `open_engine`. The API may change in any 0.x release.

Names not listed here are internal, including everything reached only through `open_engine.engine` or `open_engine.winprob`. They can change or disappear in any release.

Rating:

- `RatingEngine`: holds players and matches, records matches, and predicts outcomes. Its `predict_point_share` and `predict_win_probability` methods give team A's expected point share and calibrated match win probability. `to_snapshot` saves its whole state as JSON-safe data, and `from_snapshot` loads it back (see "Snapshots and checkpoints").
- `replay`: rebuilds authoritative ratings from seeds and the match ledger as of a date. With `from_snapshot`, it resumes from a checkpoint instead of starting over. Add `whole_ledger=True` when passing the whole ledger, so resume also catches a row voided on or before the checkpoint date.
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
- `PlayerSummary`: a player's rating, reliability, established flag, and provenance in one format.
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
- `InvalidSeedError`: an external or replay seed cannot initialize a rating. `RatingEngine.add_player`, `replay`, and `prequential_predictions` (and so `evaluate`, `fit_scale`, and `fit_win_calibration`) raise it for a seed that breaks the seed rules in `add_player`'s docstring, including a seed that is not a `Player`, a field of the wrong type, or a rating outside the rating bounds. `replay` also raises it for a seed dated after `as_of`, and `prequential_predictions` for one dated after its last ledger match. `RatingEngine.seed_external_rating` raises it for a rating format of the wrong type, a snapshot whose captured date, source, or external id has the wrong type, or whose rating or reliability has the wrong type or is out of range, or an existing rating it may not replace.
- `PlayerError`: a player identity, account link, or external identity is not valid. `seed_external_rating` raises it for a blank player id or when the snapshot's external identity cannot be linked (it names no source, it is already linked to another player, or the player already has a different id from that source), and `replay` and `prequential_predictions` raise it when two seeds share a player id. `replay(from_snapshot=...)` raises it for a seed whose player is already in the snapshot.
- `EvaluationError`: an evaluation, fit, or win probability cannot be computed.
- `ReferenceDateError`: no usable reference date to measure inactivity and reliability at. `RatingEngine.player_summary` raises it when it gets no `as_of` and the engine was never replayed, or an `as_of` that is not a plain date (a datetime is not one), before it creates any player or rating state.
- `LedgerConflictError`: ledger rows share a `match_id` but differ. Its `match_ids` lists them.
- `SnapshotError`: a snapshot cannot be loaded or resumed from. `RatingEngine.from_snapshot` raises it for a malformed snapshot, one taken under another `Config`, or one holding a state the engine cannot produce, and `RatingEngine.to_snapshot` for state that could not load back. `replay(from_snapshot=...)` raises it for a snapshot not from a replay, one with live updates or any other change since its replay, an `as_of` before the checkpoint date, a row on or before the checkpoint date that the checkpoint did not see, a seed for a player named in a row the checkpoint saw, or, with `whole_ledger=True`, a row the checkpoint saw that is missing from the ledger. It also raises it for `whole_ledger=True` without `from_snapshot`.
<!-- public-api:end -->

## Ledger order

`replay` and `prequential_predictions` sort the ledger the same way whatever order the rows arrive in.

- Matches sort by `played_on` first, the calendar date at the venue. The rules below apply within one `played_on` date.
- On one day, matches without a `played_at` come first, by `sequence` and then `match_id`.
- Matches with a `played_at` follow, by the moment they were played in UTC, then `sequence`, then `match_id`. A match at 21:00 at UTC-5 comes after one at 22:00 UTC on the same day.
- `played_at` must include a time zone and fall on `played_on` in that zone.
- Every row passed in is validated, including rows after `as_of`. An invalid row raises `InvalidMatchError`, for example one with a `played_at` without a time zone or a blank player id. A row with an invalid game format raises `ConfigError`. Both are `ValueError`s.
- Rows that repeat a `match_id` count once when they are the same match: every field matches in type and value (so `3` and `3.0` differ), with `played_at` at the same moment and the same UTC offset. Any other repeat raises `LedgerConflictError`, a `ValueError` whose `match_ids` lists every conflicting id. This covers rows after `as_of` too.

## Snapshots and checkpoints

`RatingEngine.to_snapshot()` returns the engine's whole state as plain JSON data. `json.dumps(snapshot, allow_nan=False)` works with no custom encoder. `RatingEngine.from_snapshot(snapshot, cfg)` builds the engine back, from the dict or from `json.loads` of its text. Every value comes back with its type: an int stays an int. The same state always gives the same JSON text.

A snapshot of a replay is a checkpoint. `replay(matches, seeds, cfg=cfg, as_of=later, from_snapshot=snapshot)` resumes from it and gives the same result as a full replay to `later`. You can pass the whole ledger or only the rows dated after the checkpoint. Pass only seeds that are not in the checkpoint.

Take the checkpoint snapshot right after the replay, before any other call on the engine. Every engine method that changes state makes the snapshot one resume refuses: a live match, a seed, an account or external id link, a new player, or a new rating state (`player_summary` and `state` create one for a format the player has no rating in). Changing objects the engine returns or its public attributes (for example `players`, rating states, or `processed_match_ids`) directly is not tracked, and makes the snapshot unsafe to resume.

When you pass the whole ledger, also pass `whole_ledger=True`. Resume then raises `SnapshotError` for any row the checkpoint saw that is missing from the ledger, which catches a match voided or deleted on or before the checkpoint date. `whole_ledger=True` without `from_snapshot` raises `SnapshotError`.

Resume raises instead of giving a different result:

- `SnapshotError` for a malformed snapshot, a snapshot taken under another `Config`, a snapshot that is not from a replay, a snapshot with live updates or any other change since its replay, an `as_of` before the checkpoint date, a row dated on or before the checkpoint date that the checkpoint did not see, or a seed for a player named in a row the checkpoint saw (even when every such row was rejected, so the player is not in the snapshot).
- `LedgerConflictError` for a row dated on or before the checkpoint date that differs from the row the checkpoint saw under that `match_id`, or a later row that reuses one of those ids.
- `PlayerError` for a seed whose player is already in the snapshot.
- `SnapshotError`, with `whole_ledger=True`, for a row the checkpoint saw that is missing from the ledger. It names every missing id, sorted.

The row checks run in this order: changed or reused rows (`LedgerConflictError`), then rows the checkpoint did not see, then (with `whole_ledger=True`) checkpointed rows that are missing.

Resume gives the same result as a full replay of the ledger and seeds its rows come from. It detects every row it is given that disagrees with the checkpoint. With `whole_ledger=True` it also detects checkpointed rows removed from the ledger. It cannot detect a seed that was withdrawn or corrected after the checkpoint, with or without `whole_ledger`. It cannot detect a late (backdated) row on or before the checkpoint date that you leave out. Without `whole_ledger=True`, it also cannot detect a row on or before the checkpoint date that was removed (voided) from the ledger, or an edited one that you leave out. A void, an edit, or a late row on or before the checkpoint date, and a seed withdrawn or corrected after the checkpoint, each need a full replay. Resume raises for such an edited or late row when you pass it, and for a void when you pass `whole_ledger=True`, but the fix is still a full replay. Store a match before the replay that covers its date.

A stored snapshot sets every rating when it is loaded. Give it the same write protection as the ratings themselves. `from_snapshot` checks its shape and the engine's rules, not that it is authentic.

Schema, version 1. Dates are `YYYY-MM-DD` text. Every object has exactly these keys, and `from_snapshot` rejects a missing key, an unknown key, or a value of the wrong type or out of range. It also rejects a state the engine cannot produce: an initial rating outside the rating bounds, an unplayed state with a played state's RD or with partners, opponents, or live updates, `matches_played` and `last_played` that disagree, more live updates than matches, a player listed as their own partner or opponent, or an imported reliability with no import date. Account and external ids are only type checked, since the engine can build shared or blank ones and they do not affect ratings. In a resumable snapshot, no date may be after `calculated_as_of`, and the replay counts must match `ledger_digests` and `processed_match_ids`. Counts are at most `2**63 - 1`.

- `snapshot_version`: `1`.
- `engine_version`: the package version that wrote it. Informational, not checked.
- `config_version`, `config_fingerprint`: of the `Config` it was taken under. `from_snapshot` checks both against the `Config` it is given.
- `calculated_as_of`: the date of the last replay, or `null`.
- `replay_info`: `null` when `calculated_as_of` is, otherwise `as_of`, `config_version`, `config_fingerprint`, `matches_considered`, and `matches_counted`.
- `players`: sorted by `id`. Each has `id`, `account_user_id` (text or `null`), `external_ids` (source to id), and `ratings` (rating format value to state). A state has every `RatingState` field: `rating`, `rd`, `matches_played`, `last_played`, `rd_as_of`, `initial_rating`, `initial_source`, `initial_source_date`, `initial_source_reliability`, `initial_external_player_id`, `initial_evidence_reference`, `live_updates_since_replay`, `partners`, and `opponents` (the last two map a player id to a date).
- `processed_match_ids`: sorted.
- `self_reported_log`: sorted by format, then teams. Each entry has `rating_format`, `teams` (two sorted lists of player ids), and `dates` in the order they were recorded.
- `ledger_digests`: `match_id` to the SHA-256 digest of every row a replay considered, kept across resumes. Resume uses them to check rows dated on or before the checkpoint.
- `ledger_player_ids`: sorted ids of every player named in those rows, counted or not. Resume refuses a seed for one of them.
- `resumable`: `true` only while the engine is exactly what a replay produced. Resume refuses a snapshot where it is `false`.

## Test data

Use only made-up or properly de-identified data. Do not put real player names, contact details, or match histories in this repository, including in test fixtures and screenshots. See [`tests/fixtures/README.md`](tests/fixtures/README.md).

## Contributing

See the [contributing guide](https://github.com/openpickleball/.github/blob/main/CONTRIBUTING.md). Every commit must be signed off. Please report security problems privately, as described in the [security policy](https://github.com/openpickleball/.github/blob/main/SECURITY.md).

## License

This repository is licensed under [AGPL-3.0-or-later](LICENSE). See [how OPEN is licensed](https://github.com/openpickleball/.github/blob/main/LICENSING.md).
