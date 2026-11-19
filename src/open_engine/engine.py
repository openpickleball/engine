"""
OPEN rating engine: provenance-aware, performance-based Elo for pickleball.

Status: demonstration only. These methods have not been validated on real
match data, the outputs are not OPEN ratings, and the API may change without
notice. Do not use this engine to rate real players.

This is a transparent, configurable rating engine. It is not a reproduction of
any proprietary rating algorithm. It supports ratings seeded from a verified
snapshot of a standard external rating system, independently maintained OPEN
ratings, unregistered player records, doubles categories that also feed the
overall doubles rating, match-source weighting, game format weighting,
inactivity-based uncertainty, rolling partner/opponent diversity, scale
calibration, and deterministic historical replay.

Published behavior notes:
- Each player moves by their own K (from their RD after inactivity):
  `sign * K * effective_weight * surprise`. A partner's or opponent's
  uncertainty never changes a player's step. Team average movement is not
  always zero-sum. Every MatchResult reports the net as `imbalance` (mean
  team A delta plus mean team B delta). When both teams have the same mean
  K and nothing is clamped or frozen, it is 0.0. It can be nonzero for
  three reasons:
    1. Unequal K: the team with the higher mean K moves more.
    2. `clamped`: a player hit the rating floor or ceiling, so part of their
       change was not applied.
    3. `frozen`: the match is on or before a player's imported baseline date.
       The imported rating already reflects that match, so that player's
       rating is used for the expectation but does not move. Their teammates
       move by their own K only, so the team moves less.
  It is also 0.0 when the result matched the expectation, or when clamps or
  freezes on both teams cancel. So an imbalance of 0.0 does not mean nothing
  was clamped or frozen: read `clamped` and `frozen` for that.
- Because team movement is not zero-sum, matches between high-K and low-K
  players can add rating to the pool or remove it. Colluding accounts can
  exploit this: a new account can gain rating by beating a low-K account
  again and again, or lose rating faster on purpose. `imbalance` is a
  per-match value in team-average units, so a match adds
  `team_size * imbalance` to the pool's total rating (twice the value in
  doubles). Each replay totals it per rating format in
  `ReplayInfo.net_rating_created`, from the unrounded changes, a category
  match adding to its own format and, when category_feeds_parent is on
  and its parent update counts, to doubles. The backend should store and
  monitor that total. Between replays it can add `team_size * imbalance`
  of each live MatchResult (and its parent); that running total is
  approximate, since imbalance is rounded to 5 places, and the next
  replay gives the exact total. The engine reports the total but does
  not limit it.
- Match performance is team A's share of all points across the match's
  games, blended with the match result by Config.win_weight:
  `(1 - win_weight) * point_share + win_weight * (1 if A won else 0)`.
  The default win_weight is 0.0, pure pooled point share. So a team can
  win and still lose rating when it wins by less than its expected share
  (a 5.0 team beating a 4.0 team 11-9 in doubles). It can also happen
  when it wins a best of 3 (or 5) with fewer total points. There is no
  floor on a winner's change, other than the rating range (min_rating to
  max_rating). For one engine state, raising a team's point share by
  changing only the match's game scores, with the same number of games
  and the same result, never gives that team a worse change. Longer
  games and more games can carry more weight (up to
  Config.max_information_multiplier), so across game formats and game
  counts a slightly wider winning margin can still cost more rating.
  win_weight = 1.0 scores the result only, so a winner never loses
  rating. Values between 0 and 1 make a rating loss less likely. Only
  values close to 1 rule it out, and how close depends on the rating
  range and scale and on the lowest point share a winner can have. The
  default is a declared choice, not a validated one. It changes only
  when out-of-sample evaluation shows another value predicts better.
- Authoritative ratings come from scheduled replays (see
  `Config.replay_interval_days`). Live updates between replays are
  provisional and are labeled with the count of live updates since the last
  recalculation, including the live updates a category rating copied when
  it was derived from overall doubles (`RatingState.inherited_live_updates`).
  A replay gives the same ratings as recording the same ledger live in
  ledger order: the replay date labels the result but never changes an
  update. Inactivity widens RD (`inflated_rd`) but never shrinks
  or discounts an old match's update.
- Live recording applies matches in arrival order. Only a replay gives play
  order. A live match is late when it is dated before a player's last
  match in its format, or before the date that player's rating in that
  format already reflects (for a category rating copied from overall
  doubles). It still counts, but it never moves last_played, rd_as_of, or
  a partner or opponent date back, and its MatchResult has `late` set to
  True. The backend should check `late` and, for a category match,
  `parent.late`, and schedule a replay when either is True. The engine
  stores dates, not times, so two matches on the same date that arrive
  out of played_at order are not flagged. Because dates never move back,
  a future-dated live match holds every date of its players at that
  future date until the next replay (see the production notes).
- PlayerSummary.reliability is always OPEN-earned. An imported external
  reliability is shown separately, in PlayerSummary.imported_reliability,
  only until OPEN-earned reliability exceeds it or the carry limits in
  Config are reached; otherwise that field is None. It is not carried
  before its capture date, measured at the same reference date as
  reliability (see the next note), so an unplayed import captured after
  the last replay is not shown by default; replay rejects such seeds. An
  external reliability never sets OPEN RD: an imported seed's RD is always
  Config.external_seed_rd.
- player_summary measures inactivity, RD, and reliability at a reference
  date. With no as_of, it is the later of the last replay date
  (calculated_as_of) and the player's last match. So a stale player is
  measured at the replay date and loses reliability from RD and variety
  as replays move forward (with the connectivity cap set, connectivity
  can still change; see its note), and a player with live matches since is measured at their last
  match. Players in one engine can be measured at different dates. With no
  as_of on an engine that was never replayed, it raises
  ReferenceDateError, as it does for an as_of that is not a plain date.
  An as_of earlier than the player's last match does not rebuild past
  state: RD and partner variety stay as of the stored state. `reliability`
  takes as_of as a required argument. The engine never reads the wall
  clock.
- PlayerSummary.established is True when OPEN-earned reliability, as
  displayed (rounded to 1 place), is at least
  Config.established_reliability_threshold (60 by default) and the state
  has at least Config.established_min_verified_matches (3 by default)
  counted tournament, league, or recreational verified matches
  (RatingState.verified_matches_played). Self-reported matches never
  count toward that minimum, and neither does a match in which the
  player was frozen. So while the minimum is above 0, a player with
  fewer counted verified matches in the format than the minimum is not
  established at any reliability. A category state derived from overall
  doubles starts its count at 0, as it starts matches_played at 0. With
  a minimum of 0 the rule is reliability alone. Imported
  reliability never counts toward established, and established is
  measured at the same reference date as reliability, so it lapses with
  inactivity unless the connectivity cap is set and connectivity changes
  (see its note). Under
  the defaults, a new
  player with no imported rating, playing one match a week with varied
  partners and opponents, becomes established after about 10 to 13
  tournament, league, or recreational verified matches (10 tournament, 11
  league, or 13 recreational verified matches of one game, fewer for best
  of 3), and after about twice as many against the same players.
  Self-reported matches raise reliability past the threshold after about
  20 to 25 with varied players (never against the same players, because
  repeat matches are discounted and then rejected), but such a player is
  established only once they also have the minimum of counted verified
  matches. A player seeded from a recently captured
  imported rating starts at a lower RD (Config.external_seed_rd) and is
  established after about 3 to 4 tournament, league, or recreational
  verified matches. RD grows from the capture date, so an older import
  gives less of a head start, and a very old one none. The imported rating
  and its reliability value do not change the count. A rating that is not
  established is what external rating systems usually call provisional.
  That is different from OPEN's provisional live updates between replays
  and from the open_provisional initial source. The threshold is a label
  only, and so is the minimum: neither changes any rating, prediction,
  evaluate, prequential_predictions, fit_scale, or fit_win_calibration
  output. Evaluation has its own eligibility rule: it scores a match only when
  every participant has an imported baseline or at least burn_in_matches
  OPEN matches. That rule is separate from PlayerSummary.established.
  With Config.connectivity_reliability_cap set, the counts above are for
  a player whose network connectivity is connected (see the next note).
- Network connectivity (PlayerSummary.connectivity, a NetworkConnectivity):
  RD and variety cannot see whether a player's partners and opponents
  ever play anyone outside their own group, so a closed pool that plays
  only itself could reach full variety, low RD, and established on its
  own private scale. player_summary measures it per rating format, at
  the reference date, over Config.diversity_window_days, from the
  partners and opponents stored in each rating state (no other data, so
  a snapshot or a resume carries it and it needs no snapshot field). Two
  players with a state in the format are in contact when either one's
  state lists the other with a date in the window. A group is a set of
  players linked by contact, and it is a main network when it has at
  least half as many players as the largest group, so two separate
  networks of similar size are each a main network. The label depends
  on group sizes alone. Two players in contact are
  tied when they share at least Config.connectivity_min_shared_contacts
  (4) contacts and each of them is in contact with those shared players
  on at least Config.connectivity_min_contact_days (3) distinct dates.
  A pool is a set of players linked by ties; a player with no tie is a
  pool of one. The pool's fringe is every player outside it in contact
  with it whose own contacts are all in the pool or in contact with it,
  such as a drop-in who met only the pool. Its outside players are the
  other players in contact with it. A player is connected when they
  have a contact in the window, their group is a main network, and
  either the pool with its fringe holds more than half the group, or
  the pool has at least Config.connectivity_min_outside_players (8)
  outside players on at least connectivity_min_contact_days distinct
  contact dates with them. connectivity is always reported. When
  Config.connectivity_reliability_cap is set (it is None, off, by
  default), a player who is not connected has PlayerSummary.reliability
  at most the cap, shown rounded down to 1 place, so with a cap below
  the established threshold they are not established. The cap is off by
  default because the rule can be gamed through group sizes (the limits
  below) and can cap players in a real but smaller network; it waits
  for out-of-sample review of real ledgers. The cap changes the displayed
  reliability only: `reliability()` gives the value from RD and
  variety, and no rating, RD, K, prediction, evaluate,
  prequential_predictions, fit_scale, or fit_win_calibration output
  reads either. connectivity is built for every summary, with the cap
  on or off. Limits: a pool in a main network that with its fringe
  holds more than half its group is that group's own scale, which no
  ledger can place on another, so a club in a main network that
  outnumbers the players it met is not capped and can leave them
  capped; pools that play each other but not
  the rest of their group count each other's players as outside
  players; accounts the backend has not verified can pose as outside
  players if they also play players the pool never met, and a cluster
  of accounts more than twice the size of a real network makes that
  network no longer a main network; drop-in accounts, or one match
  between two closed groups, can make a closed group large enough to be
  a main network or to hold more than half its group; a player whose
  only contacts are a closed group can count its members as outside
  players when they have not tied with that player (for example when
  the group plays so densely that every contact keeps one last date); a
  single match between two real networks can make the smaller one no
  longer a main network; a state keeps only the last date
  per contact, so repeated contact with the same player counts one
  date; contacts leave the window as the reference date moves on, so
  connectivity can change with no new match, and a player can become
  connected (with the cap set, reliability then rises up to the value
  from RD and variety) when the groups that outnumbered theirs drop out of the
  window; and, as for variety, an as_of earlier than the stored state
  does not rebuild past contacts. A summary for a format the player has
  no state in is measured as if the unplayed state a seed would store
  were stored: a player with no contacts, so not connected. The result
  for one format and reference date is rebuilt after each state change
  (the engine keeps the 16 most recent),
  which costs about the sum over players of their contacts times their
  contacts' contacts.
- Ledger order: `replay` and `prequential_predictions` sort matches by
  `played_on` first. Within one `played_on` date, untimed matches come
  first (by `sequence`, then `match_id`), then timed matches (by the UTC
  instant of `played_at`, then `sequence`, then `match_id`). Every row
  passed in is validated, including rows after `as_of`, so a naive
  `played_at` raises InvalidMatchError wherever it sits. Rows that repeat a
  `match_id` collapse to one only when every field matches in type and
  value (recursively, so each member of a tuple, list, set, or frozenset
  must match in type too), with `played_at` compared by its UTC instant
  and UTC offset; any
  other repeat raises `LedgerConflictError`, even after `as_of`. The
  result never depends on input order.
- Errors: every error the engine raises on purpose is an `OpenEngineError`,
  which is a `ValueError`. `ConfigError` covers Config values and a
  GameFormat reached other than through a match (`GameFormat.validate`,
  the win probability functions, and
  `RatingEngine.predict_win_probability`), `InvalidMatchError` covers
  matches, teams, scores, `played_at`, and a match's GameFormat,
  `InvalidSeedError` covers external and replay seeds (a
  seed state that breaks the seed rules in `RatingEngine.add_player`,
  including a field of the wrong type (a str or dict subclass, or a
  Player or RatingState subclass, counts as the wrong type), a blank id,
  a blank `initial_source`, `initial_external_player_id`, or
  `initial_evidence_reference`, an `external_ids` source or an
  `initial_source` that starts with the reserved prefix `open_` (such
  an `initial_source` is allowed only as `open_provisional` or a
  category's `open_parent:` name under the rules listed in
  `RatingEngine.add_player`), an external player id
  the seed's own `external_ids` does not link under its `initial_source`,
  or a rating or initial rating outside the bounds, raised by
  `add_player`, `replay`, and `prequential_predictions`, and so by
  `evaluate`, `fit_scale`, and `fit_win_calibration`; `replay` also
  rejects a seed dated after `as_of`, and `prequential_predictions` one
  dated after its last completed ledger match, and
  `RatingEngine.seed_external_rating` rejects a snapshot that is not
  exactly an `ExternalRatingSnapshot`, a source, external id, or
  `evidence_reference` that is not exactly a str (a str subclass
  included, and None allowed for the external id and evidence
  reference), a blank or whitespace-only source, external id, or
  evidence reference, and a source that starts with `open_`; it and
  `add_player` also reject a seed that could change an update `record`
  rejected for a reason that reads a seed, see `RejectionReason`),
  `PlayerError` covers player identities, including a seed whose
  account or external identity another player holds and an
  `attach_external_id` source that starts with `open_` (its subclass
  `UnknownPlayerError` covers a read of a player id with no player
  record), `EvaluationError` covers
  evaluation, fits and win probabilities, `LedgerConflictError` covers
  conflicting ledger rows, `SnapshotError` covers snapshots that cannot
  be loaded or resumed from, and `ReferenceDateError` covers a
  `player_summary` call with no date to measure at, and a
  `player_summary` or `replay` call with an as_of that is not a plain
  date.
- Snapshots and checkpoints: `RatingEngine.to_snapshot()` gives the whole
  engine state as JSON-safe data (schema version 8) with sorted keys and
  lists, and `RatingEngine.from_snapshot` loads it back after validating
  every key, type, and range and checking the config version and
  fingerprint. A replay's snapshot is a checkpoint:
  `replay(..., from_snapshot=snapshot)` records only rows dated after the
  checkpoint date and equals a full replay of the same ledger, given the
  whole ledger or only the newer rows. It checks every row first and
  raises instead of differing: SnapshotError for a snapshot not from a
  replay, with live updates, or changed in any other way after its replay
  (the snapshot's `resumable` mark, which replay sets and every engine
  method that changes state clears; changing objects the engine returns,
  such as players, rating states, or processed_match_ids, directly is not
  tracked and makes the snapshot unsafe to resume), an as_of before the
  checkpoint, a row on or before the checkpoint date that the checkpoint
  did not consider (compared by a digest that mirrors the ledger's
  duplicate rule), or a seed for a player the checkpoint's rows named;
  LedgerConflictError for such a row that changed or a later row that
  reuses its id. With `whole_ledger=True` the caller passes the whole
  ledger, and resume also raises SnapshotError for a checkpointed row
  missing from it (a void or deletion). Without it, resume cannot see a
  voided row on or before the checkpoint, and given only the newer rows
  it cannot see a backdated row either. No flag lets it see a seed
  withdrawn or corrected after the checkpoint. Those need a full replay.
- Likely duplicates: the same match can be reported twice under different
  match ids. Two matches have the same content when they have the same
  `played_on`, the same two teams as sets of players (either side may be
  team A), and the same game scores in order, read from the same side.
  The match_id, match type, rating format, game format, best_of,
  played_at, sequence, and confirmed_by are left out, since two reports
  of one match often disagree on labels. The later match in processing
  order is the possible duplicate: arrival order live, ledger order in a
  replay, so a replay gives the same answer whatever order rows arrive
  in. Under the default `Config.reject_content_duplicates=False` it is
  not rejected for this: it goes through the other checks and, if it
  counts, its MatchResult has `possible_duplicate_of` set to the match_id
  of the first counted match with that content. With True it is not
  counted, with `RejectionReason.POSSIBLE_DUPLICATE` and
  `possible_duplicate_of` set. Only counted matches are compared against,
  so a third copy points to the first, and a match that was rejected for
  any reason is not a target. A real rematch on the same date between
  the same teams with the same scores looks the same: the default flags
  it, and True does not count it, so keep the default where that matters
  and let the backend confirm. Reports that disagree on the date are not
  caught. RatingEngine.possible_duplicates maps each match flagged or
  rejected as a copy to the match_id it repeats, live and after a replay.
  An entry never clears: a counted copy's match_id is processed, and a
  rejected copy's match_id is reserved (see the next note).
  Which report wins is set by processing order alone. With True, the
  report that survives is the first one to count in processing order
  (arrival order live; in a replay, ledger order as in the ledger order
  note: untimed rows on a date first, by sequence then match_id, then
  timed rows by the UTC instant of played_at, then sequence and
  match_id),
  whatever its match type, rating format, or game format. So a weaker or
  relabeled copy (for example an untimed self-report of a timed
  tournament match, or a mixed doubles copy of a doubles row) can count
  while the stronger report is rejected, a category copy that counts
  first can keep the other row from updating its rating format, and live
  recording and a replay can keep different rows. Under the default the
  same order decides which report is flagged, so the flagged row can be
  the stronger one. Use True only when the backend trusts who files
  reports; otherwise resolve flags in the backend. Under the default, a
  category copy of a doubles match can count twice in overall doubles: once
  directly and once through its `parent` update. `parent` has
  `possible_duplicate_of` None, so read the top-level field.
- `record` validates every match first. A valid match whose `match_id` was
  already processed or reserved returns a result that is not counted,
  with `RejectionReason.DUPLICATE_MATCH_ID`, instead of raising, unless
  its `as_of` is invalid (not a plain date, or earlier than `played_on`),
  which raises `InvalidMatchError` first. Every other match that
  `record` does not count, whatever its RejectionReason, reserves its
  match_id: every later `record` call for a match with that id that does
  not raise returns DUPLICATE_MATCH_ID, with the same content or not.
  That matches replay, where a match_id names one row: a second,
  different row with it is a LedgerConflictError, and an identical one
  collapses into the first. So a corrected resubmission needs a new
  match_id, live and in the ledger. Resubmit only after a rejection that
  comes from the row itself (GAME_FORMAT_NOT_ACCEPTED,
  SELF_REPORTED_UNCONFIRMED, MATCH_NOT_COMPLETED): for example a
  self-reported match resubmitted after confirmation, or a match
  corrected to an accepted game format. Under the same Config, replay
  never counts a row rejected for one of those, in any order. The other
  rejections depend on engine state, seeds, or other rows, so a row
  rejected live can count in a replay, and a copy of it under a new
  match_id could then count too. Do not resubmit those. For
  POSSIBLE_DUPLICATE, the ledger already holds a row with the same
  content. A live reservation
  clears the snapshot's resumable mark. A match with
  the same content as an earlier counted match under another match_id is
  flagged or, by config, rejected (see the note on likely duplicates).
- Match status: `Match.status` is a MatchStatus, and `games` is a tuple
  or list for every status. COMPLETED (the default) needs at least one
  game and a decided winner. A FORFEIT must have no games. RETIRED and
  INCOMPLETE take zero or more finished games, each valid in the game
  format, and no decided winner, so in a best of 1 they have no games; a
  partial game is not recorded. A match that is not completed is never
  rated: `record` returns `RejectionReason.MATCH_NOT_COMPLETED` after the
  match_id check (a processed or reserved match_id gets
  DUPLICATE_MATCH_ID). It changes no rating, count, or content key, and
  is not marked processed, but like any rejected match it reserves its
  match_id (see the note above), so a forfeit relabeled as completed
  needs a new match_id live.
  `replay` accepts such a row: it counts toward
  `ReplayInfo.matches_considered`, not `matches_counted`. A full replay
  gives each player the same rating states as a full replay of the
  ledger without those rows. Like any rejected row, a checkpoint tracks
  the row's digest and players, so resume raises SnapshotError for a new
  seed for a player named only in such a row. Replacing the row with a
  completed one under the same match_id is an edited row: on or before a
  checkpoint date it needs a full replay (resume raises
  LedgerConflictError when given it). In one ledger, a non-completed row
  and a completed row with the same match_id conflict
  (LedgerConflictError). `prequential_predictions` (and so `evaluate`,
  `fit_scale`, and `fit_win_calibration`) never predicts a match that is
  not completed, and takes its seed date limit and default anchor from
  completed rows only.
  Rating a retirement on its finished games would change how ratings
  move, so it is not done; it would need a declared Config parameter
  backed by out-of-sample evaluation.

Production notes:
- Persist the match ledger and player identities in a database. This module's
  in-memory dictionaries are an engine, not a persistence layer.
- Do not show `possible_duplicate_of` (or possible_duplicates) to whoever
  submitted the match. It reveals another report's match_id and that
  those players played that result on that date.
- Verify a match's status as you verify its scores. A status other than
  completed keeps a match from moving any rating, so a team could report
  a loss as a forfeit, retirement, or unfinished match to avoid it. The
  engine cannot tell; the backend must. The engine does not record which
  team forfeited or retired, so the backend stores that.
- Set match_type from organizer, league, or club provenance, and never
  let whoever submits a match choose a verified type (tournament, league,
  or rec_verified). match_type sets a match's weight and decides whether
  it counts toward PlayerSummary.established, so a self-reported match
  relabeled as verified would let players report their way to
  established. The engine cannot tell; the backend must.
- Assign match_ids in the backend. A party that can choose a match_id
  can submit a match that gets rejected under it first (for example one
  that is not completed or not confirmed), and then the real match with
  that id never counts live (replay raises LedgerConflictError for the
  pair). DUPLICATE_MATCH_ID does not say whether the id was counted or
  reserved.
- Give a corrected resubmission a new match_id. Once `record` has
  considered a match_id, counted or not, a later match with it is never
  counted live, and a different ledger row with it conflicts in replay.
  Keep the rejected row in the ledger as it was. Resubmit only after a
  rejection that comes from the row itself (GAME_FORMAT_NOT_ACCEPTED,
  SELF_REPORTED_UNCONFIRMED, MATCH_NOT_COMPLETED). Do not resubmit after
  any other rejection: a replay can count the original row, and a copy
  under a new match_id could then count too. A replay under a different
  Config (a new allowed_game_formats or self_reported_requires_both_teams)
  can count rows rejected for game format or confirmation, so after such
  a change, review resubmitted rows before the replay.
- Require verified identity and authorized evidence before importing an
  external rating. An external refresh can never overwrite an OPEN-earned
  rating once OPEN matches have been processed.
- Store each replay's `ReplayInfo` (as_of date, config version, config
  fingerprint, counts, net_rating_created) for auditability. Watch
  net_rating_created for a pool that grows or shrinks: colluding accounts
  with different K can create rating, and the engine does not cap it.
- Reject a match whose `played_on` is after the date it was submitted.
  The engine never reads the clock, so it cannot tell. A future-dated
  live match, by typo or on purpose, holds last_played, rd_as_of, and
  partner and opponent dates at that future date for every player in it
  until the next replay: RD stops growing, reliability is held up, and
  player_summary's default reference date jumps ahead. A later honest
  match with those players that counts in the same rating format comes
  back with `late` set to True. For a category match, `parent.late` covers the
  overall doubles rating.
- To avoid replaying the whole ledger every time, persist the snapshot of
  each scheduled replay and resume the next one from it. Take the
  snapshot right after the replay, before any other call on the engine.
  Store a match before the replay that covers its date. Pass the whole
  ledger with `whole_ledger=True` so resume raises for a voided or
  deleted row on or before the checkpoint. Given only the newer rows,
  resume cannot see a removed row or a backdated one, so a void, an
  edit, or a backdated row then needs a full replay. A seed withdrawn or
  corrected after the checkpoint needs a full replay too, with or
  without whole_ledger, since the checkpoint already holds the old seed.
  A snapshot does not
  replace the ledger: keep the ledger as the source of truth and run a
  full replay when in doubt.
- A stored snapshot sets every rating when it is loaded, so give it the
  same write protection as the ratings themselves. from_snapshot checks
  its shape and the engine's invariants, not that it is authentic.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import UTC, date, datetime
from enum import Enum, StrEnum
from typing import Literal, TypeGuard, TypeVar, cast

# Explicit re-exports, so `from open_engine.engine import X` passes mypy --strict.
from ._errors import ConfigError as ConfigError
from ._errors import EvaluationError as EvaluationError
from ._errors import InvalidMatchError as InvalidMatchError
from ._errors import InvalidSeedError as InvalidSeedError
from ._errors import LedgerConflictError as LedgerConflictError
from ._errors import OpenEngineError as OpenEngineError
from ._errors import PlayerError as PlayerError
from ._errors import ReferenceDateError as ReferenceDateError
from ._errors import SnapshotError as SnapshotError
from ._errors import UnknownPlayerError as UnknownPlayerError
from ._version import __version__
from .winprob import calibrate_win_probability, match_win_probability

# ---------------------------------------------------------------------------
# Enums and game formats
# ---------------------------------------------------------------------------


class _StrEnum(StrEnum):
    """
    Base of the public string enums. str() and format() give the value.

    StrEnum already does this. Defining the two methods here as well keeps them
    on this one class: without them, the enum machinery copies them into every
    subclass.
    """

    def __str__(self) -> str:
        return str.__str__(self)

    def __format__(self, format_spec: str) -> str:
        return str.__format__(self, format_spec)


class RatingFormat(_StrEnum):
    """A rating pool. Each player has a separate rating in each format."""

    SINGLES = "singles"
    DOUBLES = "doubles"
    MIXED_DOUBLES = "mixed_doubles"
    DOUBLES_MEN = "doubles_men"
    DOUBLES_WOMEN = "doubles_women"

    @property
    def team_size(self) -> int:
        """Players per team: 1 for singles, 2 for every doubles format."""
        return 1 if self is RatingFormat.SINGLES else 2

    @property
    def parent(self) -> RatingFormat | None:
        """Category formats also feed the overall doubles rating."""
        if self in (
            RatingFormat.MIXED_DOUBLES,
            RatingFormat.DOUBLES_MEN,
            RatingFormat.DOUBLES_WOMEN,
        ):
            return RatingFormat.DOUBLES
        return None


class MatchType(_StrEnum):
    """How a match was organized and verified. Sets its evidence weight."""

    TOURNAMENT = "tournament"
    LEAGUE = "league"
    REC_VERIFIED = "rec_verified"
    SELF_REPORTED = "self_reported"


# Match types an organizer, club, or verified record stands behind.
# RatingState.verified_matches_played counts these, and
# PlayerSummary.established needs Config.established_min_verified_matches of them.
_VERIFIED_MATCH_TYPES = frozenset({MatchType.TOURNAMENT, MatchType.LEAGUE, MatchType.REC_VERIFIED})


class ScoringType(_StrEnum):
    """Side-out scoring (only the serving side scores) or rally scoring."""

    SIDE_OUT = "side_out"
    RALLY = "rally"


class MatchStatus(_StrEnum):
    """
    How a match ended. Only a completed match can count.

    COMPLETED: played to a decided winner. FORFEIT: not played, so it has no
    games. RETIRED: a player stopped (injury or similar) before a winner was
    decided. INCOMPLETE: stopped for another reason, such as time or
    weather. A retired or incomplete match records only the games that were
    finished, and no decided winner. record never counts a match that is not
    completed (see the module docstring).
    """

    COMPLETED = "completed"
    FORFEIT = "forfeit"
    RETIRED = "retired"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class GameFormat:
    """How each game in a match is scored. Recorded on every match."""

    scoring: ScoringType = ScoringType.SIDE_OUT
    points_to: int = 11
    win_by: int = 2
    cap: int | None = None  # optional hard cap where win-by-1 ends the game

    def validate(self) -> None:
        """
        Raise ConfigError when the format itself is not valid.

        scoring must be a ScoringType member (a str equal to a member's value
        is rejected). points_to and win_by must be ints, and cap an int or
        None. A bool or a float is not accepted as an int; an int subclass
        is. The types are checked before the ranges: points_to and win_by
        positive, cap greater than points_to. Match.validate calls this
        method and raises a ConfigError from it as InvalidMatchError with
        the same message.
        """
        if not isinstance(self.scoring, ScoringType):
            raise ConfigError(f"scoring must be a ScoringType, not {type(self.scoring).__name__}")
        for name, number in (("points_to", self.points_to), ("win_by", self.win_by)):
            if not isinstance(number, int) or isinstance(number, bool):
                raise ConfigError(f"{name} must be an int, not {type(number).__name__}")
        if self.cap is not None and (not isinstance(self.cap, int) or isinstance(self.cap, bool)):
            raise ConfigError(f"cap must be an int or None, not {type(self.cap).__name__}")
        if self.points_to < 1:
            raise ConfigError("points_to must be positive")
        if self.win_by < 1:
            raise ConfigError("win_by must be positive")
        if self.cap is not None and self.cap <= self.points_to:
            raise ConfigError("cap must be greater than points_to")

    def validate_game(self, score_a: int, score_b: int) -> None:
        """Raise InvalidMatchError when a game score cannot end a game in this format."""
        high, low = max(score_a, score_b), min(score_a, score_b)
        if high < self.points_to:
            raise InvalidMatchError("A game must reach its target score")
        if self.cap is not None and high > self.cap:
            raise InvalidMatchError("Game score exceeds the cap")
        if high == self.points_to:
            if high - low < self.win_by:
                raise InvalidMatchError("Game was not won by the required margin")
            return
        # Extended game (deuce): it ends the moment the margin is reached,
        # or at the cap.
        if self.cap is not None and high == self.cap:
            if not high - self.win_by <= low <= high - 1:
                raise InvalidMatchError("Capped game score is not reachable")
            return
        if high - low != self.win_by:
            raise InvalidMatchError("Extended game must end at exactly the win-by margin")


ALL_GAME_FORMATS: frozenset[tuple[ScoringType, int]] = frozenset(
    (scoring, points) for scoring in ScoringType for points in (11, 15, 21)
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_K = TypeVar("_K")
_V = TypeVar("_V")


class _FrozenMapping(Mapping[_K, _V]):
    """A read-only copy of a mapping. Hashable, picklable, and deep-copyable."""

    __slots__ = ("_data",)

    _data: dict[_K, _V]

    def __init__(self, data: Mapping[_K, _V]) -> None:
        self._data = dict(data)

    def __getitem__(self, key: _K) -> _V:
        return self._data[key]

    def __iter__(self) -> Iterator[_K]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __hash__(self) -> int:
        return hash(frozenset(self._data.items()))

    def __repr__(self) -> str:
        return repr(self._data)

    def __reduce__(self) -> tuple[type[_FrozenMapping[_K, _V]], tuple[dict[_K, _V]]]:
        return (type(self), (self._data,))


_SCORING_VALUES = frozenset(ScoringType)

# Config fields that hold a mapping. __post_init__ stores each as a read-only copy.
_CONFIG_MAPPING_FIELDS = (
    "scale_by_scoring",
    "win_calibration_by_scoring",
    "match_weights",
    "game_length_weights",
)

# The largest rating bound, RD bound, or daily RD growth a Config accepts.
# Squaring a value this size is finite, and so are team sums, rating gaps,
# and imbalance across the whole rating range.
_RATING_LIMIT = 2.0**511
_RATING_LIMIT_TEXT = "2**511"

# Int-only bounds. Exact int arithmetic raises OverflowError where float
# arithmetic goes to inf, so these two fields have a lower bound as ints.
_INT_GROWTH_LIMIT = 2**500
_INT_GROWTH_LIMIT_TEXT = "2**500"
_INT_BONUS_LIMIT = 2**511
_INT_BONUS_LIMIT_TEXT = "2**511"
# The most games the multiplier check allows: len() returns at most sys.maxsize.
_MAX_GAME_COUNT = sys.maxsize

# The longest day window a Config accepts: the days from date.min to
# date.max, which timedelta holds. A window that reaches back before date.min
# from a match or reference date stops at date.min (_days_before, issue #92).
_MAX_DAYS = (date.max - date.min).days
_MAX_ORDINAL = date.max.toordinal()


def _days_before(day: date, days: int) -> date:
    """
    The date `days` days before `day`, clamped to date.min and date.max.

    Window cutoffs use it, so a match or reference date near date.min, or a
    very long window, never raises OverflowError. Every cutoff is compared
    inclusively (a date on or after it is in the window), so a cutoff at
    date.min keeps every date, as one before it would: no result changes.
    A negative `days` moves forward and stops at date.max; the one caller
    that moves forward (a scheduled window's grid date) is always before a
    match date, so it never reaches that bound.
    """
    return date.fromordinal(min(max(1, day.toordinal() - days), _MAX_ORDINAL))


# Config scalar fields by kind, checked for type and finiteness first.
_CONFIG_NUMBER_FIELDS = (
    "min_rating",
    "max_rating",
    "default_rating",
    "k_min",
    "k_max",
    "rd_min",
    "rd_max",
    "new_player_rd",
    "external_seed_rd",
    "category_seed_rd",
    "rd_shrink_per_match",
    "rd_growth_per_day",
    "win_weight",
    "game_count_bonus",
    "max_information_multiplier",
    "self_reported_repeat_factor",
    "established_reliability_threshold",
)
_CONFIG_WHOLE_FIELDS = (
    "replay_interval_days",
    "diversity_window_days",
    "diversity_target",
    "self_reported_repeat_window_days",
    "self_reported_max_repeats",
    "import_reliability_carry_matches",
    "import_reliability_max_age_days",
    "established_min_verified_matches",
    "connectivity_min_shared_contacts",
    "connectivity_min_contact_days",
    "connectivity_min_outside_players",
)
_CONFIG_OPTIONAL_NUMBER_FIELDS = (
    "connectivity_reliability_cap",
    "max_singles_rating_gap",
    "max_doubles_partner_gap",
)
_CONFIG_BOOL_FIELDS = (
    "self_reported_requires_both_teams",
    "reject_content_duplicates",
    "category_feeds_parent",
)
_CONFIG_DAY_FIELDS = (
    "replay_interval_days",
    "diversity_window_days",
    "self_reported_repeat_window_days",
    "import_reliability_max_age_days",
)
# Type failures that keep the message an earlier release already raised.
_CONFIG_TYPE_MESSAGES = {
    "established_reliability_threshold": (
        "established_reliability_threshold must be between 0 and 100"
    ),
    "established_min_verified_matches": (
        "established_min_verified_matches must be a nonnegative int"
    ),
    "connectivity_reliability_cap": (
        "connectivity_reliability_cap must be None or a number from 0 to 100"
    ),
    "connectivity_min_shared_contacts": "connectivity_min_shared_contacts must be a positive int",
    "connectivity_min_contact_days": "connectivity_min_contact_days must be a positive int",
    "connectivity_min_outside_players": "connectivity_min_outside_players must be a positive int",
}


def _is_config_number(value: object) -> bool:
    """
    True for an int (not bool) or a float that is finite as a float.

    Unlike _is_finite_number, an int too large to convert to a float is not
    a number here. An int is judged by its own value, not by what a
    subclass's __float__ says.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(_as_float(value))
    except OverflowError:
        return False


def _as_float(value: float) -> float:
    """value as a plain float, from its own value, never a subclass's __float__."""
    if isinstance(value, int):
        return int.__float__(value)
    return float.__float__(value)


_N = TypeVar("_N")


class _SpoofedNumber:
    """Stands in for a value whose __class__ claims int or float but is not."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<not a number>"


_SPOOFED_NUMBER = _SpoofedNumber()


def _plain_number(value: _N) -> _N:
    """
    An int or float as exactly int or float, from its own value (issue #107).

    It tests the value's real type, not isinstance, which a __class__
    property can fool. A bool is returned as is, a real int subclass gives
    int.__int__(value), and a real float subclass float.__float__(value),
    so a subclass's operators never reach the caller's checks. A value that
    only claims to be an int or float is replaced with a marker that is
    neither, so the caller's type check rejects it. Anything else is
    returned as is, for the caller's type check to reject.
    """
    real = type(value)
    if issubclass(real, bool):
        return value
    if issubclass(real, int):
        return cast(_N, int.__int__(cast(int, value)))
    if issubclass(real, float):
        return cast(_N, float.__float__(cast(float, value)))
    if isinstance(value, (int, float)):
        return cast(_N, _SPOOFED_NUMBER)
    return value


def _is_config_whole(value: object) -> bool:
    """True for an int (not bool) that is also a config number."""
    return _is_whole_number(value) and _is_config_number(value)


@dataclass(frozen=True, eq=False)
class Config:
    """
    Every rating parameter. Frozen, so a config cannot change during a replay.

    The defaults are uncalibrated placeholders. fingerprint() identifies the
    exact parameter set behind published ratings.

    Mapping fields are stored as read-only copies whose values must be
    finite numbers, and allowed_game_formats as a frozenset of (scoring
    type, whole number) pairs. Keys and pairs are stored as their own
    types: a ScoringType or MatchType key given as its str value becomes
    the enum member, and an int subclass becomes the plain int of its own
    value (not what its __int__ returns). Changing
    the dict or set a config was built from does not change the config,
    and changing the config's own raises
    TypeError. Two configs are equal exactly when their fingerprint payloads
    are equal, so equal configs share a fingerprint and a hash, and a Config
    can be a dict key or a set member. to_dict() and from_dict() store and
    load a config.

    Every field is checked when the config is built, and any failure
    raises ConfigError. A number is an int or a float, never a bool, that
    is finite as a float: not NaN or infinite, and not an int too large to
    convert. A whole number is an int that is also a number. An int or
    float subclass is accepted in any number field, mapping value, game
    length key, or allowed_game_formats points value: before any check,
    the config stores the plain int or float of its own value
    (int.__int__ or float.__float__, keeping an int as int and a float as
    float), so the checks, fingerprint(), and rating code all read that
    plain value (issue #107). fingerprint() reads the fields of Config
    itself, not of the instance, so a subclass's extra fields do not
    change it. So a built
    config always gives plain JSON, and fingerprint(), ==, and hash()
    never raise. The rules:

    - version is a str. The three flags are bools.
    - Other scalar fields are numbers, except the day counts, the
      repeat, carry, verified match, and connectivity counts, and
      diversity_target, which are whole numbers.
      connectivity_reliability_cap and the two gap filters are None or a
      number.
    - min_rating, max_rating, and default_rating are each at most 2**511
      in magnitude, min_rating < max_rating, and default_rating lies
      between them.
    - Mapping values are numbers greater than 0, with exactly one key for
      every scoring type or match type. game_length_weights keys are whole
      numbers of at least 1, one for the points of every allowed format,
      and no two keys name the same length.
    - 0 <= k_min <= k_max, and the largest K, k_min + (k_max - k_min) *
      1.0 as player_k computes it (it can round just above k_max), times
      the largest match weight times max_information_multiplier is
      finite in floats. A self-reported match's step weight can be an
      exact int product: when the self-reported match weight,
      max_information_multiplier, and self_reported_repeat_factor are all
      ints, and information_multiplier can return
      max_information_multiplier itself. The check counts it as
      reachable when, for some (scoring, points) in allowed_game_formats,
      game_length_weights[points] * (1.0 + game_count_bonus *
      (sys.maxsize - 1)) is not below it (sys.maxsize is the most games
      len() can report). No real match has that many games, so the check
      can reject a config whose multiplier no real match reaches. When it
      is reachable, the self-reported weight times the multiplier must
      convert to a float, the largest K
      times that float must be finite, and with an int
      rd_shrink_per_match, rd_shrink_per_match times the exact product
      must convert too.
    - 0 < rd_min < rd_max <= 2**511, and rd_max - rd_min is greater
      than 0 as computed (an int and a float that are equal once
      converted are rejected). The three seed RDs lie within
      rd_min..rd_max. rd_shrink_per_match >= 0, and rd_growth_per_day is
      from 0 to 2**511.
    - Some bounds apply to ints only, because exact int arithmetic raises
      OverflowError where float arithmetic goes to inf: an int
      rd_growth_per_day is at most 2**500, an int game_count_bonus at
      most 2**511, and the exact self-reported step weight rule above.
    - win_weight is from 0 to 1, game_count_bonus >= 0,
      max_information_multiplier >= 1, self_reported_repeat_factor is in
      (0, 1], and established_reliability_threshold and
      connectivity_reliability_cap are from 0 to 100.
    - replay_interval_days, diversity_window_days, and
      self_reported_repeat_window_days are from 1 to 3,652,058 (the days
      from date.min to date.max, which timedelta holds), and
      import_reliability_max_age_days from 0 to that. A window that
      reaches back before date.min from a match or reference date stops
      at date.min instead of raising OverflowError. diversity_target,
      self_reported_max_repeats, and the three connectivity counts are at
      least 1;
      import_reliability_carry_matches and
      established_min_verified_matches at least 0.
    - The gap filters are None or at least 0.
    """

    version: str = __version__

    # Rating scale
    min_rating: float = 2.0
    max_rating: float = 8.0
    default_rating: float = 3.5

    # Logistic expectation curve, one scale per scoring type, because side-out
    # and rally scoring produce different point shares for the same skill gap.
    # A rating gap equal to the scale gives 10:1 expected point-share odds.
    # Both values are UNCALIBRATED placeholders. Fit them with fit_scale().
    scale_by_scoring: Mapping[ScoringType, float] = field(
        default_factory=lambda: {
            ScoringType.SIDE_OUT: 2.9,
            ScoringType.RALLY: 2.9,
        }
    )

    # Win-probability calibration, one exponent per scoring type: the match win
    # probability p becomes sigmoid(a * logit(p)). 1.0 leaves it unchanged.
    win_calibration_by_scoring: Mapping[ScoringType, float] = field(
        default_factory=lambda: {
            ScoringType.SIDE_OUT: 1.0,
            ScoringType.RALLY: 1.0,
        }
    )

    # Per-player responsiveness. K is derived from RD, and each player moves
    # by their own K, so team-average movement is not always zero-sum.
    # MatchResult.imbalance reports the net.
    k_min: float = 0.12
    k_max: float = 0.60

    # Rating deviation (RD) is OPEN's internal uncertainty measure.
    rd_min: float = 0.10
    rd_max: float = 0.80
    new_player_rd: float = 0.80
    external_seed_rd: float = 0.42
    category_seed_rd: float = 0.50
    rd_shrink_per_match: float = 0.08
    rd_growth_per_day: float = 0.012

    # Match performance: actual = (1 - win_weight) * pooled point share
    # + win_weight * (1 if team A won else 0). The default 0.0 is pure
    # point share, so a team can win and still lose rating when it wins by
    # less than expected or wins a best of 3 (or 5) with fewer total
    # points. 1.0 scores the result only, so a winner never loses rating.
    # There is no floor on a winner's change, other than the rating range.
    # The default changes only when out-of-sample evaluation supports
    # another value (issue #20). See the match performance note in the
    # module docstring for the full policy.
    win_weight: float = 0.0

    # Evidence quality. These are OPEN policy choices.
    match_weights: Mapping[MatchType, float] = field(
        default_factory=lambda: {
            MatchType.TOURNAMENT: 1.00,
            MatchType.LEAGUE: 0.90,
            MatchType.REC_VERIFIED: 0.75,
            MatchType.SELF_REPORTED: 0.40,
        }
    )

    # Game formats accepted for rating, as (scoring type, points to).
    allowed_game_formats: frozenset[tuple[ScoringType, int]] = ALL_GAME_FORMATS

    # Information multiplier: longer games and more games carry more evidence.
    # multiplier = min(max, length_weight[points_to] * (1 + bonus * (games - 1)))
    game_length_weights: Mapping[int, float] = field(
        default_factory=lambda: {
            11: 1.00,
            15: 1.10,
            21: 1.20,
        }
    )
    game_count_bonus: float = 0.12
    max_information_multiplier: float = 1.30

    # Published recalculation schedule. Informational: the platform runs
    # replay() on this interval and labels ratings between runs as live.
    replay_interval_days: int = 7

    # Diversity uses a rolling window.
    diversity_window_days: int = 365
    diversity_target: int = 12

    # Self-reported match safeguards.
    self_reported_requires_both_teams: bool = True
    self_reported_repeat_window_days: int = 90
    self_reported_repeat_factor: float = 0.5
    self_reported_max_repeats: int = 4

    # Likely duplicates: a match with the same played_on, the same two teams
    # (either side may be team A), and the same game scores as an earlier
    # counted match, under another match_id. False does not reject it for
    # this, and a counted one has MatchResult.possible_duplicate_of set.
    # True does not count it (RejectionReason.POSSIBLE_DUPLICATE). A real
    # same-day rematch with the same scores is caught too, so the default
    # only flags. Under True the first report to count in processing order
    # survives, whatever its match type or format, so a weaker or
    # relabeled copy can win. Use True only when the backend trusts who
    # files reports. See the likely duplicates note in the module docstring.
    reject_content_duplicates: bool = False

    # Imported reliability display.
    import_reliability_carry_matches: int = 10
    import_reliability_max_age_days: int = 365

    # Reliability label. PlayerSummary.established is True when OPEN-earned
    # reliability, as displayed, is at least the threshold (0-100) and the
    # state has at least established_min_verified_matches counted
    # tournament, league, or recreational verified matches. Self-reported
    # matches never count toward the minimum. A label only: it changes no
    # rating, prediction, or evaluation. A minimum of 0 leaves the
    # reliability rule alone.
    established_reliability_threshold: float = 60.0
    established_min_verified_matches: int = 3

    # Network connectivity, a reliability label (issue #58). Measured per
    # rating format over the diversity window at the summary's reference
    # date. Two players in contact are tied when they share at least
    # connectivity_min_shared_contacts partners or opponents and each of
    # them reached those players on at least connectivity_min_contact_days
    # distinct dates; a pool is a set of players linked by ties. A player
    # is connected when their group of players linked by contact has at
    # least half as many players as the largest group and either the pool
    # with its fringe (drop-ins who met only the pool) holds more than half
    # the group or the pool has at least connectivity_min_outside_players
    # other outside players on at least connectivity_min_contact_days
    # dates. PlayerSummary.reliability of a player who is not connected is
    # at most connectivity_reliability_cap; None, the default, turns the
    # cap off. The rule can be gamed through group sizes and can cap
    # players in a real but smaller network, so the cap stays off until
    # out-of-sample review of real ledgers supports a default (issue #58). A label only: it
    # changes no rating, prediction, or evaluation. The defaults are
    # declared choices, not calibrated ones.
    connectivity_reliability_cap: float | None = None
    connectivity_min_shared_contacts: int = 4
    connectivity_min_contact_days: int = 3
    connectivity_min_outside_players: int = 8

    # Category doubles matches also update overall doubles.
    category_feeds_parent: bool = True

    # Optional eligibility filters. Disabled by default.
    max_singles_rating_gap: float | None = None
    max_doubles_partner_gap: float | None = None

    def __post_init__(self) -> None:
        # One pass in three steps. First the type and finiteness of every
        # scalar field, so no comparison below can raise TypeError.
        def must_be(name: str, kind: str) -> str:
            return _CONFIG_TYPE_MESSAGES.get(name, f"{name} must be {kind}")

        # Exactly str: the snapshot loader accepts only a plain str, so a
        # subclass would give a snapshot that does not load.
        if type(self.version) is not str:
            raise ConfigError(must_be("version", "a str"))
        # Store every number as the plain int or float of its own value, so
        # the checks, fingerprint(), and rating code all read the same value
        # (issue #107). A bool or a non-number is kept for the checks below.
        for name in _CONFIG_NUMBER_FIELDS + _CONFIG_WHOLE_FIELDS + _CONFIG_OPTIONAL_NUMBER_FIELDS:
            object.__setattr__(self, name, _plain_number(getattr(self, name)))
        for name in _CONFIG_NUMBER_FIELDS:
            if not _is_config_number(getattr(self, name)):
                raise ConfigError(must_be(name, "a finite number"))
        for name in _CONFIG_WHOLE_FIELDS:
            if not _is_config_whole(getattr(self, name)):
                raise ConfigError(must_be(name, "a whole number"))
        for name in _CONFIG_OPTIONAL_NUMBER_FIELDS:
            value = getattr(self, name)
            if value is not None and not _is_config_number(value):
                raise ConfigError(must_be(name, "None or a finite number"))
        for name in _CONFIG_BOOL_FIELDS:
            if not isinstance(getattr(self, name), bool):
                raise ConfigError(must_be(name, "a bool"))

        # Then the mapping and format fields: types, keys, and read-only
        # copies. Keys and format entries are stored as their own types (an
        # enum member, a plain int), so the fingerprint never depends on how
        # an equal key prints or whether JSON can hold it.
        # One read of each caller's mapping, as (key, plain value) pairs;
        # every check below and the stored copy read these pairs, never the
        # caller's mapping, and no key is hashed before its own checks.
        pairs: dict[str, list[tuple[object, object]]] = {}
        for name in _CONFIG_MAPPING_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise ConfigError(f"{name} must be a mapping")
            pairs[name] = [(key, _plain_number(item)) for key, item in value.items()]
            if not all(_is_config_number(item) for _, item in pairs[name]):
                raise ConfigError(f"{name} values must be finite numbers")
        # Each game length key as its plain int, from its own value.
        length_pairs = [(_plain_number(key), item) for key, item in pairs["game_length_weights"]]
        if not all(_is_config_whole(key) for key, _ in length_pairs):
            raise ConfigError("game_length_weights keys must be whole numbers")
        lengths: dict[int, float] = {}
        for key, item in length_pairs:
            points = cast(int, key)
            if points in lengths:
                raise ConfigError(f"game_length_weights repeats the game length {points}")
            lengths[points] = cast(float, item)
        object.__setattr__(self, "game_length_weights", _FrozenMapping(lengths))
        if any(key < 1 for key in self.game_length_weights):
            raise ConfigError("game_length_weights keys must be at least 1")
        if any(value <= 0 for value in self.game_length_weights.values()):
            raise ConfigError("game_length_weights values must be positive")
        if not isinstance(self.allowed_game_formats, (set, frozenset)):
            raise ConfigError("allowed_game_formats must be a set or frozenset")
        # Each entry is read once, through tuple's own methods, so a tuple
        # subclass cannot show the check one pair and the stored set another.
        formats: set[tuple[ScoringType, int]] = set()
        for entry in self.allowed_game_formats:
            if not (issubclass(type(entry), tuple) and tuple.__len__(entry) == 2):
                raise ConfigError(
                    "allowed_game_formats entries must be (scoring type, whole number) pairs"
                )
            entry_scoring = tuple.__getitem__(entry, 0)
            entry_points = _plain_number(tuple.__getitem__(entry, 1))
            if not (entry_scoring in _SCORING_VALUES and _is_config_whole(entry_points)):
                raise ConfigError(
                    "allowed_game_formats entries must be (scoring type, whole number) pairs"
                )
            formats.add((ScoringType(cast(str, entry_scoring)), cast(int, entry_points)))
        object.__setattr__(self, "allowed_game_formats", frozenset(formats))
        for name, kind, label in (
            ("scale_by_scoring", ScoringType, "scoring type"),
            ("win_calibration_by_scoring", ScoringType, "scoring type"),
            ("match_weights", MatchType, "match type"),
        ):
            if {key for key, _ in pairs[name]} != set(kind):
                raise ConfigError(f"{name} needs a value for every {label}")
            try:
                keyed = {kind(cast(str, key)): item for key, item in pairs[name]}
            except ValueError as exc:
                raise ConfigError(f"{name} has an unknown key") from exc
            object.__setattr__(self, name, _FrozenMapping(keyed))
        if any(value <= 0 for value in self.scale_by_scoring.values()):
            raise ConfigError("scales must be positive")
        if any(value <= 0 for value in self.win_calibration_by_scoring.values()):
            raise ConfigError("win calibration values must be positive")
        if any(value <= 0 for value in self.match_weights.values()):
            raise ConfigError("match_weights values must be positive")
        for _, points in self.allowed_game_formats:
            if points not in self.game_length_weights:
                raise ConfigError(f"No game_length_weight for games to {points}")

        # Then ranges and cross-field rules.
        limit = _RATING_LIMIT_TEXT
        for name in ("min_rating", "max_rating", "default_rating"):
            if not -_RATING_LIMIT <= getattr(self, name) <= _RATING_LIMIT:
                raise ConfigError(f"{name} must be between -{limit} and {limit}")
        if not self.min_rating < self.max_rating:
            raise ConfigError("min_rating must be less than max_rating")
        if not self.min_rating <= self.default_rating <= self.max_rating:
            raise ConfigError("default_rating must be within rating bounds")
        if not 0 <= self.k_min <= self.k_max:
            raise ConfigError("K-factor bounds are invalid")
        # player_k and reliability divide by rd_max - rd_min as computed here,
        # so an int and a float that differ only before conversion are equal.
        if not (0 < self.rd_min < self.rd_max and self.rd_max - self.rd_min > 0):
            raise ConfigError("RD bounds are invalid")
        if self.rd_max > _RATING_LIMIT:
            raise ConfigError(f"rd_max must be at most {limit}")
        for name in ("new_player_rd", "external_seed_rd", "category_seed_rd"):
            if not self.rd_min <= getattr(self, name) <= self.rd_max:
                raise ConfigError(f"{name} must be within RD bounds")
        if self.rd_shrink_per_match < 0:
            raise ConfigError("rd_shrink_per_match must be at least 0")
        if not 0 <= self.rd_growth_per_day <= _RATING_LIMIT:
            raise ConfigError(f"rd_growth_per_day must be between 0 and {limit}")
        # Exact int arithmetic raises where float arithmetic goes to inf, so an
        # int gets a lower bound: rd_max**2 + growth**2 * _MAX_DAYS stays a float.
        if isinstance(self.rd_growth_per_day, int) and self.rd_growth_per_day > _INT_GROWTH_LIMIT:
            raise ConfigError(
                f"rd_growth_per_day must be at most {_INT_GROWTH_LIMIT_TEXT} when it is an int"
            )
        if not 0.0 <= self.win_weight <= 1.0:
            raise ConfigError("win_weight must be between 0 and 1")
        if self.game_count_bonus < 0:
            raise ConfigError("game_count_bonus must be at least 0")
        # The same for an int bonus times a game count below 2**63.
        if isinstance(self.game_count_bonus, int) and self.game_count_bonus > _INT_BONUS_LIMIT:
            raise ConfigError(
                f"game_count_bonus must be at most {_INT_BONUS_LIMIT_TEXT} when it is an int"
            )
        if self.max_information_multiplier < 1.0:
            raise ConfigError("max_information_multiplier must be at least 1")
        # The largest step size a match can reach. When it overflows to inf,
        # a surprise of exactly 0 gives a NaN change. The largest K is what
        # player_k returns at fraction 1.0, which can round above k_max. The
        # first check bounds the step as computed in floats.
        largest_k = _as_float(self.k_min + (self.k_max - self.k_min) * 1.0)
        largest_weight = _as_float(max(self.match_weights.values())) * _as_float(
            self.max_information_multiplier
        )
        k_message = (
            "k_max times the largest match weight and max_information_multiplier must be finite"
        )
        if not math.isfinite(largest_k * largest_weight):
            raise ConfigError(k_message)
        # On a self-reported match the step weight is weight * factor**prior
        # * information_multiplier. With an int weight, an int factor
        # (factor**prior is then the int 1), and an int multiplier that
        # information_multiplier returns, that product is an exact int.
        # Converting it can raise OverflowError, or land above the float
        # product checked above, and an int rd_shrink_per_match times
        # it is another exact int. So form those products as the engine does
        # (issue #93).
        weight = self.match_weights[MatchType.SELF_REPORTED]
        multiplier = self.max_information_multiplier
        if (
            isinstance(weight, int)
            and isinstance(multiplier, int)
            and isinstance(self.self_reported_repeat_factor, int)
            and self._multiplier_reachable()
        ):
            exact_weight = int.__int__(weight) * int.__int__(multiplier)
            try:
                step_weight = _as_float(exact_weight)
            except OverflowError:
                raise ConfigError(k_message) from None
            if not math.isfinite(largest_k * step_weight):
                raise ConfigError(k_message)
            if isinstance(self.rd_shrink_per_match, int):
                try:
                    _as_float(int.__int__(self.rd_shrink_per_match) * exact_weight)
                except OverflowError:
                    raise ConfigError(
                        "rd_shrink_per_match times the largest match weight and "
                        "max_information_multiplier must be finite"
                    ) from None
        if self.replay_interval_days <= 0:
            raise ConfigError("replay_interval_days must be positive")
        if self.diversity_window_days <= 0 or self.diversity_target <= 0:
            raise ConfigError("diversity settings must be positive")
        if not 0.0 < self.self_reported_repeat_factor <= 1.0:
            raise ConfigError("self_reported_repeat_factor must be in (0, 1]")
        if self.self_reported_max_repeats < 1 or self.self_reported_repeat_window_days <= 0:
            raise ConfigError("self-reported repeat limits must be positive")
        if self.import_reliability_carry_matches < 0 or self.import_reliability_max_age_days < 0:
            raise ConfigError("import reliability limits must be nonnegative")
        for name in _CONFIG_DAY_FIELDS:
            if getattr(self, name) > _MAX_DAYS:
                raise ConfigError(f"{name} must be at most {_MAX_DAYS}")
        if not 0.0 <= self.established_reliability_threshold <= 100.0:
            raise ConfigError("established_reliability_threshold must be between 0 and 100")
        if self.established_min_verified_matches < 0:
            raise ConfigError("established_min_verified_matches must be a nonnegative int")
        cap = self.connectivity_reliability_cap
        if cap is not None and not 0.0 <= cap <= 100.0:
            raise ConfigError("connectivity_reliability_cap must be None or a number from 0 to 100")
        for name in (
            "connectivity_min_shared_contacts",
            "connectivity_min_contact_days",
            "connectivity_min_outside_players",
        ):
            if getattr(self, name) < 1:
                raise ConfigError(f"{name} must be a positive int")
        for name in ("max_singles_rating_gap", "max_doubles_partner_gap"):
            gap = getattr(self, name)
            if gap is not None and gap < 0:
                raise ConfigError(f"{name} must be None or at least 0")

    def _multiplier_reachable(self) -> bool:
        """
        True when information_multiplier can return max_information_multiplier.

        It returns the multiplier when length * count is not below it (min
        keeps its first argument on a tie), for the length weight of an
        allowed format (record rejects any other format first) and some game
        count. count = 1.0 + game_count_bonus * (games - 1) never falls as
        games grows, since the bonus is at least 0 and float rounding keeps
        that order, so the largest game count decides. It converts: an int
        bonus is at most 2**511. Computed on the plain values.
        """

        def plain(value: float) -> float:
            return int.__int__(value) if isinstance(value, int) else float.__float__(value)

        multiplier = plain(self.max_information_multiplier)
        count = 1.0 + plain(self.game_count_bonus) * (_MAX_GAME_COUNT - 1)
        return any(
            not plain(self.game_length_weights[points]) * count < multiplier
            for _, points in sorted(self.allowed_game_formats)
        )

    def _payload(self) -> dict[str, object]:
        """Every parameter as fresh JSON-safe data: the input to fingerprint()."""

        def norm(value: object) -> object:
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, Mapping):
                return {str(norm(k)): norm(v) for k, v in value.items()}
            if isinstance(value, (set, frozenset)):
                return sorted((norm(v) for v in value), key=str)
            if isinstance(value, tuple):
                return [norm(v) for v in value]
            return value

        # The fields of Config itself: an instance __dataclass_fields__ or a
        # subclass's extra fields never change the fingerprint (issue #107).
        return {f.name: norm(getattr(self, f.name)) for f in fields(Config)}

    def _canonical(self) -> str:
        return json.dumps(self._payload(), sort_keys=True)

    def fingerprint(self) -> str:
        """Stable hash of every parameter, stored with each replay."""
        return hashlib.sha256(self._canonical().encode()).hexdigest()[:16]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Config):
            return NotImplemented
        return self._canonical() == other._canonical()

    def __hash__(self) -> int:
        return hash(self._canonical())

    def to_dict(self) -> dict[str, object]:
        """
        Every parameter as a new dict of JSON data, for storage.

        Enum keys and values become their string values, game length keys
        become strings, and allowed_game_formats becomes a sorted list of
        [scoring, points] pairs. Other values are passed through, so the
        dict is plain JSON when every field has its documented type.
        from_dict() turns it back into an equal Config with the same
        fingerprint. A store that rewrites 8.0 as 8 changes the config: an
        int and a float are different values to the fingerprint.
        """
        return self._payload()

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Config:
        """
        Build a Config from to_dict() output, also after a round trip
        through Python's json module.

        data must have exactly the keys to_dict() gives, so a stored config
        names every parameter. Raises ConfigError for a missing or unknown
        key, a value that cannot be converted, or a value Config rejects.
        """
        if not isinstance(data, Mapping):
            raise ConfigError("Config data must be a mapping")
        # The fields of Config itself, as to_dict() gives them.
        names = [f.name for f in fields(Config)]
        missing = sorted(set(names) - set(data))
        if missing:
            raise ConfigError(f"Config data has missing keys: {', '.join(missing)}")
        unknown = sorted(_shown(key, str) for key in set(data) - set(names))
        if unknown:
            raise ConfigError(f"Config data has unknown keys: {', '.join(unknown)}")
        values = {name: data[name] for name in names}
        values["scale_by_scoring"] = _enum_keyed(
            values["scale_by_scoring"], ScoringType, "scale_by_scoring"
        )
        values["win_calibration_by_scoring"] = _enum_keyed(
            values["win_calibration_by_scoring"], ScoringType, "win_calibration_by_scoring"
        )
        values["match_weights"] = _enum_keyed(values["match_weights"], MatchType, "match_weights")
        values["game_length_weights"] = _int_keyed(values["game_length_weights"])
        values["allowed_game_formats"] = _game_formats(values["allowed_game_formats"])
        try:
            return cls(**values)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ConfigError(f"Config data has a value of the wrong type: {exc}") from exc


def _is_whole_number(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


_E = TypeVar("_E", bound=Enum)


def _enum_keyed(value: object, kind: type[_E], name: str) -> dict[_E, object]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{name} must be a mapping")
    out: dict[_E, object] = {}
    for key, item in value.items():
        try:
            out[kind(key)] = item
        except ValueError as exc:
            raise ConfigError(f"{name} has an unknown key: {_shown(key)}") from exc
    return out


def _shown(value: object, show: Callable[[object], str] = repr) -> str:
    """value for an error message. An int over the int-to-str limit cannot print."""
    try:
        return show(value)
    except ValueError:
        return "a value too large to show"


def _int_keyed(value: object) -> dict[int, object]:
    if not isinstance(value, Mapping):
        raise ConfigError("game_length_weights must be a mapping")
    out: dict[int, object] = {}
    for key, item in value.items():
        if _is_whole_number(key):
            points = key
        elif isinstance(key, str) and key.isascii() and key.isdigit():
            try:
                points = int(key)
            except ValueError as exc:  # more digits than int() converts
                raise ConfigError(
                    f"game_length_weights has a key that is not a whole number: {_shown(key)}"
                ) from exc
        else:
            raise ConfigError(
                f"game_length_weights has a key that is not a whole number: {_shown(key)}"
            )
        if points in out:
            raise ConfigError(f"game_length_weights repeats the game length {points}")
        out[points] = item
    return out


def _game_formats(value: object) -> frozenset[tuple[ScoringType, int]]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise ConfigError("allowed_game_formats must be a list of [scoring, points] pairs")
    out: set[tuple[ScoringType, int]] = set()
    for item in value:
        if isinstance(item, (str, bytes)) or not isinstance(item, Sequence) or len(item) != 2:
            raise ConfigError(
                f"allowed_game_formats has an entry that is not a pair: {_shown(item)}"
            )
        scoring, points = item
        if not isinstance(points, int) or isinstance(points, bool):
            raise ConfigError(
                f"allowed_game_formats has points that are not a whole number: {_shown(item)}"
            )
        try:
            out.add((ScoringType(scoring), points))
        except ValueError as exc:
            raise ConfigError(
                f"allowed_game_formats has an unknown scoring type: {_shown(item)}"
            ) from exc
    return frozenset(out)


# ---------------------------------------------------------------------------
# Identity, provenance, and player rating state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExternalRatingSnapshot:
    """A verified external rating captured on a date. Used only as a seed."""

    source: str
    rating: float
    captured_on: date
    reliability: float | None = None
    external_player_id: str | None = None
    evidence_reference: str | None = None


@dataclass
class RatingState:
    """One player's rating in one format, with its uncertainty and provenance."""

    rating: float
    rd: float
    matches_played: int = 0
    last_played: date | None = None

    # Date the RD value was last set. Inactivity inflation runs from here, so
    # an old imported snapshot is treated as uncertain even before any match.
    rd_as_of: date | None = None

    # Provenance of the initial rating. The current rating is always
    # maintained by OPEN after initialization.
    initial_rating: float = 3.5
    initial_source: str = "open_provisional"
    initial_source_date: date | None = None
    initial_source_reliability: float | None = None
    initial_external_player_id: str | None = None
    initial_evidence_reference: str | None = None

    # Live updates applied since the last authoritative replay.
    live_updates_since_replay: int = 0

    # Most recent interaction date by player ID, allowing rolling diversity.
    partners: dict[str, date] = field(default_factory=dict)
    opponents: dict[str, date] = field(default_factory=dict)

    # Live updates since the last replay that were already in the parent
    # state when this category state was derived from it. Set once, at
    # derivation; 0 for a state not derived from its parent. A replay
    # gives 0, since no state has live updates during a replay.
    inherited_live_updates: int = 0

    # Counted tournament, league, and recreational verified matches that
    # moved this state: matches_played without the self-reported ones. A
    # seed, and a category state derived from its parent, start at 0.
    verified_matches_played: int = 0


@dataclass
class Player:
    """A player record: ratings by format, external identities, and account link."""

    id: str
    ratings: dict[RatingFormat, RatingState] = field(default_factory=dict)

    # External identities and OPEN account linkage are separate from ratings.
    # A player record can exist and accumulate OPEN matches before registration.
    external_ids: dict[str, str] = field(default_factory=dict)
    account_user_id: str | None = None


# ---------------------------------------------------------------------------
# Match input and output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Match:
    """
    One match: teams, game scores, date, format, provenance, and status.

    status (last field, default MatchStatus.COMPLETED) says how the match
    ended. A completed match has at least one game and a decided winner. A
    forfeit must have no games. A retired or incomplete match has zero or more
    finished games, each valid in game_format, and no decided winner; a
    partial game is not recorded. record never counts a match that is not
    completed: after the match_id check (a processed or reserved match_id
    gets DUPLICATE_MATCH_ID), it returns RejectionReason.MATCH_NOT_COMPLETED
    and, like every rejected match, reserves the match_id: a later record
    call for a match with it that does not raise returns DUPLICATE_MATCH_ID.

    The match must be a Match itself, not a subclass: record, replay, and
    prequential_predictions (and so evaluate, fit_scale, and
    fit_win_calibration) and ledger_order raise InvalidMatchError for a
    subclass before they change any state or call its validate, and
    Match.validate raises it for a subclass that does not override it. A
    subclass could return one value to validation and another to rating.
    The same calls raise it for a Match whose instance __dict__ holds
    anything but its own fields, which could hide a method validate calls.

    match_id, the team player ids, and each member of confirmed_by must be a
    non-blank str, exactly a str (not a subclass); validate raises
    InvalidMatchError otherwise.

    validate checks the type of status, rating_format, match_type, team_a,
    team_b, confirmed_by, game_format, played_at, sequence, and best_of. It
    raises InvalidMatchError when status is not a MatchStatus, rating_format
    is not a RatingFormat, or match_type is not a MatchType (a str is
    rejected, even one equal to a member's value); when a team is not
    exactly a tuple; when confirmed_by is not exactly a set or frozenset;
    when game_format is not a GameFormat; when played_at is not a datetime
    or None; or when sequence or best_of is not an int (a bool or float is
    rejected). games and each game in it must be exactly a tuple or list,
    and each score exactly an int (a subclass of any of these is rejected).
    It also calls game_format.validate, which checks the
    fields inside the format, and raises InvalidMatchError with the same
    message for a ConfigError that call raises.
    """

    match_id: str
    team_a: tuple[str, ...]
    team_b: tuple[str, ...]
    games: tuple[tuple[int, int], ...]
    played_on: date
    rating_format: RatingFormat
    match_type: MatchType = MatchType.REC_VERIFIED
    best_of: int = 1
    game_format: GameFormat = GameFormat()

    # Order of play within a day. played_on is the calendar date at the
    # venue. played_at must be timezone-aware and fall on played_on; send it
    # when known. Within one played_on, untimed matches come first, then
    # timed ones by UTC instant; sequence (for example, round number) and
    # then match_id break ties.
    played_at: datetime | None = None
    sequence: int = 0

    # Players who confirmed the reported score. Required for self-reported
    # matches: at least one player from each team.
    confirmed_by: frozenset[str] = frozenset()

    # How the match ended. Only a completed match can count.
    status: MatchStatus = MatchStatus.COMPLETED

    def validate(self) -> None:
        """Raise InvalidMatchError when not valid, its game format included."""
        _require_plain_match(self)
        if not _is_id(self.match_id):
            raise InvalidMatchError("match_id is required for duplicate protection")
        if not isinstance(self.status, MatchStatus):
            raise InvalidMatchError(
                f"status must be a MatchStatus, not {type(self.status).__name__}"
            )
        Match._validate_field_types(self)
        completed = self.status is MatchStatus.COMPLETED
        expected_size = self.rating_format.team_size
        if len(self.team_a) != expected_size or len(self.team_b) != expected_size:
            raise InvalidMatchError(
                f"{self.rating_format.value} requires {expected_size} player(s) per team"
            )
        all_ids = self.team_a + self.team_b
        if not all(_is_id(pid) for pid in all_ids):
            raise InvalidMatchError("Player ids must be non-empty strings")
        if len(set(all_ids)) != len(all_ids):
            raise InvalidMatchError("A player cannot appear more than once in a match")
        if not all(_is_id(pid) for pid in self.confirmed_by):
            raise InvalidMatchError("confirmed_by player ids must be non-empty strings")
        if not set(self.confirmed_by) <= set(all_ids):
            raise InvalidMatchError("confirmed_by may only contain match participants")
        if not _is_plain_date(self.played_on):
            raise InvalidMatchError(
                "played_on must be a plain date (not a datetime), "
                f"not {type(self.played_on).__name__}"
            )
        if self.played_at is not None:
            _utc_instant(self.played_at)
        if self.played_at is not None and self.played_at.date() != self.played_on:
            raise InvalidMatchError("played_at must fall on played_on")
        if self.sequence < 0:
            raise InvalidMatchError("sequence must be nonnegative")
        if type(self.games) not in (tuple, list):
            raise InvalidMatchError(
                f"games must be a tuple or list, not {type(self.games).__name__}"
            )
        if completed and not self.games:
            raise InvalidMatchError("At least one game score is required")
        if self.status is MatchStatus.FORFEIT and self.games:
            raise InvalidMatchError("A forfeit cannot have game scores")
        if not all(type(game) in (tuple, list) and len(game) == 2 for game in self.games):
            raise InvalidMatchError("Each game must be a pair of scores")
        if self.best_of < 1 or self.best_of % 2 == 0:
            raise InvalidMatchError("best_of must be a positive odd number")
        if len(self.games) > self.best_of:
            raise InvalidMatchError("Games played cannot exceed best_of")
        try:
            self.game_format.validate()
        except ConfigError as err:
            raise InvalidMatchError(str(err)) from None
        for score_a, score_b in self.games:
            if not all(type(score) is int for score in (score_a, score_b)):
                raise InvalidMatchError("Game scores must be integers")
            if score_a < 0 or score_b < 0 or score_a == score_b:
                raise InvalidMatchError("Game scores must be nonnegative and cannot be tied")
            self.game_format.validate_game(score_a, score_b)
        wins_needed = self.best_of // 2 + 1
        running_a = running_b = 0
        for index, (score_a, score_b) in enumerate(self.games):
            if score_a > score_b:
                running_a += 1
            else:
                running_b += 1
            if running_a >= wins_needed or running_b >= wins_needed:
                if index != len(self.games) - 1:
                    raise InvalidMatchError("No games may be recorded after the match is decided")
                break
        decided = running_a >= wins_needed or running_b >= wins_needed
        if completed and not decided:
            raise InvalidMatchError("Match score does not contain a completed winner")
        if not completed and decided:
            raise InvalidMatchError(f"A {self.status.value} match cannot have a decided winner")

    def _validate_field_types(self) -> None:
        """
        Raise InvalidMatchError when a field has the wrong type.

        Runs before validate uses any of these fields. A str equal to an enum
        member's value is rejected, a list team is rejected, and a bool or
        float sequence or best_of is rejected. Teams and confirmed_by must be
        exactly their types: a subclass could give validation one value and
        rating another.
        """
        checks: tuple[tuple[str, object, type | tuple[type, ...], str], ...] = (
            ("rating_format", self.rating_format, RatingFormat, "a RatingFormat"),
            ("match_type", self.match_type, MatchType, "a MatchType"),
            ("game_format", self.game_format, GameFormat, "a GameFormat"),
        )
        for name, value, kind, label in checks:
            if not isinstance(value, kind):
                raise InvalidMatchError(f"{name} must be {label}, not {type(value).__name__}")
        exact: tuple[tuple[str, object, tuple[type, ...], str], ...] = (
            ("team_a", self.team_a, (tuple,), "a tuple"),
            ("team_b", self.team_b, (tuple,), "a tuple"),
            ("confirmed_by", self.confirmed_by, (set, frozenset), "a set or frozenset"),
        )
        for name, value, kinds, label in exact:
            if type(value) not in kinds:
                raise InvalidMatchError(f"{name} must be {label}, not {type(value).__name__}")
        if self.played_at is not None and not isinstance(self.played_at, datetime):
            raise InvalidMatchError(
                f"played_at must be a datetime or None, not {type(self.played_at).__name__}"
            )
        for name, number in (("sequence", self.sequence), ("best_of", self.best_of)):
            if not isinstance(number, int) or isinstance(number, bool):
                raise InvalidMatchError(f"{name} must be an int, not {type(number).__name__}")


def _require_plain_match(match: object) -> None:
    """
    Raise InvalidMatchError unless match is a Match itself, not a subclass.

    A subclass can override a field with a property that changes between
    reads, or override validate, and so pass validation with one value and
    be rated with another. An exact Match must also hold nothing in its
    instance __dict__ but its own fields: another entry there could hide a
    method validate calls, such as validate or _validate_field_types, or
    the field list (__dataclass_fields__).
    """
    if type(match) is not Match:
        raise InvalidMatchError(f"match must be a Match, not {type(match).__name__}")
    held = vars(match)
    # An exact dict, so __len__ and __iter__ cannot hide an entry.
    if (
        type(held) is not dict
        or len(held) != len(_MATCH_FIELD_NAMES)
        or not all(type(name) is str and name in _MATCH_FIELD_NAMES for name in held)
    ):
        raise InvalidMatchError("match has attributes that are not Match fields")


_MATCH_FIELD_NAMES = frozenset(f.name for f in fields(Match))


def _validate_match(match: Match) -> None:
    """
    Check that match is a Match itself, then validate it; InvalidMatchError if not.

    The type check comes first, so a subclass's own validate never runs and
    an instance __dict__ that holds anything but the fields raises.
    """
    _require_plain_match(match)
    Match.validate(match)


def _is_plain_str(value: object) -> TypeGuard[str]:
    """
    True for a str that is not a subclass.

    A subclass can override __eq__, __hash__, or strip and so pass a check
    that the plain value would fail, so identity checks accept only str.
    """
    return type(value) is str


def _is_id(value: object) -> TypeGuard[str]:
    """True for a plain str (not a subclass) with a non-whitespace character."""
    return _is_plain_str(value) and bool(value.strip())


_UTC_MIN = datetime.min.replace(tzinfo=UTC)


def _utc_instant(played_at: datetime) -> datetime:
    """The UTC instant of an aware played_at; InvalidMatchError when there is none."""
    if played_at.tzinfo is None or played_at.utcoffset() is None:
        raise InvalidMatchError("played_at must be timezone-aware")
    try:
        return played_at.astimezone(UTC)
    except OverflowError:
        raise InvalidMatchError("played_at is outside the range that UTC can represent") from None


def ledger_order(match: Match) -> tuple[date, int, datetime, int, str]:
    """
    Deterministic order of play.

    Matches sort by played_on. Within one day, untimed matches come first,
    by sequence then match_id. Timed matches follow, by the UTC instant of
    played_at, then sequence, then match_id. played_at must be
    timezone-aware; a naive value raises InvalidMatchError, and so does a
    match that is not exactly a Match (a subclass included).
    """
    _require_plain_match(match)
    if match.played_at is None:
        return (match.played_on, 0, _UTC_MIN, match.sequence, match.match_id)
    return (match.played_on, 1, _utc_instant(match.played_at), match.sequence, match.match_id)


def _ledger(matches: Iterable[Match]) -> list[Match]:
    """
    Unique ledger rows in ledger order.

    Every row is validated first, including rows after a replay's as_of, so
    an invalid row raises wherever it sits in the input. Rows that repeat a
    match_id collapse to one only when every field matches in type and
    value, with played_at compared by its UTC instant and UTC offset (see
    _canonical). Any other repeat raises
    LedgerConflictError naming every conflicting id, so the result never
    depends on input order.
    """
    by_id: dict[str, tuple[Match, tuple[object, ...]]] = {}
    conflicts: set[str] = set()
    for match in matches:
        _validate_match(match)
        key = _canonical(match)
        _, kept_key = by_id.setdefault(match.match_id, (match, key))
        if kept_key != key:
            conflicts.add(match.match_id)
    if conflicts:
        raise LedgerConflictError(conflicts)
    return sorted((match for match, _ in by_id.values()), key=ledger_order)


def _canonical(match: Match) -> tuple[object, ...]:
    """
    A form of a validated row that is equal only for interchangeable rows.

    Every field is paired with its exact type, recursively, so values that
    are equal under == but differ in type (3 and 3.0, a str and an Enum
    member) do not match. played_at is compared by its UTC instant and its
    UTC offset instead: Match equality ignores the offset, treats the two
    readings of a repeated wall time (a DST fold) as equal, and treats rows
    with distinct zone objects inside a fold as unequal.
    """
    others = tuple(
        (f.name, _strict(getattr(match, f.name))) for f in fields(Match) if f.name != "played_at"
    )
    if match.played_at is None:
        return (others, None, None)
    return (others, _utc_instant(match.played_at), match.played_at.utcoffset())


def _strict(value: object) -> object:
    """
    value paired with its exact type, recursing into dataclasses and collections.

    Tuples and lists recurse member by member, and so do sets and
    frozensets, so a member's type counts wherever it sits. _json_form
    mirrors this; change both together.
    """
    if is_dataclass(value) and not isinstance(value, type):
        return (
            type(value),
            tuple((f.name, _strict(getattr(value, f.name))) for f in fields(type(value))),
        )
    if isinstance(value, (tuple, list)):
        return (type(value), tuple(_strict(item) for item in value))
    if isinstance(value, (frozenset, set)):
        return (type(value), frozenset(_strict(item) for item in value))
    return (type(value), value)


def _match_digest(match: Match) -> str:
    """
    SHA-256 hex digest of a validated row, equal only for interchangeable rows.

    It mirrors _canonical in JSON form: every field but played_at, tagged
    with its exact type, and played_at as its UTC instant and UTC offset.
    A checkpoint stores it for each row it considered, so resume can tell
    whether a row it is given is the row the checkpoint saw.
    """
    others = [
        [f.name, _json_form(getattr(match, f.name))] for f in fields(Match) if f.name != "played_at"
    ]
    timing: list[object] | None = None
    if match.played_at is not None:
        instant = _utc_instant(match.played_at)
        offset = match.played_at.utcoffset()
        timing = [instant.isoformat(), None if offset is None else offset.total_seconds()]
    return hashlib.sha256(_json_text([others, timing]).encode()).hexdigest()


def _json_text(form: object) -> str:
    """Compact JSON text with sorted keys, for hashing and for sorting forms."""
    return json.dumps(form, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _json_form(value: object) -> object:
    """
    value as JSON data tagged with its exact type, recursing like _strict.

    Ints are written in hex, which has no digit limit. A float is written as
    a number with negative zero folded into zero (as == does), or as its
    repr when it is not finite. Set and frozenset members are sorted by
    their JSON text, so the form never depends on set order or the hash
    seed.
    """
    tag = f"{type(value).__module__}.{type(value).__qualname__}"
    if is_dataclass(value) and not isinstance(value, type):
        return [tag, [[f.name, _json_form(getattr(value, f.name))] for f in fields(type(value))]]
    if isinstance(value, (tuple, list)):
        return [tag, [_json_form(item) for item in value]]
    if isinstance(value, (frozenset, set)):
        return [tag, sorted((_json_form(item) for item in value), key=_json_text)]
    if isinstance(value, Enum):
        return [tag, _json_form(value.value)]
    if isinstance(value, date):
        return [tag, value.isoformat()]
    if isinstance(value, bool) or value is None:
        return [tag, value]
    if isinstance(value, int):
        return [tag, hex(value)]
    if isinstance(value, float):
        return [tag, value + 0.0 if math.isfinite(value) else repr(value)]
    if isinstance(value, str):
        return [tag, str(value)]
    return [tag, repr(value)]


class RejectionReason(_StrEnum):
    """
    Why record() did not count a match.

    MATCH_NOT_COMPLETED covers every MatchStatus other than COMPLETED; the
    status itself is on the match. DUPLICATE_MATCH_ID covers a match_id
    already processed or reserved: record reserves the match_id of every
    match it rejects for any other reason.

    BEFORE_IMPORTED_BASELINE, SINGLES_RATING_GAP, and DOUBLES_PARTNER_GAP
    read the players' seeded ratings or dates, so record also keeps the
    players of an update rejected for one of them, and
    RatingEngine.seed_external_rating and RatingEngine.add_player refuse a
    seed that could change it (see seed_external_rating).
    """

    DUPLICATE_MATCH_ID = "duplicate_match_id"
    GAME_FORMAT_NOT_ACCEPTED = "game_format_not_accepted"
    SELF_REPORTED_UNCONFIRMED = "self_reported_unconfirmed"
    SELF_REPORTED_REPEAT_LIMIT = "self_reported_repeat_limit"
    BEFORE_IMPORTED_BASELINE = "before_imported_baseline"
    SINGLES_RATING_GAP = "singles_rating_gap"
    DOUBLES_PARTNER_GAP = "doubles_partner_gap"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    MATCH_NOT_COMPLETED = "match_not_completed"  # forfeit, retired, or incomplete


# Rejections whose outcome a seed's rating or date can change. record keeps
# the players of each such rejected update, and the seed methods refuse a
# seed that could change it (issue #99).
_SEED_SENSITIVE_REASONS = frozenset(
    {
        RejectionReason.BEFORE_IMPORTED_BASELINE,
        RejectionReason.SINGLES_RATING_GAP,
        RejectionReason.DOUBLES_PARTNER_GAP,
    }
)


@dataclass
class MatchResult:
    """
    What record() did with one match in one rating format.

    reason is None when the match counted. deltas lists the rating changes.
    frozen and clamped list the players whose change was not fully applied.
    imbalance is the mean team A delta plus the mean team B delta, from the
    unrounded changes, rounded to 5 places. Each delta is rounded on its
    own, so imbalance can differ from the same sum of the rounded deltas by
    up to 1.5e-5. imbalance is in team-average units, so a match adds
    team_size * imbalance to the pool's total rating (twice the value in
    doubles). It is 0.0 when both teams have the same mean K and no
    player is clamped or frozen, and for a match that did not count. It can
    be nonzero when the teams' mean K differ or a player is clamped or
    frozen. A value of 0.0 does not mean nothing was clamped or frozen: read
    clamped and frozen for that. parent holds the overall doubles update for
    a category match, with its own imbalance.

    late is True for a counted match dated before a player's last match in
    this rating format, or before the date that player's rating in this
    format already reflects (for a category rating copied from overall
    doubles). Frozen players count. Live recording applies matches in
    arrival order, so the backend should check late and, for a category
    match, parent.late, and schedule a replay when either is True. It
    compares dates only, so a match on the same date as a player's last
    match is not late. It is False for a match that did not count. parent
    has its own value, measured against the overall doubles dates. A late
    category match leaves its rd_as_of at the date that player's rating
    already reflects, so later live category matches dated before that
    date are late too.

    possible_duplicate_of is the match_id of the earliest counted match the
    engine has seen with the same content: the same played_on, the same two
    teams (either side may be team A, in any player order), and the same
    game scores read from the same side. Other fields, the match_id and
    rating format included, are left out. It is None when there is no such
    earlier match. Only a counted match and a match rejected with
    RejectionReason.POSSIBLE_DUPLICATE (under
    Config.reject_content_duplicates) carry it; it is None on every other
    result and on parent. A real rematch on the same date with the same
    scores is flagged too, so it is a signal for the backend to check, not
    proof. Processing order alone decides which report is the copy, so the
    flagged or rejected one can be the stronger report (see the module
    docstring). A category copy of a doubles match also reaches overall
    doubles through parent, whose possible_duplicate_of is None, so read
    this top-level field. Do not show it to whoever submitted the match:
    it names another report.
    """

    counted: bool
    rating_format: RatingFormat
    reason: RejectionReason | None = None  # None when the match counted
    expected_a: float = 0.0
    actual_a: float = 0.0
    surprise: float = 0.0
    effective_weight: float = 0.0
    deltas: dict[str, float] = field(default_factory=dict)
    frozen: tuple[str, ...] = ()  # predates the player's imported baseline
    clamped: tuple[str, ...] = ()  # hit the rating floor or ceiling
    imbalance: float = 0.0  # mean team A delta plus mean team B delta
    late: bool = False  # dated before what a player's rating here reflects
    parent: MatchResult | None = None  # overall doubles update for categories
    possible_duplicate_of: str | None = None  # earlier counted match, same content


@dataclass(frozen=True)
class NetworkConnectivity:
    """
    Whether a player's pool connects to the wider network, in one format.

    Measured over Config.diversity_window_days at the summary's reference
    date, from the partners and opponents stored in each rating state.
    Two players with a state in the format are in contact when either
    one's state lists the other with a date in the window. A group is a
    set of players linked by contact. A group is a main network when it
    has at least half as many players as the largest group, so two
    separate networks of similar size are each a main network.

    Two players in contact are tied when they share at least
    Config.connectivity_min_shared_contacts contacts and each of them is
    in contact with those shared players on at least
    Config.connectivity_min_contact_days distinct dates. A pool is a set
    of players linked by ties; a player with no tie is a pool of one. The
    pool's fringe is every player outside the pool who is in contact with
    it and whose own contacts are all in the pool or in contact with it:
    a drop-in who met only the pool and its other contacts. The pool's
    outside players are the other players outside it in contact with it,
    and outside_days counts the distinct contact dates between the pool
    and them.

    connected is True when the player has a contact in the window, their
    group is a main network, and either the pool with its fringe holds
    more than half the group's players, or the pool has at least
    Config.connectivity_min_outside_players outside players on at least
    Config.connectivity_min_contact_days outside days. When it is False
    and Config.connectivity_reliability_cap is set (it is None by
    default), PlayerSummary.reliability is at most that cap. It is a
    label only and changes no rating. The rule can be gamed through
    group sizes; see the network connectivity note in the module
    docstring for its limits.
    """

    pool_size: int  # players in the pool, this player included
    fringe_players: int  # drop-ins who met only the pool and its other contacts
    outside_players: int  # other players outside the pool in contact with it
    outside_days: int  # distinct contact dates between the pool and its outside players
    group_size: int  # players linked to this player by contact, this player included
    in_main_network: bool  # the group has at least half as many players as the largest
    connected: bool


# Connectivity results kept per engine, by (format, reference date).
_CONNECTIVITY_CACHE_SIZE = 16


class _UnionFind:
    """Groups of ids. Each group's root is its smallest id, so the result is order-free."""

    def __init__(self, ids: Iterable[str]) -> None:
        self.parent = {pid: pid for pid in ids}

    def find(self, pid: str) -> str:
        root = pid
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[pid] != root:
            self.parent[pid], pid = root, self.parent[pid]
        return root

    def union(self, a: str, b: str) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            low, high = sorted((root_a, root_b))
            self.parent[high] = low

    def sizes(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for pid in sorted(self.parent):
            root = self.find(pid)
            out[root] = out.get(root, 0) + 1
        return out


def _contact_graph(
    players: Mapping[str, Player], rating_format: RatingFormat, cutoff: date
) -> dict[str, dict[str, date]]:
    """
    Each player with a state in the format, to their contacts dated on or after cutoff.

    Only players with a state in the format are nodes, and a contact's
    date is the later of the two states' dates for each other.
    """
    graph: dict[str, dict[str, date]] = {}
    for pid in sorted(players):
        if rating_format in players[pid].ratings:
            graph[pid] = {}
    for pid in sorted(graph):
        state = players[pid].ratings[rating_format]
        for contacts in (state.partners, state.opponents):
            for other in sorted(contacts):
                on = contacts[other]
                if other == pid or other not in graph or on < cutoff:
                    continue
                for a, b in ((pid, other), (other, pid)):
                    graph[a][b] = _latest(graph[a].get(b), on)
    return graph


def _network_connectivity(
    players: Mapping[str, Player], rating_format: RatingFormat, reference: date, cfg: Config
) -> tuple[dict[str, NetworkConnectivity], int]:
    """
    NetworkConnectivity of every player with a state in the format, and the largest group size.

    Every loop runs over sorted ids, every group is a union-find with its
    smallest id as root, and sets are read only for their sizes, so the
    result depends only on the stored states, the config, and the
    reference date.
    """
    cutoff = _days_before(reference, cfg.diversity_window_days)
    graph = _contact_graph(players, rating_format, cutoff)
    ids = sorted(graph)
    network = _UnionFind(ids)
    pools = _UnionFind(ids)
    minimum_days = cfg.connectivity_min_contact_days
    for u in ids:
        row = graph[u]
        for v in sorted(row):
            if v <= u:
                continue
            network.union(u, v)
            other = graph[v]
            shared = row.keys() & other.keys()
            if len(shared) < cfg.connectivity_min_shared_contacts:
                continue
            # Each of them reached the shared contacts on enough dates, so,
            # with a minimum of 2 or more, a visitor for one day (all of
            # their dates equal) never ties.
            if len({row[w] for w in shared}) >= minimum_days and (
                len({other[w] for w in shared}) >= minimum_days
            ):
                pools.union(u, v)

    group_sizes = network.sizes()
    largest = max(group_sizes.values(), default=0)
    members: dict[str, list[str]] = {}
    for pid in ids:
        members.setdefault(pools.find(pid), []).append(pid)
    out: dict[str, NetworkConnectivity] = {}
    for root in sorted(members):
        pool = members[root]
        in_pool = set(pool)
        boundary = {w for u in pool for w in graph[u] if w not in in_pool}
        near = in_pool | boundary
        fringe = {w for w in boundary if all(x in near for x in graph[w])}
        outside = boundary - fringe
        outside_days = {graph[u][w] for u in pool for w in graph[u] if w in outside}
        group_size = group_sizes[network.find(root)]
        in_main = 2 * group_size >= largest
        connected = (
            (len(pool) > 1 or bool(boundary))
            and in_main
            and (
                2 * (len(pool) + len(fringe)) > group_size
                or (
                    len(outside) >= cfg.connectivity_min_outside_players
                    and len(outside_days) >= minimum_days
                )
            )
        )
        for pid in pool:
            out[pid] = NetworkConnectivity(
                pool_size=len(pool),
                fringe_players=len(fringe),
                outside_players=len(outside),
                outside_days=len(outside_days),
                group_size=group_size,
                in_main_network=in_main,
                connected=connected,
            )
    return out, largest


@dataclass(frozen=True)
class PlayerSummary:
    """
    A player's published rating in one format, with reliability and provenance.

    reliability is always OPEN-earned. imported_reliability is the imported
    external reliability, rounded to 1 place, while it is carried, otherwise
    None. It is carried when it was captured on or before the reference date
    (player_summary's as_of, else the later of the engine's calculated_as_of
    and the player's last_played), is
    fresh, is within the carry match limit, and is higher than the earned
    value. It is shown separately and never replaces or converts into
    reliability.

    When Config.connectivity_reliability_cap is set (None, off, by
    default), reliability is at most the cap while connectivity.connected
    is False (see NetworkConnectivity). connectivity, the last field, is
    always reported.

    established is True when reliability (as displayed, rounded to 1 place)
    is at least Config.established_reliability_threshold and the state's
    verified_matches_played (counted tournament, league, and recreational
    verified matches) is at least Config.established_min_verified_matches.
    Self-reported matches never count toward that minimum. Imported
    reliability never counts toward established, and established is
    measured at the same reference date as reliability, so it lapses with
    inactivity unless the connectivity cap is set and connectivity
    changes. A recently
    captured imported rating can reach the threshold in fewer OPEN matches
    because of its lower starting RD; see the module docstring for counts.
    It is a label only and changes no rating. A rating that is not
    established is what external rating systems usually call provisional.
    That is different from OPEN's provisional live updates between replays
    and from the open_provisional initial source. Evaluation's eligibility
    rule (an imported baseline or at least burn_in_matches OPEN matches) is
    a separate rule from established.

    live_updates_since_recalculation counts the live updates since the last
    replay that are in the state's rating: the state's own
    live_updates_since_replay plus its inherited_live_updates, which for a
    category state derived from overall doubles is the parent's live count
    when the state was derived. It is 0 right after a replay.
    """

    player_id: str
    rating_format: RatingFormat
    rating: float  # rounded to 3 places
    reliability: float  # OPEN-earned reliability, 0-100
    imported_reliability: float | None  # carried imported reliability, 0-100
    established: bool  # reliability over the threshold and enough verified matches
    rd: float  # inflated to the reference date, rounded to 4 places
    matches_played: int
    last_played: date | None
    initial_rating: float
    initial_source: str
    initial_source_reliability: float | None
    initial_source_date: date | None
    calculated_as_of: date | None  # date of the last replay, None when never replayed
    live_updates_since_recalculation: int
    config_version: str
    account_linked: bool
    connectivity: NetworkConnectivity  # whether the pool connects to the main network


_FORMAT_VALUES = frozenset(fmt.value for fmt in RatingFormat)


def _zero_by_format() -> dict[RatingFormat, float]:
    return dict.fromkeys(RatingFormat, 0.0)


@dataclass(frozen=True)
class ReplayInfo:
    """
    Provenance of one replay: as_of date, config version and fingerprint, counts.

    net_rating_created is, per rating format, the rating the replay's
    counted updates added to that pool (negative when they removed it):
    the sum over every counted update of the unrounded rating changes of
    every player in it, a frozen player adding 0.0. A category match adds
    its category update to its own format and, when category_feeds_parent
    is on and its overall doubles update counts, that update to doubles.
    Each player moves by their own K, so it can be nonzero when K differs
    between the teams or a player is clamped or frozen, and colluding
    accounts can grow it on purpose. It is
    stored as a read-only mapping with a key for every RatingFormat in
    definition order (0.0 where nothing counted). Building a ReplayInfo
    copies the mapping given: a key may be a RatingFormat or its str
    value, a missing format gets 0.0, and a value that is not a mapping,
    an unknown key, or a format given twice raises SnapshotError (a
    ReplayInfo is built only by replay and by snapshot loading). Updates
    are added in ledger order, and a resume continues from the checkpoint's total, so
    a resume gives the same floats as a full replay. Live updates after
    the replay do not change it. The backend can add team_size *
    imbalance of each live MatchResult (and its parent) to follow them;
    that is approximate (imbalance is rounded to 5 places), and the next
    replay gives the exact total.
    """

    as_of: date
    config_version: str
    config_fingerprint: str
    matches_considered: int
    matches_counted: int
    net_rating_created: Mapping[RatingFormat, float] = field(default_factory=_zero_by_format)

    def __post_init__(self) -> None:
        given = self.net_rating_created
        name = "ReplayInfo.net_rating_created"
        if not isinstance(given, Mapping):
            raise SnapshotError(f"{name} must be a mapping")
        values: dict[RatingFormat, float] = {}
        for key, value in given.items():
            if isinstance(key, RatingFormat):
                fmt = key
            elif isinstance(key, str) and key in _FORMAT_VALUES:
                fmt = RatingFormat(key)
            else:
                raise SnapshotError(f"{name} has an unknown key: {key!r}")
            if fmt in values:
                raise SnapshotError(f"{name} repeats the format {fmt.value!r}")
            values[fmt] = value
        ordered = {fmt: values.get(fmt, 0.0) for fmt in RatingFormat}
        object.__setattr__(self, "net_rating_created", _FrozenMapping(ordered))


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------


def expected_point_share(team_a_rating: float, team_b_rating: float, scale: float) -> float:
    """Expected fraction of total points won by team A."""
    exponent = (team_b_rating - team_a_rating) / scale
    exponent = max(-12.0, min(12.0, exponent))
    return 1.0 / (1.0 + float(10.0**exponent))


def inflated_rd(state: RatingState, on: date, cfg: Config) -> float:
    """RD after inactivity since rd_as_of. The rating itself does not drift."""
    if state.rd_as_of is None or on <= state.rd_as_of:
        return state.rd
    days = (on - state.rd_as_of).days
    return min(cfg.rd_max, math.sqrt(state.rd**2 + (cfg.rd_growth_per_day**2) * days))


def player_k(state: RatingState, cfg: Config) -> float:
    fraction = (state.rd - cfg.rd_min) / (cfg.rd_max - cfg.rd_min)
    fraction = max(0.0, min(1.0, fraction))
    return cfg.k_min + (cfg.k_max - cfg.k_min) * fraction


def information_multiplier(match: Match, cfg: Config) -> float:
    length = cfg.game_length_weights[match.game_format.points_to]
    count = 1.0 + cfg.game_count_bonus * (len(match.games) - 1)
    return min(cfg.max_information_multiplier, length * count)


def _actual_point_share(match: Match, cfg: Config) -> tuple[float, bool]:
    points_a = sum(a for a, _ in match.games)
    points_b = sum(b for _, b in match.games)
    total = points_a + points_b
    if total <= 0:
        raise InvalidMatchError("Match must contain at least one point")
    games_a = sum(a > b for a, b in match.games)
    games_b = len(match.games) - games_a
    a_won = games_a > games_b
    point_share = points_a / total
    actual = (1.0 - cfg.win_weight) * point_share + cfg.win_weight * float(a_won)
    return actual, a_won


def _rolling_unique_count(interactions: dict[str, date], on: date, window_days: int) -> int:
    cutoff = _days_before(on, window_days)
    return sum(1 for last_seen in interactions.values() if last_seen >= cutoff)


def _latest(current: date | None, on: date) -> date:
    """The later of a stored date and a match date, so a date never moves back."""
    return on if current is None else max(current, on)


def _is_late(state: RatingState, on: date) -> bool:
    """
    True when a match on `on` is dated before what the state already reflects.

    That is its last match, or its rd_as_of when that is not just the
    imported capture date: a category rating copied from overall doubles
    keeps the doubles rd_as_of but has no last_played.
    """
    if state.last_played is not None and on < state.last_played:
        return True
    return (
        state.rd_as_of is not None
        and state.rd_as_of != state.initial_source_date
        and on < state.rd_as_of
    )


def _seen(interactions: dict[str, date], ids: Iterable[str], on: date) -> None:
    """Record each id as seen on `on`, keeping a later date already stored."""
    for other in ids:
        interactions[other] = _latest(interactions.get(other), on)


def reliability(state: RatingState, cfg: Config, as_of: date) -> float:
    """
    OPEN-earned reliability (0-100). Never a conversion of an external score.

    Combines rating certainty (from RD, inflated for inactivity) with rolling
    partner/opponent variety. as_of is the date inactivity and variety are
    measured at.
    """
    rd = inflated_rd(state, as_of, cfg)
    certainty = 1.0 - (rd - cfg.rd_min) / (cfg.rd_max - cfg.rd_min)
    variety_count = _rolling_unique_count(
        state.partners, as_of, cfg.diversity_window_days
    ) + _rolling_unique_count(state.opponents, as_of, cfg.diversity_window_days)
    variety = min(1.0, variety_count / cfg.diversity_target)
    score = 100.0 * certainty * (0.60 + 0.40 * variety)
    return round(max(0.0, min(100.0, score)), 1)


def _clone_state(state: RatingState) -> RatingState:
    return replace(
        state,
        partners=dict(state.partners),
        opponents=dict(state.opponents),
    )


def clone_player(player: Player) -> Player:
    return Player(
        id=player.id,
        ratings={fmt: _clone_state(state) for fmt, state in player.ratings.items()},
        external_ids=dict(player.external_ids),
        account_user_id=player.account_user_id,
    )


MatchupKey = tuple[RatingFormat, frozenset[frozenset[str]]]


def _matchup_key(match: Match) -> MatchupKey:
    return (
        match.rating_format,
        frozenset({frozenset(match.team_a), frozenset(match.team_b)}),
    )


# Scores below this are written as JSON numbers. It is under the lowest
# int to text digit limit Python allows (640 digits), so the key never
# depends on sys.set_int_max_str_digits.
_CONTENT_SCORE_DECIMAL_LIMIT = 10**600


def _content_score(score: int) -> int | str:
    """
    A validated game score in the content key's JSON form.

    int() folds an int subclass into a plain int. A score below 10**600 stays a JSON
    number; a larger one is hex text, which has no digit limit and never
    equals a number.
    """
    value = int(score)
    if value < _CONTENT_SCORE_DECIMAL_LIMIT:
        return value
    return hex(value)


def _content_key(match: Match) -> str:
    """
    SHA-256 hex digest of what makes two reports the same match.

    It uses played_on, the two teams as sets of players, and the game
    scores in order, read from the same side. Each team is a sorted list
    of ids, and the two teams are put in sorted order; when that swaps
    them, every game score is swapped too. Everything else is left out on
    purpose (match_id, match_type, rating_format, game_format, best_of,
    played_at, sequence, confirmed_by), since two reports of one match
    often disagree on labels. The digest is over JSON text, so it never
    depends on set order or the hash seed. Scores go through
    _content_score, so an int subclass equals its int and a huge score never
    hits the int to text digit limit.
    """
    team_a = sorted(match.team_a)
    team_b = sorted(match.team_b)
    games = [[_content_score(a), _content_score(b)] for a, b in match.games]
    if team_b < team_a:
        team_a, team_b = team_b, team_a
        games = [[score_b, score_a] for score_a, score_b in games]
    form = [match.played_on.isoformat(), team_a, team_b, games]
    return hashlib.sha256(_json_text(form).encode()).hexdigest()


def _is_finite_number(value: object) -> bool:
    """
    True for an int (not bool) or a finite float.

    Every int is finite. math.isfinite would overflow on a huge int, so ints
    skip it and the seed rules' bounds reject out-of-range values instead.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _is_plain_date(value: object) -> bool:
    """True for a date that is not a datetime (the two do not compare)."""
    return isinstance(value, date) and not isinstance(value, datetime)


def _seed_type_broken(state: RatingState) -> str | None:
    """The first seed field with the wrong type, or None."""
    for name in (
        "matches_played",
        "verified_matches_played",
        "live_updates_since_replay",
        "inherited_live_updates",
    ):
        value = getattr(state, name)
        if not isinstance(value, int) or isinstance(value, bool):
            return f"{name} must be an int"
    for name in ("partners", "opponents"):
        # Exactly dict: a subclass can report itself empty while holding items.
        if type(getattr(state, name)) is not dict:
            return f"{name} must be a dict"
    for name in ("rating", "rd", "initial_rating"):
        if not _is_finite_number(getattr(state, name)):
            return f"{name} must be a finite number"
    imported = state.initial_source_reliability
    if imported is not None and not _is_finite_number(imported):
        return "initial_source_reliability must be a finite number"
    for name in ("initial_source_date", "rd_as_of"):
        value = getattr(state, name)
        if value is not None and not _is_plain_date(value):
            return f"{name} must be a date"
    if not _is_plain_str(state.initial_source):
        return "initial_source must be a str"
    external_id = state.initial_external_player_id
    if external_id is not None and not _is_plain_str(external_id):
        return "initial_external_player_id must be a str or None"
    reference = state.initial_evidence_reference
    if reference is not None and not _is_plain_str(reference):
        return "initial_evidence_reference must be a str or None"
    return None


# Provenance names that start with this prefix belong to the engine: no
# external source may use one (seed_external_rating, attach_external_id, seed
# external_ids, and snapshot external_ids reject it), and a seed or snapshot
# state may hold one only in the shapes _reserved_source_broken checks.
_RESERVED_SOURCE_PREFIX = "open_"


def _reserved_source_broken(
    state: RatingState,
    fmt: RatingFormat,
    states: Mapping[RatingFormat, RatingState],
    cfg: Config,
) -> str | None:
    """
    A message when the state's reserved initial_source breaks a shape rule, or None.

    states holds the same player's states (the seed's ratings, or the
    snapshot player's), and cfg is the config they are checked under. A
    reserved initial_source carries no imported reliability, external player
    id, or evidence reference, and is one of:
    - "open_provisional" with no initial_source_date (any provisional
      rating, since it is RatingState's default initial_source);
    - in a category format, "open_parent:" plus that format's parent's
      value, as _new_state derives it: category_feeds_parent is on, the
      player has a parent state, the state is dated exactly when the parent
      state is (the dates may differ: a parent reseed refreshes an unplayed
      derived state, so the engine's own states share the parent's date,
      but a seed or snapshot made before that refresh existed, or built by
      a caller, can hold another date, and keeps it once played), an
      undated state has a played parent, and an unplayed state has an RD
      of at least category_seed_rd.
    This does not check the rating or the date against the parent state's,
    so a caller can still build a state in these shapes.
    """
    source = state.initial_source
    if not source.startswith(_RESERVED_SOURCE_PREFIX):
        return None
    prefix = (
        f"initial_source {source!r} uses the reserved prefix {_RESERVED_SOURCE_PREFIX} "
        "but is not provenance the engine writes: "
    )
    if (
        state.initial_source_reliability is not None
        or state.initial_external_player_id is not None
        or state.initial_evidence_reference is not None
    ):
        return prefix + (
            "it has an imported reliability, initial_external_player_id, or "
            "initial_evidence_reference"
        )
    if source == "open_provisional":
        if state.initial_source_date is not None:
            return prefix + "open_provisional must have no initial_source_date"
        return None
    parent = fmt.parent
    if parent is None or source != f"open_parent:{parent.value}":
        return prefix + "the engine writes no such initial_source in this format"
    if not cfg.category_feeds_parent:
        return prefix + "category_feeds_parent is off"
    base = states.get(parent)
    if base is None:
        return prefix + f"the player has no {parent.value} state"
    if (state.initial_source_date is None) != (base.initial_source_date is None):
        return prefix + (
            f"initial_source_date must be set exactly when the {parent.value} state's is"
        )
    if state.initial_source_date is None and base.matches_played == 0:
        return prefix + f"an undated derived state needs a played {parent.value} state"
    if state.matches_played == 0 and state.rd < cfg.category_seed_rd:
        return prefix + "rd must be at least category_seed_rd while the state is unplayed"
    return None


def _seed_rule_broken(state: RatingState, cfg: Config) -> str | None:
    """The first seed rule a state breaks, or None when it follows every rule."""
    wrong_type = _seed_type_broken(state)
    if wrong_type is not None:
        return wrong_type
    if not _is_id(state.initial_source):
        return "initial_source must not be blank"
    external_id = state.initial_external_player_id
    if external_id is not None and not _is_id(external_id):
        return "initial_external_player_id must not be blank"
    reference = state.initial_evidence_reference
    if reference is not None and not _is_id(reference):
        return "initial_evidence_reference must not be blank"
    if state.matches_played != 0:
        return "matches_played must be 0"
    if state.verified_matches_played != 0:
        return "verified_matches_played must be 0"
    if state.last_played is not None:
        return "last_played must be None"
    if state.partners:
        return "partners must be empty"
    if state.opponents:
        return "opponents must be empty"
    if state.live_updates_since_replay != 0:
        return "live_updates_since_replay must be 0"
    if state.inherited_live_updates != 0:
        return "inherited_live_updates must be 0"
    if not cfg.min_rating <= state.rating <= cfg.max_rating:
        return "rating must be between min_rating and max_rating"
    if not cfg.min_rating <= state.initial_rating <= cfg.max_rating:
        return "initial_rating must be between min_rating and max_rating"
    if state.initial_source_date is None:
        if state.rd != cfg.new_player_rd:
            return "rd must equal new_player_rd for a provisional seed"
        if state.rd_as_of is not None:
            return "rd_as_of must be None for a provisional seed"
        if state.initial_source_reliability is not None:
            return "initial_source_reliability must be None for a provisional seed"
        return None
    seed_rds = (cfg.external_seed_rd, max(cfg.external_seed_rd, cfg.category_seed_rd))
    if state.rd not in seed_rds:
        return "rd must equal external_seed_rd or the category seed RD"
    if state.rd_as_of != state.initial_source_date:
        return "rd_as_of must equal initial_source_date"
    imported = state.initial_source_reliability
    if imported is not None and not 0 <= imported <= 100:
        return "initial_source_reliability must be between 0 and 100"
    return None


def _provenance_linked(state: RatingState, external_ids: Mapping[str, str]) -> bool:
    """True when the state names no external id, or external_ids links it under its source."""
    external_id = state.initial_external_player_id
    return external_id is None or external_ids.get(state.initial_source) == external_id


def _check_seed_date(player: Player, latest: date, limit: str) -> None:
    """Raise InvalidSeedError for a validated seed state dated after latest."""
    for fmt in sorted(player.ratings):
        source_date = player.ratings[fmt].initial_source_date
        if source_date is not None and source_date > latest:
            raise InvalidSeedError(
                f"Seed for {player.id} in {fmt}: initial_source_date must not be after {limit}"
            )


@dataclass
class _HeldIdentities:
    """Accounts and (source, external id) pairs held by players, for seed checks."""

    accounts: set[str] = field(default_factory=set)
    external_ids: set[tuple[str, str]] = field(default_factory=set)

    def add(self, player: Player) -> None:
        """Record the player's account and external identities as held."""
        if player.account_user_id is not None:
            self.accounts.add(player.account_user_id)
        self.external_ids.update(player.external_ids.items())


def _check_seed_identity(player: object) -> None:
    """
    Raise InvalidSeedError unless the seed is exactly a Player with a
    non-blank plain str id (not a subclass).
    """
    # Exactly Player: a subclass can return a different id on a later read.
    if type(player) is not Player:
        raise InvalidSeedError(f"Seed must be a Player, not {type(player).__name__}")
    if not _is_plain_str(player.id):
        raise InvalidSeedError(f"Seed player id must be a str, not {type(player.id).__name__}")
    if not _is_id(player.id):
        raise InvalidSeedError("Seed player id must not be blank")


_SEED_NUMBER_FIELDS = (
    "rating",
    "rd",
    "initial_rating",
    "initial_source_reliability",
    "matches_played",
    "verified_matches_played",
    "live_updates_since_replay",
    "inherited_live_updates",
)


def _plain_seed(player: Player) -> Player:
    """
    A copy of a seed whose rating states hold plain int and float numbers.

    Only a ratings dict that is exactly a dict, and states that are exactly
    RatingState, are copied; anything else is left for _validate_seed to
    reject. The caller's player and states are not changed (issue #107).
    """
    if type(player.ratings) is not dict:
        return player
    ratings = {
        fmt: _plain_state(state) if type(state) is RatingState else state
        for fmt, state in player.ratings.items()
    }
    # Fields read from the class, not dataclasses.replace, which reads an
    # instance __dataclass_fields__ that the caller can set.
    copied: dict[str, object] = {f.name: getattr(player, f.name) for f in fields(Player)}
    copied["ratings"] = ratings
    return Player(**copied)  # type: ignore[arg-type]


def _plain_state(state: RatingState) -> RatingState:
    """A copy of a rating state with plain int and float number fields."""
    copied: dict[str, object] = {}
    for f in fields(RatingState):
        value = getattr(state, f.name)
        copied[f.name] = _plain_number(value) if f.name in _SEED_NUMBER_FIELDS else value
    return RatingState(**copied)  # type: ignore[arg-type]


def _validate_seed(player: Player, cfg: Config) -> None:
    """Raise InvalidSeedError unless the seed and every seed state follow the seed rules."""
    _check_seed_identity(player)
    # Exact dict and str: a subclass could hand the checks below, or
    # clone_player, values that differ from what it reports.
    if type(player.ratings) is not dict:
        raise InvalidSeedError(f"Seed for {player.id}: ratings must be a dict")
    if type(player.external_ids) is not dict:
        raise InvalidSeedError(f"Seed for {player.id}: external_ids must be a dict")
    if not all(_is_plain_str(k) and _is_plain_str(v) for k, v in player.external_ids.items()):
        raise InvalidSeedError(f"Seed for {player.id}: external_ids must map str to str")
    if player.account_user_id is not None and not _is_plain_str(player.account_user_id):
        raise InvalidSeedError(f"Seed for {player.id}: account_user_id must be a str or None")
    if player.account_user_id is not None and not _is_id(player.account_user_id):
        raise InvalidSeedError(f"Seed for {player.id}: account_user_id must not be blank")
    if not all(_is_id(k) and _is_id(v) for k, v in player.external_ids.items()):
        raise InvalidSeedError(
            f"Seed for {player.id}: external_ids must not have a blank source or id"
        )
    if any(k.startswith(_RESERVED_SOURCE_PREFIX) for k in player.external_ids):
        raise InvalidSeedError(
            f"Seed for {player.id}: external_ids source must not start with "
            f"{_RESERVED_SOURCE_PREFIX}"
        )
    for key in player.ratings:
        if not isinstance(key, RatingFormat):
            raise InvalidSeedError(
                f"Seed for {player.id}: ratings key {key!r} is not a RatingFormat"
            )
    for fmt in sorted(player.ratings):
        state = player.ratings[fmt]
        # Exactly RatingState: a subclass can return different values on
        # later reads, and _clone_state would keep the subclass.
        if type(state) is not RatingState:
            raise InvalidSeedError(
                f"Seed for {player.id} in {fmt}: rating state must be a RatingState"
            )
        rule = _seed_rule_broken(state, cfg)
        if rule is None:
            rule = _reserved_source_broken(state, fmt, player.ratings, cfg)
        if rule is not None:
            raise InvalidSeedError(f"Seed for {player.id} in {fmt}: {rule}")
        # The seed's own external_ids must hold its provenance pair, so the
        # identity uniqueness check in _add_seed covers it.
        if not _provenance_linked(state, player.external_ids):
            raise InvalidSeedError(
                f"Seed for {player.id} in {fmt}: initial_external_player_id must be linked "
                "in external_ids under initial_source"
            )


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

SNAPSHOT_VERSION = 8

_TOP_KEYS = frozenset(
    {
        "snapshot_version",
        "engine_version",
        "config_version",
        "config_fingerprint",
        "calculated_as_of",
        "replay_info",
        "players",
        "processed_match_ids",
        "reserved_match_ids",
        "self_reported_log",
        "content_keys",
        "possible_duplicates",
        "ledger_digests",
        "ledger_player_ids",
        "seed_sensitive_rejections",
        "resumable",
    }
)
_REJECTION_KEYS = frozenset({"rating_format", "players"})
_REPLAY_INFO_KEYS = frozenset(
    {
        "as_of",
        "config_version",
        "config_fingerprint",
        "matches_considered",
        "matches_counted",
        "net_rating_created",
    }
)
_PLAYER_KEYS = frozenset({"id", "account_user_id", "external_ids", "ratings"})
_STATE_KEYS = frozenset(
    {
        "rating",
        "rd",
        "matches_played",
        "verified_matches_played",
        "last_played",
        "rd_as_of",
        "initial_rating",
        "initial_source",
        "initial_source_date",
        "initial_source_reliability",
        "initial_external_player_id",
        "initial_evidence_reference",
        "live_updates_since_replay",
        "inherited_live_updates",
        "partners",
        "opponents",
    }
)
_LOG_KEYS = frozenset({"rating_format", "teams", "dates"})
_HEX = frozenset("0123456789abcdef")


@dataclass
class _Snapshot:
    """Engine state read from a validated snapshot."""

    players: dict[str, Player]
    processed_match_ids: set[str]
    reserved_match_ids: set[str]
    self_reported_log: dict[MatchupKey, list[date]]
    content_keys: dict[str, str]
    possible_duplicates: dict[str, str]
    calculated_as_of: date | None
    replay_info: ReplayInfo | None
    ledger_digests: dict[str, str]
    ledger_player_ids: set[str]
    seed_sensitive_rejections: dict[str, tuple[RatingFormat, tuple[str, ...]]]
    resumable: bool


def _iso(value: object) -> object:
    """A plain date as ISO text. Anything else is left for the validator to reject."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    return value


def _sorted_dict(items: Iterable[tuple[object, object]]) -> dict[object, object]:
    """A dict with keys inserted in sorted order (by their text), so output is stable."""
    return dict(sorted(items, key=lambda item: str(item[0])))


def _date_map_form(value: object) -> object:
    if not isinstance(value, dict):
        return value
    return _sorted_dict((key, _iso(on)) for key, on in value.items())


def _state_form(state: RatingState) -> dict[object, object]:
    items: list[tuple[object, object]] = [
        (name, getattr(state, name)) for name in sorted(_STATE_KEYS)
    ]
    return _sorted_dict(
        (name, _date_map_form(value) if name in ("partners", "opponents") else _iso(value))
        for name, value in items
    )


def _replay_info_value(name: str, value: object) -> object:
    """A ReplayInfo field in snapshot form: format values as keys, dates as ISO text."""
    if name == "net_rating_created" and isinstance(value, Mapping):
        return _sorted_dict(
            (key.value if isinstance(key, RatingFormat) else key, item)
            for key, item in value.items()
        )
    return _iso(value)


def _player_form(player: Player) -> dict[object, object]:
    ratings: object = player.ratings
    if isinstance(ratings, dict):
        ratings = _sorted_dict(
            (fmt.value if isinstance(fmt, RatingFormat) else fmt, _state_form(state))
            for fmt, state in ratings.items()
        )
    external_ids: object = player.external_ids
    if isinstance(external_ids, dict):
        external_ids = _sorted_dict(external_ids.items())
    return _sorted_dict(
        [
            ("account_user_id", player.account_user_id),
            ("external_ids", external_ids),
            ("id", player.id),
            ("ratings", ratings),
        ]
    )


def _log_form(log: dict[MatchupKey, list[date]]) -> list[dict[object, object]]:
    rows: list[tuple[str, list[list[str]], list[object]]] = []
    for (fmt, teams), dates in log.items():
        rows.append(
            (
                str(fmt),
                sorted(sorted(team) for team in teams),
                [_iso(on) for on in dates],
            )
        )
    rows.sort(key=lambda row: (row[0], row[1]))
    return [
        _sorted_dict([("dates", dates), ("rating_format", fmt), ("teams", teams)])
        for fmt, teams, dates in rows
    ]


def _snap_object(value: object, path: str, keys: frozenset[str]) -> Mapping[object, object]:
    """value as a mapping with exactly keys; SnapshotError otherwise."""
    if not isinstance(value, Mapping):
        raise SnapshotError(f"{path} must be an object")
    missing = sorted(keys - set(value))
    if missing:
        raise SnapshotError(f"{path}: missing key {missing[0]!r}")
    unknown = sorted(repr(key) for key in value if key not in keys)
    if unknown:
        raise SnapshotError(f"{path}: unknown key {unknown[0]}")
    return value


def _snap_list(value: object, path: str) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise SnapshotError(f"{path} must be a list")
    return value


def _snap_str(value: object, path: str) -> str:
    if not _is_plain_str(value):
        raise SnapshotError(f"{path} must be a str")
    return value


def _snap_opt_str(value: object, path: str) -> str | None:
    if value is not None and not _is_plain_str(value):
        raise SnapshotError(f"{path} must be a str or null")
    return value


def _snap_id(value: object, path: str) -> str:
    if not _is_id(value):
        raise SnapshotError(f"{path} must be a non-blank str")
    return value


def _snap_date(value: object, path: str) -> date:
    """A date written as exact YYYY-MM-DD text."""
    message = f"{path} must be a date in YYYY-MM-DD form"
    if not _is_plain_str(value):
        raise SnapshotError(message)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise SnapshotError(message) from None
    if parsed.isoformat() != value:
        raise SnapshotError(message)
    return parsed


def _snap_opt_date(value: object, path: str) -> date | None:
    return None if value is None else _snap_date(value, path)


def _snap_number(value: object, path: str) -> float:
    value = _plain_number(value)
    if isinstance(value, (int, float)) and _is_finite_number(value):
        return value
    raise SnapshotError(f"{path} must be a finite number (an int or float, not bool)")


# The largest count a snapshot holds. json.dumps writes any int up to it.
_MAX_COUNT = 2**63 - 1


def _snap_count(value: object, path: str) -> int:
    count = _plain_number(value)
    if not _is_whole_number(count) or not 0 <= count <= _MAX_COUNT:
        raise SnapshotError(f"{path} must be a nonnegative int no larger than 2**63 - 1")
    return count


def _snap_format(value: object, path: str) -> RatingFormat:
    if not _is_plain_str(value) or value not in {fmt.value for fmt in RatingFormat}:
        raise SnapshotError(f"{path} {value!r} is not a RatingFormat")
    return RatingFormat(value)


def _snap_date_map(value: object, path: str) -> dict[str, date]:
    if not isinstance(value, Mapping):
        raise SnapshotError(f"{path} must be an object")
    result: dict[str, date] = {}
    for key, on in value.items():
        if not _is_id(key):
            raise SnapshotError(f"{path} has an id that is not a non-blank str")
        result[key] = _snap_date(on, f"{path}[{key!r}]")
    return result


def _snap_state(value: object, path: str, player_id: str, cfg: Config) -> RatingState:
    """A rating state with the right types, checked against the engine's invariants."""
    state = _snap_state_fields(value, path, cfg)
    _check_state_invariants(state, path, player_id, cfg)
    return state


def _check_state_invariants(state: RatingState, path: str, player_id: str, cfg: Config) -> None:
    """
    Raise SnapshotError for a state the engine cannot produce.

    Only an engine-made state may load, because a loaded state sets the
    rating and the reliability shown for it: a low RD with no matches
    would show earned reliability nobody earned, and a played state
    relabeled unplayed would let an external seed replace an OPEN-earned
    rating.
    """
    if not cfg.min_rating <= state.initial_rating <= cfg.max_rating:
        raise SnapshotError(f"{path}.initial_rating must be between min_rating and max_rating")
    if (state.matches_played == 0) != (state.last_played is None):
        raise SnapshotError(f"{path}: matches_played must be 0 exactly when last_played is null")
    if state.live_updates_since_replay > state.matches_played:
        raise SnapshotError(f"{path}.live_updates_since_replay must not exceed matches_played")
    if state.verified_matches_played > state.matches_played:
        raise SnapshotError(f"{path}.verified_matches_played must not exceed matches_played")
    if player_id in state.partners:
        raise SnapshotError(f"{path}.partners: a player cannot be their own partner")
    if player_id in state.opponents:
        raise SnapshotError(f"{path}.opponents: a player cannot be their own opponent")
    if state.initial_source_reliability is not None and state.initial_source_date is None:
        raise SnapshotError(f"{path}.initial_source_reliability needs an initial_source_date")
    if state.matches_played:
        return
    if state.partners:
        raise SnapshotError(f"{path}: an unplayed state must have no partners")
    if state.opponents:
        raise SnapshotError(f"{path}: an unplayed state must have no opponents")
    # A seed or new state has one of these RDs; a category state derived
    # from its parent has max(parent RD, category_seed_rd). Only a counted
    # match lowers RD from there.
    if state.rd not in (cfg.new_player_rd, cfg.external_seed_rd) and (
        state.rd < cfg.category_seed_rd
    ):
        raise SnapshotError(
            f"{path}: rd of an unplayed state must be new_player_rd, external_seed_rd, "
            "or at least category_seed_rd"
        )


def _snap_state_fields(value: object, path: str, cfg: Config) -> RatingState:
    raw = _snap_object(value, path, _STATE_KEYS)
    rating = _snap_number(raw["rating"], f"{path}.rating")
    if not cfg.min_rating <= rating <= cfg.max_rating:
        raise SnapshotError(f"{path}.rating must be between min_rating and max_rating")
    rd = _snap_number(raw["rd"], f"{path}.rd")
    if not cfg.rd_min <= rd <= cfg.rd_max:
        raise SnapshotError(f"{path}.rd must be between rd_min and rd_max")
    imported = _plain_number(raw["initial_source_reliability"])
    if imported is not None:
        if not isinstance(imported, (int, float)) or not _is_finite_number(imported):
            raise SnapshotError(
                f"{path}.initial_source_reliability must be a finite number or null"
            )
        if not 0 <= imported <= 100:
            raise SnapshotError(f"{path}.initial_source_reliability must be between 0 and 100")
    return RatingState(
        rating=rating,
        rd=rd,
        matches_played=_snap_count(raw["matches_played"], f"{path}.matches_played"),
        verified_matches_played=_snap_count(
            raw["verified_matches_played"], f"{path}.verified_matches_played"
        ),
        last_played=_snap_opt_date(raw["last_played"], f"{path}.last_played"),
        rd_as_of=_snap_opt_date(raw["rd_as_of"], f"{path}.rd_as_of"),
        initial_rating=_snap_number(raw["initial_rating"], f"{path}.initial_rating"),
        initial_source=_snap_str(raw["initial_source"], f"{path}.initial_source"),
        initial_source_date=_snap_opt_date(
            raw["initial_source_date"], f"{path}.initial_source_date"
        ),
        initial_source_reliability=imported,
        initial_external_player_id=_snap_opt_str(
            raw["initial_external_player_id"], f"{path}.initial_external_player_id"
        ),
        initial_evidence_reference=_snap_opt_str(
            raw["initial_evidence_reference"], f"{path}.initial_evidence_reference"
        ),
        live_updates_since_replay=_snap_count(
            raw["live_updates_since_replay"], f"{path}.live_updates_since_replay"
        ),
        inherited_live_updates=_snap_count(
            raw["inherited_live_updates"], f"{path}.inherited_live_updates"
        ),
        partners=_snap_date_map(raw["partners"], f"{path}.partners"),
        opponents=_snap_date_map(raw["opponents"], f"{path}.opponents"),
    )


def _snap_player(value: object, path: str, cfg: Config) -> Player:
    raw = _snap_object(value, path, _PLAYER_KEYS)
    player_id = _snap_id(raw["id"], f"{path}.id")
    external_raw = raw["external_ids"]
    if not isinstance(external_raw, Mapping):
        raise SnapshotError(f"{path}.external_ids must map str to str")
    external_ids: dict[str, str] = {}
    for source, external_id in external_raw.items():
        if not _is_plain_str(source) or not _is_plain_str(external_id):
            raise SnapshotError(f"{path}.external_ids must map str to str")
        if not _is_id(source) or not _is_id(external_id):
            raise SnapshotError(f"{path}.external_ids must map non-blank str to non-blank str")
        if source.startswith(_RESERVED_SOURCE_PREFIX):
            raise SnapshotError(
                f"{path}.external_ids source must not start with {_RESERVED_SOURCE_PREFIX}"
            )
        external_ids[source] = external_id
    account_user_id = _snap_opt_str(raw["account_user_id"], f"{path}.account_user_id")
    if account_user_id is not None and not _is_id(account_user_id):
        raise SnapshotError(f"{path}.account_user_id must be null or a non-blank str")
    ratings = raw["ratings"]
    if not isinstance(ratings, Mapping):
        raise SnapshotError(f"{path}.ratings must be an object")
    states: dict[RatingFormat, RatingState] = {}
    for key, state in ratings.items():
        fmt = _snap_format(key, f"{path}.ratings key")
        states[fmt] = _snap_state(state, f"{path}.ratings.{fmt.value}", player_id, cfg)
    _check_inherited_live_updates(states, path, cfg)
    _check_provenance(states, external_ids, path, cfg)
    return Player(
        id=player_id,
        ratings=states,
        external_ids=external_ids,
        account_user_id=account_user_id,
    )


def _check_provenance(
    states: Mapping[RatingFormat, RatingState],
    external_ids: Mapping[str, str],
    path: str,
    cfg: Config,
) -> None:
    """
    Raise SnapshotError for a state's provenance the engine cannot produce.

    Every initial_source and initial_evidence_reference the engine writes is
    non-blank. Every entry point rejects an external source that starts with
    the reserved prefix, and a state's reserved initial_source must pass
    _reserved_source_broken under the snapshot's config, which checks the
    name, the import-only fields, and, for "open_parent:", the rules of
    how _new_state derives a category state. It does not check the rating
    or the date against the parent state's, so a state that passes can
    still be one a caller built or an older version imported. A state that names
    an external id comes in through seed_external_rating, which links the
    (source, id) pair to the same player, or through a seed (add_player,
    replay seeds, resume), which must already list the pair in its own
    external_ids. No engine method unlinks an id, so the pair stays in the
    player's external_ids. Every entry point requires the seed, its
    states, an external snapshot, and the ids and provenance strs checked
    here to be exact types (not subclasses), so a subclass of one of them
    cannot pass these provenance checks there and then load as different
    values. Other seed fields can still hold number subclasses, which this
    does not cover.
    """
    for fmt in sorted(states, key=lambda f: f.value):
        state = states[fmt]
        state_path = f"{path}.ratings.{fmt.value}"
        if not _is_id(state.initial_source):
            raise SnapshotError(f"{state_path}.initial_source must not be blank")
        external_id = state.initial_external_player_id
        if external_id is not None and not _is_id(external_id):
            raise SnapshotError(
                f"{state_path}.initial_external_player_id must be null or not blank"
            )
        reference = state.initial_evidence_reference
        if reference is not None and not _is_id(reference):
            raise SnapshotError(
                f"{state_path}.initial_evidence_reference must be null or not blank"
            )
        reserved = _reserved_source_broken(state, fmt, states, cfg)
        if reserved is not None:
            raise SnapshotError(f"{state_path}.{reserved}")
        if not _provenance_linked(state, external_ids):
            raise SnapshotError(
                f"{state_path}.initial_external_player_id must be linked in "
                f"{path}.external_ids under initial_source"
            )


def _check_inherited_live_updates(
    states: Mapping[RatingFormat, RatingState], path: str, cfg: Config
) -> None:
    """
    Raise SnapshotError for an inherited live count the engine cannot produce.

    Only a category state derived from its parent inherits a count, and the
    parent's own count only grows until the next replay, so it is never
    below what the category inherited. A derived state takes the parent's
    initial_source_date and no other provenance, and a parent with live
    updates has been played, so it can no longer be reseeded: a state with
    a nonzero count still has its parent's initial_source_date. Until it
    plays, it also keeps the RD it was derived with, max(parent RD,
    category_seed_rd), and the parent's rd_as_of at derivation, which a
    played parent has set and never moves back.
    """
    for fmt in sorted(states, key=lambda f: f.value):
        inherited = states[fmt].inherited_live_updates
        if not inherited:
            continue
        state_path = f"{path}.ratings.{fmt.value}.inherited_live_updates"
        parent = fmt.parent
        if (
            parent is None
            or not cfg.category_feeds_parent
            or states[fmt].initial_source != f"open_parent:{parent.value}"
        ):
            raise SnapshotError(
                f"{state_path} must be 0 unless the state is derived from its parent"
            )
        base = states.get(parent)
        if base is None:
            raise SnapshotError(f"{state_path} needs a {parent.value} state, which is missing")
        if inherited > base.live_updates_since_replay:
            raise SnapshotError(
                f"{state_path} must not exceed the parent's live_updates_since_replay"
            )
        state = states[fmt]
        if (
            state.initial_source_reliability is not None
            or state.initial_external_player_id is not None
            or state.initial_evidence_reference is not None
        ):
            raise SnapshotError(
                f"{path}.ratings.{fmt.value}: a state with inherited_live_updates must have "
                "no initial_source_reliability, initial_external_player_id, or "
                "initial_evidence_reference"
            )
        if state.initial_source_date != base.initial_source_date:
            raise SnapshotError(
                f"{path}.ratings.{fmt.value}.initial_source_date must equal the parent "
                "state's initial_source_date when inherited_live_updates is nonzero"
            )
        if state.matches_played:
            continue
        state_path = f"{path}.ratings.{fmt.value}"
        if state.rd < cfg.category_seed_rd:
            raise SnapshotError(
                f"{state_path}.rd must be at least category_seed_rd for an unplayed state "
                "with inherited_live_updates"
            )
        if state.rd_as_of is None:
            raise SnapshotError(
                f"{state_path}.rd_as_of must be set for an unplayed state with "
                "inherited_live_updates"
            )
        if base.rd_as_of is None or state.rd_as_of > base.rd_as_of:
            raise SnapshotError(
                f"{state_path}.rd_as_of must not be after the parent state's rd_as_of for an "
                "unplayed state with inherited_live_updates"
            )


def _check_resumable(snap: _Snapshot, paths: Mapping[str, str]) -> None:
    """
    Raise SnapshotError unless a resumable snapshot is consistent with its replay.

    A replay never records anything dated after its as_of, and it considers
    and counts exactly the rows behind its digests and processed ids. A
    live engine can hold later dates, so only resumable snapshots are held
    to this.
    """
    as_of = snap.calculated_as_of
    info = snap.replay_info
    if as_of is None or info is None:
        raise SnapshotError("snapshot.resumable must be false when calculated_as_of is null")

    def check(on: date | None, path: str) -> None:
        if on is not None and on > as_of:
            raise SnapshotError(f"{path} is after calculated_as_of in a resumable snapshot")

    for player_id, player in snap.players.items():
        played = any(state.matches_played for state in player.ratings.values())
        if played and player_id not in snap.ledger_player_ids:
            raise SnapshotError(
                f"{paths[player_id]}.id {player_id!r} has played matches but is not in "
                "ledger_player_ids in a resumable snapshot"
            )
        for fmt, state in player.ratings.items():
            path = f"{paths[player_id]}.ratings.{fmt.value}"
            check(state.initial_source_date, f"{path}.initial_source_date")
            check(state.last_played, f"{path}.last_played")
            check(state.rd_as_of, f"{path}.rd_as_of")
            if state.inherited_live_updates:
                raise SnapshotError(
                    f"{path}.inherited_live_updates must be 0 in a resumable snapshot"
                )
            for name, seen in (("partners", state.partners), ("opponents", state.opponents)):
                for other, on in seen.items():
                    check(on, f"{path}.{name}[{other!r}]")
    for index, dates in enumerate(snap.self_reported_log.values()):
        for position, on in enumerate(dates):
            check(on, f"snapshot.self_reported_log[{index}].dates[{position}]")
    for player_id, player in snap.players.items():
        for fmt in sorted(player.ratings, key=lambda f: f.value):
            state = player.ratings[fmt]
            source_date = state.initial_source_date
            if not state.matches_played or source_date is None:
                continue
            # A replay's seeds precede every row, and a row on or before a
            # state's initial_source_date freezes that state, so a moved
            # dated state was last played after its date.
            if state.last_played is None or state.last_played <= source_date:
                raise SnapshotError(
                    f"{paths[player_id]}.ratings.{fmt.value}.last_played must be after "
                    "initial_source_date in a resumable snapshot"
                )
            # Partners and opponents are written only on a move, so they
            # too are dated after the state's initial_source_date.
            path = f"{paths[player_id]}.ratings.{fmt.value}"
            for name, seen in (("partners", state.partners), ("opponents", state.opponents)):
                for other_id in sorted(seen):
                    if seen[other_id] <= source_date:
                        raise SnapshotError(
                            f"{path}.{name}[{other_id!r}] must be after initial_source_date "
                            "in a resumable snapshot"
                        )
    for player_id, player in snap.players.items():
        for fmt in sorted(player.ratings, key=lambda f: f.value):
            if player.ratings[fmt].live_updates_since_replay:
                raise SnapshotError(
                    f"{paths[player_id]}.ratings.{fmt.value}.live_updates_since_replay "
                    "must be 0 in a resumable snapshot"
                )
    if info.matches_considered != len(snap.ledger_digests):
        raise SnapshotError(
            "snapshot.replay_info.matches_considered must equal the number of "
            "ledger_digests in a resumable snapshot"
        )
    if info.matches_counted != len(snap.processed_match_ids):
        raise SnapshotError(
            "snapshot.replay_info.matches_counted must equal the number of "
            "processed_match_ids in a resumable snapshot"
        )
    undigested = sorted(snap.processed_match_ids - snap.ledger_digests.keys())
    if undigested:
        raise SnapshotError(
            f"snapshot.processed_match_ids names {undigested[0]!r}, which is not in "
            "ledger_digests in a resumable snapshot"
        )


def _snap_log_entry(value: object, path: str) -> tuple[MatchupKey, list[date]]:
    raw = _snap_object(value, path, _LOG_KEYS)
    fmt = _snap_format(raw["rating_format"], f"{path}.rating_format")
    teams = _snap_list(raw["teams"], f"{path}.teams")
    if len(teams) != 2:
        raise SnapshotError(f"{path}.teams must be two teams")
    members: list[frozenset[str]] = []
    for index, team in enumerate(teams):
        team_path = f"{path}.teams[{index}]"
        message = f"{team_path} must be a team of {fmt.team_size} player id(s)"
        if not isinstance(team, (list, tuple)) or len(team) != fmt.team_size:
            raise SnapshotError(message)
        if not all(_is_id(pid) for pid in team):
            raise SnapshotError(message)
        ids = frozenset(str(pid) for pid in team)
        if len(ids) != fmt.team_size:
            raise SnapshotError(message)
        members.append(ids)
    if members[0] & members[1]:
        raise SnapshotError(f"{path}.teams has a player in both teams")
    dates = raw["dates"]
    if not isinstance(dates, (list, tuple)) or not dates:
        raise SnapshotError(f"{path}.dates must be a non-empty list")
    return (
        (fmt, frozenset(members)),
        [_snap_date(on, f"{path}.dates[{index}]") for index, on in enumerate(dates)],
    )


def _snap_replay_info(
    value: object, calculated_as_of: date | None, version: str, fingerprint: str
) -> ReplayInfo | None:
    path = "snapshot.replay_info"
    if calculated_as_of is None:
        if value is not None:
            raise SnapshotError(f"{path} must be null when calculated_as_of is null")
        return None
    if value is None:
        raise SnapshotError(f"{path} is required when calculated_as_of is set")
    raw = _snap_object(value, path, _REPLAY_INFO_KEYS)
    if _snap_date(raw["as_of"], f"{path}.as_of") != calculated_as_of:
        raise SnapshotError(f"{path}.as_of must equal calculated_as_of")
    if raw["config_version"] != version:
        raise SnapshotError(f"{path}.config_version must equal config_version")
    if raw["config_fingerprint"] != fingerprint:
        raise SnapshotError(f"{path}.config_fingerprint must equal config_fingerprint")
    considered = _snap_count(raw["matches_considered"], f"{path}.matches_considered")
    counted = _snap_count(raw["matches_counted"], f"{path}.matches_counted")
    if counted > considered:
        raise SnapshotError(f"{path}.matches_counted must not exceed matches_considered")
    created_path = f"{path}.net_rating_created"
    created_raw = _snap_object(raw["net_rating_created"], created_path, _FORMAT_VALUES)
    created: dict[RatingFormat, float] = {}
    for fmt in RatingFormat:
        value = created_raw[fmt.value]
        # A float only: a resume must continue from the exact float a full
        # replay had, and an int would change its type.
        if type(value) is not float or not math.isfinite(value):
            raise SnapshotError(f"{created_path}.{fmt.value} must be a finite float")
        if value == 0.0 and math.copysign(1.0, value) < 0:
            # Totals start at 0.0 and every term adds 0.0, so replay never
            # writes -0.0, and a resume from it would write different text.
            raise SnapshotError(f"{created_path}.{fmt.value} must not be a negative zero")
        if counted == 0 and value != 0.0:
            raise SnapshotError(f"{created_path}.{fmt.value} must be 0.0 when matches_counted is 0")
        created[fmt] = value
    return ReplayInfo(calculated_as_of, version, fingerprint, considered, counted, created)


def _check_net_rating_created(info: ReplayInfo | None, players: Mapping[str, Player]) -> None:
    """
    Raise SnapshotError for a nonzero total in a format nobody has played.

    A counted update moves at least one player who is not frozen, and a
    played state is never removed or replaced, so a nonzero total needs a
    played state in its format.
    """
    if info is None:
        return
    for fmt in RatingFormat:
        if info.net_rating_created[fmt] == 0.0:
            continue
        if not any(
            fmt in player.ratings and player.ratings[fmt].matches_played
            for player in players.values()
        ):
            raise SnapshotError(
                f"snapshot.replay_info.net_rating_created.{fmt.value} must be 0.0 when no "
                f"player has a played {fmt.value} state"
            )


def _snap_content_keys(value: object, processed: set[str]) -> dict[str, str]:
    """
    Content keys read from a snapshot: digest to the match_id first counted with it.

    Each key is 64 lowercase hex characters, each id a processed match, and
    no id appears twice, since a match has one content key.
    """
    if not isinstance(value, Mapping):
        raise SnapshotError("snapshot.content_keys must be an object")
    keys: dict[str, str] = {}
    owners: set[str] = set()
    for key, match_id in value.items():
        if not _is_plain_str(key) or len(key) != 64 or not set(key) <= _HEX:
            raise SnapshotError(
                f"snapshot.content_keys has a key that is not 64 lowercase hex characters: {key!r}"
            )
        path = f"snapshot.content_keys[{key!r}]"
        match_id = _snap_id(match_id, path)
        if match_id not in processed:
            raise SnapshotError(f"{path} names {match_id!r}, which is not in processed_match_ids")
        if match_id in owners:
            raise SnapshotError(f"snapshot.content_keys maps more than one key to {match_id!r}")
        owners.add(match_id)
        keys[key] = match_id
    return keys


def _snap_possible_duplicates(
    value: object,
    twins: set[str],
    processed: set[str],
    reject: bool,
    considered: Mapping[str, str] | None,
) -> dict[str, str]:
    """
    Possible duplicates read from a snapshot: copy match_id to the match it repeats.

    Each id is a non-blank str, a copy never names itself, and each twin is
    a content_keys value (twins). A copy is a processed match id under the
    default config, which counts it, and is not one under
    reject_content_duplicates, which does not. A copy never registers its
    own content, so it is never a content_keys value. considered is the
    ledger digests of a resumable snapshot, else None: a replay records a
    copy only for a row it considered, so each copy must be one of them.
    """
    if not isinstance(value, Mapping):
        raise SnapshotError("snapshot.possible_duplicates must be an object")
    duplicates: dict[str, str] = {}
    for key, twin in value.items():
        if not _is_id(key):
            raise SnapshotError(
                "snapshot.possible_duplicates has a key that is not a non-blank str"
            )
        path = f"snapshot.possible_duplicates[{key!r}]"
        twin = _snap_id(twin, path)
        if twin == key:
            raise SnapshotError(f"{path} names the match itself")
        if twin not in twins:
            raise SnapshotError(f"{path} names {twin!r}, which is not a content_keys value")
        where = "snapshot.possible_duplicates has key"
        if reject and key in processed:
            raise SnapshotError(
                f"{where} {key!r}, which is in processed_match_ids; under "
                "reject_content_duplicates a possible duplicate is never counted"
            )
        if not reject and key not in processed:
            raise SnapshotError(
                f"{where} {key!r}, which is not in processed_match_ids; under the "
                "default config a possible duplicate is counted"
            )
        if key in twins:
            raise SnapshotError(f"{where} {key!r}, which is a content_keys value")
        if considered is not None and key not in considered:
            raise SnapshotError(f"{where} {key!r}, which is not in ledger_digests")
        duplicates[key] = twin
    return duplicates


def _baseline_date(player: Player | None, fmt: RatingFormat, cfg: Config) -> date | None:
    """
    The date on or before which a fmt match would freeze this player, or None.

    That is a stored fmt state's initial_source_date, or, with none
    stored, under category_feeds_parent in a category format, the parent
    state's: the state a category match starts from carries the parent's
    date (see RatingEngine._new_state).
    """
    if player is None:
        return None
    state = player.ratings.get(fmt)
    if state is not None:
        return state.initial_source_date
    parent = fmt.parent
    if parent is None or not cfg.category_feeds_parent:
        return None
    base = player.ratings.get(parent)
    return None if base is None else base.initial_source_date


def _has_dated_baseline(player: Player | None, fmt: RatingFormat, cfg: Config) -> bool:
    """True when a fmt match would freeze this player for a date on or before an import."""
    return _baseline_date(player, fmt, cfg) is not None


def _names_category_match(named: Sequence[str], players: Mapping[str, Player]) -> bool:
    """
    True when named could be the players of a counted category match.

    A counted match moves at least one player, and that player's played
    state in the match's category records one other player as a partner
    and the other two as opponents. Those maps are never pruned. Not every
    player needs a state there: a frozen player can be frozen through a
    category state derived from overall doubles that was never stored.
    """
    for pid in named:
        player = players[pid]
        others = set(named) - {pid}
        for fmt in RatingFormat:
            if fmt.parent is not RatingFormat.DOUBLES:
                continue
            state = player.ratings.get(fmt)
            if state is None or state.matches_played == 0:
                continue
            # The maps span every match the state played, so a named
            # player can be in both: try each one as the partner.
            for partner in sorted(others & set(state.partners)):
                if others - {partner} <= set(state.opponents):
                    return True
    return False


def _snap_seed_sensitive_rejections(
    value: object,
    players: Mapping[str, Player],
    processed: set[str],
    reserved: set[str],
    duplicates: Mapping[str, str],
    ledger_player_ids: set[str] | None,
    cfg: Config,
) -> dict[str, tuple[RatingFormat, tuple[str, ...]]]:
    """
    Seed-sensitive rejections read from a snapshot: match_id to (format, sorted player ids).

    record adds an entry only for an update rejected for
    BEFORE_IMPORTED_BASELINE, SINGLES_RATING_GAP, or DOUBLES_PARTNER_GAP,
    naming every player of the match. A match's own rejected update
    reserves its id. A counted match's rejected update is its overall
    doubles update under category_feeds_parent, and every player of a
    counted match has a player record (a frozen one already had a state).
    A possible duplicate is rejected before any rating is read, so a
    reserved one is never an entry. With no gap rule for the format, only
    BEFORE_IMPORTED_BASELINE can add an entry, which needs every player
    frozen, and the seed methods refuse to replace those states, so each
    named player still has a dated baseline there (_has_dated_baseline).
    ledger_player_ids is that set in a resumable snapshot, else None: a
    replay names only players in rows it considered.
    """
    if not isinstance(value, Mapping):
        raise SnapshotError("snapshot.seed_sensitive_rejections must be an object")
    rejections: dict[str, tuple[RatingFormat, tuple[str, ...]]] = {}
    for key, entry in value.items():
        if not _is_id(key):
            raise SnapshotError(
                "snapshot.seed_sensitive_rejections has a key that is not a non-blank str"
            )
        path = f"snapshot.seed_sensitive_rejections[{key!r}]"
        raw = _snap_object(entry, path, _REJECTION_KEYS)
        fmt_value = raw["rating_format"]
        if not _is_plain_str(fmt_value) or fmt_value not in _FORMAT_VALUES:
            raise SnapshotError(f"{path}.rating_format must be a RatingFormat value")
        fmt = RatingFormat(fmt_value)
        named = [
            _snap_id(item, f"{path}.players[{index}]")
            for index, item in enumerate(_snap_list(raw["players"], f"{path}.players"))
        ]
        if len(named) != 2 * fmt.team_size:
            raise SnapshotError(
                f"{path}.players must name {2 * fmt.team_size} players for {fmt.value}"
            )
        if named != sorted(set(named)):
            raise SnapshotError(f"{path}.players must be sorted with no repeats")
        if key in processed:
            if fmt is not RatingFormat.DOUBLES:
                raise SnapshotError(
                    f"{path} is a processed match id, so its rating_format must be doubles: "
                    "a counted match records only a rejected overall doubles update"
                )
            if not cfg.category_feeds_parent:
                raise SnapshotError(
                    f"{path} is a processed match id, which needs category_feeds_parent: "
                    "only then does a counted match send a doubles update"
                )
            for pid in named:
                if pid not in players:
                    raise SnapshotError(f"{path}.players names {pid!r}, which is not in players")
            if not _names_category_match(named, players):
                raise SnapshotError(
                    f"{path} names no player with a played category state that has one "
                    "other named player as a partner and the other two as opponents, as a "
                    "counted category match leaves"
                )
        elif key in reserved:
            if key in duplicates:
                raise SnapshotError(
                    f"{path} is a possible_duplicates key; a possible duplicate is rejected "
                    "before any rating is read"
                )
        else:
            raise SnapshotError(
                f"snapshot.seed_sensitive_rejections has key {key!r}, which is in neither "
                "processed_match_ids nor reserved_match_ids"
            )
        gap = (
            cfg.max_singles_rating_gap
            if fmt is RatingFormat.SINGLES
            else (cfg.max_doubles_partner_gap)
        )
        if gap is None:
            for pid in named:
                if not _has_dated_baseline(players.get(pid), fmt, cfg):
                    raise SnapshotError(
                        f"{path}.players names {pid!r}, which has no dated {fmt.value} "
                        f"baseline; with no gap rule for {fmt.value}, only "
                        "before_imported_baseline is recorded, and it freezes every player"
                    )
        if ledger_player_ids is not None:
            for pid in named:
                if pid not in ledger_player_ids:
                    raise SnapshotError(
                        f"{path}.players names {pid!r}, which is not in ledger_player_ids "
                        "in a resumable snapshot"
                    )
        rejections[key] = (fmt, tuple(named))
    return rejections


def _check_reserved(
    reserved: set[str],
    processed: set[str],
    duplicates: Mapping[str, str],
    reject: bool,
    digests: Mapping[str, str],
    considered: Mapping[str, str] | None,
) -> None:
    """
    Raise SnapshotError unless the reserved match ids fit the rest of the snapshot.

    record checks both sets before it adds an id to either, so no reserved
    id is processed. record reserves the id of every match it considers and
    does not count, so under reject_content_duplicates, which never counts a
    possible duplicate, every possible_duplicates key is reserved. digests
    is the snapshot's ledger digests: a replay counts or reserves every row
    it digests, so each digest id is processed or reserved in any snapshot.
    considered is the ledger digests of a resumable snapshot, else None: a replay
    reserves only a row it considered, so there each reserved id is a
    ledger_digests id.
    """
    where = "snapshot.reserved_match_ids names"
    for match_id in sorted(reserved):
        if match_id in processed:
            raise SnapshotError(f"{where} {match_id!r}, which is in processed_match_ids")
        if considered is not None and match_id not in considered:
            raise SnapshotError(f"{where} {match_id!r}, which is not in ledger_digests")
    if reject:
        for match_id in sorted(duplicates):
            if match_id not in reserved:
                raise SnapshotError(
                    f"snapshot.possible_duplicates has key {match_id!r}, which is not in "
                    "reserved_match_ids; under reject_content_duplicates a rejected "
                    "possible duplicate reserves its match_id"
                )
    # A replay counts or reserves every row it digests, and no id ever
    # leaves either set, live or not.
    for match_id in sorted(digests):
        if match_id not in processed and match_id not in reserved:
            raise SnapshotError(
                f"snapshot.ledger_digests names {match_id!r}, which is in neither "
                "processed_match_ids nor reserved_match_ids"
            )


def _snap_match_ids(value: object, path: str) -> set[str]:
    """A list of non-blank str match ids with no repeats, read as a set."""
    ids: set[str] = set()
    for index, item in enumerate(_snap_list(value, path)):
        item_path = f"{path}[{index}]"
        match_id = _snap_id(item, item_path)
        if match_id in ids:
            raise SnapshotError(f"{item_path}: duplicate match id {match_id!r}")
        ids.add(match_id)
    return ids


def _check_held_identities(players: Mapping[str, Player], paths: Mapping[str, str]) -> None:
    """
    Raise SnapshotError when two players hold the same account or external id.

    The seed and link methods give each identity to one player, so a
    snapshot names the later holder in player order.
    """
    held = _HeldIdentities()
    for player_id, player in players.items():
        path = paths[player_id]
        if player.account_user_id is not None and player.account_user_id in held.accounts:
            raise SnapshotError(f"{path}.account_user_id is held by another player")
        for source in sorted(player.external_ids):
            if (source, player.external_ids[source]) in held.external_ids:
                raise SnapshotError(f"{path}.external_ids[{source!r}] is held by another player")
        held.add(player)


def _check_named_players(players: Mapping[str, Player], paths: Mapping[str, str]) -> None:
    """
    Raise SnapshotError for a partner or opponent that is not a player in the snapshot.

    record creates every match player before it writes partners and
    opponents, and no method removes a player.
    """
    for player_id, player in players.items():
        for fmt in sorted(player.ratings, key=lambda f: f.value):
            state = player.ratings[fmt]
            path = f"{paths[player_id]}.ratings.{fmt.value}"
            for name, seen in (("partners", state.partners), ("opponents", state.opponents)):
                for other in sorted(seen):
                    if other not in players:
                        raise SnapshotError(
                            f"{path}.{name} names {other!r}, which is not in players"
                        )


def _check_log_players(
    players: Mapping[str, Player],
    log: Sequence[tuple[MatchupKey, list[date]]],
    cfg: Config,
) -> None:
    """
    Raise SnapshotError for a log entry its named players' states rule out.

    record appends a date to the log, under the match's own format, only
    when that format's update counts. In that update a named player is
    frozen exactly when the state it starts from has an
    initial_source_date on or after the date: the stored state in the
    format, or, with none stored, under category_feeds_parent in a
    category format, the one derived from the stored doubles state, which
    carries its date (_baseline_date). Every other named player moves, and
    at least one does, or the update would not count. A moved player's
    stored state in the format is played and records every teammate in
    partners and every player of the other team in opponents, each dated
    on or after the match (_seen keeps the latest date). Once a counted
    match names a player, that baseline does not change and those link
    dates never move back (a later match can move them later): a played
    state is never reseeded, and a frozen player's baseline is found
    through the movers' links (RatingEngine._in_counted_match). So
    for an entry's latest date, every named player has a baseline on or
    after it or a played state last played on or after it with those
    links, and some named player has
    no such baseline. Entries are checked in snapshot order, and within an
    entry every named player's state first, then the movers' links, each
    in sorted order, so the first error is the same for the same snapshot.
    """
    for index, ((fmt, teams), dates) in enumerate(log):
        last = max(dates)
        on = last.isoformat()
        where = f"snapshot.self_reported_log[{index}].teams"
        movers: list[tuple[str, RatingState]] = []
        for pid in sorted(pid for team in teams for pid in team):
            player = players[pid]
            baseline = _baseline_date(player, fmt, cfg)
            if baseline is not None and baseline >= last:
                continue
            state = player.ratings.get(fmt)
            # A stored state has a last_played exactly when it is played.
            if state is None or state.last_played is None or state.last_played < last:
                raise SnapshotError(
                    f"{where} names {pid!r}, who has no {fmt.value} state that a match on "
                    f"{on} could have moved or frozen"
                )
            movers.append((pid, state))
        for pid, state in movers:
            own = next(team for team in teams if pid in team)
            other = next(team for team in teams if pid not in team)
            for role, seen, ids in (
                ("a partner", state.partners, own - {pid}),
                ("an opponent", state.opponents, other),
            ):
                for linked in sorted(ids):
                    recorded = seen.get(linked)
                    if recorded is None or recorded < last:
                        raise SnapshotError(
                            f"{where} names {pid!r}, whose {fmt.value} state does not record "
                            f"{linked!r} as {role} on or after {on}"
                        )
        if not movers:
            raise SnapshotError(f"{where} names no player who moved in the match on {on}")


def _check_verified_against_log(
    players: Mapping[str, Player],
    paths: Mapping[str, str],
    log: Mapping[MatchupKey, list[date]],
    cfg: Config,
    resumable: bool,
) -> None:
    """
    Raise SnapshotError for a verified_matches_played the self-reported log rules out.

    record appends one date to the log, under the match's own format, for
    every counted self-reported match, and the log is never pruned. So
    matches_played - verified_matches_played (the self-reported updates
    that moved a state) is at most the logged matches that could move it:
    those in its format, plus, for doubles under category_feeds_parent,
    those in the category formats. A frozen player is logged but not
    moved, so that is only an upper bound. A state with no
    initial_source_date is never frozen and, once logged, never reseeded.
    For a singles or category state, or for doubles with
    category_feeds_parent off, its count then equals the logged matches
    in its format. For doubles under category_feeds_parent with
    max_doubles_partner_gap unset, a counted category match's doubles
    update is rejected only when every player is frozen in doubles, so
    the count equals the logged doubles and category matches (#84). With
    max_doubles_partner_gap set, the gap can also reject that doubles
    update, so the doubles count is only at least the doubles ones and at
    most the upper bound. A resumable snapshot is exactly a replay, whose
    seeds precede every row, so a logged match dated after a state's
    initial_source_date moved that state: the same rules then hold
    counting only those dates. A snapshot that is not resumable does not
    record when each seed was set relative to the logged matches, so for
    a state with an initial_source_date there the check keeps only the
    upper bound.

    A snapshot edited in a second field as well is held to more rules,
    checked elsewhere: the players a log entry names match the movers'
    partners and opponents, or were frozen, in the entry's latest match
    (_check_log_players), and in a resumable snapshot a played dated state
    was last played, and met every partner and opponent, after its
    initial_source_date (_check_resumable). Some edits to several fields
    still load, for example: in a snapshot that is not resumable, a played
    state given an initial_source_date before every logged date that moved
    it, with verified_matches_played raised to match, which cannot be told
    apart from a live import followed by those matches (it needs an
    external initial_source or an "open_parent:" one dated with its
    parent, since the reserved-name rules reject a dated
    "open_provisional" state); the dated-state edits _check_resumable
    rejects, in a snapshot that is not resumable; in a resumable
    snapshot, a state dated on or after an earlier match that moved it,
    with verified_matches_played raised to match, when its last_played
    and every partner and opponent date are still after the new date (for
    example, because it met the same partners and opponents again later),
    since those maps keep only the latest date; a
    log name swapped to a player who later played in that format with the
    same teammates and opponents, on or after the entry's latest date,
    with the verified counts adjusted to match; a whole log entry
    deleted with the named players' verified counts raised, since the
    snapshot does not tie a match id to its match type; and edits that
    also rewrite the partner and opponent names or dates to match.
    """
    logged: dict[tuple[str, RatingFormat], list[date]] = {}
    for (fmt, teams), dates in log.items():
        for team in teams:
            for pid in team:
                logged.setdefault((pid, fmt), []).extend(dates)
    categories = [f for f in RatingFormat if f.parent is RatingFormat.DOUBLES]

    def count(pid: str, fmt: RatingFormat, after: date | None) -> int:
        dates = logged.get((pid, fmt), [])
        return len(dates) if after is None else sum(1 for on in dates if on > after)

    for player_id, player in players.items():
        for fmt in sorted(player.ratings, key=lambda f: f.value):
            state = player.ratings[fmt]
            path = f"{paths[player_id]}.ratings.{fmt.value}"
            unverified = state.matches_played - state.verified_matches_played
            exact = state.initial_source_date is None or resumable
            after = state.initial_source_date if exact else None
            own = count(player_id, fmt, after)
            prefix = f"{path}: matches_played - verified_matches_played ({unverified})"
            if exact and fmt is not RatingFormat.DOUBLES:
                if unverified != own:
                    raise SnapshotError(
                        f"{prefix} must equal the self-reported {fmt.value} matches logged for "
                        f"this player ({own})"
                    )
                continue
            feeds = fmt is RatingFormat.DOUBLES and cfg.category_feeds_parent
            upper = own + (sum(count(player_id, c, after) for c in categories) if feeds else 0)
            what = "doubles and category" if feeds else fmt.value
            if unverified > upper:
                raise SnapshotError(
                    f"{prefix} must not exceed the self-reported {what} matches logged for "
                    f"this player ({upper})"
                )
            if exact and unverified < own:
                raise SnapshotError(
                    f"{prefix} must be at least the self-reported doubles matches logged "
                    f"for this player ({own})"
                )
            if exact and feeds and cfg.max_doubles_partner_gap is None and unverified != upper:
                raise SnapshotError(
                    f"{prefix} must equal the self-reported doubles and category matches "
                    f"logged for this player ({upper})"
                )


def _read_snapshot(snapshot: object, cfg: Config) -> _Snapshot:
    """Validate a snapshot against cfg and read it. Raises SnapshotError naming the bad value."""
    if not isinstance(snapshot, Mapping):
        raise SnapshotError("snapshot must be an object")
    if "snapshot_version" in snapshot:
        version = snapshot["snapshot_version"]
        if type(version) is not int or version != SNAPSHOT_VERSION:
            raise SnapshotError(
                f"snapshot.snapshot_version must be {SNAPSHOT_VERSION}, not {version!r}"
            )
    raw = _snap_object(snapshot, "snapshot", _TOP_KEYS)
    _snap_str(raw["engine_version"], "snapshot.engine_version")
    config_version = _snap_str(raw["config_version"], "snapshot.config_version")
    if config_version != cfg.version:
        raise SnapshotError(
            f"snapshot.config_version {config_version!r} does not match the Config "
            f"({cfg.version!r})"
        )
    fingerprint = _snap_str(raw["config_fingerprint"], "snapshot.config_fingerprint")
    if fingerprint != cfg.fingerprint():
        raise SnapshotError(
            f"snapshot.config_fingerprint {fingerprint!r} does not match the Config "
            f"({cfg.fingerprint()!r})"
        )
    calculated_as_of = _snap_opt_date(raw["calculated_as_of"], "snapshot.calculated_as_of")
    replay_info = _snap_replay_info(
        raw["replay_info"], calculated_as_of, config_version, fingerprint
    )

    players: dict[str, Player] = {}
    player_paths: dict[str, str] = {}
    for index, value in enumerate(_snap_list(raw["players"], "snapshot.players")):
        path = f"snapshot.players[{index}]"
        player = _snap_player(value, path, cfg)
        if player.id in players:
            raise SnapshotError(f"{path}: duplicate player id {player.id!r}")
        players[player.id] = player
        player_paths[player.id] = path
    _check_held_identities(players, player_paths)
    _check_named_players(players, player_paths)
    _check_net_rating_created(replay_info, players)

    processed = _snap_match_ids(raw["processed_match_ids"], "snapshot.processed_match_ids")
    reserved = _snap_match_ids(raw["reserved_match_ids"], "snapshot.reserved_match_ids")

    log: dict[MatchupKey, list[date]] = {}
    entries = _snap_list(raw["self_reported_log"], "snapshot.self_reported_log")
    for index, value in enumerate(entries):
        path = f"snapshot.self_reported_log[{index}]"
        key, dates = _snap_log_entry(value, path)
        if key in log:
            raise SnapshotError(f"{path} repeats a matchup")
        for pid in sorted(pid for team in key[1] for pid in team):
            if pid not in players:
                raise SnapshotError(f"{path}.teams names {pid!r}, which is not in players")
        log[key] = dates
    _check_log_players(players, list(log.items()), cfg)

    content_keys = _snap_content_keys(raw["content_keys"], processed)
    resumable = raw["resumable"]
    if not isinstance(resumable, bool):
        raise SnapshotError("snapshot.resumable must be a bool")
    _check_verified_against_log(players, player_paths, log, cfg, resumable)
    digests_raw = raw["ledger_digests"]
    if not isinstance(digests_raw, Mapping):
        raise SnapshotError("snapshot.ledger_digests must be an object")
    digests: dict[str, str] = {}
    for key, digest in digests_raw.items():
        if not _is_id(key):
            raise SnapshotError("snapshot.ledger_digests has an id that is not a non-blank str")
        if not _is_plain_str(digest) or len(digest) != 64 or not set(digest) <= _HEX:
            raise SnapshotError(
                f"snapshot.ledger_digests[{key!r}] must be 64 lowercase hex characters"
            )
        digests[key] = digest

    ledger_player_ids: set[str] = set()
    named = _snap_list(raw["ledger_player_ids"], "snapshot.ledger_player_ids")
    for index, value in enumerate(named):
        path = f"snapshot.ledger_player_ids[{index}]"
        player_id = _snap_id(value, path)
        if player_id in ledger_player_ids:
            raise SnapshotError(f"{path}: duplicate player id {player_id!r}")
        ledger_player_ids.add(player_id)

    possible_duplicates = _snap_possible_duplicates(
        raw["possible_duplicates"],
        set(content_keys.values()),
        processed,
        cfg.reject_content_duplicates,
        digests if resumable else None,
    )
    _check_reserved(
        reserved,
        processed,
        possible_duplicates,
        cfg.reject_content_duplicates,
        digests,
        digests if resumable else None,
    )

    seed_sensitive = _snap_seed_sensitive_rejections(
        raw["seed_sensitive_rejections"],
        players,
        processed,
        reserved,
        possible_duplicates,
        ledger_player_ids if resumable else None,
        cfg,
    )

    snap = _Snapshot(
        players,
        processed,
        reserved,
        log,
        content_keys,
        possible_duplicates,
        calculated_as_of,
        replay_info,
        digests,
        ledger_player_ids,
        seed_sensitive,
        resumable,
    )
    if resumable:
        _check_resumable(snap, player_paths)
    return snap


# ---------------------------------------------------------------------------
# Rating engine
# ---------------------------------------------------------------------------


class RatingEngine:
    """
    Holds players and processed matches, and records matches one at a time.

    Ratings recorded live are provisional. Use replay() for authoritative ratings.
    """

    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config()
        self.players: dict[str, Player] = {}
        self.processed_match_ids: set[str] = set()
        # match_id of each match record considered and did not count. A later
        # record with one of these ids that does not raise is a duplicate,
        # as in replay, where a second row with the id is a conflict or
        # collapses into the first.
        # Never in processed_match_ids.
        self._reserved_match_ids: set[str] = set()
        self.self_reported_log: dict[MatchupKey, list[date]] = {}
        self.calculated_as_of: date | None = None
        self.replay_info: ReplayInfo | None = None
        # Content key (see _content_key) of each counted match, to the
        # match_id of the first one counted with it. Never overwritten.
        self._content_keys: dict[str, str] = {}
        # match_id of each match flagged or rejected as a possible
        # duplicate, to the match_id it repeats (its possible_duplicate_of).
        # An entry never clears: its match_id is processed or reserved.
        self.possible_duplicates: dict[str, str] = {}
        # Digest of each ledger row a replay considered, by match_id. Only
        # replay fills it; resume compares rows against it.
        self._ledger_digests: dict[str, str] = {}
        # Every player id named in a row a replay considered, counted or
        # not. Resume refuses a seed for one of them.
        self._ledger_player_ids: set[str] = set()
        # match_id of each update record rejected for a reason a seed can
        # change (_SEED_SENSITIVE_REASONS), to the rating format of that
        # update and the sorted ids of the match's players: a match's own
        # update (its id is reserved), or the overall doubles update of a
        # counted category match (its id is processed). The seed methods
        # refuse a seed that could change one of these. Never cleared.
        self._seed_sensitive_rejections: dict[str, tuple[RatingFormat, tuple[str, ...]]] = {}
        # True only while the state is exactly what replay produced, so a
        # snapshot of it can be resumed from. Replay sets it at the end.
        self._resumable = False
        # NetworkConnectivity by (format, reference date), with the main
        # network size: derived from the rating states, never engine state,
        # so it is not in the snapshot. _changed clears it.
        self._connectivity: dict[
            tuple[RatingFormat, date], tuple[dict[str, NetworkConnectivity], int]
        ] = {}

    # -- state changes ---------------------------------------------------------
    #
    # Every change to engine state outside replay goes through these three
    # methods, which clear the resumable mark. A new method that changes a
    # player record, a rating state, or an identity must use them too, or a
    # snapshot taken after it could resume to a result a full replay would
    # not give.

    def _changed(self) -> None:
        """Mark the engine as no longer exactly what a replay produced."""
        self._resumable = False
        self._connectivity.clear()

    def _put_player(self, player: Player) -> None:
        """Store a new player record."""
        self._changed()
        self.players[player.id] = player

    def _put_state(self, player: Player, rating_format: RatingFormat, state: RatingState) -> None:
        """Store a player's rating state in one format."""
        self._changed()
        player.ratings[rating_format] = state

    # -- snapshots -------------------------------------------------------------

    def to_snapshot(self) -> dict[str, object]:
        """
        The engine's whole state as JSON-safe data (schema version 8).

        json.dumps(snapshot, allow_nan=False) works with no custom encoder,
        and RatingEngine.from_snapshot rebuilds the engine from it, or from
        json.loads of its text. Every dict is emitted with sorted keys and
        every list in a fixed order, so the same state always gives the same
        text. Numbers keep their type: an int stays an int.

        Keys: snapshot_version (8), engine_version (informational),
        config_version and config_fingerprint (of this engine's Config),
        calculated_as_of (ISO date or null), replay_info (null, or its
        fields with as_of as an ISO date and net_rating_created as format
        value to float), players (sorted by id: id,
        account_user_id, external_ids, and ratings by format value, each
        state with every RatingState field, dates as ISO text, and partners
        and opponents as id to ISO date), processed_match_ids (sorted),
        reserved_match_ids (sorted ids of matches record considered and did
        not count, for any reason other than DUPLICATE_MATCH_ID; a later
        record call for a match with one of these ids that does not raise
        returns DUPLICATE_MATCH_ID),
        self_reported_log (sorted by format then teams: rating_format,
        teams as two sorted id lists, and dates in stored order),
        content_keys (the content key digest of each counted match, see
        MatchResult.possible_duplicate_of, to the match_id first counted
        with it, sorted by digest), possible_duplicates (sorted by
        match_id: each match flagged or rejected as a possible duplicate,
        to the match_id it repeats; an entry never clears),
        ledger_digests (match_id to the SHA-256 digest of every row any
        replay behind this engine considered, cumulative across resumes),
        ledger_player_ids (sorted ids of every player named in those rows,
        counted or not), seed_sensitive_rejections (sorted by match_id:
        each update record rejected for BEFORE_IMPORTED_BASELINE,
        SINGLES_RATING_GAP, or DOUBLES_PARTNER_GAP, live or in a replay,
        as players, the sorted ids of the match's players, and
        rating_format, the format of the rejected update: the match's own,
        or overall doubles for the doubles update of a counted category
        match; the seed methods refuse a seed that could change one, and
        an entry never clears), and resumable (true only while the engine
        is exactly what a replay produced). The README lists the schema in
        full.

        To use the snapshot as a checkpoint, take it right after replay,
        before any other call on the engine. Every engine method that
        changes state (creating a player or rating state, a seed, an
        identity link, a live match) sets resumable to false, and resume refuses it.
        Changing objects the engine returns (players, rating states,
        processed_match_ids and the like) directly is not tracked, and
        makes the snapshot unsafe to resume. A stored snapshot sets
        every rating when loaded, so protect it from writes as you protect
        the ratings themselves: from_snapshot checks its shape and the
        engine's invariants, not that it is authentic.

        The snapshot is checked with the rules from_snapshot applies, so
        state that could not load back (a value of the wrong type, a
        rating out of bounds) raises SnapshotError naming the field
        instead of being returned.
        """
        info = self.replay_info
        replay_info: object = None
        if info is not None:
            replay_info = _sorted_dict(
                (f.name, _replay_info_value(f.name, getattr(info, f.name))) for f in fields(info)
            )
        snapshot: dict[str, object] = {
            "calculated_as_of": _iso(self.calculated_as_of),
            "config_fingerprint": self.cfg.fingerprint(),
            "config_version": self.cfg.version,
            "content_keys": _sorted_dict(self._content_keys.items()),
            "engine_version": __version__,
            "ledger_digests": _sorted_dict(self._ledger_digests.items()),
            "ledger_player_ids": sorted(self._ledger_player_ids, key=str),
            "players": [
                _player_form(player)
                for player in sorted(self.players.values(), key=lambda p: str(p.id))
            ],
            "possible_duplicates": _sorted_dict(self.possible_duplicates.items()),
            "processed_match_ids": sorted(self.processed_match_ids, key=str),
            "replay_info": replay_info,
            "reserved_match_ids": sorted(self._reserved_match_ids, key=str),
            "resumable": self._resumable,
            "seed_sensitive_rejections": _sorted_dict(
                (match_id, {"players": list(players), "rating_format": fmt.value})
                for match_id, (fmt, players) in self._seed_sensitive_rejections.items()
            ),
            "self_reported_log": _log_form(self.self_reported_log),
            "snapshot_version": SNAPSHOT_VERSION,
        }
        _read_snapshot(snapshot, self.cfg)
        return snapshot

    @classmethod
    def from_snapshot(
        cls, snapshot: Mapping[str, object], cfg: Config | None = None
    ) -> RatingEngine:
        """
        Rebuild an engine from to_snapshot() output, or json.loads of its text.

        cfg must be the Config the snapshot was taken under (default
        Config()): its version and fingerprint must equal the snapshot's.
        The whole snapshot is validated before anything is built: exactly
        the schema's keys at every level, snapshot_version 8, dates as
        YYYY-MM-DD text, non-blank str ids with no repeated player or match
        id (ids, external_ids sources and ids, and provenance strs exactly
        str, not a subclass), RatingFormat values as format keys, ratings
        within min_rating and max_rating, RD within rd_min and rd_max, an imported
        reliability between 0 and 100, nonnegative int counts no larger
        than 2**63 - 1 (an int or float subclass in a state's numbers or
        the replay counts is checked and stored as the plain int or float
        of its own value), replay_info present exactly when calculated_as_of
        is and consistent with it (its net_rating_created a finite float,
        not a negative zero, for every RatingFormat, 0.0 for every format when matches_counted
        is 0 and for a format in which no player has a played state),
        self-reported matchups of two disjoint
        teams of the format's size, content keys and ledger digests of 64
        lowercase hex characters, content keys that each name a different
        processed match id, possible duplicates that each name another
        match that is a content_keys value and whose own id is not one, a
        possible duplicate id that is a processed match id under the
        default config and is not one under reject_content_duplicates (and,
        in a resumable snapshot, is a ledger_digests id), reserved match
        ids with no repeats and none in processed_match_ids (and, in a
        resumable snapshot, each a ledger_digests id), every
        possible_duplicates key a reserved id under
        reject_content_duplicates, every ledger_digests id processed or
        reserved, and a bool resumable that is false without a replay.
        Each seed_sensitive_rejections entry has a RatingFormat value as
        rating_format and, as players, exactly twice that format's team
        size of non-blank str ids, sorted with no repeats. Its match_id is
        a reserved id that is not a possible_duplicates key, or a processed
        id with rating_format doubles under category_feeds_parent whose
        every named player is in players, one of them with a played
        category state that has one other named player as a partner and
        the other two as opponents. With no gap rule for its format
        (max_singles_rating_gap for singles, max_doubles_partner_gap for
        the others), every named player has a state with an
        initial_source_date there or, with none stored, under
        category_feeds_parent in a category format, an overall doubles
        state with one. In a resumable snapshot every named
        player is in ledger_player_ids.
        Every state must be one the engine can produce: initial_rating
        within the rating bounds; an unplayed state with RD new_player_rd,
        external_seed_rd, or at least category_seed_rd, and no partners,
        opponents, or live updates; matches_played 0 exactly when
        last_played is null; live updates and verified matches no more
        than matches played; no
        player their own partner or opponent; an imported reliability only
        with an initial_source_date; a nonzero inherited_live_updates only
        on a category state derived from its parent (category_feeds_parent
        on, initial_source "open_parent:" plus the parent's value) with a
        parent state, no more than the parent state's live updates, the
        parent's initial_source_date, and no imported reliability, external
        player id, or evidence reference; while such a state is unplayed,
        also an RD of at least category_seed_rd and an rd_as_of that is set
        and no later than the parent state's. Every partner, opponent, and
        self-reported team member is a player in the snapshot. Each state's
        matches_played - verified_matches_played (its self-reported updates)
        is at most the self-reported log dates in its format that name the
        player, plus, for doubles under category_feeds_parent, those in the
        category formats. For a state with no initial_source_date, and for
        every state of a resumable snapshot counting only log dates after
        its initial_source_date, an overall doubles count under
        category_feeds_parent with max_doubles_partner_gap set is at least
        the logged doubles matches, since the gap can reject a category
        match's doubles update; every other such count equals the logged
        matches in its format, plus, for doubles under
        category_feeds_parent, the logged category matches. A snapshot
        that is not resumable does not record when each seed was set, so
        for a state with an initial_source_date there only the upper bound
        is checked. For each self-reported log entry's latest date, every
        named player has a baseline dated on or after it (the state's
        initial_source_date in the entry's format or, with no state stored
        there, under category_feeds_parent in a category format, the
        overall doubles state's), or a played state in that format whose
        last_played is on or after it and that records every teammate in
        partners and every player of the other team in opponents, each
        dated on or after it; and at least one named player has no
        baseline on or after it. A snapshot edited in several
        fields can still load, for example: one that is not resumable
        where a played state gets an initial_source_date before every
        logged date that moved it and verified_matches_played raised to
        match (with an external initial_source, or an "open_parent:" one
        dated with its parent; a dated "open_provisional" state does not
        load); the dated-state edits the resumable rules below reject, in
        a snapshot that is not resumable; in a resumable snapshot, a state
        dated on or after an earlier match that moved it, with
        verified_matches_played raised to match, when its last_played and
        every partner and opponent date are still after the new date (for
        example, because it met the same partners and opponents again
        later), since those maps keep only the latest date; a log name
        swapped to a player who later played in that format with the same
        teammates and opponents, on or after the entry's latest date, with
        the verified counts adjusted to match; a whole log entry deleted
        with the named players' verified counts raised, since the snapshot
        does not tie a match id to its match type; and edits that also
        rewrite the partner and opponent names or dates to match. Identities
        follow the seed and link rules: account_user_id is null or a
        non-blank str, every external_ids source and id is a non-blank str,
        and no two players hold the same account or the same (source,
        external id) pair. No external_ids source starts with "open_".
        Every state's initial_source is a non-blank str, and one that starts
        with "open_" follows the reserved-name rules in add_player, checked
        under cfg (an "open_parent:" state may also be played, and is then
        undated only with a played parent state, with no RD rule). Every
        state's initial_evidence_reference is null or a non-blank str, and
        its initial_external_player_id is null or a non-blank str that
        the same player's external_ids links under initial_source. A
        resumable snapshot also holds no date after calculated_as_of, no
        played state with an initial_source_date whose last_played, or
        any partner or opponent date, is on or before that date (a replay
        adds every seed before any row, and a row on or before a state's
        date freezes it), no
        nonzero live_updates_since_replay or inherited_live_updates, no
        processed match id without a ledger digest, and no player with a
        played state missing from ledger_player_ids, and its replay_info
        counts equal the number of ledger digests and processed match ids.
        These checks cover shape and invariants, not authenticity. Raises
        SnapshotError naming the first bad value. The engine shares no state with the
        snapshot.
        """
        engine = cls(cfg)
        state = _read_snapshot(snapshot, engine.cfg)
        engine.players = state.players
        engine.processed_match_ids = state.processed_match_ids
        engine._reserved_match_ids = state.reserved_match_ids
        engine.self_reported_log = state.self_reported_log
        engine._content_keys = state.content_keys
        engine.possible_duplicates = state.possible_duplicates
        engine.calculated_as_of = state.calculated_as_of
        engine.replay_info = state.replay_info
        engine._ledger_digests = state.ledger_digests
        engine._ledger_player_ids = state.ledger_player_ids
        engine._seed_sensitive_rejections = state.seed_sensitive_rejections
        engine._resumable = state.resumable
        return engine

    # -- players and identity ------------------------------------------------

    def player(self, player_id: str) -> Player:
        """
        The player record for player_id. Read-only: it never creates a player.

        Raises PlayerError for a player_id that is blank or not exactly a
        str (a subclass is rejected), and UnknownPlayerError when no player
        record exists for it. A player is created by add_player,
        seed_provisional_rating, seed_external_rating, link_account,
        attach_external_id, or a counted record (and so replay). An id named
        only in uncounted rows, or in rows after a replay's as_of, has no
        player record.
        """
        if not _is_id(player_id):
            raise PlayerError("player_id is required")
        if player_id not in self.players:
            raise UnknownPlayerError(f"Unknown player: {player_id}")
        return self.players[player_id]

    def _player_or_new(self, player_id: str) -> Player:
        """The player record for player_id, created empty if it does not exist."""
        self._check_player_id(player_id)
        if player_id not in self.players:
            self._put_player(Player(id=player_id))
        return self.players[player_id]

    def add_player(self, player: Player) -> None:
        """
        Add a copy of a player record as a seed.

        Seed rules, checked for the seed and every rating state:
        - The seed is a Player with a str id that is not blank or
          whitespace only, its ratings are a dict, every key is a
          RatingFormat, and every value is a RatingState. Its external_ids
          are a dict of str to str with no blank or whitespace-only source
          or id, and its account_user_id is None or a str that is not blank
          or whitespace only. The seed must be exactly a Player and each
          state exactly a RatingState, and every str and dict here, the
          provenance strs below, and partners and opponents must be
          exactly str and dict, not a subclass.
        - rating, rd, initial_rating, and an imported reliability are ints
          or finite floats (not bool), the counts are ints, partners and
          opponents are dicts, dates are dates, initial_source is a str,
          and initial_external_player_id and initial_evidence_reference
          are each a str or None. An int or float subclass in one of these
          number or count fields is accepted, and is checked and stored as
          the plain int or float of its own value (int.__int__ or
          float.__float__, issue #107). The caller's Player and RatingState
          are not changed.
        - initial_source is not blank or whitespace only, and neither is
          an initial_external_player_id or initial_evidence_reference
          that is set.
        - No external_ids source starts with "open_", the prefix reserved
          for provenance the engine writes. The prefix is matched exactly,
          so "OPEN_x" or " open_x" is not reserved. An initial_source that
          starts with it has no imported reliability,
          initial_external_player_id, or initial_evidence_reference, and
          is one of two names. "open_provisional" (RatingState's default)
          has no initial_source_date, so a dated seed must set its own
          initial_source. In a category format, "open_parent:" plus that
          format's parent's value (doubles) needs Config.category_feeds_parent
          on, a doubles state in the same seed, an initial_source_date
          when that doubles state has one and none when it has none (the
          two dates may differ: a doubles reseed refreshes an unplayed
          derived state, so the engine's own states share the doubles
          date, but a seed or snapshot made before that refresh existed,
          or built by a caller, can hold another date), and an RD of at least
          Config.category_seed_rd. An undated one also needs a played
          doubles state, which a seed cannot have, so such a seed is
          always rejected. A seed taken from an engine with
          category_feeds_parent on is rejected by one with it off when it
          holds an "open_parent:" state. These rules do not check the
          rating or the date against the doubles state's, so a seed can
          still claim a derived state with any rating in range, and an
          "open_provisional" seed can carry any rating in range.
        - An initial_external_player_id that is set is linked in the seed's
          own external_ids under initial_source, so the uniqueness check
          below covers it. Since the seed must be exactly a Player, each
          state exactly a RatingState, and these ids exactly str and dict,
          a seed cannot carry another player's external identity as its
          provenance.
        - No matches, last_played, partners, opponents, or live updates:
          matches_played, verified_matches_played,
          live_updates_since_replay, and inherited_live_updates are 0.
        - rating and initial_rating are within Config.min_rating and
          Config.max_rating.
        - A provisional state (no initial_source_date) has RD new_player_rd,
          no rd_as_of, and no imported reliability.
        - An imported state, or a category state derived from one, has RD
          external_seed_rd or max(external_seed_rd, category_seed_rd),
          rd_as_of equal to initial_source_date, and an imported reliability
          that is None or between 0 and 100.
        Played states are rebuilt through replay instead.
        - The seed has no state in the format of an update record rejected
          for BEFORE_IMPORTED_BASELINE, SINGLES_RATING_GAP, or
          DOUBLES_PARTNER_GAP that names the seed's id, nor, under
          Config.category_feeds_parent, in that format's parent, as
          seed_external_rating refuses. Only add_player checks this, after
          the other seed rules and the duplicate id check and before the
          account and external identity uniqueness checks: replay adds its seeds before any row, and
          resume refuses a seed for any player in a row its checkpoint
          considered.
        add_player has no reference date, so it does not check that
        initial_source_date is in the past: replay rejects a seed dated after
        its as_of, and prequential_predictions one dated after its last match.

        Raises InvalidSeedError for a seed that breaks these rules (a seed
        that is not exactly a Player (a subclass included), or has a blank
        id or one that is not a str (a str subclass included), is checked
        before the duplicate id), and PlayerError if the id exists, or if
        another player already holds the seed's account_user_id or one of
        its (source, external id) pairs, with the messages link_account and
        attach_external_id use. Nothing changes when either is raised.
        """
        self._add_seed(player, self._held_identities(), check_rejections=True)

    def _held_identities(self) -> _HeldIdentities:
        """The accounts and (source, external id) pairs the engine's players hold."""
        held = _HeldIdentities()
        for p in self.players.values():
            held.add(p)
        return held

    def _add_seed(
        self, player: Player, held: _HeldIdentities, *, check_rejections: bool = False
    ) -> None:
        """
        add_player, with the identities already held passed in.

        held must hold every identity of the engine's players; it gains the
        seed's identities once the seed is added. A batch of seeds builds it
        once, so each seed's uniqueness check is a set lookup instead of a
        scan of every player. check_rejections (add_player only) refuses a
        seed that could change a seed-sensitive rejection. Replay adds its
        seeds before any row, and resume refuses a seed for any player named
        in a row the checkpoint considered, so neither needs it.
        """
        _check_seed_identity(player)
        if player.id in self.players:
            raise PlayerError(f"Player already exists: {player.id}")
        # Check and store plain numbers, never the caller's objects.
        player = _plain_seed(player)
        _validate_seed(player, self.cfg)
        if check_rejections:
            for fmt in sorted(player.ratings, key=lambda f: f.value):
                self._check_seed_sensitive(player.id, fmt)
        # The same uniqueness rules, with the same messages, as link_account
        # and attach_external_id.
        account = player.account_user_id
        if account is not None and account in held.accounts:
            raise PlayerError("This account is already linked to another player profile")
        for source in sorted(player.external_ids):
            if (source, player.external_ids[source]) in held.external_ids:
                raise PlayerError("This external identity is already linked to another player")
        self._put_player(clone_player(player))
        held.add(player)

    @staticmethod
    def _check_player_id(player_id: str) -> None:
        """Raise PlayerError for a player id that is blank or not exactly a str."""
        if not _is_id(player_id):
            raise PlayerError("player_id is required")

    def _check_account(self, player_id: str, account_user_id: str) -> None:
        """Raise PlayerError unless the account can link to player_id. Changes nothing."""
        if not _is_id(account_user_id):
            raise PlayerError("account_user_id is required")
        self._check_player_id(player_id)
        player = self.players.get(player_id)
        if player is not None and player.account_user_id not in (None, account_user_id):
            raise PlayerError("Player profile is already linked to another account")
        if any(
            p.account_user_id == account_user_id and p.id != player_id
            for p in self.players.values()
        ):
            raise PlayerError("This account is already linked to another player profile")

    def _check_external_id(self, player_id: str, source: str, external_player_id: str) -> None:
        """Raise PlayerError unless the identity can link to player_id. Changes nothing."""
        if not _is_id(source) or not _is_id(external_player_id):
            raise PlayerError("source and external_player_id are required")
        if source.startswith(_RESERVED_SOURCE_PREFIX):
            raise PlayerError(f"source must not start with {_RESERVED_SOURCE_PREFIX}")
        self._check_player_id(player_id)
        player = self.players.get(player_id)
        if player is not None and player.external_ids.get(source) not in (
            None,
            external_player_id,
        ):
            raise PlayerError("A different external identity is already linked")
        for other in self.players.values():
            if other.id != player_id and other.external_ids.get(source) == external_player_id:
                raise PlayerError("This external identity is already linked to another player")

    def link_account(self, player_id: str, account_user_id: str) -> None:
        """
        Claim an existing external-only player profile for an OPEN account.

        player_id and account_user_id must be exactly str (not a subclass).
        Raises PlayerError for a blank or non-str player id, a blank,
        whitespace-only, or non-str account, or when the player or the
        account is already linked elsewhere. Every check runs before anything changes, so a failed
        call creates no player.
        """
        self._check_account(player_id, account_user_id)
        player = self._player_or_new(player_id)
        self._changed()
        player.account_user_id = account_user_id

    def attach_external_id(self, player_id: str, source: str, external_player_id: str) -> None:
        """
        Link an external identity to a player. Each identity links to one player.

        player_id, source, and external_player_id must be exactly str (not
        a subclass). Raises PlayerError for a blank or non-str player id, a
        blank, whitespace-only, or non-str source or external id, a source
        that starts with "open_" (the prefix reserved for provenance the
        engine writes), when the player already has a different id from
        that source, or when the identity is linked to another player.
        Every check runs before anything changes, so a failed call creates
        no player.
        """
        self._check_external_id(player_id, source, external_player_id)
        player = self._player_or_new(player_id)
        self._changed()
        player.external_ids[source] = external_player_id

    # -- seeding ---------------------------------------------------------------

    def seed_external_rating(
        self,
        player_id: str,
        rating_format: RatingFormat,
        snapshot: ExternalRatingSnapshot,
        *,
        replace_unplayed_seed: bool = False,
    ) -> RatingState:
        """
        Initialize an OPEN rating from a verified external snapshot.

        The external rating is an initial condition, not a live dependency.
        Once OPEN matches exist, external refreshes cannot overwrite the rating.
        That holds in every rating format: it refuses to replace a category
        state derived from overall doubles (initial_source "open_parent:"
        plus the parent's value; an import through this method cannot take
        that name, since a source that starts with "open_" is rejected),
        and it refuses to seed a format in which a counted OPEN update
        included the player, even one in which the player was frozen, with
        or without a stored state in that format.
        For overall doubles, that includes the doubles update of a category
        match. Under category_feeds_parent, it also refuses to seed overall
        doubles when a counted category match included the player, whatever
        the player's state in that category is: a new doubles seed could
        change whether that match's doubles update counts, or the state a
        replay starts the category from. Otherwise a new seed rating or
        date could change a counted match, so live ratings and a replay
        could disagree. It also refuses to seed a player named in an
        update record rejected for BEFORE_IMPORTED_BASELINE,
        SINGLES_RATING_GAP, or DOUBLES_PARTNER_GAP (a match's own update,
        or the doubles update of a counted category match; see
        seed_sensitive_rejections in to_snapshot) when the seed's format
        is that update's format or, under category_feeds_parent, its
        parent. Those reasons read the players' ratings or dates, and a
        replay adds every seed before any row, so a new seed could make
        the replay count an update live rejected. This holds with or
        without replace_unplayed_seed, and for a player with no record
        yet. A rejection for another reason is not recorded and blocks no
        seed. A category format with no stored state, no counted match,
        and no such rejected update can still be seeded, even when the
        player has an overall doubles rating.
        Under category_feeds_parent, a successful seed of overall doubles
        also replaces each of the player's stored category states that is
        unplayed and derived from overall doubles (initial_source
        "open_parent:doubles") with the state a category match would start
        from right after the seed: the new rating and date, with an RD of
        at least Config.category_seed_rd. Other category states, imported
        or provisional, are not changed. A stored category state does not
        follow later changes to the doubles state from matches (issue #112).
        The seed's RD is always Config.external_seed_rd: an external
        reliability is kept for display (imported_reliability), never turned
        into OPEN RD. There is no reference date here, so a snapshot captured
        in the future is accepted; the next replay with an earlier as_of
        rejects the seed.

        rating_format must be a RatingFormat (a str is not converted),
        snapshot must be exactly an ExternalRatingSnapshot (not a subclass),
        and the snapshot is checked before anything changes: captured_on is a
        date (not a datetime), source is a str that is not blank or whitespace
        only and does not start with "open_" (the prefix reserved for
        provenance the engine writes, matched exactly), external_player_id
        is None or a str that is not blank or whitespace only,
        evidence_reference is None or a str that is not blank or whitespace
        only (each of these strs exactly a str, not a subclass; the same
        evidence reference may be used for many seeds), rating is an int or finite float
        (not bool) within the rating bounds, and reliability is None or an int
        or finite float (not bool) between 0 and 100. An int or float
        subclass rating or reliability is checked and stored as the plain
        int or float of its own value; the caller's snapshot is not
        changed. An external_player_id
        links the (source, external_player_id) pair to the player, and None
        links nothing. So every state seeded here passes the seed rules in
        add_player.

        Raises InvalidSeedError when the rating format, the snapshot or a
        checked snapshot field, the existing rating, or the player's OPEN
        matches (counted, or rejected for a reason a seed can change) do
        not allow the seed. Once the rating format and snapshot
        fields pass their checks, it raises PlayerError when player_id is
        blank or not a str (a str subclass included). Once any existing
        rating and the player's OPEN matches also allow the seed, it
        raises PlayerError when the snapshot's external identity is already
        linked to another player, or when the player already has a
        different id from that source.
        Every check runs before anything changes, so a failed call creates
        no player.
        """
        cfg = self.cfg
        # The same type rules the seed check applies, so every state seeded
        # here is one add_player and replay accept.
        if not isinstance(rating_format, RatingFormat):
            raise InvalidSeedError("rating_format must be a RatingFormat")
        # Exactly ExternalRatingSnapshot: a subclass can return different
        # values on later reads than the ones checked here.
        if type(snapshot) is not ExternalRatingSnapshot:
            raise InvalidSeedError("snapshot must be an ExternalRatingSnapshot")
        # A new snapshot with plain numbers: the checks and the stored state
        # read the same values, and the caller's snapshot is not changed.
        # Fields read from the class, not dataclasses.replace, which reads
        # an instance __dataclass_fields__ that the caller can set.
        copied: dict[str, object] = {
            f.name: getattr(snapshot, f.name) for f in fields(ExternalRatingSnapshot)
        }
        copied["rating"] = _plain_number(snapshot.rating)
        copied["reliability"] = _plain_number(snapshot.reliability)
        snapshot = ExternalRatingSnapshot(**copied)  # type: ignore[arg-type]
        if not _is_plain_date(snapshot.captured_on):
            raise InvalidSeedError("External snapshot captured_on must be a date")
        if not _is_plain_str(snapshot.source):
            raise InvalidSeedError("External snapshot source must be a str")
        if snapshot.external_player_id is not None and not _is_plain_str(
            snapshot.external_player_id
        ):
            raise InvalidSeedError("External snapshot external_player_id must be a str or None")
        if snapshot.evidence_reference is not None and not _is_plain_str(
            snapshot.evidence_reference
        ):
            raise InvalidSeedError("External snapshot evidence_reference must be a str or None")
        if not _is_id(snapshot.source):
            raise InvalidSeedError("External snapshot source must not be blank")
        if snapshot.source.startswith(_RESERVED_SOURCE_PREFIX):
            raise InvalidSeedError(
                f"External snapshot source must not start with {_RESERVED_SOURCE_PREFIX}"
            )
        if snapshot.external_player_id is not None and not _is_id(snapshot.external_player_id):
            raise InvalidSeedError("External snapshot external_player_id must not be blank")
        if snapshot.evidence_reference is not None and not _is_id(snapshot.evidence_reference):
            raise InvalidSeedError("External snapshot evidence_reference must not be blank")
        if not _is_finite_number(snapshot.rating):
            raise InvalidSeedError("External rating must be a finite number")
        if snapshot.reliability is not None and not _is_finite_number(snapshot.reliability):
            raise InvalidSeedError("External reliability must be a finite number")
        if not cfg.min_rating <= snapshot.rating <= cfg.max_rating:
            raise InvalidSeedError("External rating is outside the configured scale")
        if snapshot.reliability is not None and not 0 <= snapshot.reliability <= 100:
            raise InvalidSeedError("External reliability must be between 0 and 100")

        self._check_player_id(player_id)
        known = self.players.get(player_id)
        existing = known.ratings.get(rating_format) if known is not None else None
        if existing is not None:
            if existing.matches_played > 0:
                raise InvalidSeedError(
                    "Cannot replace an OPEN rating after OPEN matches have been processed"
                )
            if existing.initial_source.startswith("open_parent:"):
                raise InvalidSeedError("Cannot replace a rating derived from OPEN overall doubles")
        if self._in_counted_match(player_id, rating_format):
            raise InvalidSeedError(
                "Cannot seed a rating format in which OPEN matches have counted for this player"
            )
        # A counted category match sends a doubles update for its players,
        # and may start the player's category state from this format. A new
        # seed rating or date could change either, so refuse whenever a
        # counted category match included the player, whatever that
        # category state is.
        if self.cfg.category_feeds_parent and known is not None:
            for child in RatingFormat:
                if child.parent is rating_format and self._in_counted_match(player_id, child):
                    raise InvalidSeedError(
                        f"Cannot seed a rating that a counted {child.value} match for this "
                        "player depends on"
                    )
        self._check_seed_sensitive(player_id, rating_format)
        if existing is not None:
            if not replace_unplayed_seed:
                raise InvalidSeedError(
                    "A rating already exists; explicitly allow replacing an unplayed seed"
                )

        if snapshot.external_player_id is not None:
            self._check_external_id(player_id, snapshot.source, snapshot.external_player_id)

        # Every check has passed: create the player and link the identity.
        player = self._player_or_new(player_id)
        if snapshot.external_player_id is not None:
            self.attach_external_id(player_id, snapshot.source, snapshot.external_player_id)

        state = RatingState(
            rating=snapshot.rating,
            rd=cfg.external_seed_rd,
            rd_as_of=snapshot.captured_on,
            initial_rating=snapshot.rating,
            initial_source=snapshot.source,
            initial_source_date=snapshot.captured_on,
            initial_source_reliability=snapshot.reliability,
            initial_external_player_id=snapshot.external_player_id,
            initial_evidence_reference=snapshot.evidence_reference,
        )
        self._put_state(player, rating_format, state)
        # A stored, unplayed category state derived from the format just
        # seeded must start from the new seed, as _new_state would build it
        # now, or live recording and a replay of later matches disagree.
        if cfg.category_feeds_parent:
            for child in RatingFormat:
                if child.parent is not rating_format:
                    continue
                derived = player.ratings.get(child)
                if (
                    derived is not None
                    and derived.matches_played == 0
                    and derived.initial_source == f"open_parent:{rating_format.value}"
                ):
                    self._put_state(player, child, self._new_state(player_id, child))
        return state

    def _check_seed_sensitive(self, player_id: str, rating_format: RatingFormat) -> None:
        """
        Raise InvalidSeedError when a seed could change a seed-sensitive rejection.

        A rejected update in rating_format depends on the player's state
        there, and, under category_feeds_parent, a rejected category update
        also depends on the player's parent state, from which a category
        state without a stored one starts. A replay adds every seed before
        any row, so a new seed could make it count a row live rejected.
        """
        feeds = self.cfg.category_feeds_parent
        blocking = [
            match_id
            for match_id, (fmt, players) in self._seed_sensitive_rejections.items()
            if player_id in players
            and (fmt is rating_format or (feeds and fmt.parent is rating_format))
        ]
        if blocking:
            # The smallest id, so the message does not depend on dict order.
            match_id = min(blocking)
            fmt = self._seed_sensitive_rejections[match_id][0]
            raise InvalidSeedError(
                f"Cannot seed: a rejected {fmt.value} match that named this player "
                f"({match_id}) could count with a new seed; a new seed for this player "
                "needs a full replay from corrected seeds"
            )

    def _in_counted_match(self, player_id: str, rating_format: RatingFormat) -> bool:
        """
        True when player_id was in a counted match in rating_format, frozen or not.

        A frozen player's own state is not written, but a match counts only
        when some player in it moves, and each moved player records every
        other player in the match in partners or opponents. Those maps are
        never pruned, so another state naming player_id finds the match.
        """
        for other_id, other in self.players.items():
            state = other.ratings.get(rating_format)
            if state is None:
                continue
            if other_id == player_id:
                if state.matches_played > 0:
                    return True
            elif player_id in state.partners or player_id in state.opponents:
                return True
        return False

    def seed_provisional_rating(self, player_id: str, rating_format: RatingFormat) -> RatingState:
        """
        The player's state in a format. Creates a new unplayed state if it is missing.

        Raises InvalidSeedError when rating_format is not a RatingFormat (a
        str is not converted), and PlayerError for a player_id that is
        blank or not exactly a str (a subclass is rejected). Both are
        checked before anything changes, so a failed call creates no player.
        """
        if not isinstance(rating_format, RatingFormat):
            raise InvalidSeedError("rating_format must be a RatingFormat")
        player = self._player_or_new(player_id)
        if rating_format not in player.ratings:
            self._put_state(player, rating_format, self._new_state(player_id, rating_format))
        return player.ratings[rating_format]

    def _new_state(self, player_id: str, rating_format: RatingFormat) -> RatingState:
        """
        Starting state for a format the player has no rating in.

        A category rating (mixed, men's, women's doubles) starts from the
        player's overall doubles rating when one exists, with extra RD.
        """
        cfg = self.cfg
        parent = rating_format.parent
        player = self.players.get(player_id)
        if parent and cfg.category_feeds_parent and player and parent in player.ratings:
            base = player.ratings[parent]
            if base.matches_played > 0 or base.initial_source_date is not None:
                return RatingState(
                    rating=base.rating,
                    rd=max(base.rd, cfg.category_seed_rd),
                    rd_as_of=base.rd_as_of,
                    initial_rating=base.rating,
                    initial_source=f"open_parent:{parent.value}",
                    initial_source_date=base.initial_source_date,
                    inherited_live_updates=(
                        base.live_updates_since_replay + base.inherited_live_updates
                    ),
                )
        return RatingState(
            rating=cfg.default_rating,
            rd=cfg.new_player_rd,
            initial_rating=cfg.default_rating,
            initial_source="open_provisional",
        )

    def _peek_state(self, player_id: str, rating_format: RatingFormat) -> RatingState:
        """Current state without creating anything."""
        player = self.players.get(player_id)
        if player and rating_format in player.ratings:
            return player.ratings[rating_format]
        return self._new_state(player_id, rating_format)

    def state(self, player_id: str, rating_format: RatingFormat) -> RatingState:
        """
        The player's current state in a format. Read-only: it never stores.

        For a format the player has no state in, it returns the starting state
        the player's first match in that format would use if played now: a
        category state derived from overall doubles where that applies, else a
        provisional default. That object is a new copy each call and is not
        engine state, so changing it changes nothing. Use
        seed_provisional_rating to store it. Raises UnknownPlayerError when no
        player record exists for the id, and PlayerError for an id that is
        blank or not exactly a str (a subclass is rejected).
        """
        player = self.player(player_id)
        if rating_format in player.ratings:
            return player.ratings[rating_format]
        return self._new_state(player_id, rating_format)

    # -- prediction ------------------------------------------------------------

    def predict_point_share(
        self,
        team_a: Sequence[str],
        team_b: Sequence[str],
        rating_format: RatingFormat,
        scoring: ScoringType = ScoringType.SIDE_OUT,
    ) -> float:
        """Expected share of points won by team A, from current team-average ratings."""
        self._validate_teams(team_a, team_b, rating_format)
        ratings_a = [self._peek_state(pid, rating_format).rating for pid in team_a]
        ratings_b = [self._peek_state(pid, rating_format).rating for pid in team_b]
        return expected_point_share(
            math.fsum(ratings_a) / len(ratings_a),
            math.fsum(ratings_b) / len(ratings_b),
            self.cfg.scale_by_scoring[scoring],
        )

    def predict_win_probability(
        self,
        team_a: Sequence[str],
        team_b: Sequence[str],
        rating_format: RatingFormat,
        game_format: GameFormat,
        best_of: int = 1,
    ) -> float:
        """
        Calibrated probability that team A wins the match.

        Raises ConfigError when game_format is not valid (GameFormat.validate).
        """
        game_format.validate()
        share = self.predict_point_share(team_a, team_b, rating_format, game_format.scoring)
        raw = match_win_probability(share, game_format, best_of)
        return calibrate_win_probability(
            raw, self.cfg.win_calibration_by_scoring[game_format.scoring]
        )

    def _validate_teams(
        self, team_a: Sequence[str], team_b: Sequence[str], rating_format: RatingFormat
    ) -> None:
        size = rating_format.team_size
        if len(team_a) != size or len(team_b) != size:
            raise InvalidMatchError(f"{rating_format.value} requires {size} player(s) per team")
        ids = list(team_a) + list(team_b)
        if not all(_is_id(pid) for pid in ids):
            raise InvalidMatchError("Player ids must be non-empty strings")
        if len(set(ids)) != len(ids):
            raise InvalidMatchError("A player cannot appear more than once in a match")

    # -- recording -------------------------------------------------------------

    def record(self, match: Match, *, as_of: date | None = None) -> MatchResult:
        """
        Process one match exactly once.

        Leave `as_of` unset for live processing (labeled provisional until the
        next replay). replay() passes a fixed as_of for authoritative rebuilds.
        `as_of` only labels the update as part of a replay and must not be
        earlier than the match date; the update itself does not depend on it.
        An as_of that is not a plain date (a datetime is not one), or one
        earlier than played_on, raises InvalidMatchError right after the
        match is validated, before any check that rejects the match.
        A match_id already processed or reserved returns
        RejectionReason.DUPLICATE_MATCH_ID and changes nothing. Any other
        match that does not count reserves its match_id, whatever the
        reason: it is not marked processed and moves no rating, count, or
        content key, but every later call for a match with that id that
        does not raise returns DUPLICATE_MATCH_ID, as replay never counts a
        second row with one id. A corrected resubmission (for example a
        self-reported match after confirmation) needs a new match_id; see
        the module notes for which rejections allow one. A match rejected as
        a possible duplicate is also recorded in possible_duplicates. A
        match rejected for BEFORE_IMPORTED_BASELINE, SINGLES_RATING_GAP, or
        DOUBLES_PARTNER_GAP is also recorded, with its players and format,
        in seed_sensitive_rejections (see to_snapshot), and so is a counted
        category match whose overall doubles update is rejected for one of
        those, under doubles. Each of these changes clears the snapshot's
        resumable mark. A match whose status
        is not MatchStatus.COMPLETED is never counted: after the match_id
        check, it returns RejectionReason.MATCH_NOT_COMPLETED.
        Live results apply in
        arrival order: MatchResult.late (and parent.late, for a category
        match) flags a match dated before a player's last match in that
        format, or before the date that player's rating in that format
        already reflects (for a category rating copied from overall
        doubles). Stored dates never move back.

        A match with the same content as an earlier counted match (same
        played_on, teams, and scores; see MatchResult.possible_duplicate_of)
        goes through the other checks by default and, if it counts, has
        possible_duplicate_of set. With Config.reject_content_duplicates it
        is not counted, with RejectionReason.POSSIBLE_DUPLICATE. That check runs after the
        match_id and game format checks and before the self-reported
        checks, so a rejected duplicate never enters the self-reported log.
        """
        return self._record(match, as_of=as_of)[0]

    def _record(
        self, match: Match, *, as_of: date | None = None
    ) -> tuple[MatchResult, dict[RatingFormat, float]]:
        """
        record, also returning the net rating each counted update added, by format.

        The net of one update is math.fsum of the unrounded rating changes
        it applied, a frozen player adding 0.0. A format appears only when
        its update counted. replay totals these into
        ReplayInfo.net_rating_created.
        """
        net: dict[RatingFormat, float] = {}
        _validate_match(match)
        if as_of is not None and not _is_plain_date(as_of):
            raise InvalidMatchError(
                f"as_of must be a plain date (not a datetime), not {type(as_of).__name__}"
            )
        if as_of is not None and as_of < match.played_on:
            raise InvalidMatchError("as_of cannot be earlier than the match date")
        match_id = match.match_id
        if match_id in self.processed_match_ids or match_id in self._reserved_match_ids:
            return (
                MatchResult(False, match.rating_format, RejectionReason.DUPLICATE_MATCH_ID),
                net,
            )
        result, net = self._consider(match, as_of)
        if not result.counted:
            # Reserve the id of every row considered and not counted, so a
            # later record with it that does not raise is a duplicate, as in
            # replay, where a match_id names one row. No rating, count, or
            # content key changes.
            self._changed()
            self._reserved_match_ids.add(match_id)
        return result, net

    def _consider(
        self, match: Match, as_of: date | None
    ) -> tuple[MatchResult, dict[RatingFormat, float]]:
        """
        _record after the match_id check: the result and net of a match not seen before.

        Changes state only when the match counts, when it is rejected as
        a possible duplicate (possible_duplicates), or when an update is
        rejected for a reason in _SEED_SENSITIVE_REASONS
        (_seed_sensitive_rejections: the match's own update, or the
        overall doubles update of a counted category match). _record
        reserves the match_id of every result that does not count.
        """
        net: dict[RatingFormat, float] = {}
        if match.status is not MatchStatus.COMPLETED:
            return (
                MatchResult(False, match.rating_format, RejectionReason.MATCH_NOT_COMPLETED),
                net,
            )

        cfg = self.cfg
        format_key = (match.game_format.scoring, match.game_format.points_to)
        if format_key not in cfg.allowed_game_formats:
            return (
                MatchResult(False, match.rating_format, RejectionReason.GAME_FORMAT_NOT_ACCEPTED),
                net,
            )

        live = as_of is None

        content_key = _content_key(match)
        twin = self._content_keys.get(content_key)
        if twin is not None and cfg.reject_content_duplicates:
            self._changed()
            self.possible_duplicates[match.match_id] = twin
            rejected = MatchResult(
                False,
                match.rating_format,
                RejectionReason.POSSIBLE_DUPLICATE,
                possible_duplicate_of=twin,
            )
            return rejected, net

        repeat_factor = 1.0
        matchup = None
        if match.match_type == MatchType.SELF_REPORTED:
            if cfg.self_reported_requires_both_teams:
                confirmed = set(match.confirmed_by)
                if not (confirmed & set(match.team_a) and confirmed & set(match.team_b)):
                    unconfirmed = MatchResult(
                        False, match.rating_format, RejectionReason.SELF_REPORTED_UNCONFIRMED
                    )
                    return unconfirmed, net
            matchup = _matchup_key(match)
            cutoff = _days_before(match.played_on, cfg.self_reported_repeat_window_days)
            prior = sum(
                1 for d in self.self_reported_log.get(matchup, []) if cutoff <= d <= match.played_on
            )
            if prior >= cfg.self_reported_max_repeats:
                limited = MatchResult(
                    False, match.rating_format, RejectionReason.SELF_REPORTED_REPEAT_LIMIT
                )
                return limited, net
            repeat_factor = cfg.self_reported_repeat_factor**prior

        primary, primary_updates, primary_net = self._compute(
            match, match.rating_format, repeat_factor
        )
        if not primary.counted or primary_updates is None:
            self._note_seed_sensitive(match, primary)
            return primary, net
        primary.possible_duplicate_of = twin
        net[match.rating_format] = primary_net

        parent_updates = None
        parent_format = match.rating_format.parent if cfg.category_feeds_parent else None
        if parent_format is not None:
            # A parent rejection (baseline or filter) never blocks the category.
            primary.parent, parent_updates, parent_net = self._compute(
                match, parent_format, repeat_factor
            )
            if parent_updates is not None:
                net[parent_format] = parent_net
            else:
                self._note_seed_sensitive(match, primary.parent)

        self._commit(match.rating_format, primary_updates, live)
        if parent_updates and parent_format is not None:
            self._commit(parent_format, parent_updates, live)
        if matchup is not None:
            self.self_reported_log.setdefault(matchup, []).append(match.played_on)
        self._content_keys.setdefault(content_key, match.match_id)
        if twin is not None:
            self.possible_duplicates[match.match_id] = twin
        self.processed_match_ids.add(match.match_id)
        return primary, net

    def _note_seed_sensitive(self, match: Match, result: MatchResult) -> None:
        """Record a rejected update whose outcome a new seed could change."""
        if result.reason in _SEED_SENSITIVE_REASONS:
            self._changed()
            players = tuple(sorted(match.team_a + match.team_b))
            self._seed_sensitive_rejections[match.match_id] = (result.rating_format, players)

    def _commit(
        self, rating_format: RatingFormat, updates: dict[str, RatingState], live: bool
    ) -> None:
        for player_id, state in updates.items():
            if live:
                state.live_updates_since_replay += 1
            self._put_state(self._player_or_new(player_id), rating_format, state)

    def _compute(
        self, match: Match, rating_format: RatingFormat, repeat_factor: float
    ) -> tuple[MatchResult, dict[str, RatingState] | None, float]:
        """
        Compute one format's update on working copies. Mutates nothing.

        Returns the result, the new states of the players who moved (None
        when the update does not count), and the net rating the update adds:
        math.fsum of the unrounded applied changes (0.0 when it does not
        count).
        """
        cfg = self.cfg
        self._validate_teams(match.team_a, match.team_b, rating_format)
        ids = match.team_a + match.team_b
        work = {pid: _clone_state(self._peek_state(pid, rating_format)) for pid in ids}

        # A player whose imported baseline is dated on or after this match
        # already has it reflected in that baseline: use, but do not move.
        frozen = {
            pid
            for pid in ids
            if (baseline_date := work[pid].initial_source_date) is not None
            and match.played_on <= baseline_date
        }
        if len(frozen) == len(ids):
            return (
                MatchResult(False, rating_format, RejectionReason.BEFORE_IMPORTED_BASELINE),
                None,
                0.0,
            )

        # Live results apply in arrival order. A match dated before any
        # player's last match here, or before the date a copied category
        # rating already reflects (frozen players included), arrived late.
        late = any(_is_late(work[pid], match.played_on) for pid in ids)

        states_a = [work[pid] for pid in match.team_a]
        states_b = [work[pid] for pid in match.team_b]
        rating_a = math.fsum(s.rating for s in states_a) / len(states_a)
        rating_b = math.fsum(s.rating for s in states_b) / len(states_b)

        if (
            rating_format is RatingFormat.SINGLES
            and cfg.max_singles_rating_gap is not None
            and abs(rating_a - rating_b) > cfg.max_singles_rating_gap
        ):
            return MatchResult(False, rating_format, RejectionReason.SINGLES_RATING_GAP), None, 0.0
        if cfg.max_doubles_partner_gap is not None and rating_format.team_size == 2:
            for team_states in (states_a, states_b):
                if abs(team_states[0].rating - team_states[1].rating) > cfg.max_doubles_partner_gap:
                    return (
                        MatchResult(False, rating_format, RejectionReason.DOUBLES_PARTNER_GAP),
                        None,
                        0.0,
                    )

        for pid in ids:
            if pid not in frozen:
                work[pid].rd = inflated_rd(work[pid], match.played_on, cfg)

        scale = cfg.scale_by_scoring[match.game_format.scoring]
        actual, _ = _actual_point_share(match, cfg)
        expected = expected_point_share(rating_a, rating_b, scale)
        surprise = actual - expected

        effective_weight = (
            cfg.match_weights[match.match_type] * repeat_factor * information_multiplier(match, cfg)
        )

        # Each player moves by their own K, so team-average movement is not
        # always zero-sum; imbalance reports the net.
        deltas: dict[str, float] = {}
        applied: dict[str, float] = {}  # unrounded, for imbalance
        clamped: list[str] = []
        for team_ids, opponent_ids, sign in (
            (match.team_a, match.team_b, 1.0),
            (match.team_b, match.team_a, -1.0),
        ):
            for pid in team_ids:
                if pid in frozen:
                    deltas[pid] = applied[pid] = 0.0
                    continue
                state = work[pid]
                raw_delta = sign * player_k(state, cfg) * effective_weight * surprise
                old_rating = state.rating
                state.rating = max(cfg.min_rating, min(cfg.max_rating, old_rating + raw_delta))
                if abs(state.rating - (old_rating + raw_delta)) > 1e-12:
                    clamped.append(pid)
                state.rd = max(
                    cfg.rd_min,
                    state.rd * (1.0 - cfg.rd_shrink_per_match * effective_weight),
                )
                # Dates never move back, so a late match does not rerun
                # inactivity or drop a recent partner from the window.
                state.rd_as_of = _latest(state.rd_as_of, match.played_on)
                state.matches_played += 1
                if match.match_type in _VERIFIED_MATCH_TYPES:
                    state.verified_matches_played += 1
                state.last_played = _latest(state.last_played, match.played_on)
                _seen(
                    state.partners, (other for other in team_ids if other != pid), match.played_on
                )
                _seen(state.opponents, opponent_ids, match.played_on)
                applied[pid] = state.rating - old_rating
                deltas[pid] = round(applied[pid], 5)

        # From the unrounded changes, so rounding residue never shows. Adding
        # 0.0 turns a negative zero into 0.0.
        imbalance = (
            round(
                math.fsum(applied[pid] for pid in match.team_a) / len(match.team_a)
                + math.fsum(applied[pid] for pid in match.team_b) / len(match.team_b),
                5,
            )
            + 0.0
        )

        result = MatchResult(
            counted=True,
            rating_format=rating_format,
            expected_a=round(expected, 5),
            actual_a=round(actual, 5),
            surprise=round(surprise, 5),
            effective_weight=round(effective_weight, 5),
            deltas=deltas,
            frozen=tuple(sorted(frozen)),
            clamped=tuple(clamped),
            imbalance=imbalance,
            late=late,
        )
        moved = {pid: work[pid] for pid in ids if pid not in frozen}
        return result, moved, math.fsum(applied.values()) + 0.0

    # -- reporting -------------------------------------------------------------

    def player_summary(
        self, player_id: str, rating_format: RatingFormat, as_of: date | None = None
    ) -> PlayerSummary:
        """
        The player's rating, reliability, and provenance in one format.

        as_of sets the reference date that inactivity and reliability are
        measured at. With no as_of, it is the later of calculated_as_of (the
        last replay date) and the player's last_played. So a stale player is
        measured at the replay date, and a player with live matches since at
        their last match. Players in one engine can be measured at different
        dates by default. A late live match never moves last_played back.
        The engine never reads the wall clock.

        An as_of earlier than the player's last match does not rebuild past
        state: RD and partner variety stay as of the stored state.

        connectivity is measured at the same reference date over every
        player's stored state in the format (see NetworkConnectivity), and
        when Config.connectivity_reliability_cap is set, reliability is at
        most the cap when it is not connected. The result for one format
        and date is cached until the next state change through the
        engine's own methods, for the 16 most recent dates; changing
        objects the engine returns directly is not tracked.

        imported_reliability is not carried when the import's capture date is
        after the reference date. So an unplayed import captured after the
        last replay is not shown by default until the next replay or an
        explicit as_of.

        live_updates_since_recalculation labels the provisional movement in
        the rating: the state's own live updates since the last replay, plus,
        for a category state derived from overall doubles, the parent's live
        updates already in the rating it copied (see PlayerSummary).

        It never changes engine state; it only fills a private cache of
        connectivity results, derived from the stored states. For a known player with no state in
        the format, it reports the starting state the player's first match in
        that format would use if played now (see state), without storing it.

        Raises ReferenceDateError when as_of is None and the engine was never
        replayed, or when as_of is not a plain date (a datetime is not one).
        These checks come first. Then it raises UnknownPlayerError when no
        player record exists for the id, and PlayerError for an id that is
        blank or not exactly a str (a subclass is rejected).
        """
        if as_of is not None and not _is_plain_date(as_of):
            raise ReferenceDateError(
                "player_summary as_of must be a date, not a str, datetime, or other type"
            )
        reference = as_of if as_of is not None else self.calculated_as_of
        if reference is None:
            raise ReferenceDateError(
                "player_summary needs as_of when the engine has not been replayed:"
                " pass as_of or replay first"
            )
        cfg = self.cfg
        player = self.player(player_id)
        state = self.state(player_id, rating_format)
        if as_of is None and state.last_played is not None:
            reference = max(reference, state.last_played)
        connectivity = self._connectivity_of(player_id, rating_format, reference)
        earned = reliability(state, cfg, reference)
        cap = cfg.connectivity_reliability_cap
        if cap is not None and not connectivity.connected:
            # The cap as displayed, 1 place, never above the cap itself.
            shown = round(float(cap), 1)
            if shown > cap:
                shown = round(shown - 0.1, 1)
            earned = min(earned, shown)

        # Imported reliability is shown, separately, only while it is fresh,
        # higher than what OPEN has earned, and within the carry match limit.
        # Both values are compared as displayed, rounded to 1 place.
        imported: float | None = None
        seed_reliability = state.initial_source_reliability
        if (
            seed_reliability is not None
            and state.initial_source_date is not None
            and state.matches_played < cfg.import_reliability_carry_matches
            and 0
            <= (reference - state.initial_source_date).days
            <= cfg.import_reliability_max_age_days
            and round(float(seed_reliability), 1) > earned
        ):
            imported = round(float(seed_reliability), 1)

        return PlayerSummary(
            player_id=player.id,
            rating_format=rating_format,
            rating=round(state.rating, 3),
            reliability=earned,
            imported_reliability=imported,
            established=(
                earned >= cfg.established_reliability_threshold
                and state.verified_matches_played >= cfg.established_min_verified_matches
            ),
            rd=round(inflated_rd(state, reference, cfg), 4),
            matches_played=state.matches_played,
            last_played=state.last_played,
            initial_rating=state.initial_rating,
            initial_source=state.initial_source,
            initial_source_reliability=state.initial_source_reliability,
            initial_source_date=state.initial_source_date,
            calculated_as_of=self.calculated_as_of,
            live_updates_since_recalculation=(
                state.live_updates_since_replay + state.inherited_live_updates
            ),
            config_version=cfg.version,
            account_linked=player.account_user_id is not None,
            connectivity=connectivity,
        )

    def _connectivity_of(
        self, player_id: str, rating_format: RatingFormat, reference: date
    ) -> NetworkConnectivity:
        """The player's NetworkConnectivity, from the cache or a new build of the format."""
        key = (rating_format, reference)
        if key not in self._connectivity:
            if len(self._connectivity) >= _CONNECTIVITY_CACHE_SIZE:
                # Drop the oldest entry. Insertion order is fixed by the calls
                # made, and a cache entry never changes a result.
                del self._connectivity[next(iter(self._connectivity))]
            self._connectivity[key] = _network_connectivity(
                self.players, rating_format, reference, self.cfg
            )
        found, largest = self._connectivity[key]
        if player_id in found:
            return found[player_id]
        # No stored state: measure the unplayed state a seed would store, a
        # player with no contacts and a group of one.
        return NetworkConnectivity(
            pool_size=1,
            fringe_players=0,
            outside_players=0,
            outside_days=0,
            group_size=1,
            in_main_network=2 >= max(largest, 1),
            connected=False,
        )


# ---------------------------------------------------------------------------
# Deterministic historical replay
# ---------------------------------------------------------------------------


def replay(
    matches: Iterable[Match],
    seed_players: Iterable[Player] = (),
    *,
    cfg: Config | None = None,
    as_of: date,
    from_snapshot: Mapping[str, object] | None = None,
    whole_ledger: bool = False,
) -> RatingEngine:
    """
    Rebuild authoritative ratings from seeds plus the immutable match ledger.

    Run on the published schedule (Config.replay_interval_days). Same ledger,
    seeds, config and as_of always produce the same result, whatever order
    the ledger rows arrive in. Every row is validated, even after as_of.
    Rows that repeat a match_id and match in every field, in type and
    value, are counted once; any other repeat raises LedgerConflictError, even when it
    falls after as_of. Seeds must be
    unplayed imported or provisional states that follow the seed rules (see
    RatingEngine.add_player), dated on or before as_of.

    Raises ReferenceDateError for an as_of that is not a plain date (a str,
    a datetime, None, or any other type). It checks this first, before
    whole_ledger, from_snapshot, the ledger rows, and the seeds.

    Raises InvalidSeedError for a seed state that breaks the seed rules (see
    add_player) or whose initial_source_date is after as_of, and PlayerError
    when two seeds share a player id, an account_user_id, or a (source,
    external id) pair. On resume, a seed that shares an identity with a
    checkpoint player raises PlayerError too.

    from_snapshot resumes from a checkpoint: the to_snapshot() output (or
    json.loads of its text) of an earlier replay under the same cfg. The
    result equals a full replay to as_of of the ledger and seeds the rows
    come from (the checkpoint's seeds plus seed_players), whether matches
    holds the whole ledger or only the rows after the checkpoint date.
    Rows dated on or before the checkpoint date are not recorded again:
    each must be a row the checkpoint considered, unchanged. seed_players
    then holds only seeds not in the checkpoint.
    Take the checkpoint snapshot right after its replay, before any other
    call on the engine. Every engine method that changes state clears the
    snapshot's resumable mark; changing objects the engine returns
    directly is not tracked and makes the snapshot unsafe to resume.

    whole_ledger=True (keyword only, with from_snapshot) promises that
    matches holds the whole ledger, so resume also raises SnapshotError,
    naming the ids sorted, for any row the checkpoint considered that is
    missing from it: a row voided or deleted on or before the checkpoint
    date. whole_ledger=True without from_snapshot raises SnapshotError,
    and so does a whole_ledger that is not a bool (an int, str, or None is
    a usage error of the resume API, not a ledger flag).

    Resume raises instead of giving a different result. The rows are
    checked in this order: LedgerConflictError for changed or reused rows,
    then SnapshotError for rows not in the checkpoint, then (with
    whole_ledger) SnapshotError for checkpointed rows that are missing.
    - SnapshotError for a malformed snapshot or one taken under another
      Config (see RatingEngine.from_snapshot), a snapshot not from a
      replay, one with live updates or any other change since its replay
      (resumable is false), an as_of before the checkpoint date, a row
      dated on or before the checkpoint date that the checkpoint did not
      consider, or a seed for a player named in a row the checkpoint
      considered (even if every such row was rejected).
    - LedgerConflictError for a row dated on or before the checkpoint date
      that differs from the row the checkpoint considered under its
      match_id, or a row dated after it that reuses one of those ids.
    - PlayerError for a seed whose player is already in the snapshot.
    - SnapshotError, with whole_ledger=True, for a checkpointed row missing
      from matches.
    Without whole_ledger, resume cannot see a row removed (voided) from the
    ledger on or before the checkpoint date, and given only the newer rows
    it cannot see a late row added on or before it either. With or without
    whole_ledger, it cannot see a seed withdrawn or corrected after the
    checkpoint. Those need a full replay.
    """
    if not _is_plain_date(as_of):
        raise ReferenceDateError("replay as_of must be a date, not a str, datetime, or other type")
    if not isinstance(whole_ledger, bool):
        raise SnapshotError(f"whole_ledger must be a bool, not {type(whole_ledger).__name__}")
    if whole_ledger and from_snapshot is None:
        raise SnapshotError("whole_ledger needs from_snapshot: it only applies to a resume")
    start = None
    if from_snapshot is not None:
        start = _resume_start(from_snapshot, cfg, as_of)
    return _replay(
        matches,
        seed_players,
        cfg=cfg,
        as_of=as_of,
        check_seed_dates=True,
        track=True,
        start=start,
        whole_ledger=whole_ledger,
    )


def _resume_start(snapshot: Mapping[str, object], cfg: Config | None, as_of: date) -> RatingEngine:
    """The engine a resumed replay starts from. SnapshotError when it cannot resume."""
    engine = RatingEngine.from_snapshot(snapshot, cfg)
    checkpoint = engine.calculated_as_of
    if checkpoint is None:
        raise SnapshotError("Cannot resume: the snapshot is not from a replay")
    live = sorted(engine.processed_match_ids - set(engine._ledger_digests))
    if live or any(
        state.live_updates_since_replay or state.inherited_live_updates
        for player in engine.players.values()
        for state in player.ratings.values()
    ):
        raise SnapshotError("Cannot resume: the snapshot has live updates since its replay")
    if not engine._resumable:
        raise SnapshotError(
            "Cannot resume: the engine changed after its replay. Take the checkpoint "
            "snapshot right after replay, before any other call on the engine"
        )
    if as_of < checkpoint:
        raise SnapshotError(
            f"Cannot resume: as_of {as_of.isoformat()} is before the checkpoint "
            f"date {checkpoint.isoformat()}"
        )
    return engine


def _replay(
    matches: Iterable[Match],
    seed_players: Iterable[Player],
    *,
    cfg: Config | None,
    as_of: date,
    check_seed_dates: bool,
    track: bool,
    start: RatingEngine | None = None,
    whole_ledger: bool = False,
) -> RatingEngine:
    """
    replay, with the seed date check optional, from a fresh engine or start.

    Scheduled prequential windows replay to dates before a seed captured
    mid-ledger, which is legitimate there (earlier matches are frozen for
    that player), so they skip the check. They also pass track=False: only
    replay records what a checkpoint needs (row digests), since hashing
    every row in every window would slow evaluation for nothing.

    start is a checked checkpoint engine (see _resume_start). Every row is
    checked against its digests before any row is recorded, and only rows
    dated after its checkpoint are recorded.
    """
    engine = RatingEngine(cfg) if start is None else start
    named_before = frozenset(engine._ledger_player_ids)
    held = engine._held_identities()
    for player in seed_players:
        engine._add_seed(player, held)
        if player.id in named_before:
            raise SnapshotError(
                f"Cannot resume: the seed for {player.id} names a player in rows the "
                "checkpoint considered; a new seed for that player needs a full replay"
            )
        if check_seed_dates:
            _check_seed_date(player, as_of, "as_of")

    rows = _ledger(matches)
    considered = counted = 0
    created = _zero_by_format()
    checkpoint: date | None = None
    if start is not None:
        checkpoint = start.calculated_as_of
        info = start.replay_info
        if checkpoint is None or info is None:
            raise SnapshotError("Cannot resume: the snapshot is not from a replay")
        _check_resume_rows(rows, checkpoint, start._ledger_digests, whole_ledger)
        considered = info.matches_considered
        counted = info.matches_counted
        created.update(info.net_rating_created)

    for match in rows:
        if match.played_on > as_of:
            continue
        if checkpoint is not None and match.played_on <= checkpoint:
            continue
        considered += 1
        if track:
            engine._ledger_digests[match.match_id] = _match_digest(match)
            engine._ledger_player_ids.update(match.team_a + match.team_b)
        result, net = engine._record(match, as_of=as_of)
        if result.counted:
            counted += 1
        # Added in ledger order, so a resume continues from the same float.
        for fmt, value in net.items():
            created[fmt] += value

    engine.calculated_as_of = as_of
    engine._resumable = track
    engine.replay_info = ReplayInfo(
        as_of=as_of,
        config_version=engine.cfg.version,
        config_fingerprint=engine.cfg.fingerprint(),
        matches_considered=considered,
        matches_counted=counted,
        net_rating_created=created,
    )
    return engine


def _check_resume_rows(
    rows: list[Match], checkpoint: date, digests: Mapping[str, str], whole_ledger: bool
) -> None:
    """
    Raise unless every row agrees with the checkpoint.

    A row dated on or before the checkpoint date must be a row the
    checkpoint considered, unchanged. A row dated after it must not reuse
    one of those ids. With whole_ledger, every row the checkpoint
    considered must also be among the rows. Conflicts raise
    LedgerConflictError first, then rows not in the checkpoint raise
    SnapshotError, then (with whole_ledger) checkpointed rows missing from
    the ledger raise SnapshotError, each naming every id, sorted.
    """
    conflicts: set[str] = set()
    missing: set[str] = set()
    for match in rows:
        seen = digests.get(match.match_id)
        if match.played_on > checkpoint:
            if seen is not None:
                conflicts.add(match.match_id)
        elif seen is None:
            missing.add(match.match_id)
        elif seen != _match_digest(match):
            conflicts.add(match.match_id)
    if conflicts:
        raise LedgerConflictError(conflicts)
    if missing:
        raise SnapshotError(
            "Cannot resume: rows dated on or before the checkpoint date are not in the "
            "checkpoint: " + ", ".join(sorted(missing))
        )
    if whole_ledger:
        removed = set(digests) - {match.match_id for match in rows}
        if removed:
            raise SnapshotError(
                "Cannot resume: rows the checkpoint considered are missing from the ledger: "
                + ", ".join(sorted(removed))
            )


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationReport:
    """Point-share error and calibration bins for a set of prequential predictions."""

    scoring: str | None
    scale: float | None
    matches_scored: int
    mse: float
    mae: float
    bias: float  # mean(actual - expected); near zero when well calibrated
    calibration: tuple[tuple[float, float, float, int], ...]
    # Each bin: (expected lower bound, mean expected, mean actual, count)


@dataclass(frozen=True)
class PrequentialPrediction:
    """One match predicted from ratings that had not yet seen it."""

    match_id: str
    played_on: date
    players: tuple[str, ...]
    expected: float  # team A's expected point share
    actual: float  # raw points_a / total, not win_weight-adjusted
    team_a_won: bool
    # Win probabilities are modeled only for win_by 1 or 2. For any other
    # win_by both are math.nan; the point-share fields are unaffected.
    win_probability_raw: float  # before calibration
    win_probability: float  # calibrated with cfg


PredictionMode = Literal["live", "scheduled"]


def _is_established(engine: RatingEngine, match: Match, burn_in_matches: int) -> bool:
    return all(
        (s := engine._peek_state(pid, match.rating_format)).initial_source_date is not None
        or s.matches_played >= burn_in_matches
        for pid in match.team_a + match.team_b
    )


def _predict_match(engine: RatingEngine, match: Match) -> PrequentialPrediction:
    cfg = engine.cfg
    scoring = match.game_format.scoring
    expected = engine.predict_point_share(match.team_a, match.team_b, match.rating_format, scoring)
    if match.game_format.win_by in (1, 2):
        raw = match_win_probability(expected, match.game_format, match.best_of)
        calibrated = calibrate_win_probability(raw, cfg.win_calibration_by_scoring[scoring])
    else:
        raw = calibrated = math.nan
    points_a = sum(a for a, _ in match.games)
    total = sum(a + b for a, b in match.games)
    games_a = sum(a > b for a, b in match.games)
    return PrequentialPrediction(
        match_id=match.match_id,
        played_on=match.played_on,
        players=match.team_a + match.team_b,
        expected=expected,
        actual=points_a / total,
        team_a_won=games_a > len(match.games) - games_a,
        win_probability_raw=raw,
        win_probability=calibrated,
    )


def prequential_predictions(
    matches: Iterable[Match],
    seed_players: Iterable[Player] = (),
    *,
    cfg: Config | None = None,
    scoring: ScoringType | None = None,
    burn_in_matches: int = 5,
    mode: PredictionMode = "live",
    score_from: date | None = None,
    anchor: date | None = None,
) -> list[PrequentialPrediction]:
    """
    Predict each eligible match from ratings that have not seen it.

    A match is eligible when every participant is established in its rating
    format (imported baseline, or at least `burn_in_matches` OPEN matches),
    its scoring equals `scoring` when given, it is played on or after
    `score_from` when given, and its status is MatchStatus.COMPLETED (a
    forfeit, retired, or incomplete row is recorded, which changes no
    rating, but never predicted). Only matches that `record` counts are kept.
    This "established" is not `PlayerSummary.established`.

    "live" records every match live in one engine, in ledger order.
    "scheduled" mirrors production: replay dates are
    `anchor + k * cfg.replay_interval_days` (anchor defaults to the first
    completed match date). A match on day d is predicted from replay(matches on or before s,
    as_of=s) for the largest replay date s strictly before d, and matches in
    that window are then predicted and recorded live in ledger order.

    Every seed is validated up front, in both modes, by the seed rules (see
    RatingEngine.add_player), and a seed whose initial_source_date is after
    the last completed ledger match raises InvalidSeedError. A ledger with
    no completed match has no seed date check and gives no predictions. A
    repeated seed id, or two seeds that share an account_user_id or a
    (source, external id) pair, raise PlayerError, as add_player does. A
    seed captured mid-ledger is accepted: scheduled replays before its date
    freeze it as usual.

    A score_from or anchor that is not None and not a plain date (a str, a
    datetime, or any other type) raises EvaluationError naming the argument,
    before the ledger is read, in both modes and for an empty ledger too.
    """
    for name, value in (("score_from", score_from), ("anchor", anchor)):
        if value is not None and not _is_plain_date(value):
            raise EvaluationError(f"{name} must be a plain date, not {type(value).__name__}")
    if mode not in ("live", "scheduled"):
        raise EvaluationError(f"mode must be 'live' or 'scheduled', not {mode!r}")
    cfg = cfg or Config()
    ordered = _ledger(matches)
    # Non-completed rows move no rating: they stay in the replays and
    # windows, but never set the seed date limit or the default anchor.
    completed = [m for m in ordered if m.status is MatchStatus.COMPLETED]
    seeds = list(seed_players)
    # add_player on a scratch engine applies every seed check, against the
    # earlier seeds too, in the same order and with the same messages, so
    # both modes reject the same seeds before any rating work.
    scratch = RatingEngine(cfg)
    held = scratch._held_identities()
    for player in seeds:
        scratch._add_seed(player, held)
        if completed:
            _check_seed_date(player, completed[-1].played_on, "the last completed ledger match")
    predictions: list[PrequentialPrediction] = []

    def step(engine: RatingEngine, match: Match) -> None:
        _validate_match(match)
        eligible = (
            (scoring is None or match.game_format.scoring is scoring)
            and (score_from is None or match.played_on >= score_from)
            and match.status is MatchStatus.COMPLETED
            and _is_established(engine, match, burn_in_matches)
        )
        prediction = _predict_match(engine, match) if eligible else None
        if engine.record(match).counted and prediction is not None:
            predictions.append(prediction)

    if mode == "live":
        engine = RatingEngine(cfg)
        held = engine._held_identities()
        for player in seeds:
            engine._add_seed(player, held)
        for match in ordered:
            step(engine, match)
        return predictions

    if not completed:
        return predictions
    interval = cfg.replay_interval_days
    if anchor is None:
        anchor = completed[0].played_on
    # Each match goes in window k, the largest k with anchor + k * interval
    # strictly before played_on, and is predicted from a replay to that grid
    # date. The matches dated on or before it are exactly those in earlier
    # windows. Windows are keyed by k, not by date, because the first grid
    # date can fall before date.min (issue #92). Only that one can, and no
    # match is before it, so its replay date is clamped at date.min without
    # adding a match dated date.min to the replay that predicts it.
    window_of = {m.match_id: ((m.played_on - anchor).days - 1) // interval for m in ordered}
    windows: dict[int, list[Match]] = {}
    for match in ordered:
        windows.setdefault(window_of[match.match_id], []).append(match)

    for k, window in sorted(windows.items()):
        if score_from is not None and all(m.played_on < score_from for m in window):
            continue
        replay_date = _days_before(anchor, -k * interval)
        engine = _replay(
            [m for m in ordered if window_of[m.match_id] < k],
            seeds,
            cfg=cfg,
            as_of=replay_date,
            check_seed_dates=False,
            track=False,
        )
        for match in window:
            step(engine, match)
    return predictions


def evaluate(
    matches: Iterable[Match],
    seed_players: Iterable[Player] = (),
    *,
    cfg: Config | None = None,
    scoring: ScoringType | None = None,
    burn_in_matches: int = 5,
    mode: PredictionMode = "live",
    score_from: date | None = None,
    anchor: date | None = None,
) -> EvaluationReport:
    """
    Prequential evaluation: each match is predicted before it updates ratings.

    Scores the matches that prequential_predictions() returns, with the same
    eligibility, mode, score_from and anchor rules. A score_from or anchor
    that is not None and not a plain date raises EvaluationError naming the
    argument, as prequential_predictions does, for an empty ledger too.
    """
    cfg = cfg or Config()
    predictions = prequential_predictions(
        matches,
        seed_players,
        cfg=cfg,
        scoring=scoring,
        burn_in_matches=burn_in_matches,
        mode=mode,
        score_from=score_from,
        anchor=anchor,
    )
    return _report_from_predictions(predictions, cfg, scoring)[0]


def _scored_pairs(
    predictions: list[PrequentialPrediction], cfg: Config
) -> list[tuple[float, float]]:
    # Same target and 5-decimal precision as MatchResult.expected_a/actual_a.
    w = cfg.win_weight
    return [
        (
            round(p.expected, 5),
            round((1.0 - w) * p.actual + w * float(p.team_a_won), 5),
        )
        for p in predictions
    ]


def _report_from_predictions(
    predictions: list[PrequentialPrediction], cfg: Config, scoring: ScoringType | None
) -> tuple[EvaluationReport, float]:
    """The rounded report plus the unrounded mean squared error."""
    pairs = _scored_pairs(predictions, cfg)
    if not pairs:
        raise EvaluationError("No established matches to evaluate")
    errors = [actual - expected for expected, actual in pairs]
    bins: list[tuple[float, float, float, int]] = []
    for index in range(10):
        lower = index / 10
        members = [
            (e, a) for e, a in pairs if lower <= e < lower + 0.1 or (index == 9 and e == 1.0)
        ]
        if members:
            bins.append(
                (
                    lower,
                    round(math.fsum(e for e, _ in members) / len(members), 4),
                    round(math.fsum(a for _, a in members) / len(members), 4),
                    len(members),
                )
            )
    mse = math.fsum(e * e for e in errors) / len(errors)
    report = EvaluationReport(
        scoring=scoring.value if scoring else None,
        scale=cfg.scale_by_scoring[scoring] if scoring else None,
        matches_scored=len(pairs),
        mse=round(mse, 6),
        mae=round(math.fsum(abs(e) for e in errors) / len(errors), 6),
        bias=round(math.fsum(errors) / len(errors), 6),
        calibration=tuple(bins),
    )
    return report, mse


_SEARCH_STEPS = 14  # coarse grid intervals in fit_scale and fit_win_calibration


def _check_search_bounds(low: float, high: float, tolerance: float) -> None:
    """Raise EvaluationError for a search range or tolerance the fits cannot use."""
    if not (math.isfinite(low) and math.isfinite(high)):
        raise EvaluationError("low and high must be finite")
    # The search adds two points of the range (midpoints) and multiplies the
    # width by the grid step count before dividing. Both must stay finite.
    if not (math.isfinite(2 * low) and math.isfinite(2 * high)):
        raise EvaluationError("2 * low and 2 * high must be finite")
    if not math.isfinite((high - low) * _SEARCH_STEPS):
        raise EvaluationError(f"(high - low) * {_SEARCH_STEPS} must be finite")
    if not low < high:
        raise EvaluationError("low must be less than high")
    if not (math.isfinite(tolerance) and tolerance > 0):
        raise EvaluationError("tolerance must be finite and greater than 0")


@dataclass(frozen=True)
class ScaleFit:
    """
    Result of fit_scale for one scoring type.

    A fit with at_bound set, or with a flat profile, is not identified.
    """

    scoring: str
    scale: float
    default_scale: float
    fitted: EvaluationReport
    at_default: EvaluationReport
    profile: tuple[tuple[float, float], ...] = ()  # (scale, unrounded mse), sorted by scale
    at_bound: bool = False  # fitted scale within tolerance of low or high


def fit_scale(
    matches: Iterable[Match],
    seed_players: Iterable[Player] = (),
    *,
    cfg: Config | None = None,
    scoring: ScoringType = ScoringType.SIDE_OUT,
    low: float = 1.0,
    high: float = 8.0,
    tolerance: float = 0.01,
    burn_in_matches: int = 5,
    mode: PredictionMode = "live",
    score_from: date | None = None,
    anchor: date | None = None,
) -> ScaleFit:
    """
    Fit one scoring type's scale by minimizing prequential point-share MSE.

    The search compares unrounded MSE, so ties at the 6-decimal precision of
    EvaluationReport.mse do not decide the fit.

    Coarse grid first, then golden-section search around the best grid point.
    Publish the returned ScaleFit alongside the config that adopts it.
    Raises EvaluationError, before any prediction work, unless low < high,
    tolerance is finite and greater than 0, and 2 * low, 2 * high, and
    (high - low) * 14 are finite. A score_from or anchor that is not None and
    not a plain date raises EvaluationError naming the argument, as
    prequential_predictions does, for an empty ledger too, unless another
    argument fails first: the trial config is built before the dates are
    checked, so for example a low of 0 or less raises ConfigError.
    """
    _check_search_bounds(low, high, tolerance)
    cfg = cfg or Config()
    ledger = list(matches)
    seeds = list(seed_players)
    cache: dict[float, tuple[EvaluationReport, float]] = {}

    def run(scale: float) -> tuple[EvaluationReport, float]:
        key = round(scale, 6)
        if key not in cache:
            trial = replace(cfg, scale_by_scoring={**cfg.scale_by_scoring, scoring: scale})
            predictions = prequential_predictions(
                ledger,
                seeds,
                cfg=trial,
                scoring=scoring,
                burn_in_matches=burn_in_matches,
                mode=mode,
                score_from=score_from,
                anchor=anchor,
            )
            cache[key] = _report_from_predictions(predictions, trial, scoring)
        return cache[key]

    steps = _SEARCH_STEPS
    grid = [low + (high - low) * i / steps for i in range(steps + 1)]
    best = min(grid, key=lambda s: run(s)[1])
    step = (high - low) / steps
    a, b = max(low, best - step), min(high, best + step)

    ratio = (math.sqrt(5) - 1) / 2
    c, d = b - ratio * (b - a), a + ratio * (b - a)
    while b - a > tolerance:
        width = b - a
        if run(c)[1] <= run(d)[1]:
            b, d = d, c
            c = b - ratio * (b - a)
        else:
            a, c = c, d
            d = a + ratio * (b - a)
        if b - a >= width:
            break  # the bracket hit float spacing above a tiny tolerance
    fitted_scale = round((a + b) / 2, 3)

    default_scale = cfg.scale_by_scoring[scoring]
    fitted = run(fitted_scale)[0]
    at_default = run(default_scale)[0]
    return ScaleFit(
        scoring=scoring.value,
        scale=fitted_scale,
        default_scale=default_scale,
        fitted=fitted,
        at_default=at_default,
        profile=tuple((float(scale), mse) for scale, (_, mse) in sorted(cache.items())),
        at_bound=fitted_scale - low <= tolerance or high - fitted_scale <= tolerance,
    )


@dataclass(frozen=True)
class WinCalibrationFit:
    """
    Result of fit_win_calibration for one scoring type.

    A fit with at_bound set is not identified.
    """

    scoring: str
    a: float  # exponent on the logit of the raw win probability, rounded to 3 places
    brier: float  # at the fitted a
    brier_uncalibrated: float  # at a = 1
    n: int  # predictions used (those with a modeled win probability)
    at_bound: bool = False  # fitted a within tolerance of low or high


def fit_win_calibration(
    matches: Iterable[Match],
    seed_players: Iterable[Player] = (),
    *,
    cfg: Config | None = None,
    scoring: ScoringType = ScoringType.SIDE_OUT,
    low: float = 0.2,
    high: float = 3.0,
    tolerance: float = 0.001,
    burn_in_matches: int = 5,
    mode: PredictionMode = "live",
    score_from: date | None = None,
    anchor: date | None = None,
) -> WinCalibrationFit:
    """
    Fit one scoring type's win-probability exponent by minimizing the Brier score.

    Uses the raw win probabilities computed with cfg's scale, so fit the scale
    first. Predictions without a modeled win probability (win_by not 1 or 2)
    are skipped. Same search as fit_scale: coarse grid, then golden section.
    Raises EvaluationError when no prediction remains, and, before any
    prediction work, for the same low, high, and tolerance as fit_scale.
    A score_from or anchor that is not None and not a plain date raises
    EvaluationError naming the argument, as prequential_predictions does,
    for an empty ledger too, unless the low, high, and tolerance checks
    fail first.
    """
    _check_search_bounds(low, high, tolerance)
    predictions = prequential_predictions(
        matches,
        seed_players,
        cfg=cfg or Config(),
        scoring=scoring,
        burn_in_matches=burn_in_matches,
        mode=mode,
        score_from=score_from,
        anchor=anchor,
    )
    data = [
        (p.win_probability_raw, 1.0 if p.team_a_won else 0.0)
        for p in predictions
        if not math.isnan(p.win_probability_raw)
    ]
    if not data:
        raise EvaluationError("no predictions with a modeled win probability to fit")
    cache: dict[float, float] = {}

    def brier(a: float) -> float:
        key = round(a, 6)
        if key not in cache:
            cache[key] = math.fsum(
                (calibrate_win_probability(r, key) - w) ** 2 for r, w in data
            ) / len(data)
        return cache[key]

    steps = _SEARCH_STEPS
    grid = [low + (high - low) * i / steps for i in range(steps + 1)]
    best = min(grid, key=brier)
    step = (high - low) / steps
    lo, hi = max(low, best - step), min(high, best + step)

    ratio = (math.sqrt(5) - 1) / 2
    c, d = hi - ratio * (hi - lo), lo + ratio * (hi - lo)
    while hi - lo > tolerance:
        width = hi - lo
        if brier(c) <= brier(d):
            hi, d = d, c
            c = hi - ratio * (hi - lo)
        else:
            lo, c = c, d
            d = lo + ratio * (hi - lo)
        if hi - lo >= width:
            break  # the bracket hit float spacing above a tiny tolerance
    fitted = round((lo + hi) / 2, 3)

    return WinCalibrationFit(
        scoring=scoring.value,
        a=fitted,
        brier=brier(fitted),
        brier_uncalibrated=brier(1.0),
        n=len(data),
        at_bound=fitted - low <= tolerance or high - fitted <= tolerance,
    )
