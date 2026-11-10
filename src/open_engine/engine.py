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
- Team average movement is zero-sum, with two documented exceptions, both
  reported on every MatchResult:
    1. `clamped`: a player hit the rating floor or ceiling, so part of their
       change was not applied.
    2. `frozen`: the match is on or before a player's imported baseline date.
       The imported rating already reflects that match, so that player's
       rating is used for the expectation but does not move. Their teammates
       still receive only their own share, so the team moves less.
- Authoritative ratings come from scheduled replays (see
  `Config.replay_interval_days`). Live updates between replays are
  provisional and are labeled with the count of live updates since the last
  recalculation. Because recency decay depends on the replay date, every
  recalculation shifts ratings slightly even with no new matches.
- An imported external reliability is shown separately from OPEN-earned
  reliability. It is carried, clearly labeled, only until OPEN-earned
  reliability exceeds it or the carry limits in Config are reached.

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
from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime, time, timedelta
from enum import Enum

# ---------------------------------------------------------------------------
# Enums and game formats
# ---------------------------------------------------------------------------


class RatingFormat(str, Enum):
    SINGLES = "singles"
    DOUBLES = "doubles"
    MIXED_DOUBLES = "mixed_doubles"
    DOUBLES_MEN = "doubles_men"
    DOUBLES_WOMEN = "doubles_women"

    @property
    def team_size(self) -> int:
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


class MatchType(str, Enum):
    TOURNAMENT = "tournament"
    LEAGUE = "league"
    REC_VERIFIED = "rec_verified"
    SELF_REPORTED = "self_reported"


class ScoringType(str, Enum):
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
        if self.points_to < 1:
            raise ValueError("points_to must be positive")
        if self.win_by < 1:
            raise ValueError("win_by must be positive")
        if self.cap is not None and self.cap <= self.points_to:
            raise ValueError("cap must be greater than points_to")

    def validate_game(self, score_a: int, score_b: int) -> None:
        high, low = max(score_a, score_b), min(score_a, score_b)
        if high < self.points_to:
            raise ValueError("A game must reach its target score")
        if self.cap is not None and high > self.cap:
            raise ValueError("Game score exceeds the cap")
        if high == self.points_to:
            if high - low < self.win_by:
                raise ValueError("Game was not won by the required margin")
            return
        # Extended game (deuce): it ends the moment the margin is reached,
        # or at the cap.
        if self.cap is not None and high == self.cap:
            if not high - self.win_by <= low <= high - 1:
                raise ValueError("Capped game score is not reachable")
            return
        if high - low != self.win_by:
            raise ValueError("Extended game must end at exactly the win-by margin")


ALL_GAME_FORMATS: frozenset[tuple[ScoringType, int]] = frozenset(
    (scoring, points) for scoring in ScoringType for points in (11, 15, 21)
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    version: str = "0.1.0"

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

    # Per-player responsiveness. K is derived from RD, then normalized across
    # the match so team-average movement is zero-sum before clamps/freezes.
    k_min: float = 0.12
    k_max: float = 0.60

    # Rating deviation (RD) is OPEN's internal uncertainty measure.
    rd_min: float = 0.10
    rd_max: float = 0.80
    new_player_rd: float = 0.80
    external_seed_rd: float = 0.42
    category_seed_rd: float = 0.50
    external_reliability_maps_to_rd: bool = False
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

    # Replay-based time decay. A match's influence halves for each half-life
    # between match date and replay as_of date. None disables decay.
    recency_half_life_days: int | None = 365

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
            raise ValueError("min_rating must be less than max_rating")
        if not self.min_rating <= self.default_rating <= self.max_rating:
            raise ValueError("default_rating must be within rating bounds")
        if set(self.scale_by_scoring) != set(ScoringType):
            raise ValueError("scale_by_scoring needs a value for every scoring type")
        if any(value <= 0 for value in self.scale_by_scoring.values()):
            raise ValueError("scales must be positive")
        if not 0 <= self.k_min <= self.k_max:
            raise ValueError("K-factor bounds are invalid")
        if not 0 < self.rd_min <= self.rd_max:
            raise ValueError("RD bounds are invalid")
        for name in ("new_player_rd", "external_seed_rd", "category_seed_rd"):
            if not self.rd_min <= getattr(self, name) <= self.rd_max:
                raise ValueError(f"{name} must be within RD bounds")
        if not 0.0 <= self.win_weight <= 1.0:
            raise ValueError("win_weight must be between 0 and 1")
        if set(self.match_weights) != set(MatchType):
            raise ValueError("match_weights needs a value for every match type")
        for _, points in self.allowed_game_formats:
            if points not in self.game_length_weights:
                raise ValueError(f"No game_length_weight for games to {points}")
        if self.max_information_multiplier < 1.0:
            raise ValueError("max_information_multiplier must be at least 1")
        if self.recency_half_life_days is not None and self.recency_half_life_days <= 0:
            raise ValueError("recency_half_life_days must be positive or None")
        if self.replay_interval_days <= 0:
            raise ValueError("replay_interval_days must be positive")
        if self.diversity_window_days <= 0 or self.diversity_target <= 0:
            raise ValueError("diversity settings must be positive")
        if not 0.0 < self.self_reported_repeat_factor <= 1.0:
            raise ValueError("self_reported_repeat_factor must be in (0, 1]")
        if self.self_reported_max_repeats < 1 or self.self_reported_repeat_window_days <= 0:
            raise ValueError("self-reported repeat limits must be positive")
        if self.import_reliability_carry_matches < 0 or self.import_reliability_max_age_days < 0:
            raise ValueError("import reliability limits must be nonnegative")

    def fingerprint(self) -> str:
        """Stable hash of every parameter, stored with each replay."""

        def norm(value):
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
    source: str
    rating: float
    captured_on: date
    reliability: float | None = None
    external_player_id: str | None = None
    evidence_reference: str | None = None


@dataclass
class RatingState:
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
    match_id: str
    team_a: tuple[str, ...]
    team_b: tuple[str, ...]
    games: tuple[tuple[int, int], ...]
    played_on: date
    rating_format: RatingFormat
    match_type: MatchType = MatchType.REC_VERIFIED
    best_of: int = 1
    game_format: GameFormat = GameFormat()

    # Order of play within a day. Platforms should send played_at when they
    # have it; sequence breaks ties (for example, round number).
    played_at: datetime | None = None
    sequence: int = 0

    # Players who confirmed the reported score. Required for self-reported
    # matches: at least one player from each team.
    confirmed_by: frozenset[str] = frozenset()

    def validate(self) -> None:
        if not self.match_id.strip():
            raise ValueError("match_id is required for duplicate protection")
        expected_size = self.rating_format.team_size
        if len(self.team_a) != expected_size or len(self.team_b) != expected_size:
            raise ValueError(
                f"{self.rating_format.value} requires {expected_size} player(s) per team"
            )
        all_ids = self.team_a + self.team_b
        if len(set(all_ids)) != len(all_ids):
            raise ValueError("A player cannot appear more than once in a match")
        if not set(self.confirmed_by) <= set(all_ids):
            raise ValueError("confirmed_by may only contain match participants")
        if self.played_at is not None and self.played_at.date() != self.played_on:
            raise ValueError("played_at must fall on played_on")
        if self.sequence < 0:
            raise ValueError("sequence must be nonnegative")
        if not self.games:
            raise ValueError("At least one game score is required")
        if self.best_of < 1 or self.best_of % 2 == 0:
            raise ValueError("best_of must be a positive odd number")
        if len(self.games) > self.best_of:
            raise ValueError("Games played cannot exceed best_of")
        self.game_format.validate()
        for score_a, score_b in self.games:
            if not isinstance(score_a, int) or not isinstance(score_b, int):
                raise ValueError("Game scores must be integers")
            if score_a < 0 or score_b < 0 or score_a == score_b:
                raise ValueError("Game scores must be nonnegative and cannot be tied")
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
                    raise ValueError("No games may be recorded after the match is decided")
                break
        if running_a < wins_needed and running_b < wins_needed:
            raise ValueError("Match score does not contain a completed winner")


def ledger_order(match: Match) -> tuple:
    """Deterministic order of play: date, time, sequence, then match_id."""
    played_time = match.played_at.replace(tzinfo=None).time() if match.played_at else time.min
    return (match.played_on, played_time, match.sequence, match.match_id)


@dataclass
class MatchResult:
    counted: bool
    reason: str = ""
    rating_format: str = ""
    expected_a: float = 0.0
    actual_a: float = 0.0
    surprise: float = 0.0
    effective_weight: float = 0.0
    deltas: dict[str, float] = field(default_factory=dict)
    frozen: tuple[str, ...] = ()  # predates the player's imported baseline
    clamped: tuple[str, ...] = ()  # hit the rating floor or ceiling
    parent: MatchResult | None = None  # overall doubles update for categories


@dataclass(frozen=True)
class ReplayInfo:
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
    return 1.0 / (1.0 + 10.0**exponent)


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


def recency_multiplier(played_on: date, as_of: date, cfg: Config) -> float:
    if cfg.recency_half_life_days is None:
        return 1.0
    age_days = max(0, (as_of - played_on).days)
    return 0.5 ** (age_days / cfg.recency_half_life_days)


def _actual_point_share(match: Match, cfg: Config) -> tuple[float, bool]:
    points_a = sum(a for a, _ in match.games)
    points_b = sum(b for _, b in match.games)
    total = points_a + points_b
    if total <= 0:
        raise ValueError("Match must contain at least one point")
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


def _matchup_key(match: Match) -> tuple:
    return (
        match.rating_format,
        frozenset({frozenset(match.team_a), frozenset(match.team_b)}),
    )


# ---------------------------------------------------------------------------
# Rating engine
# ---------------------------------------------------------------------------


class RatingEngine:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config()
        self.players: dict[str, Player] = {}
        self.processed_match_ids: set[str] = set()
        self.self_reported_log: dict[tuple, list[date]] = {}
        self.calculated_as_of: date | None = None
        self.replay_info: ReplayInfo | None = None

    # -- players and identity ------------------------------------------------

    def player(self, player_id: str) -> Player:
        if not player_id or not player_id.strip():
            raise ValueError("player_id is required")
        if player_id not in self.players:
            self.players[player_id] = Player(id=player_id)
        return self.players[player_id]

    def add_player(self, player: Player) -> None:
        if player.id in self.players:
            raise ValueError(f"Player already exists: {player.id}")
        self.players[player.id] = clone_player(player)

    def link_account(self, player_id: str, account_user_id: str) -> None:
        """Claim an existing external-only player profile for an OPEN account."""
        if not account_user_id:
            raise ValueError("account_user_id is required")
        player = self.player(player_id)
        if player.account_user_id not in (None, account_user_id):
            raise ValueError("Player profile is already linked to another account")
        if any(
            p.account_user_id == account_user_id and p.id != player_id
            for p in self.players.values()
        ):
            raise ValueError("This account is already linked to another player profile")
        player.account_user_id = account_user_id

    def attach_external_id(self, player_id: str, source: str, external_player_id: str) -> None:
        if not source or not external_player_id:
            raise ValueError("source and external_player_id are required")
        player = self.player(player_id)
        existing = player.external_ids.get(source)
        if existing not in (None, external_player_id):
            raise ValueError("A different external identity is already linked")
        for other in self.players.values():
            if other.id != player_id and other.external_ids.get(source) == external_player_id:
                raise ValueError("This external identity is already linked to another player")
        player.external_ids[source] = external_player_id

    # -- seeding ---------------------------------------------------------------

    def seed_external_rating(
        self,
        player_id: str,
        rating_format: RatingFormat,
        snapshot: ExternalRatingSnapshot,
        *,
        rd_override: float | None = None,
        replace_unplayed_seed: bool = False,
    ) -> RatingState:
        """
        Initialize an OPEN rating from a verified external snapshot.

        The external rating is an initial condition, not a live dependency.
        Once OPEN matches exist, external refreshes cannot overwrite the rating.
        """
        cfg = self.cfg
        if not cfg.min_rating <= snapshot.rating <= cfg.max_rating:
            raise ValueError("External rating is outside the configured scale")
        if snapshot.reliability is not None and not 0 <= snapshot.reliability <= 100:
            raise ValueError("External reliability must be between 0 and 100")

        player = self.player(player_id)
        existing = player.ratings.get(rating_format)
        if existing is not None:
            if existing.matches_played > 0:
                raise ValueError(
                    "Cannot replace an OPEN rating after OPEN matches have been processed"
                )
            if not replace_unplayed_seed:
                raise ValueError(
                    "A rating already exists; explicitly allow replacing an unplayed seed"
                )

        if rd_override is not None:
            rd = rd_override
        elif cfg.external_reliability_maps_to_rd and snapshot.reliability is not None:
            # Must be empirically calibrated before production use.
            fraction = snapshot.reliability / 100.0
            rd = cfg.rd_max - fraction * (cfg.rd_max - cfg.rd_min)
        else:
            rd = cfg.external_seed_rd

        if not cfg.rd_min <= rd <= cfg.rd_max:
            raise ValueError("Seed RD is outside configured bounds")

        if snapshot.external_player_id:
            self.attach_external_id(player_id, snapshot.source, snapshot.external_player_id)

        state = RatingState(
            rating=snapshot.rating,
            rd=rd,
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
        return self.seed_provisional_rating(player_id, rating_format)

    # -- prediction ------------------------------------------------------------

    def predict(
        self,
        team_a: Sequence[str],
        team_b: Sequence[str],
        rating_format: RatingFormat,
        scoring: ScoringType = ScoringType.SIDE_OUT,
    ) -> float:
        self._validate_teams(team_a, team_b, rating_format)
        ratings_a = [self._peek_state(pid, rating_format).rating for pid in team_a]
        ratings_b = [self._peek_state(pid, rating_format).rating for pid in team_b]
        return expected_point_share(
            sum(ratings_a) / len(ratings_a),
            sum(ratings_b) / len(ratings_b),
            self.cfg.scale_by_scoring[scoring],
        )

    def _validate_teams(
        self, team_a: Sequence[str], team_b: Sequence[str], rating_format: RatingFormat
    ) -> None:
        size = rating_format.team_size
        if len(team_a) != size or len(team_b) != size:
            raise ValueError(f"{rating_format.value} requires {size} player(s) per team")
        ids = list(team_a) + list(team_b)
        if len(set(ids)) != len(ids):
            raise ValueError("A player cannot appear more than once in a match")

    # -- recording -------------------------------------------------------------

    def record(self, match: Match, *, as_of: date | None = None) -> MatchResult:
        """
        Process one match exactly once.

        Leave `as_of` unset for live processing (labeled provisional until the
        next replay). replay() passes a fixed as_of for authoritative rebuilds.
        Nothing is changed unless the match counts.
        """
        match.validate()
        if match.match_id in self.processed_match_ids:
            return MatchResult(False, "duplicate match_id")

        cfg = self.cfg
        format_key = (match.game_format.scoring, match.game_format.points_to)
        if format_key not in cfg.allowed_game_formats:
            return MatchResult(False, "game format is not accepted for rating")

        live = as_of is None
        as_of = as_of or match.played_on
        if as_of < match.played_on:
            raise ValueError("as_of cannot be earlier than the match date")

        repeat_factor = 1.0
        matchup = None
        if match.match_type is MatchType.SELF_REPORTED:
            if cfg.self_reported_requires_both_teams:
                confirmed = set(match.confirmed_by)
                if not (confirmed & set(match.team_a) and confirmed & set(match.team_b)):
                    return MatchResult(
                        False, "self-reported match needs confirmation from both teams"
                    )
            matchup = _matchup_key(match)
            cutoff = match.played_on - timedelta(days=cfg.self_reported_repeat_window_days)
            prior = sum(
                1 for d in self.self_reported_log.get(matchup, []) if cutoff <= d <= match.played_on
            )
            if prior >= cfg.self_reported_max_repeats:
                return MatchResult(False, "self-reported matchup repeat limit reached")
            repeat_factor = cfg.self_reported_repeat_factor**prior

        primary, primary_updates = self._compute(match, match.rating_format, as_of, repeat_factor)
        if not primary.counted:
            return primary

        parent_updates = None
        parent_format = match.rating_format.parent if cfg.category_feeds_parent else None
        if parent_format is not None:
            # A parent rejection (baseline or filter) never blocks the category.
            primary.parent, parent_updates = self._compute(
                match, parent_format, as_of, repeat_factor
            )

        self._commit(match.rating_format, primary_updates, live)
        if parent_updates:
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
        self, match: Match, rating_format: RatingFormat, as_of: date, repeat_factor: float
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
            if work[pid].initial_source_date is not None
            and match.played_on <= work[pid].initial_source_date
        }
        if len(frozen) == len(ids):
            return MatchResult(
                False,
                "match is on or before every participant's imported baseline",
                rating_format=rating_format.value,
            ), None

        states_a = [work[pid] for pid in match.team_a]
        states_b = [work[pid] for pid in match.team_b]
        rating_a = sum(s.rating for s in states_a) / len(states_a)
        rating_b = sum(s.rating for s in states_b) / len(states_b)

        if (
            rating_format is RatingFormat.SINGLES
            and cfg.max_singles_rating_gap is not None
            and abs(rating_a - rating_b) > cfg.max_singles_rating_gap
        ):
            return MatchResult(
                False,
                "singles rating gap exceeds configured limit",
                rating_format=rating_format.value,
            ), None
        if cfg.max_doubles_partner_gap is not None and rating_format.team_size == 2:
            for team_states in (states_a, states_b):
                if abs(team_states[0].rating - team_states[1].rating) > cfg.max_doubles_partner_gap:
                    return MatchResult(
                        False,
                        "doubles partner rating gap exceeds configured limit",
                        rating_format=rating_format.value,
                    ), None

        for pid in ids:
            if pid not in frozen:
                work[pid].rd = inflated_rd(work[pid], match.played_on, cfg)

        scale = cfg.scale_by_scoring[match.game_format.scoring]
        actual, _ = _actual_point_share(match, cfg)
        expected = expected_point_share(rating_a, rating_b, scale)
        surprise = actual - expected

        effective_weight = (
            cfg.match_weights[match.match_type]
            * repeat_factor
            * information_multiplier(match, cfg)
            * recency_multiplier(match.played_on, as_of, cfg)
        )

        # One shared match K keeps team-average movement zero-sum. Within each
        # team, higher-uncertainty players receive a larger share.
        all_k = [player_k(work[pid], cfg) for pid in ids]
        match_k = sum(all_k) / len(all_k)
        team_size = rating_format.team_size

        deltas: dict[str, float] = {}
        clamped: list[str] = []
        for team_ids, opponent_ids, sign in (
            (match.team_a, match.team_b, 1.0),
            (match.team_b, match.team_a, -1.0),
        ):
            team_ks = [player_k(work[pid], cfg) for pid in team_ids]
            team_k_total = sum(team_ks)
            for pid, individual_k in zip(team_ids, team_ks, strict=True):
                if pid in frozen:
                    deltas[pid] = 0.0
                    continue
                state = work[pid]
                raw_delta = (
                    sign
                    * team_size
                    * match_k
                    * effective_weight
                    * surprise
                    * (individual_k / team_k_total)
                )
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
                deltas[pid] = round(state.rating - old_rating, 5)

        result = MatchResult(
            counted=True,
            rating_format=rating_format.value,
            expected_a=round(expected, 5),
            actual_a=round(actual, 5),
            surprise=round(surprise, 5),
            effective_weight=round(effective_weight, 5),
            deltas=deltas,
            frozen=tuple(sorted(frozen)),
            clamped=tuple(clamped),
        )
        return result, {pid: work[pid] for pid in ids if pid not in frozen}

    # -- reporting -------------------------------------------------------------

    def player_summary(
        self, player_id: str, rating_format: RatingFormat, as_of: date | None = None
    ) -> dict:
        cfg = self.cfg
        player = self.player(player_id)
        state = self.state(player_id, rating_format)
        reference = as_of or state.rd_as_of or date.today()
        earned = reliability(state, cfg, reference)

        # Imported reliability is shown, labeled, only while it is fresh,
        # higher than what OPEN has earned, and within the carry match limit.
        displayed, basis = earned, "open"
        seed_reliability = state.initial_source_reliability
        if (
            seed_reliability is not None
            and state.initial_source_date is not None
            and state.matches_played < cfg.import_reliability_carry_matches
            and (reference - state.initial_source_date).days <= cfg.import_reliability_max_age_days
            and seed_reliability > earned
        ):
            displayed, basis = round(seed_reliability, 1), "imported"

        return {
            "player_id": player.id,
            "rating_format": rating_format.value,
            "rating": round(state.rating, 3),
            "reliability": displayed,
            "reliability_basis": basis,
            "open_reliability": earned,
            "rd": round(inflated_rd(state, reference, cfg), 4),
            "matches_played": state.matches_played,
            "last_played": state.last_played.isoformat() if state.last_played else None,
            "initial_rating": state.initial_rating,
            "initial_source": state.initial_source,
            "initial_source_date": (
                state.initial_source_date.isoformat() if state.initial_source_date else None
            ),
            "initial_source_reliability": state.initial_source_reliability,
            "calculated_as_of": (
                self.calculated_as_of.isoformat() if self.calculated_as_of else None
            ),
            "live_updates_since_recalculation": state.live_updates_since_replay,
            "config_version": cfg.version,
            "account_linked": player.account_user_id is not None,
        }


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
    seeds, config and as_of always produce the same result. Seeds must be
    unplayed: imported or provisional states only.
    """
    engine = RatingEngine(cfg)
    for player in seed_players:
        if any(state.matches_played for state in player.ratings.values()):
            raise ValueError(f"Seed for {player.id} already contains OPEN matches")
        engine.add_player(player)

    considered = counted = 0
    for match in sorted(matches, key=ledger_order):
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
    scoring: str | None
    scale: float | None
    matches_scored: int
    mse: float
    mae: float
    bias: float  # mean(actual - expected); near zero when well calibrated
    calibration: tuple[tuple[float, float, float, int], ...]
    # Each bin: (expected lower bound, mean expected, mean actual, count)


def evaluate(
    matches: Iterable[Match],
    seed_players: Iterable[Player] = (),
    *,
    cfg: Config | None = None,
    scoring: ScoringType | None = None,
    burn_in_matches: int = 5,
) -> EvaluationReport:
    """
    Prequential evaluation: each match is predicted before it updates ratings.

    Only matches where every participant is established (imported baseline,
    or at least `burn_in_matches` OPEN matches in that format) are scored.
    """
    cfg = cfg or Config()
    engine = RatingEngine(cfg)
    for player in seed_players:
        engine.add_player(player)

    pairs: list[tuple[float, float]] = []
    for match in sorted(matches, key=ledger_order):
        scored = scoring is None or match.game_format.scoring is scoring
        if scored:
            scored = all(
                (s := engine._peek_state(pid, match.rating_format)).initial_source_date is not None
                or s.matches_played >= burn_in_matches
                for pid in match.team_a + match.team_b
            )
        result = engine.record(match)
        if scored and result.counted:
            pairs.append((result.expected_a, result.actual_a))

    if not pairs:
        raise ValueError("No established matches to evaluate")
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
    return EvaluationReport(
        scoring=scoring.value if scoring else None,
        scale=cfg.scale_by_scoring[scoring] if scoring else None,
        matches_scored=len(pairs),
        mse=round(sum(e * e for e in errors) / len(errors), 6),
        mae=round(sum(abs(e) for e in errors) / len(errors), 6),
        bias=round(sum(errors) / len(errors), 6),
        calibration=tuple(bins),
    )


@dataclass(frozen=True)
class ScaleFit:
    scoring: str
    scale: float
    default_scale: float
    fitted: EvaluationReport
    at_default: EvaluationReport


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
) -> ScaleFit:
    """
    Fit one scoring type's scale by minimizing prequential point-share MSE.

    Coarse grid first, then golden-section search around the best grid point.
    Publish the returned ScaleFit alongside the config that adopts it.
    """
    cfg = cfg or Config()
    ledger = list(matches)
    seeds = list(seed_players)
    cache: dict[float, EvaluationReport] = {}

    def run(scale: float) -> EvaluationReport:
        key = round(scale, 6)
        if key not in cache:
            trial = replace(cfg, scale_by_scoring={**cfg.scale_by_scoring, scoring: scale})
            cache[key] = evaluate(
                ledger, seeds, cfg=trial, scoring=scoring, burn_in_matches=burn_in_matches
            )
        return cache[key]

    steps = 14
    grid = [low + (high - low) * i / steps for i in range(steps + 1)]
    best = min(grid, key=lambda s: run(s).mse)
    step = (high - low) / steps
    a, b = max(low, best - step), min(high, best + step)

    ratio = (math.sqrt(5) - 1) / 2
    c, d = b - ratio * (b - a), a + ratio * (b - a)
    while b - a > tolerance:
        if run(c).mse <= run(d).mse:
            b, d = d, c
            c = b - ratio * (b - a)
        else:
            a, c = c, d
            d = a + ratio * (b - a)
    fitted_scale = round((a + b) / 2, 3)

    default_scale = cfg.scale_by_scoring[scoring]
    return ScaleFit(
        scoring=scoring.value,
        scale=fitted_scale,
        default_scale=default_scale,
        fitted=run(fitted_scale),
        at_default=run(default_scale),
    )
