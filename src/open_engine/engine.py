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
  doubles). The backend should total it per pool (and per replay) and
  monitor it.
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
  measured at the replay date and loses reliability as replays move
  forward, and a player with live matches since is measured at their last
  match. Players in one engine can be measured at different dates. With no
  as_of on an engine that was never replayed, it raises
  ReferenceDateError, as it does for an as_of that is not a plain date.
  An as_of earlier than the player's last match does not rebuild past
  state: RD and partner variety stay as of the stored state. `reliability`
  takes as_of as a required argument. The engine never reads the wall
  clock.
- PlayerSummary.established is True when OPEN-earned reliability, as
  displayed (rounded to 1 place), is at least
  Config.established_reliability_threshold (60 by default). Imported
  reliability never counts, and it is measured at the same reference date
  as reliability, so it lapses with inactivity. Under the defaults, a new
  player with no imported rating, playing one match a week with varied
  partners and opponents, becomes established after about 10 to 13
  tournament, league, or recreational verified matches (10 tournament, 11
  league, or 13 recreational verified matches of one game, fewer for best
  of 3), and after about twice as many against the same players.
  Self-reported matches need about 20 to 25 with varied players and never
  get there against the same players, because repeat matches are
  discounted and then rejected. A player seeded from a recently captured
  imported rating starts at a lower RD (Config.external_seed_rd) and is
  established after about 3 to 4 tournament, league, or recreational
  verified matches. RD grows from the capture date, so an older import
  gives less of a head start, and a very old one none. The imported rating
  and its reliability value do not change the count. A rating that is not
  established is what external rating systems usually call provisional.
  That is different from OPEN's provisional live updates between replays
  and from the open_provisional initial source. The threshold is a label
  only: it changes no rating, prediction, evaluate,
  prequential_predictions, fit_scale, or fit_win_calibration output.
  Evaluation has its own eligibility rule: it scores a match only when
  every participant has an imported baseline or at least burn_in_matches
  OPEN matches. That rule is separate from PlayerSummary.established.
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
  which is a `ValueError`. `ConfigError` covers Config and GameFormat
  values, `InvalidMatchError` covers matches, teams, scores and
  `played_at`, `InvalidSeedError` covers external and replay seeds (a
  seed state that breaks the seed rules in `RatingEngine.add_player`,
  including a field of the wrong type, a blank id, or a rating or
  initial rating outside the bounds, raised by `add_player`, `replay`,
  and `prequential_predictions`, and so
  by `evaluate`, `fit_scale`, and `fit_win_calibration`; `replay` also
  rejects a seed dated after `as_of`, and `prequential_predictions` one
  dated after its last completed ledger match),
  `PlayerError` covers player identities, including a seed whose
  account or external identity another player holds (its subclass
  `UnknownPlayerError` covers a read of a player id with no player
  record), `EvaluationError` covers
  evaluation, fits and win probabilities, `LedgerConflictError` covers
  conflicting ledger rows, `SnapshotError` covers snapshots that cannot
  be loaded or resumed from, and `ReferenceDateError` covers a
  `player_summary` call with no date to measure at or with an as_of that
  is not a plain date.
- Snapshots and checkpoints: `RatingEngine.to_snapshot()` gives the whole
  engine state as JSON-safe data (schema version 5) with sorted keys and
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
  An entry stays until that match_id later counts with no twin, which
  only a copy rejected under True can do.
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
  already processed, or reserved by a match that was not completed,
  returns a result that is not counted, with
  `RejectionReason.DUPLICATE_MATCH_ID`, instead of raising, unless its
  `as_of` is invalid (not a plain date, or earlier than `played_on`),
  which raises `InvalidMatchError` first. A match with
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
  is not marked processed, but it reserves its match_id: every later
  `record` call for a match with that id that does not raise returns
  DUPLICATE_MATCH_ID, completed or not, with the same players or not.
  That matches replay, where a second row with the id is a conflict or
  collapses into the first. A live reservation clears the snapshot's
  resumable mark. A forfeit relabeled as completed needs a new match_id
  live.
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
- Assign match_ids in the backend. A party that can choose a match_id
  can submit a non-completed match under it first, and then the real
  match with that id never counts live (replay raises
  LedgerConflictError for the pair). DUPLICATE_MATCH_ID does not say
  whether the id was counted or reserved.
- Require verified identity and authorized evidence before importing an
  external rating. An external refresh can never overwrite an OPEN-earned
  rating once OPEN matches have been processed.
- Store each replay's `ReplayInfo` (as_of date, config version, config
  fingerprint) for auditability.
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
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import UTC, date, datetime, timedelta
from enum import Enum, StrEnum
from typing import Literal, TypeGuard, TypeVar

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
        """Raise ConfigError when the format itself is not valid."""
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


@dataclass(frozen=True, eq=False)
class Config:
    """
    Every rating parameter. Frozen, so a config cannot change during a replay.

    The defaults are uncalibrated placeholders. fingerprint() identifies the
    exact parameter set behind published ratings.

    Mapping fields are stored as read-only copies whose values must be
    finite numbers, and allowed_game_formats as a frozenset of (scoring
    type, whole number) pairs. Changing the dict or set a config was built
    from does not change the config, and changing the config's own raises
    TypeError. Two configs are equal exactly when their fingerprint payloads
    are equal, so equal configs share a fingerprint and a hash, and a Config
    can be a dict key or a set member. to_dict() and from_dict() store and
    load a config.

    Only the mapping and format fields are type-checked so far. A scalar
    field given a value JSON cannot hold (a Decimal, say) makes
    fingerprint(), ==, and hash() raise TypeError, and a non-finite float
    is written as NaN or Infinity, which strict JSON rejects.
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
    # reliability, as displayed, is at least this value (0-100). A label
    # only: it changes no rating, prediction, or evaluation.
    established_reliability_threshold: float = 60.0

    # Category doubles matches also update overall doubles.
    category_feeds_parent: bool = True

    # Optional eligibility filters. Disabled by default.
    max_singles_rating_gap: float | None = None
    max_doubles_partner_gap: float | None = None

    def __post_init__(self) -> None:
        for name in _CONFIG_MAPPING_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise ConfigError(f"{name} must be a mapping")
            if not all(_is_finite_number(item) for item in value.values()):
                raise ConfigError(f"{name} values must be finite numbers")
            object.__setattr__(self, name, _FrozenMapping(value))
        if not all(_is_whole_number(key) for key in self.game_length_weights):
            raise ConfigError("game_length_weights keys must be whole numbers")
        if not isinstance(self.allowed_game_formats, (set, frozenset)):
            raise ConfigError("allowed_game_formats must be a set or frozenset")
        object.__setattr__(self, "allowed_game_formats", frozenset(self.allowed_game_formats))
        for entry in self.allowed_game_formats:
            if not (
                isinstance(entry, tuple)
                and len(entry) == 2
                and entry[0] in _SCORING_VALUES
                and _is_whole_number(entry[1])
            ):
                raise ConfigError(
                    "allowed_game_formats entries must be (scoring type, whole number) pairs"
                )
        if not self.min_rating < self.max_rating:
            raise ConfigError("min_rating must be less than max_rating")
        if not self.min_rating <= self.default_rating <= self.max_rating:
            raise ConfigError("default_rating must be within rating bounds")
        if set(self.scale_by_scoring) != set(ScoringType):
            raise ConfigError("scale_by_scoring needs a value for every scoring type")
        if any(value <= 0 for value in self.scale_by_scoring.values()):
            raise ConfigError("scales must be positive")
        if set(self.win_calibration_by_scoring) != set(ScoringType):
            raise ConfigError("win_calibration_by_scoring needs a value for every scoring type")
        if any(value <= 0 for value in self.win_calibration_by_scoring.values()):
            raise ConfigError("win calibration values must be positive")
        if not 0 <= self.k_min <= self.k_max:
            raise ConfigError("K-factor bounds are invalid")
        if not 0 < self.rd_min <= self.rd_max:
            raise ConfigError("RD bounds are invalid")
        for name in ("new_player_rd", "external_seed_rd", "category_seed_rd"):
            if not self.rd_min <= getattr(self, name) <= self.rd_max:
                raise ConfigError(f"{name} must be within RD bounds")
        if not 0.0 <= self.win_weight <= 1.0:
            raise ConfigError("win_weight must be between 0 and 1")
        if set(self.match_weights) != set(MatchType):
            raise ConfigError("match_weights needs a value for every match type")
        for _, points in self.allowed_game_formats:
            if points not in self.game_length_weights:
                raise ConfigError(f"No game_length_weight for games to {points}")
        if self.max_information_multiplier < 1.0:
            raise ConfigError("max_information_multiplier must be at least 1")
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
        if not 0.0 <= self.established_reliability_threshold <= 100.0:
            raise ConfigError("established_reliability_threshold must be between 0 and 100")
        if not isinstance(self.reject_content_duplicates, bool):
            raise ConfigError("reject_content_duplicates must be a bool")

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

        return {f.name: norm(getattr(self, f.name)) for f in fields(self)}

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
        names = [f.name for f in fields(cls)]
        missing = sorted(set(names) - set(data))
        if missing:
            raise ConfigError(f"Config data has missing keys: {', '.join(missing)}")
        unknown = sorted(str(key) for key in set(data) - set(names))
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
            raise ConfigError(f"{name} has an unknown key: {key!r}") from exc
    return out


def _int_keyed(value: object) -> dict[int, object]:
    if not isinstance(value, Mapping):
        raise ConfigError("game_length_weights must be a mapping")
    out: dict[int, object] = {}
    for key, item in value.items():
        if _is_whole_number(key):
            points = key
        elif isinstance(key, str) and key.isascii() and key.isdigit():
            points = int(key)
        else:
            raise ConfigError(f"game_length_weights has a key that is not a whole number: {key!r}")
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
            raise ConfigError(f"allowed_game_formats has an entry that is not a pair: {item!r}")
        scoring, points = item
        if not isinstance(points, int) or isinstance(points, bool):
            raise ConfigError(
                f"allowed_game_formats has points that are not a whole number: {item!r}"
            )
        try:
            out.add((ScoringType(scoring), points))
        except ValueError as exc:
            raise ConfigError(
                f"allowed_game_formats has an unknown scoring type: {item!r}"
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
    and reserves the match_id: a later record call for a match with it
    that does not raise returns DUPLICATE_MATCH_ID.
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
        """Raise InvalidMatchError (or ConfigError for its game format) when not valid."""
        if not _is_id(self.match_id):
            raise InvalidMatchError("match_id is required for duplicate protection")
        if not isinstance(self.status, MatchStatus):
            raise InvalidMatchError(
                f"status must be a MatchStatus, not {type(self.status).__name__}"
            )
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
        if not isinstance(self.games, (tuple, list)):
            raise InvalidMatchError(
                f"games must be a tuple or list, not {type(self.games).__name__}"
            )
        if completed and not self.games:
            raise InvalidMatchError("At least one game score is required")
        if self.status is MatchStatus.FORFEIT and self.games:
            raise InvalidMatchError("A forfeit cannot have game scores")
        if not all(isinstance(game, (tuple, list)) and len(game) == 2 for game in self.games):
            raise InvalidMatchError("Each game must be a pair of scores")
        if self.best_of < 1 or self.best_of % 2 == 0:
            raise InvalidMatchError("best_of must be a positive odd number")
        if len(self.games) > self.best_of:
            raise InvalidMatchError("Games played cannot exceed best_of")
        self.game_format.validate()
        for score_a, score_b in self.games:
            if not all(
                isinstance(score, int) and not isinstance(score, bool)
                for score in (score_a, score_b)
            ):
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


def _is_id(value: object) -> bool:
    """True for a str with at least one non-whitespace character."""
    return isinstance(value, str) and bool(value.strip())


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
    timezone-aware; a naive value raises InvalidMatchError.
    """
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
        match.validate()
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
        (f.name, _strict(getattr(match, f.name))) for f in fields(match) if f.name != "played_at"
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
            tuple((f.name, _strict(getattr(value, f.name))) for f in fields(value)),
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
        [f.name, _json_form(getattr(match, f.name))] for f in fields(match) if f.name != "played_at"
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
        return [tag, [[f.name, _json_form(getattr(value, f.name))] for f in fields(value)]]
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
    already processed or reserved by a match that was not completed.
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

    established is True when reliability (as displayed, rounded to 1 place)
    is at least Config.established_reliability_threshold. Imported
    reliability never counts toward it, and it is measured at the same
    reference date as reliability, so it lapses with inactivity. A recently
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
    established: bool  # reliability >= Config.established_reliability_threshold
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


@dataclass(frozen=True)
class ReplayInfo:
    """Provenance of one replay: as_of date, config version and fingerprint, counts."""

    as_of: date
    config_version: str
    config_fingerprint: str
    matches_considered: int
    matches_counted: int


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
    cutoff = on - timedelta(days=window_days)
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
    for name in ("matches_played", "live_updates_since_replay", "inherited_live_updates"):
        value = getattr(state, name)
        if not isinstance(value, int) or isinstance(value, bool):
            return f"{name} must be an int"
    for name in ("partners", "opponents"):
        if not isinstance(getattr(state, name), dict):
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
    return None


def _seed_rule_broken(state: RatingState, cfg: Config) -> str | None:
    """The first seed rule a state breaks, or None when it follows every rule."""
    wrong_type = _seed_type_broken(state)
    if wrong_type is not None:
        return wrong_type
    if state.matches_played != 0:
        return "matches_played must be 0"
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
    """Raise InvalidSeedError unless the seed is a Player with a non-blank str id."""
    if not isinstance(player, Player):
        raise InvalidSeedError(f"Seed must be a Player, not {type(player).__name__}")
    if not isinstance(player.id, str):
        raise InvalidSeedError(f"Seed player id must be a str, not {type(player.id).__name__}")
    if not _is_id(player.id):
        raise InvalidSeedError("Seed player id must not be blank")


def _validate_seed(player: Player, cfg: Config) -> None:
    """Raise InvalidSeedError unless the seed and every seed state follow the seed rules."""
    _check_seed_identity(player)
    if not isinstance(player.ratings, dict):
        raise InvalidSeedError(f"Seed for {player.id}: ratings must be a dict")
    if not isinstance(player.external_ids, dict):
        raise InvalidSeedError(f"Seed for {player.id}: external_ids must be a dict")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in player.external_ids.items()):
        raise InvalidSeedError(f"Seed for {player.id}: external_ids must map str to str")
    if player.account_user_id is not None and not isinstance(player.account_user_id, str):
        raise InvalidSeedError(f"Seed for {player.id}: account_user_id must be a str or None")
    if player.account_user_id is not None and not _is_id(player.account_user_id):
        raise InvalidSeedError(f"Seed for {player.id}: account_user_id must not be blank")
    if not all(_is_id(k) and _is_id(v) for k, v in player.external_ids.items()):
        raise InvalidSeedError(
            f"Seed for {player.id}: external_ids must not have a blank source or id"
        )
    for key in player.ratings:
        if not isinstance(key, RatingFormat):
            raise InvalidSeedError(
                f"Seed for {player.id}: ratings key {key!r} is not a RatingFormat"
            )
    for fmt in sorted(player.ratings):
        state = player.ratings[fmt]
        if not isinstance(state, RatingState):
            raise InvalidSeedError(
                f"Seed for {player.id} in {fmt}: rating state must be a RatingState"
            )
        rule = _seed_rule_broken(state, cfg)
        if rule is not None:
            raise InvalidSeedError(f"Seed for {player.id} in {fmt}: {rule}")


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

SNAPSHOT_VERSION = 5

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
        "resumable",
    }
)
_REPLAY_INFO_KEYS = frozenset(
    {"as_of", "config_version", "config_fingerprint", "matches_considered", "matches_counted"}
)
_PLAYER_KEYS = frozenset({"id", "account_user_id", "external_ids", "ratings"})
_STATE_KEYS = frozenset(
    {
        "rating",
        "rd",
        "matches_played",
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
    if not isinstance(value, str):
        raise SnapshotError(f"{path} must be a str")
    return value


def _snap_opt_str(value: object, path: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise SnapshotError(f"{path} must be a str or null")
    return value


def _snap_id(value: object, path: str) -> str:
    if not isinstance(value, str) or not _is_id(value):
        raise SnapshotError(f"{path} must be a non-blank str")
    return value


def _snap_date(value: object, path: str) -> date:
    """A date written as exact YYYY-MM-DD text."""
    message = f"{path} must be a date in YYYY-MM-DD form"
    if not isinstance(value, str):
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
    if isinstance(value, (int, float)) and _is_finite_number(value):
        return value
    raise SnapshotError(f"{path} must be a finite number (an int or float, not bool)")


# The largest count a snapshot holds. json.dumps writes any int up to it.
_MAX_COUNT = 2**63 - 1


def _snap_count(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= _MAX_COUNT:
        raise SnapshotError(f"{path} must be a nonnegative int no larger than 2**63 - 1")
    return value


def _snap_format(value: object, path: str) -> RatingFormat:
    if not isinstance(value, str) or value not in {fmt.value for fmt in RatingFormat}:
        raise SnapshotError(f"{path} {value!r} is not a RatingFormat")
    return RatingFormat(value)


def _snap_date_map(value: object, path: str) -> dict[str, date]:
    if not isinstance(value, Mapping):
        raise SnapshotError(f"{path} must be an object")
    result: dict[str, date] = {}
    for key, on in value.items():
        if not isinstance(key, str) or not _is_id(key):
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
    imported = raw["initial_source_reliability"]
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
        if not isinstance(source, str) or not isinstance(external_id, str):
            raise SnapshotError(f"{path}.external_ids must map str to str")
        if not _is_id(source) or not _is_id(external_id):
            raise SnapshotError(f"{path}.external_ids must map non-blank str to non-blank str")
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
    return Player(
        id=player_id,
        ratings=states,
        external_ids=external_ids,
        account_user_id=account_user_id,
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
        if not all(isinstance(pid, str) and _is_id(pid) for pid in team):
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
    return ReplayInfo(calculated_as_of, version, fingerprint, considered, counted)


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
        if not isinstance(key, str) or len(key) != 64 or not set(key) <= _HEX:
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
        if not isinstance(key, str) or not _is_id(key):
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


def _check_reserved(
    reserved: set[str],
    processed: set[str],
    duplicates: Mapping[str, str],
    considered: Mapping[str, str] | None,
) -> None:
    """
    Raise SnapshotError unless the reserved match ids fit the rest of the snapshot.

    record checks both sets before it adds an id to either, so no reserved
    id is processed. considered is the ledger digests of a resumable
    snapshot, else None. A replay reserves only a row it considered, and
    in one ledger a completed row and a non-completed row with the same id
    conflict, so a reserved id is never a possible duplicate there. Live,
    a copy rejected under reject_content_duplicates and then a
    non-completed match reusing its id is both.
    """
    where = "snapshot.reserved_match_ids names"
    for match_id in sorted(reserved):
        if match_id in processed:
            raise SnapshotError(f"{where} {match_id!r}, which is in processed_match_ids")
        if considered is None:
            continue
        if match_id not in considered:
            raise SnapshotError(f"{where} {match_id!r}, which is not in ledger_digests")
        if match_id in duplicates:
            raise SnapshotError(f"{where} {match_id!r}, which is a possible_duplicates key")


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

    content_keys = _snap_content_keys(raw["content_keys"], processed)
    digests_raw = raw["ledger_digests"]
    if not isinstance(digests_raw, Mapping):
        raise SnapshotError("snapshot.ledger_digests must be an object")
    digests: dict[str, str] = {}
    for key, digest in digests_raw.items():
        if not isinstance(key, str) or not _is_id(key):
            raise SnapshotError("snapshot.ledger_digests has an id that is not a non-blank str")
        if not isinstance(digest, str) or len(digest) != 64 or not set(digest) <= _HEX:
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

    resumable = raw["resumable"]
    if not isinstance(resumable, bool):
        raise SnapshotError("snapshot.resumable must be a bool")

    possible_duplicates = _snap_possible_duplicates(
        raw["possible_duplicates"],
        set(content_keys.values()),
        processed,
        cfg.reject_content_duplicates,
        digests if resumable else None,
    )
    _check_reserved(reserved, processed, possible_duplicates, digests if resumable else None)

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
        # match_id of each match record rejected as not completed. A later
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
        # An entry stays until that match_id later counts with no twin.
        self.possible_duplicates: dict[str, str] = {}
        # Digest of each ledger row a replay considered, by match_id. Only
        # replay fills it; resume compares rows against it.
        self._ledger_digests: dict[str, str] = {}
        # Every player id named in a row a replay considered, counted or
        # not. Resume refuses a seed for one of them.
        self._ledger_player_ids: set[str] = set()
        # True only while the state is exactly what replay produced, so a
        # snapshot of it can be resumed from. Replay sets it at the end.
        self._resumable = False

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
        The engine's whole state as JSON-safe data (schema version 5).

        json.dumps(snapshot, allow_nan=False) works with no custom encoder,
        and RatingEngine.from_snapshot rebuilds the engine from it, or from
        json.loads of its text. Every dict is emitted with sorted keys and
        every list in a fixed order, so the same state always gives the same
        text. Numbers keep their type: an int stays an int.

        Keys: snapshot_version (5), engine_version (informational),
        config_version and config_fingerprint (of this engine's Config),
        calculated_as_of (ISO date or null), replay_info (null, or its
        fields with as_of as an ISO date), players (sorted by id: id,
        account_user_id, external_ids, and ratings by format value, each
        state with every RatingState field, dates as ISO text, and partners
        and opponents as id to ISO date), processed_match_ids (sorted),
        reserved_match_ids (sorted ids of matches record rejected as not
        completed; a later record call for a match with one of these ids
        that does not raise returns DUPLICATE_MATCH_ID),
        self_reported_log (sorted by format then teams: rating_format,
        teams as two sorted id lists, and dates in stored order),
        content_keys (the content key digest of each counted match, see
        MatchResult.possible_duplicate_of, to the match_id first counted
        with it, sorted by digest), possible_duplicates (sorted by
        match_id: each match flagged or rejected as a possible duplicate,
        to the match_id it repeats, until that match_id later counts with
        no twin), ledger_digests (match_id to the
        SHA-256 digest of every row any replay behind this engine
        considered, cumulative across resumes),
        ledger_player_ids (sorted ids of every player named in those rows,
        counted or not), and resumable (true only while the engine is
        exactly what a replay produced). The README lists the schema in
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
            replay_info = _sorted_dict((f.name, _iso(getattr(info, f.name))) for f in fields(info))
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
        the schema's keys at every level, snapshot_version 5, dates as
        YYYY-MM-DD text, non-blank str ids with no repeated player or match
        id, RatingFormat values as format keys, ratings within min_rating
        and max_rating, RD within rd_min and rd_max, an imported
        reliability between 0 and 100, nonnegative int counts no larger
        than 2**63 - 1, replay_info present exactly when calculated_as_of
        is and consistent with it, self-reported matchups of two disjoint
        teams of the format's size, content keys and ledger digests of 64
        lowercase hex characters, content keys that each name a different
        processed match id, possible duplicates that each name another
        match that is a content_keys value and whose own id is not one, a
        possible duplicate id that is a processed match id under the
        default config and is not one under reject_content_duplicates (and,
        in a resumable snapshot, is a ledger_digests id), reserved match
        ids with no repeats and none in processed_match_ids (and, in a
        resumable snapshot, each a ledger_digests id and not a
        possible_duplicates key), and a bool resumable that is false
        without a replay.
        Every state must be one the engine can produce: initial_rating
        within the rating bounds; an unplayed state with RD new_player_rd,
        external_seed_rd, or at least category_seed_rd, and no partners,
        opponents, or live updates; matches_played 0 exactly when
        last_played is null; live updates no more than matches played; no
        player their own partner or opponent; an imported reliability only
        with an initial_source_date; a nonzero inherited_live_updates only
        on a category state derived from its parent (category_feeds_parent
        on, initial_source "open_parent:" plus the parent's value) with a
        parent state, no more than the parent state's live updates, the
        parent's initial_source_date, and no imported reliability, external
        player id, or evidence reference; while such a state is unplayed,
        also an RD of at least category_seed_rd and an rd_as_of that is set
        and no later than the parent state's. Every partner, opponent, and
        self-reported team member is a player in the snapshot. Identities
        follow the seed and link rules: account_user_id is null or a
        non-blank str, every external_ids source and id is a non-blank str,
        and no two players hold the same account or the same (source,
        external id) pair. A resumable snapshot also holds no date after
        calculated_as_of, no nonzero live_updates_since_replay or
        inherited_live_updates, no processed match id without a ledger
        digest, and no player with a played state missing from
        ledger_player_ids, and its replay_info counts equal the number of
        ledger digests and processed match ids. These checks
        cover shape and invariants, not authenticity. Raises SnapshotError
        naming the first bad value. The engine shares no state with the
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
        engine._resumable = state.resumable
        return engine

    # -- players and identity ------------------------------------------------

    def player(self, player_id: str) -> Player:
        """
        The player record for player_id. Read-only: it never creates a player.

        Raises PlayerError for a blank player_id and UnknownPlayerError when
        no player record exists for it. A player is created by add_player,
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
          or whitespace only.
        - rating, rd, initial_rating, and an imported reliability are ints
          or finite floats (not bool), the counts are ints, partners and
          opponents are dicts, and dates are dates.
        - No matches, last_played, partners, opponents, or live updates, and
          inherited_live_updates is 0.
        - rating and initial_rating are within Config.min_rating and
          Config.max_rating.
        - A provisional state (no initial_source_date) has RD new_player_rd,
          no rd_as_of, and no imported reliability.
        - An imported state, or a category state derived from one, has RD
          external_seed_rd or max(external_seed_rd, category_seed_rd),
          rd_as_of equal to initial_source_date, and an imported reliability
          that is None or between 0 and 100.
        Played states are rebuilt through replay instead.
        add_player has no reference date, so it does not check that
        initial_source_date is in the past: replay rejects a seed dated after
        its as_of, and prequential_predictions one dated after its last match.

        Raises InvalidSeedError for a seed that breaks these rules (a seed
        that is not a Player, or has a non-str or blank id, is checked
        before the duplicate id), and PlayerError if the id exists, or if
        another player already holds the seed's account_user_id or one of
        its (source, external id) pairs, with the messages link_account and
        attach_external_id use. Nothing changes when either is raised.
        """
        self._add_seed(player, self._held_identities())

    def _held_identities(self) -> _HeldIdentities:
        """The accounts and (source, external id) pairs the engine's players hold."""
        held = _HeldIdentities()
        for p in self.players.values():
            held.add(p)
        return held

    def _add_seed(self, player: Player, held: _HeldIdentities) -> None:
        """
        add_player, with the identities already held passed in.

        held must hold every identity of the engine's players; it gains the
        seed's identities once the seed is added. A batch of seeds builds it
        once, so each seed's uniqueness check is a set lookup instead of a
        scan of every player.
        """
        _check_seed_identity(player)
        if player.id in self.players:
            raise PlayerError(f"Player already exists: {player.id}")
        _validate_seed(player, self.cfg)
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
        """Raise PlayerError for a blank player id."""
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

        Raises PlayerError for a blank player id, a blank or whitespace-only
        account, or when the player or the account is already linked
        elsewhere. Every check runs before anything changes, so a failed
        call creates no player.
        """
        self._check_account(player_id, account_user_id)
        player = self._player_or_new(player_id)
        self._changed()
        player.account_user_id = account_user_id

    def attach_external_id(self, player_id: str, source: str, external_player_id: str) -> None:
        """
        Link an external identity to a player. Each identity links to one player.

        Raises PlayerError for a blank player id, a blank or whitespace-only
        source or external id, when the player already has a different id
        from that source, or when the identity is linked to another player.
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
        The seed's RD is always Config.external_seed_rd: an external
        reliability is kept for display (imported_reliability), never turned
        into OPEN RD. There is no reference date here, so a snapshot captured
        in the future is accepted; the next replay with an earlier as_of
        rejects the seed.

        rating_format must be a RatingFormat (a str is not converted), and
        the snapshot is checked before anything changes: captured_on is a
        date (not a datetime), source is a str, external_player_id is a str
        or None, rating is an int or finite float (not bool) within the
        rating bounds, and reliability is None or an int or finite float
        (not bool) between 0 and 100. So every state seeded here passes the
        seed rules in add_player.

        Raises InvalidSeedError when the rating format, a checked snapshot
        field, or the existing rating does not allow the seed, and PlayerError
        when player_id is blank or the snapshot's external identity cannot be
        linked (see attach_external_id). Every check runs before anything
        changes, so a failed call creates no player.
        """
        cfg = self.cfg
        # The same type rules the seed check applies, so every state seeded
        # here is one add_player and replay accept.
        if not isinstance(rating_format, RatingFormat):
            raise InvalidSeedError("rating_format must be a RatingFormat")
        if not _is_plain_date(snapshot.captured_on):
            raise InvalidSeedError("External snapshot captured_on must be a date")
        if not isinstance(snapshot.source, str):
            raise InvalidSeedError("External snapshot source must be a str")
        if snapshot.external_player_id is not None and not isinstance(
            snapshot.external_player_id, str
        ):
            raise InvalidSeedError("External snapshot external_player_id must be a str or None")
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
            if not replace_unplayed_seed:
                raise InvalidSeedError(
                    "A rating already exists; explicitly allow replacing an unplayed seed"
                )

        if snapshot.external_player_id:
            self._check_external_id(player_id, snapshot.source, snapshot.external_player_id)

        # Every check has passed: create the player and link the identity.
        player = self._player_or_new(player_id)
        if snapshot.external_player_id:
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
        return state

    def seed_provisional_rating(self, player_id: str, rating_format: RatingFormat) -> RatingState:
        """The player's state in a format. Creates a new unplayed state if it is missing."""
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
        player record exists for the id, and PlayerError for a blank id.
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
        """Calibrated probability that team A wins the match."""
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
        Nothing is changed unless the match counts, except that a match
        rejected as a possible duplicate is recorded in possible_duplicates,
        and a match that is not completed reserves its match_id. Both
        exceptions clear the snapshot's resumable mark.
        A match_id already processed or reserved returns
        RejectionReason.DUPLICATE_MATCH_ID. A match whose status is not
        MatchStatus.COMPLETED is never counted: after the match_id check, it
        returns RejectionReason.MATCH_NOT_COMPLETED and reserves its
        match_id. It is not marked processed and moves no rating, but every
        later call for a match with that id that does not raise returns
        DUPLICATE_MATCH_ID, as replay never counts a second row with one
        id.
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
        match.validate()
        if as_of is not None and not _is_plain_date(as_of):
            raise InvalidMatchError(
                f"as_of must be a plain date (not a datetime), not {type(as_of).__name__}"
            )
        if as_of is not None and as_of < match.played_on:
            raise InvalidMatchError("as_of cannot be earlier than the match date")
        match_id = match.match_id
        if match_id in self.processed_match_ids or match_id in self._reserved_match_ids:
            return MatchResult(False, match.rating_format, RejectionReason.DUPLICATE_MATCH_ID)
        if match.status is not MatchStatus.COMPLETED:
            # Reserve the id, so a later record with it that does not raise
            # is a duplicate, as in replay. No rating, count, or content key
            # changes.
            self._changed()
            self._reserved_match_ids.add(match_id)
            return MatchResult(False, match.rating_format, RejectionReason.MATCH_NOT_COMPLETED)

        cfg = self.cfg
        format_key = (match.game_format.scoring, match.game_format.points_to)
        if format_key not in cfg.allowed_game_formats:
            return MatchResult(False, match.rating_format, RejectionReason.GAME_FORMAT_NOT_ACCEPTED)

        live = as_of is None

        content_key = _content_key(match)
        twin = self._content_keys.get(content_key)
        if twin is not None and cfg.reject_content_duplicates:
            self._changed()
            self.possible_duplicates[match.match_id] = twin
            return MatchResult(
                False,
                match.rating_format,
                RejectionReason.POSSIBLE_DUPLICATE,
                possible_duplicate_of=twin,
            )

        repeat_factor = 1.0
        matchup = None
        if match.match_type is MatchType.SELF_REPORTED:
            if cfg.self_reported_requires_both_teams:
                confirmed = set(match.confirmed_by)
                if not (confirmed & set(match.team_a) and confirmed & set(match.team_b)):
                    return MatchResult(
                        False, match.rating_format, RejectionReason.SELF_REPORTED_UNCONFIRMED
                    )
            matchup = _matchup_key(match)
            cutoff = match.played_on - timedelta(days=cfg.self_reported_repeat_window_days)
            prior = sum(
                1 for d in self.self_reported_log.get(matchup, []) if cutoff <= d <= match.played_on
            )
            if prior >= cfg.self_reported_max_repeats:
                return MatchResult(
                    False, match.rating_format, RejectionReason.SELF_REPORTED_REPEAT_LIMIT
                )
            repeat_factor = cfg.self_reported_repeat_factor**prior

        primary, primary_updates = self._compute(match, match.rating_format, repeat_factor)
        if not primary.counted or primary_updates is None:
            return primary
        primary.possible_duplicate_of = twin

        parent_updates = None
        parent_format = match.rating_format.parent if cfg.category_feeds_parent else None
        if parent_format is not None:
            # A parent rejection (baseline or filter) never blocks the category.
            primary.parent, parent_updates = self._compute(match, parent_format, repeat_factor)

        self._commit(match.rating_format, primary_updates, live)
        if parent_updates and parent_format is not None:
            self._commit(parent_format, parent_updates, live)
        if matchup is not None:
            self.self_reported_log.setdefault(matchup, []).append(match.played_on)
        self._content_keys.setdefault(content_key, match.match_id)
        if twin is None:
            # A match_id rejected as a copy earlier, now counted on its own.
            self.possible_duplicates.pop(match.match_id, None)
        else:
            self.possible_duplicates[match.match_id] = twin
        self.processed_match_ids.add(match.match_id)
        return primary

    def _commit(
        self, rating_format: RatingFormat, updates: dict[str, RatingState], live: bool
    ) -> None:
        for player_id, state in updates.items():
            if live:
                state.live_updates_since_replay += 1
            self._put_state(self._player_or_new(player_id), rating_format, state)

    def _compute(
        self, match: Match, rating_format: RatingFormat, repeat_factor: float
    ) -> tuple[MatchResult, dict[str, RatingState] | None]:
        """Compute one format's update on working copies. Mutates nothing."""
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
            return MatchResult(False, rating_format, RejectionReason.BEFORE_IMPORTED_BASELINE), None

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
            return MatchResult(False, rating_format, RejectionReason.SINGLES_RATING_GAP), None
        if cfg.max_doubles_partner_gap is not None and rating_format.team_size == 2:
            for team_states in (states_a, states_b):
                if abs(team_states[0].rating - team_states[1].rating) > cfg.max_doubles_partner_gap:
                    return MatchResult(
                        False, rating_format, RejectionReason.DOUBLES_PARTNER_GAP
                    ), None

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
        return result, {pid: work[pid] for pid in ids if pid not in frozen}

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

        imported_reliability is not carried when the import's capture date is
        after the reference date. So an unplayed import captured after the
        last replay is not shown by default until the next replay or an
        explicit as_of.

        live_updates_since_recalculation labels the provisional movement in
        the rating: the state's own live updates since the last replay, plus,
        for a category state derived from overall doubles, the parent's live
        updates already in the rating it copied (see PlayerSummary).

        It never changes engine state. For a known player with no state in
        the format, it reports the starting state the player's first match in
        that format would use if played now (see state), without storing it.

        Raises ReferenceDateError when as_of is None and the engine was never
        replayed, or when as_of is not a plain date (a datetime is not one).
        These checks come first. Then it raises UnknownPlayerError when no
        player record exists for the id, and PlayerError for a blank id.
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
        earned = reliability(state, cfg, reference)

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
            established=earned >= cfg.established_reliability_threshold,
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
    checkpoint: date | None = None
    if start is not None:
        checkpoint = start.calculated_as_of
        info = start.replay_info
        if checkpoint is None or info is None:
            raise SnapshotError("Cannot resume: the snapshot is not from a replay")
        _check_resume_rows(rows, checkpoint, start._ledger_digests, whole_ledger)
        considered = info.matches_considered
        counted = info.matches_counted

    for match in rows:
        if match.played_on > as_of:
            continue
        if checkpoint is not None and match.played_on <= checkpoint:
            continue
        considered += 1
        if track:
            engine._ledger_digests[match.match_id] = _match_digest(match)
            engine._ledger_player_ids.update(match.team_a + match.team_b)
        if engine.record(match, as_of=as_of).counted:
            counted += 1

    engine.calculated_as_of = as_of
    engine._resumable = track
    engine.replay_info = ReplayInfo(
        as_of=as_of,
        config_version=engine.cfg.version,
        config_fingerprint=engine.cfg.fingerprint(),
        matches_considered=considered,
        matches_counted=counted,
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
    """
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
        match.validate()
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
    windows: dict[date, list[Match]] = {}
    for match in ordered:
        # Largest k with anchor + k * interval strictly before played_on.
        k = ((match.played_on - anchor).days - 1) // interval
        windows.setdefault(anchor + timedelta(days=k * interval), []).append(match)

    for replay_date, window in sorted(windows.items()):
        if score_from is not None and all(m.played_on < score_from for m in window):
            continue
        engine = _replay(
            [m for m in ordered if m.played_on <= replay_date],
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
    eligibility, mode, score_from and anchor rules.
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


def _check_search_bounds(low: float, high: float, tolerance: float) -> None:
    """Raise EvaluationError for a search range or tolerance the fits cannot use."""
    if not (math.isfinite(low) and math.isfinite(high)):
        raise EvaluationError("low and high must be finite")
    # The search takes differences and midpoints of points in the range.
    if not (math.isfinite(high - low) and math.isfinite(low + high)):
        raise EvaluationError("high - low and low + high must be finite")
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
    Raises EvaluationError, before any prediction work, unless low, high,
    high - low, and low + high are finite, low < high, and tolerance is
    finite and greater than 0.
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

    steps = 14
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

    steps = 14
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
