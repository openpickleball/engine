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
- Authoritative ratings come from scheduled replays (see
  `Config.replay_interval_days`). Live updates between replays are
  provisional and are labeled with the count of live updates since the last
  recalculation. A replay gives the same ratings as recording the same
  ledger live in ledger order: the replay date labels the result but never
  changes an update. Inactivity widens RD (`inflated_rd`) but never shrinks
  or discounts an old match's update.
- PlayerSummary.reliability is always OPEN-earned. An imported external
  reliability is shown separately, in PlayerSummary.imported_reliability,
  only until OPEN-earned reliability exceeds it or the carry limits in
  Config are reached; otherwise that field is None. An external
  reliability never sets OPEN RD: an imported seed's RD is always
  Config.external_seed_rd.
- Ledger order: `replay` and `prequential_predictions` sort matches by
  `played_on` first. Within one `played_on` date, untimed matches come
  first (by `sequence`, then `match_id`), then timed matches (by the UTC
  instant of `played_at`, then `sequence`, then `match_id`). Every row
  passed in is validated, including rows after `as_of`, so a naive
  `played_at` raises InvalidMatchError wherever it sits. Rows that repeat a
  `match_id` collapse to one only when every field matches in type and
  value, with `played_at` compared by its UTC instant and UTC offset; any
  other repeat raises `LedgerConflictError`, even after `as_of`. The
  result never depends on input order.
- Errors: every error the engine raises on purpose is an `OpenEngineError`,
  which is a `ValueError`. `ConfigError` covers Config and GameFormat
  values, `InvalidMatchError` covers matches, teams, scores and
  `played_at`, `InvalidSeedError` covers external and replay seeds
  (including a seed state the engine could not have produced before any
  match, see `RatingEngine.add_player`),
  `PlayerError` covers player identities, `EvaluationError` covers
  evaluation, fits and win probabilities, and `LedgerConflictError` covers
  conflicting ledger rows.
- `record` validates every match first. A valid match whose `match_id` was
  already processed returns a result that is not counted, with
  `RejectionReason.DUPLICATE_MATCH_ID`, instead of raising.

Production notes:
- Persist the match ledger and player identities in a database. This module's
  in-memory dictionaries are an engine, not a persistence layer.
- Require verified identity and authorized evidence before importing an
  external rating. An external refresh can never overwrite an OPEN-earned
  rating once OPEN matches have been processed.
- Store each replay's `ReplayInfo` (as_of date, config version, config
  fingerprint) for auditability.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import UTC, date, datetime, timedelta
from enum import Enum, StrEnum
from typing import Literal

# Explicit re-exports, so `from open_engine.engine import X` passes mypy --strict.
from ._errors import ConfigError as ConfigError
from ._errors import EvaluationError as EvaluationError
from ._errors import InvalidMatchError as InvalidMatchError
from ._errors import InvalidSeedError as InvalidSeedError
from ._errors import LedgerConflictError as LedgerConflictError
from ._errors import OpenEngineError as OpenEngineError
from ._errors import PlayerError as PlayerError
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


@dataclass(frozen=True)
class Config:
    """
    Every rating parameter. Frozen, so a config cannot change during a replay.

    The defaults are uncalibrated placeholders. fingerprint() identifies the
    exact parameter set behind published ratings.
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

    # Match performance. 0.0 means pure point-share performance.
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

    # Imported reliability display.
    import_reliability_carry_matches: int = 10
    import_reliability_max_age_days: int = 365

    # Category doubles matches also update overall doubles.
    category_feeds_parent: bool = True

    # Optional eligibility filters. Disabled by default.
    max_singles_rating_gap: float | None = None
    max_doubles_partner_gap: float | None = None

    def __post_init__(self) -> None:
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

    def fingerprint(self) -> str:
        """Stable hash of every parameter, stored with each replay."""

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

        payload = {f.name: norm(getattr(self, f.name)) for f in fields(self)}
        encoded = json.dumps(payload, sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()[:16]


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
    """One completed match: teams, game scores, date, format, and provenance."""

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

    def validate(self) -> None:
        """Raise InvalidMatchError (or ConfigError for its game format) when not valid."""
        if not _is_id(self.match_id):
            raise InvalidMatchError("match_id is required for duplicate protection")
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
        if self.played_at is not None:
            _utc_instant(self.played_at)
        if self.played_at is not None and self.played_at.date() != self.played_on:
            raise InvalidMatchError("played_at must fall on played_on")
        if self.sequence < 0:
            raise InvalidMatchError("sequence must be nonnegative")
        if not self.games:
            raise InvalidMatchError("At least one game score is required")
        if not all(isinstance(game, (tuple, list)) and len(game) == 2 for game in self.games):
            raise InvalidMatchError("Each game must be a pair of scores")
        if self.best_of < 1 or self.best_of % 2 == 0:
            raise InvalidMatchError("best_of must be a positive odd number")
        if len(self.games) > self.best_of:
            raise InvalidMatchError("Games played cannot exceed best_of")
        self.game_format.validate()
        for score_a, score_b in self.games:
            if not isinstance(score_a, int) or not isinstance(score_b, int):
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
        if running_a < wins_needed and running_b < wins_needed:
            raise InvalidMatchError("Match score does not contain a completed winner")


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
    """value paired with its exact type, recursing into dataclasses and collections."""
    if is_dataclass(value) and not isinstance(value, type):
        return (
            type(value),
            tuple((f.name, _strict(getattr(value, f.name))) for f in fields(value)),
        )
    if isinstance(value, tuple):
        return (type(value), tuple(_strict(item) for item in value))
    if isinstance(value, frozenset):
        return (type(value), frozenset(_strict(item) for item in value))
    return (type(value), value)


class RejectionReason(_StrEnum):
    """Why record() did not count a match."""

    DUPLICATE_MATCH_ID = "duplicate_match_id"
    GAME_FORMAT_NOT_ACCEPTED = "game_format_not_accepted"
    SELF_REPORTED_UNCONFIRMED = "self_reported_unconfirmed"
    SELF_REPORTED_REPEAT_LIMIT = "self_reported_repeat_limit"
    BEFORE_IMPORTED_BASELINE = "before_imported_baseline"
    SINGLES_RATING_GAP = "singles_rating_gap"
    DOUBLES_PARTNER_GAP = "doubles_partner_gap"


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
    parent: MatchResult | None = None  # overall doubles update for categories


@dataclass(frozen=True)
class PlayerSummary:
    """
    A player's published rating in one format, with reliability and provenance.

    reliability is always OPEN-earned. imported_reliability is the imported
    external reliability while it is carried (fresh, within the carry match
    limit, and higher than the earned value), otherwise None. It is shown
    separately and never replaces or converts into reliability.
    """

    player_id: str
    rating_format: RatingFormat
    rating: float  # rounded to 3 places
    reliability: float  # OPEN-earned reliability, 0-100
    imported_reliability: float | None  # carried imported reliability, 0-100
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


def reliability(state: RatingState, cfg: Config, as_of: date | None = None) -> float:
    """
    OPEN-earned reliability (0-100). Never a conversion of an external score.

    Combines rating certainty (from RD, inflated for inactivity) with rolling
    partner/opponent variety.
    """
    if as_of is None:
        as_of = state.rd_as_of or date.today()
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


def _is_finite_number(value: object) -> bool:
    """True for an int or float (not bool) that is finite."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_plain_date(value: object) -> bool:
    """True for a date that is not a datetime (the two do not compare)."""
    return isinstance(value, date) and not isinstance(value, datetime)


def _seed_type_broken(state: RatingState) -> str | None:
    """The first seed field with the wrong type, or None."""
    for name in ("matches_played", "live_updates_since_replay"):
        value = getattr(state, name)
        if not isinstance(value, int) or isinstance(value, bool):
            return f"{name} must be an int"
    for name in ("partners", "opponents"):
        if not isinstance(getattr(state, name), dict):
            return f"{name} must be a dict"
    for name in ("rating", "rd"):
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
    if not cfg.min_rating <= state.rating <= cfg.max_rating:
        return "rating must be between min_rating and max_rating"
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


def _validate_seed(player: Player, cfg: Config) -> None:
    """Raise InvalidSeedError unless every seed state follows the seed rules."""
    for key in player.ratings:
        if not isinstance(key, RatingFormat):
            raise InvalidSeedError(
                f"Seed for {player.id}: ratings key {key!r} is not a RatingFormat"
            )
    for fmt in sorted(player.ratings):
        rule = _seed_rule_broken(player.ratings[fmt], cfg)
        if rule is not None:
            raise InvalidSeedError(f"Seed for {player.id} in {fmt}: {rule}")


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
        self.self_reported_log: dict[MatchupKey, list[date]] = {}
        self.calculated_as_of: date | None = None
        self.replay_info: ReplayInfo | None = None

    # -- players and identity ------------------------------------------------

    def player(self, player_id: str) -> Player:
        """The player record for player_id, created empty if it does not exist."""
        if not player_id or not player_id.strip():
            raise PlayerError("player_id is required")
        if player_id not in self.players:
            self.players[player_id] = Player(id=player_id)
        return self.players[player_id]

    def add_player(self, player: Player) -> None:
        """
        Add a copy of a player record as a seed.

        Seed rules, checked for every rating state:
        - Every ratings key is a RatingFormat. rating, rd, and an imported
          reliability are finite ints or floats (not bool), the counts are
          ints, partners and opponents are dicts, and dates are dates.
        - No matches, last_played, partners, opponents, or live updates.
        - rating is within Config.min_rating and Config.max_rating.
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

        Raises PlayerError if the id exists and InvalidSeedError for a state
        that breaks these rules. Nothing changes when either is raised.
        """
        if player.id in self.players:
            raise PlayerError(f"Player already exists: {player.id}")
        _validate_seed(player, self.cfg)
        self.players[player.id] = clone_player(player)

    def link_account(self, player_id: str, account_user_id: str) -> None:
        """Claim an existing external-only player profile for an OPEN account."""
        if not account_user_id:
            raise PlayerError("account_user_id is required")
        player = self.player(player_id)
        if player.account_user_id not in (None, account_user_id):
            raise PlayerError("Player profile is already linked to another account")
        if any(
            p.account_user_id == account_user_id and p.id != player_id
            for p in self.players.values()
        ):
            raise PlayerError("This account is already linked to another player profile")
        player.account_user_id = account_user_id

    def attach_external_id(self, player_id: str, source: str, external_player_id: str) -> None:
        """Link an external identity to a player. Each identity links to one player."""
        if not source or not external_player_id:
            raise PlayerError("source and external_player_id are required")
        player = self.player(player_id)
        existing = player.external_ids.get(source)
        if existing not in (None, external_player_id):
            raise PlayerError("A different external identity is already linked")
        for other in self.players.values():
            if other.id != player_id and other.external_ids.get(source) == external_player_id:
                raise PlayerError("This external identity is already linked to another player")
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

        Raises InvalidSeedError when the snapshot or the existing rating does
        not allow the seed, and PlayerError when player_id is blank or the
        snapshot's external identity cannot be linked (see attach_external_id).
        """
        cfg = self.cfg
        if not cfg.min_rating <= snapshot.rating <= cfg.max_rating:
            raise InvalidSeedError("External rating is outside the configured scale")
        if snapshot.reliability is not None and not 0 <= snapshot.reliability <= 100:
            raise InvalidSeedError("External reliability must be between 0 and 100")

        player = self.player(player_id)
        existing = player.ratings.get(rating_format)
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
        player.ratings[rating_format] = state
        return state

    def seed_provisional_rating(self, player_id: str, rating_format: RatingFormat) -> RatingState:
        """The player's state in a format. Creates a new unplayed state if it is missing."""
        player = self.player(player_id)
        if rating_format not in player.ratings:
            player.ratings[rating_format] = self._new_state(player_id, rating_format)
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
        """The player's current state in a format. Same as seed_provisional_rating."""
        return self.seed_provisional_rating(player_id, rating_format)

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
            sum(ratings_a) / len(ratings_a),
            sum(ratings_b) / len(ratings_b),
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
        Nothing is changed unless the match counts.
        """
        match.validate()
        if match.match_id in self.processed_match_ids:
            return MatchResult(False, match.rating_format, RejectionReason.DUPLICATE_MATCH_ID)

        cfg = self.cfg
        format_key = (match.game_format.scoring, match.game_format.points_to)
        if format_key not in cfg.allowed_game_formats:
            return MatchResult(False, match.rating_format, RejectionReason.GAME_FORMAT_NOT_ACCEPTED)

        live = as_of is None
        as_of = as_of or match.played_on
        if as_of < match.played_on:
            raise InvalidMatchError("as_of cannot be earlier than the match date")

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
        self.processed_match_ids.add(match.match_id)
        return primary

    def _commit(
        self, rating_format: RatingFormat, updates: dict[str, RatingState], live: bool
    ) -> None:
        for player_id, state in updates.items():
            if live:
                state.live_updates_since_replay += 1
            self.player(player_id).ratings[rating_format] = state

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

        states_a = [work[pid] for pid in match.team_a]
        states_b = [work[pid] for pid in match.team_b]
        rating_a = sum(s.rating for s in states_a) / len(states_a)
        rating_b = sum(s.rating for s in states_b) / len(states_b)

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
                state.rd_as_of = match.played_on
                state.matches_played += 1
                state.last_played = match.played_on
                state.partners.update(
                    {other: match.played_on for other in team_ids if other != pid}
                )
                state.opponents.update({other: match.played_on for other in opponent_ids})
                applied[pid] = state.rating - old_rating
                deltas[pid] = round(applied[pid], 5)

        # From the unrounded changes, so rounding residue never shows. Adding
        # 0.0 turns a negative zero into 0.0.
        imbalance = (
            round(
                sum(applied[pid] for pid in match.team_a) / len(match.team_a)
                + sum(applied[pid] for pid in match.team_b) / len(match.team_b),
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
        )
        return result, {pid: work[pid] for pid in ids if pid not in frozen}

    # -- reporting -------------------------------------------------------------

    def player_summary(
        self, player_id: str, rating_format: RatingFormat, as_of: date | None = None
    ) -> PlayerSummary:
        """
        The player's rating, reliability, and provenance in one format.

        as_of sets the date that inactivity and reliability are measured at.
        It defaults to the date of the player's last RD update, or today.
        """
        cfg = self.cfg
        player = self.player(player_id)
        state = self.state(player_id, rating_format)
        reference = as_of or state.rd_as_of or date.today()
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
            rd=round(inflated_rd(state, reference, cfg), 4),
            matches_played=state.matches_played,
            last_played=state.last_played,
            initial_rating=state.initial_rating,
            initial_source=state.initial_source,
            initial_source_reliability=state.initial_source_reliability,
            initial_source_date=state.initial_source_date,
            calculated_as_of=self.calculated_as_of,
            live_updates_since_recalculation=state.live_updates_since_replay,
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
) -> RatingEngine:
    """
    Rebuild authoritative ratings from seeds plus the immutable match ledger.

    Run on the published schedule (Config.replay_interval_days). Same ledger,
    seeds, config and as_of always produce the same result, whatever order
    the ledger rows arrive in. Every row is validated, even after as_of.
    Rows that repeat a match_id and match in every field, in type and
    value, are counted once; any other repeat raises LedgerConflictError, even when it
    falls after as_of. Seeds must be
    unplayed: imported or provisional states only.

    Raises InvalidSeedError for a seed state that breaks the seed rules (see
    add_player) or whose initial_source_date is after as_of, and PlayerError
    when two seeds share a player id.
    """
    return _replay(matches, seed_players, cfg=cfg, as_of=as_of, check_seed_dates=True)


def _replay(
    matches: Iterable[Match],
    seed_players: Iterable[Player],
    *,
    cfg: Config | None,
    as_of: date,
    check_seed_dates: bool,
) -> RatingEngine:
    """
    replay, with the seed date check optional.

    Scheduled prequential windows replay to dates before a seed captured
    mid-ledger, which is legitimate there (earlier matches are frozen for
    that player), so they skip the check.
    """
    engine = RatingEngine(cfg)
    for player in seed_players:
        engine.add_player(player)
        if check_seed_dates:
            _check_seed_date(player, as_of, "as_of")

    considered = counted = 0
    for match in _ledger(matches):
        if match.played_on > as_of:
            continue
        considered += 1
        if engine.record(match, as_of=as_of).counted:
            counted += 1

    engine.calculated_as_of = as_of
    engine.replay_info = ReplayInfo(
        as_of=as_of,
        config_version=engine.cfg.version,
        config_fingerprint=engine.cfg.fingerprint(),
        matches_considered=considered,
        matches_counted=counted,
    )
    return engine


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
    its scoring equals `scoring` when given, and it is played on or after
    `score_from` when given. Only matches that `record` counts are kept.

    "live" records every match live in one engine, in ledger order.
    "scheduled" mirrors production: replay dates are
    `anchor + k * cfg.replay_interval_days` (anchor defaults to the first match
    date). A match on day d is predicted from replay(matches on or before s,
    as_of=s) for the largest replay date s strictly before d, and matches in
    that window are then predicted and recorded live in ledger order.

    Every seed is validated up front, in both modes, by the seed rules (see
    RatingEngine.add_player), and a seed whose initial_source_date is after
    the last ledger match raises InvalidSeedError. A seed captured mid-ledger
    is accepted: scheduled replays before its date freeze it as usual.
    """
    if mode not in ("live", "scheduled"):
        raise EvaluationError(f"mode must be 'live' or 'scheduled', not {mode!r}")
    cfg = cfg or Config()
    ordered = _ledger(matches)
    seeds = list(seed_players)
    for player in seeds:
        _validate_seed(player, cfg)
        if ordered:
            _check_seed_date(player, ordered[-1].played_on, "the last ledger match")
    predictions: list[PrequentialPrediction] = []

    def step(engine: RatingEngine, match: Match) -> None:
        match.validate()
        eligible = (
            (scoring is None or match.game_format.scoring is scoring)
            and (score_from is None or match.played_on >= score_from)
            and _is_established(engine, match, burn_in_matches)
        )
        prediction = _predict_match(engine, match) if eligible else None
        if engine.record(match).counted and prediction is not None:
            predictions.append(prediction)

    if mode == "live":
        engine = RatingEngine(cfg)
        for player in seeds:
            engine.add_player(player)
        for match in ordered:
            step(engine, match)
        return predictions

    if not ordered:
        return predictions
    interval = cfg.replay_interval_days
    if anchor is None:
        anchor = ordered[0].played_on
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
                    round(sum(e for e, _ in members) / len(members), 4),
                    round(sum(a for _, a in members) / len(members), 4),
                    len(members),
                )
            )
    mse = sum(e * e for e in errors) / len(errors)
    report = EvaluationReport(
        scoring=scoring.value if scoring else None,
        scale=cfg.scale_by_scoring[scoring] if scoring else None,
        matches_scored=len(pairs),
        mse=round(mse, 6),
        mae=round(sum(abs(e) for e in errors) / len(errors), 6),
        bias=round(sum(errors) / len(errors), 6),
        calibration=tuple(bins),
    )
    return report, mse


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
    """
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
        if run(c)[1] <= run(d)[1]:
            b, d = d, c
            c = b - ratio * (b - a)
        else:
            a, c = c, d
            d = a + ratio * (b - a)
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
    Raises EvaluationError when no prediction remains.
    """
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
            cache[key] = sum((calibrate_win_probability(r, key) - w) ** 2 for r, w in data) / len(
                data
            )
        return cache[key]

    steps = 14
    grid = [low + (high - low) * i / steps for i in range(steps + 1)]
    best = min(grid, key=brier)
    step = (high - low) / steps
    lo, hi = max(low, best - step), min(high, best + step)

    ratio = (math.sqrt(5) - 1) / 2
    c, d = hi - ratio * (hi - lo), lo + ratio * (hi - lo)
    while hi - lo > tolerance:
        if brier(c) <= brier(d):
            hi, d = d, c
            c = hi - ratio * (hi - lo)
        else:
            lo, c = c, d
            d = lo + ratio * (hi - lo)
    fitted = round((lo + hi) / 2, 3)

    return WinCalibrationFit(
        scoring=scoring.value,
        a=fitted,
        brier=brier(fitted),
        brier_uncalibrated=brier(1.0),
        n=len(data),
        at_bound=fitted - low <= tolerance or high - fitted <= tolerance,
    )
