# OPEN rating engine

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/openpickleball/engine/badge)](https://scorecard.dev/viewer/?uri=github.com/openpickleball/engine)

The reference rating engine for [OPEN](https://github.com/openpickleball), the Open Pickleball Evaluation Network.

> **Project status:** This engine is a demonstration. Its methods have not been validated on real match data, its outputs are not OPEN ratings, and its API may change without notice. Do not use it to rate real players. OPEN is in early development, and no OPEN rating is live.

## Technology

The engine is a Python package (`openpickleball-engine`, imported as `open_engine`) with no runtime dependencies. It requires Python 3.11 or later.

A standard external rating system is the intended source of imported ratings. An imported rating only seeds a player's starting rating. It never overwrites a rating earned from OPEN matches, and an imported reliability is shown separately from OPEN-earned reliability. The engine uses none of that system's code or internals, and it is not a reproduction of any proprietary rating algorithm.

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

`result.reason` is `None` when the match counted and a `RejectionReason` when it did not. Live recording applies matches in arrival order. A match dated before a player's last match in that rating format, or before the date that player's rating in that format already reflects (for a category rating copied from overall doubles), still counts, but no stored date moves back, and it is late. Check `result.late` and, for a category match, `result.parent.late`. Schedule a replay when either is `True`: only a replay gives play order. Dates are compared, not times, so a same-day match is never late. Reject a match whose `played_on` is after the date it was submitted. The engine never reads the clock, so a future-dated match would hold its players' dates at that future date until the next replay: their RD would stop growing and their reliability would stay high. `player_summary` returns a `PlayerSummary`. Its `reliability` is always OPEN-earned. `imported_reliability` is an imported external reliability while it is carried, shown separately, and `None` otherwise. `live_updates_since_recalculation` labels the provisional movement in the rating: the state's own live updates since the last replay, plus, for a category state derived from overall doubles, the parent's live updates already in the rating it copied (`RatingState.inherited_live_updates`). It is 0 right after a replay. `player_summary` measures inactivity and reliability at `as_of`. With no `as_of`, it uses the later of the last replay date and the player's last match, so a stale player is measured at the replay date and a player with live matches since at their last match. On a live engine that was never replayed, pass `as_of` or it raises `ReferenceDateError`. It also raises `ReferenceDateError` for an `as_of` that is not a plain date. After those checks, it raises `PlayerError` for a blank player id and `UnknownPlayerError` for a player id with no player record. For a known player with no rating in the format, it reports the starting state that format's first match would use if played now (a category rating derived from overall doubles where that applies), without storing it. An `as_of` earlier than the player's last match does not rebuild past state: RD and partner variety stay as of the stored state. The engine never reads the wall clock. `established` is `True` when `reliability` (as displayed, rounded to 1 place) is at least `Config.established_reliability_threshold`, 60 by default. Imported reliability never counts toward it, and it lapses with inactivity like `reliability`. Under the defaults, a new player with no imported rating, playing one match a week with varied partners and opponents, becomes established after about 10 to 13 tournament, league, or recreational verified matches (10 tournament, 11 league, or 13 recreational verified matches of one game, fewer for best of 3), and after about twice as many against the same players. Self-reported matches need about 20 to 25 with varied players and never get there against the same players, because repeat matches are discounted and then rejected. A player seeded from a recently captured imported rating starts at a lower RD (`Config.external_seed_rd`) and is established after about 3 to 4 tournament, league, or recreational verified matches. RD grows from the capture date, so an older import gives less of a head start, and a very old one none. The imported rating and its reliability value do not change the count. A rating that is not established is what external rating systems usually call provisional, which is different from OPEN's provisional live updates between replays and the `open_provisional` source. The threshold is a label only: it changes no rating and no `evaluate` output. Evaluation's eligibility rule (every participant has an imported baseline or at least `burn_in_matches` OPEN matches) is a separate rule from `established`.

Check `result.possible_duplicate_of` too. It names an earlier counted match with the same date, the same two teams, and the same game scores, under another `match_id`, so the same match may have been reported twice. By default that match is not rejected for this, so it counts unless another check rejects it. `Config(reject_content_duplicates=True)` rejects it instead, with `RejectionReason.POSSIBLE_DUPLICATE`. A real rematch on the same day with the same scores looks the same, so the default only flags it. After a replay, `engine.possible_duplicates` maps each flagged or rejected match to the match it repeats (see the Public API section for when an entry leaves it).

Processing order alone decides which report is the copy: arrival order live, and ledger order in a replay, as described in "Ledger order": within a date, untimed matches come first by `sequence` then `match_id`, then timed matches by the UTC instant of `played_at`, then `sequence` and `match_id`. Match type, rating format, and game format play no part. So with `reject_content_duplicates=True` a weaker or relabeled copy can count while the stronger report is rejected: for example an untimed self-report of a timed tournament match, or a mixed doubles copy of a doubles match. A category copy that counts first can keep the other row from updating its rating format, and live recording and a replay can keep different rows. Under the default, the flagged row can be the stronger one. Use `reject_content_duplicates=True` only when the backend trusts who files reports. Otherwise resolve flags in the backend. Under the default, a category copy of a doubles match can count twice in overall doubles, once directly and once through `result.parent`. `result.parent.possible_duplicate_of` is `None`, so read the top-level field. Do not show `possible_duplicate_of` to whoever submitted the match: it names another report and tells them those players played that result.

Authoritative ratings come from `replay(ledger, as_of=...)`. Calibrate the expectation scale on match history with `fit_scale(...)`. The expectation scale and the K factor together set how sharp predictions are, and in a closed pool they also set the rating spread. Run `fit_scale` with `mode="scheduled"` and a `score_from` date that skips immature ratings, then check `at_bound` and `profile` on the result: a fit that sits on a search edge or has a flat error profile is not identified. `RatingEngine.predict_win_probability` assumes points are independent, which tends to make match win probabilities overconfident. Calibrate them with `fit_win_calibration` and store the result in `Config.win_calibration_by_scoring`.

See [`examples/`](examples) for a fuller example.

## Public API

<!-- public-api:start -->
These are the public names, all importable from `open_engine`. The API may change in any 0.x release.

Names not listed here are internal, including everything reached only through `open_engine.engine` or `open_engine.winprob`. They can change or disappear in any release.

Rating:

- `RatingEngine`: holds players and matches, records matches, and predicts outcomes. Its `predict_point_share` and `predict_win_probability` methods give team A's expected point share and calibrated match win probability. `to_snapshot` saves its whole state as JSON-safe data, and `from_snapshot` loads it back (see "Snapshots and checkpoints"). Its `possible_duplicates` attribute maps the `match_id` of each match flagged or rejected as a possible duplicate to the `match_id` it repeats, live and after a replay. An entry stays until that `match_id` later counts with no twin, which only a match rejected under `reject_content_duplicates=True` can do.
- `replay`: rebuilds authoritative ratings from seeds and the match ledger as of a date. With `from_snapshot`, it resumes from a checkpoint instead of starting over. Add `whole_ledger=True` when passing the whole ledger, so resume also catches a row voided on or before the checkpoint date.
- `ledger_order`: the sort key that sets the order of play in a ledger.
- `reliability`: OPEN-earned reliability (0 to 100) for one rating state.
- `expected_point_share`: team A's expected share of points from two team ratings and a scale.

Inputs:

- `Config`: every rating parameter, with a fingerprint for provenance. Its mapping fields are read-only copies, and a config is hashable. Equal configs have the same fingerprint and hash. `Config.to_dict()` and `Config.from_dict()` store and load one with the same fingerprint.
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
- `InvalidSeedError`: an external or replay seed cannot initialize a rating. `RatingEngine.add_player`, `replay`, and `prequential_predictions` (and so `evaluate`, `fit_scale`, and `fit_win_calibration`) raise it for a seed that breaks the seed rules in `add_player`'s docstring, including a seed that is not a `Player`, a field of the wrong type, a blank or whitespace-only player id, `account_user_id`, external source, or external id, or a rating or initial rating outside the rating bounds. `replay` also raises it for a seed dated after `as_of`, and `prequential_predictions` for one dated after its last completed ledger match. `RatingEngine.seed_external_rating` raises it for a rating format of the wrong type, a snapshot whose captured date, source, or external id has the wrong type, or whose rating or reliability has the wrong type or is out of range, or an existing rating it may not replace.
- `PlayerError`: a player identity, account link, or external identity is not valid. `seed_external_rating` raises it for a blank player id or when the snapshot's external identity cannot be linked (it names a non-empty external id and its source or that id is whitespace only, it is already linked to another player, or the player already has a different id from that source). `link_account` and `attach_external_id` raise it for a blank or whitespace-only value and for an account or identity already linked elsewhere. `add_player` raises it for a seed whose account or external identity another player already holds, and `replay` and `prequential_predictions` raise it when two seeds share a player id, an `account_user_id`, or an external identity (the same source and external id). `replay(from_snapshot=...)` raises it for a seed whose player is already in the snapshot, or whose account or external identity a player in the snapshot holds.
- `UnknownPlayerError`: a `PlayerError` for a player id with no player record. `RatingEngine.player`, `RatingEngine.state`, and `RatingEngine.player_summary` raise it instead of creating the player. A player is created by `add_player`, `seed_provisional_rating`, `seed_external_rating`, `link_account`, `attach_external_id`, or a counted `record` (and so `replay`). An id named only in uncounted rows, or in rows after a replay's `as_of`, has no player record.
- `EvaluationError`: an evaluation, fit, or win probability cannot be computed. `fit_scale` and `fit_win_calibration` raise it, before any prediction work, when `low`, `high`, or `high - low` is not finite, `low` is not less than `high`, or `tolerance` is not finite and greater than 0.
- `ReferenceDateError`: no usable reference date to measure inactivity and reliability at. `RatingEngine.player_summary` raises it when it gets no `as_of` and the engine was never replayed, or an `as_of` that is not a plain date (a datetime is not one), before it checks the player id.
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

## Match performance

A match moves ratings by how the result compared with the expectation.

- Performance is team A's share of all points across the match's games, blended with the match result by `Config.win_weight`. The default, 0.0, uses point share only.
- So a team can win and still lose rating. That happens when it wins by less than expected, such as a 5.0 team beating a 4.0 team 11-9 in doubles. It can also happen when a team wins a best of 3 (or 5) with fewer total points.
- There is no floor on a winner's change, other than the rating range (`min_rating` to `max_rating`).
- For one engine state, raising a team's point share by changing only the match's game scores, with the same number of games and the same result, never gives that team a worse change. Longer games and more games can carry more weight (up to `Config.max_information_multiplier`), so across game formats and game counts a slightly wider winning margin can still cost more rating.
- `win_weight = 1.0` scores the result only, so a winner never loses rating. Values between 0 and 1 make a rating loss less likely. Only values close to 1 rule it out, and how close depends on the rating range and scale and on the lowest point share a winner can have.
- The default is a declared choice, not a validated one. It changes only when out-of-sample evaluation shows another value predicts better.

## Match status

`Match.status` says how a match ended. It is a `MatchStatus` and defaults to `COMPLETED`. A status that is not a `MatchStatus` member, such as the plain text `"forfeit"` or `None`, raises `InvalidMatchError`.

- `COMPLETED`: at least one game and a decided winner. Nothing changes for these matches.
- `FORFEIT`: the match was not played, so it must have no games.
- `RETIRED`: a player stopped, for example through injury, before a winner was decided.
- `INCOMPLETE`: the match stopped for another reason, such as time or weather.

A retired or incomplete match records zero or more finished games, each valid in its game format, and no decided winner. So in a best of 1 it has no games. A partial game in progress, such as `(7, 4)` to 11, is not recorded. `games` must be a tuple or list for every status.

A match that is not completed is never rated:

- `record` validates the match, then checks the `match_id` before the status: a `match_id` already processed or reserved gets `RejectionReason.DUPLICATE_MATCH_ID`. Otherwise it returns a result that is not counted, with `RejectionReason.MATCH_NOT_COMPLETED`. It moves no rating and does not mark the `match_id` processed, but it reserves the `match_id`. Every later `record` call for a match with that id that does not raise returns `DUPLICATE_MATCH_ID`, whether that match is completed or not and whatever its players. This matches `replay`, which never counts a second row with one `match_id`. To relabel a forfeit as completed live, use a new `match_id`. A live reservation sets the snapshot's `resumable` to `false`.
- `replay` accepts the row. It counts toward `matches_considered`, not `matches_counted`. A full replay gives each player the same ratings as a full replay of the ledger without those rows. In one ledger, a non-completed row and a completed row with the same `match_id` conflict (`LedgerConflictError`), so replace the row instead.
- As for any rejected row, a checkpoint tracks the row and its players. Resume raises `SnapshotError` for a new seed for a player named only in such a row.
- Replacing the row with a completed one under the same `match_id` is an edited row. For a row dated on or before a checkpoint, that needs a full replay (resume raises `LedgerConflictError` when given it).
- `prequential_predictions`, `evaluate`, `fit_scale`, and `fit_win_calibration` never predict it. The seed date limit and the default scheduled anchor come from completed rows only.

Rating a retirement on its finished games would change how ratings move. It is not done here. It would need a declared `Config` parameter backed by out-of-sample evaluation.

A status keeps a match from moving any rating, so a team could report a loss as a forfeit or an unfinished match. The engine cannot tell. The backend should verify a status as it verifies scores, and store which team forfeited or retired, which the engine does not record.

The backend should also assign `match_id`s itself. A party that can choose a `match_id` can submit a match that is not completed under it first. Then the real match with that id never counts live, and `replay` raises `LedgerConflictError` for the pair. `DUPLICATE_MATCH_ID` does not say whether the id was counted or reserved.

## Snapshots and checkpoints

`RatingEngine.to_snapshot()` returns the engine's whole state as plain JSON data. `json.dumps(snapshot, allow_nan=False)` works with no custom encoder. `RatingEngine.from_snapshot(snapshot, cfg)` builds the engine back, from the dict or from `json.loads` of its text. Every value comes back with its type: an int stays an int. The same state always gives the same JSON text.

A snapshot of a replay is a checkpoint. `replay(matches, seeds, cfg=cfg, as_of=later, from_snapshot=snapshot)` resumes from it and gives the same result as a full replay to `later`. You can pass the whole ledger or only the rows dated after the checkpoint. Pass only seeds that are not in the checkpoint.

Take the checkpoint snapshot right after the replay, before any other call on the engine. Every engine method that changes state makes the snapshot one resume refuses: a live match, a seed, an account or external id link, a new player, or a new rating state. Reads (`player`, `state`, `player_summary`, the predictions, and `to_snapshot`) change nothing, so they keep the snapshot resumable. Changing objects the engine returns or its public attributes (for example `players`, rating states, or `processed_match_ids`) directly is not tracked, and makes the snapshot unsafe to resume.

When you pass the whole ledger, also pass `whole_ledger=True`. Resume then raises `SnapshotError` for any row the checkpoint saw that is missing from the ledger, which catches a match voided or deleted on or before the checkpoint date. `whole_ledger=True` without `from_snapshot` raises `SnapshotError`.

Resume raises instead of giving a different result:

- `SnapshotError` for a malformed snapshot, a snapshot taken under another `Config`, a snapshot that is not from a replay, a snapshot with live updates or any other change since its replay, an `as_of` before the checkpoint date, a row dated on or before the checkpoint date that the checkpoint did not see, or a seed for a player named in a row the checkpoint saw (even when every such row was rejected, so the player is not in the snapshot).
- `LedgerConflictError` for a row dated on or before the checkpoint date that differs from the row the checkpoint saw under that `match_id`, or a later row that reuses one of those ids.
- `PlayerError` for a seed whose player is already in the snapshot.
- `SnapshotError`, with `whole_ledger=True`, for a row the checkpoint saw that is missing from the ledger. It names every missing id, sorted.

The row checks run in this order: changed or reused rows (`LedgerConflictError`), then rows the checkpoint did not see, then (with `whole_ledger=True`) checkpointed rows that are missing.

Resume gives the same result as a full replay of the ledger and seeds its rows come from. It detects every row it is given that disagrees with the checkpoint. With `whole_ledger=True` it also detects checkpointed rows removed from the ledger. It cannot detect a seed that was withdrawn or corrected after the checkpoint, with or without `whole_ledger`. It cannot detect a late (backdated) row on or before the checkpoint date that you leave out. Without `whole_ledger=True`, it also cannot detect a row on or before the checkpoint date that was removed (voided) from the ledger, or an edited one that you leave out. A void, an edit, or a late row on or before the checkpoint date, and a seed withdrawn or corrected after the checkpoint, each need a full replay. Resume raises for such an edited or late row when you pass it, and for a void when you pass `whole_ledger=True`, but the fix is still a full replay. Store a match before the replay that covers its date.

A stored snapshot sets every rating when it is loaded. Give it the same write protection as the ratings themselves. `from_snapshot` checks its shape and the engine's rules, not that it is authentic.

Schema, version 5. Dates are `YYYY-MM-DD` text. Every object has exactly these keys, and `from_snapshot` rejects a missing key, an unknown key, or a value of the wrong type or out of range. It also rejects a state the engine cannot produce: an initial rating outside the rating bounds, an unplayed state with a played state's RD or with partners, opponents, or live updates, `matches_played` and `last_played` that disagree, more live updates than matches, a player listed as their own partner or opponent, or an imported reliability with no import date. A nonzero `inherited_live_updates` is accepted only on a state derived from its parent, and only when the player has that parent state with at least as many live updates, the state has the parent state's `initial_source_date` and no imported reliability, external player id, or evidence reference, and, while the state is unplayed, its RD is at least `category_seed_rd` and its `rd_as_of` is set and no later than the parent state's. Every partner, opponent, and self-reported team member must be a player in the snapshot. Identities follow the seed and link rules: `account_user_id` is null or a non-blank string, every `external_ids` source and id is a non-blank string, and no two players hold the same account or the same source and external id pair. Each `content_keys` value must be a processed match id, and no two keys may name the same id. No reserved match id may be a processed one. In a resumable snapshot, no date may be after `calculated_as_of`, no state may have a nonzero `live_updates_since_replay` or `inherited_live_updates`, every processed match id must be a `ledger_digests` id, every player with a played state must be in `ledger_player_ids`, and the replay counts must match `ledger_digests` and `processed_match_ids`. Counts are at most `2**63 - 1`.

- `snapshot_version`: `5`. A version 1, 2, 3, or 4 snapshot does not load. Run a full replay to make a new one. Version 5 adds `reserved_match_ids`. Version 4 adds `inherited_live_updates` to each state. Version 3 added no key: it marked that each row digest in `ledger_digests` covers `Match.status`.
- `engine_version`: the package version that wrote it. Informational, not checked.
- `config_version`, `config_fingerprint`: of the `Config` it was taken under. `from_snapshot` checks both against the `Config` it is given.
- `calculated_as_of`: the date of the last replay, or `null`.
- `replay_info`: `null` when `calculated_as_of` is, otherwise `as_of`, `config_version`, `config_fingerprint`, `matches_considered`, and `matches_counted`.
- `players`: sorted by `id`. Each has `id`, `account_user_id` (text or `null`), `external_ids` (source to id), and `ratings` (rating format value to state). A state has every `RatingState` field: `rating`, `rd`, `matches_played`, `last_played`, `rd_as_of`, `initial_rating`, `initial_source`, `initial_source_date`, `initial_source_reliability`, `initial_external_player_id`, `initial_evidence_reference`, `live_updates_since_replay`, `partners`, `opponents` (these two map a player id to a date), and `inherited_live_updates`. `inherited_live_updates` is the parent's live count when a category state was derived from overall doubles, and 0 for any other state. Only a state derived from its parent can have a nonzero value, which needs `category_feeds_parent` and an `initial_source` of `open_parent:` plus the parent's value.
- `processed_match_ids`: sorted.
- `reserved_match_ids`: sorted. The `match_id` of each match `record` rejected as not completed. A later `record` call for a match with one of these ids gets `DUPLICATE_MATCH_ID`, unless it raises first (for example for an `as_of` earlier than the match date). No id repeats or is also in `processed_match_ids`. In a resumable snapshot every id is also a `ledger_digests` id, since a replay reserves only a row it considered, and no id is a `possible_duplicates` key. A live snapshot can hold an id that is both reserved and a `possible_duplicates` key: a copy rejected under `reject_content_duplicates=True`, then a match that is not completed under its id.
- `self_reported_log`: sorted by format, then teams. Each entry has `rating_format`, `teams` (two sorted lists of player ids), and `dates` in the order they were recorded.
- `content_keys`: sorted by key. Each key is the SHA-256 digest of a counted match's content (its date, its two teams, and its game scores), and each value is the `match_id` of the first match counted with that content. Later matches with the same content are checked against it.
- `possible_duplicates`: sorted by key. The `match_id` of each match flagged or rejected as a possible duplicate, to the `match_id` it repeats (its `possible_duplicate_of`). An entry stays until that `match_id` later counts with no twin. A key never names itself and is never a `content_keys` value, and each value must be a `content_keys` value. Under the default config every key is a processed match id. Under `reject_content_duplicates=True` no key is. In a resumable snapshot every key is also a `ledger_digests` id, under either config, since a replay records a copy only for a row it considered. A live snapshot can hold a rejected id that no replay saw.
- `ledger_digests`: `match_id` to the SHA-256 digest of every row a replay considered, kept across resumes. Resume uses them to check rows dated on or before the checkpoint.
- `ledger_player_ids`: sorted ids of every player named in those rows, counted or not. Resume refuses a seed for one of them.
- `resumable`: `true` only while the engine is exactly what a replay produced. Resume refuses a snapshot where it is `false`.

## Test data

Use only made-up or properly de-identified data. Do not put real player names, contact details, or match histories in this repository, including in test fixtures and screenshots. See [`tests/fixtures/README.md`](tests/fixtures/README.md).

## Contributing

See the [contributing guide](https://github.com/openpickleball/.github/blob/main/CONTRIBUTING.md). Every commit must be signed off. Please report security problems privately, as described in the [security policy](https://github.com/openpickleball/.github/blob/main/SECURITY.md).

## License

This repository is licensed under [AGPL-3.0-or-later](LICENSE). See [how OPEN is licensed](https://github.com/openpickleball/.github/blob/main/LICENSING.md).
