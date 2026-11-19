"""
The input and output records: external seeds, rating state, players,
matches, rejection reasons, match results, summaries, and replay info.

Demonstration only: not validated on real match data, outputs are not OPEN
ratings, and the API may change without notice.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import UTC, date, datetime, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

from ._config import _FrozenMapping
from ._errors import ConfigError, InvalidMatchError, SnapshotError
from ._types import (
    GameFormat,
    MatchStatus,
    MatchType,
    RatingFormat,
    _enum_shown,
    _game_format_broken,
    _is_member,
    _StrEnum,
)
from ._validation import _is_id, _is_plain_date, _is_plain_str, _type_is, _type_name

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
    ended. A completed match has at least one game and a decided winner.
    record never counts a match that is not completed.

    The match must be a Match itself, not a subclass, and validate raises
    InvalidMatchError for a field of the wrong type. match_id, the team
    player ids, and each member of confirmed_by must be a non-blank str,
    exactly a str (not a subclass).

    See docs/behavior.md, "Match fields" and "Match status".
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
    # venue. played_at must be timezone-aware, with exactly a
    # datetime.timezone or exactly a zoneinfo.ZoneInfo as its tzinfo, and
    # fall on played_on; send it when known. Within one played_on, untimed matches come first, then
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
        if self.played_at is not None:
            _utc_instant(self.played_at)
        if self.played_at is not None and self.played_at.date() != self.played_on:
            raise InvalidMatchError("played_at must fall on played_on")
        if self.sequence < 0:
            raise InvalidMatchError("sequence must be nonnegative")
        if not _type_is(self.games, tuple, list):
            raise InvalidMatchError(f"games must be a tuple or list, not {_type_name(self.games)}")
        if completed and not self.games:
            raise InvalidMatchError("At least one game score is required")
        if self.status is MatchStatus.FORFEIT and self.games:
            raise InvalidMatchError("A forfeit cannot have game scores")
        if not all(_type_is(game, tuple, list) and len(game) == 2 for game in self.games):
            raise InvalidMatchError("Each game must be a pair of scores")
        if self.best_of < 1 or self.best_of % 2 == 0:
            raise InvalidMatchError("best_of must be a positive odd number")
        if len(self.games) > self.best_of:
            raise InvalidMatchError("Games played cannot exceed best_of")
        try:
            GameFormat.validate(self.game_format)
        except ConfigError as err:
            raise InvalidMatchError(str(err)) from None
        for score_a, score_b in self.games:
            if not all(type(score) is int for score in (score_a, score_b)):
                raise InvalidMatchError("Game scores must be integers")
            if score_a < 0 or score_b < 0 or score_a == score_b:
                raise InvalidMatchError("Game scores must be nonnegative and cannot be tied")
            GameFormat.validate_game(self.game_format, score_a, score_b)
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

        Runs before validate uses any of these fields, and in ledger_order.
        Every type is checked exactly: a subclass, or an object whose
        __class__ only claims the type, could give validation one value
        and rating another, or run caller code while it is read. A str
        equal to an enum member's value is rejected, a list team is
        rejected, and a bool, float, or int subclass sequence or best_of
        is rejected. game_format must be exactly a GameFormat with only
        its fields, each of its exact type (see GameFormat.validate).
        played_on must be exactly a date, and played_at None or exactly a
        datetime whose tzinfo is None (rejected later as not aware),
        exactly datetime.timezone, or exactly zoneinfo.ZoneInfo with no
        instance __dict__ (see _is_plain_zone): any other tzinfo, a
        subclass of either included, can run its own code to give the UTC
        offset.
        """
        if not _is_plain_str(self.match_id):
            raise InvalidMatchError(f"match_id must be a str, not {_type_name(self.match_id)}")
        if not _is_member(self.status, MatchStatus):
            raise InvalidMatchError(
                f"status must be a MatchStatus, not {_enum_shown(self.status, MatchStatus)}"
            )
        checks: tuple[tuple[str, object, type[Enum], str], ...] = (
            ("rating_format", self.rating_format, RatingFormat, "a RatingFormat"),
            ("match_type", self.match_type, MatchType, "a MatchType"),
        )
        for name, value, kind, label in checks:
            if not _is_member(value, kind):
                raise InvalidMatchError(f"{name} must be {label}, not {_enum_shown(value, kind)}")
        problem = _game_format_broken(self.game_format)
        if problem is not None:
            raise InvalidMatchError(problem)
        exact: tuple[tuple[str, object, tuple[type, ...], str], ...] = (
            ("team_a", self.team_a, (tuple,), "a tuple"),
            ("team_b", self.team_b, (tuple,), "a tuple"),
            ("confirmed_by", self.confirmed_by, (set, frozenset), "a set or frozenset"),
        )
        for name, value, kinds, label in exact:
            if not _type_is(value, *kinds):
                raise InvalidMatchError(f"{name} must be {label}, not {_type_name(value)}")
        if not _is_plain_date(self.played_on):
            raise InvalidMatchError(
                f"played_on must be a plain date (not a datetime), not {_type_name(self.played_on)}"
            )
        if self.played_at is not None:
            if type(self.played_at) is not datetime:
                raise InvalidMatchError(
                    f"played_at must be a datetime or None, not {_type_name(self.played_at)}"
                )
            zone = self.played_at.tzinfo
            if zone is not None and not _is_plain_zone(zone):
                raise InvalidMatchError(
                    "played_at tzinfo must be exactly datetime.timezone or zoneinfo.ZoneInfo, "
                    f"not {_type_name(zone)}; convert with {_PLAYED_AT_RECIPE}"
                )
            # An exact timezone keeps the timedelta it was built with, and a
            # timedelta subclass runs its own methods in the digest and the
            # duplicate check. timezone.utcoffset and ZoneInfo.utcoffset run
            # no caller code.
            if zone is not None:
                # A ZoneInfo's tz data can hold an offset of a day or more,
                # which datetime refuses with its own ValueError.
                try:
                    offset = self.played_at.utcoffset()
                except ValueError:
                    raise InvalidMatchError(
                        "played_at has a UTC offset its zone cannot give: "
                        "it must be less than a day"
                    ) from None
                if type(offset) is not timedelta:
                    raise InvalidMatchError(
                        f"played_at offset must be exactly a timedelta, not {_type_name(offset)}; "
                        f"convert with {_PLAYED_AT_RECIPE}"
                    )
        for name, number in (("sequence", self.sequence), ("best_of", self.best_of)):
            if type(number) is not int:
                raise InvalidMatchError(f"{name} must be an int, not {_type_name(number)}")


# The conversion a rejected played_at message gives: an exact timezone of a
# plain timedelta with the same instant and offset, so the same digest.
_PLAYED_AT_RECIPE = (
    "played_at.astimezone(timezone(timedelta(seconds=played_at.utcoffset().total_seconds())))"
)


def _is_plain_zone(zone: object) -> bool:
    """
    True for exactly a datetime.timezone, or exactly a zoneinfo.ZoneInfo with no instance __dict__.

    Both are standard library C code whose utcoffset runs no caller code;
    a ZoneInfo's offsets come from its tz data (the tz database, or the
    file given to ZoneInfo.from_file). Any other tzinfo, a subclass of
    either included, can run its own code while the engine reads the
    offset. On CPython ZoneInfo is a C type with no instance __dict__. On
    a Python build that uses the pure Python zoneinfo fallback, ZoneInfo
    instances have a __dict__ and are rejected, since an instance
    attribute there could hide utcoffset.
    """
    if type(zone) is timezone:
        return True
    return type(zone) is ZoneInfo and not hasattr(zone, "__dict__")


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
        raise InvalidMatchError(f"match must be a Match, not {_type_name(match)}")
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


_UTC_MIN = datetime.min.replace(tzinfo=UTC)


def _utc_instant(played_at: datetime) -> datetime:
    """
    The UTC instant of an aware played_at; InvalidMatchError when there is none.

    Called only after Match._validate_field_types, so the tzinfo is None,
    exactly a datetime.timezone, or exactly a zoneinfo.ZoneInfo, with an
    offset that is exactly a timedelta, and neither utcoffset nor
    astimezone runs caller code.
    """
    if played_at.tzinfo is None or played_at.utcoffset() is None:
        raise InvalidMatchError("played_at must be timezone-aware")
    try:
        return played_at.astimezone(UTC)
    except OverflowError:
        raise InvalidMatchError("played_at is outside the range that UTC can represent") from None


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
    seed that could change it (see docs/behavior.md, "External seeds").
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
    A player is clamped when the new rating, after the range clamp, differs
    from the rounded sum old rating + raw delta by more than 1e-12, or when
    the applied change differs from the raw delta by at least 5e-6 (half a
    unit in the fifth place). A loss that meets neither rule is not
    listed, including a range cut that float rounding of that sum absorbs first.
    imbalance is the mean team A delta plus the mean team B delta, from the
    unrounded changes, rounded to 5 places. parent holds the overall doubles
    update for a category match, with its own imbalance.

    late is True for a counted match dated before a player's last match in
    this rating format, or before the date that player's rating in this
    format already reflects (for a category rating copied from overall
    doubles). possible_duplicate_of can name the earliest counted match the
    engine has seen with the same content. Do not show it to
    whoever submitted the match: it names another report.

    See docs/behavior.md, "Match results".
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
    clamped: tuple[str, ...] = ()  # range cut, or rounding lost >= 5e-6
    imbalance: float = 0.0  # mean team A delta plus mean team B delta
    late: bool = False  # dated before what a player's rating here reflects
    parent: MatchResult | None = None  # overall doubles update for categories
    possible_duplicate_of: str | None = None  # earlier counted match, same content


@dataclass(frozen=True)
class NetworkConnectivity:
    """
    Whether a player's pool connects to the wider network, in one format.

    Measured over Config.diversity_window_days at the summary's reference
    date, from the partners and opponents stored in each rating state. When
    connected is False and Config.connectivity_reliability_cap is set (it is
    None by default), PlayerSummary.reliability is at most that cap. It is
    a label only and changes no rating.

    See docs/behavior.md, "Network connectivity", for each field, the rule, and its
    limits.
    """

    pool_size: int  # players in the pool, this player included
    fringe_players: int  # drop-ins who met only the pool and its other contacts
    outside_players: int  # other players outside the pool in contact with it
    outside_days: int  # distinct contact dates between the pool and its outside players
    group_size: int  # players linked to this player by contact, this player included
    in_main_network: bool  # the group has at least half as many players as the largest
    connected: bool


@dataclass(frozen=True)
class PlayerSummary:
    """
    A player's published rating in one format, with reliability and provenance.

    reliability is always OPEN-earned. imported_reliability is the imported
    external reliability, rounded to 1 place, while it is carried, otherwise
    None. It is shown separately and never replaces or converts into
    reliability. established and connectivity are labels and change no
    rating. live_updates_since_recalculation counts the live updates since
    the last replay that are in the state's rating. It is 0 right after a
    replay.

    See docs/behavior.md, "Player summaries", "Established", and "Network connectivity".
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
    between the teams, a player is clamped or frozen, or float rounding
    leaves a residue at very large ratings or steps, and colluding
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


# Public classes keep open_engine.engine, which re-exports them, as their
# __module__. _json_form tags each value in a match digest with its type's
# module and name, and snapshots store those digests, so a new module name
# would change every digest and stop resume from older snapshots (#127).
ExternalRatingSnapshot.__module__ = "open_engine.engine"
RatingState.__module__ = "open_engine.engine"
Player.__module__ = "open_engine.engine"
Match.__module__ = "open_engine.engine"
RejectionReason.__module__ = "open_engine.engine"
MatchResult.__module__ = "open_engine.engine"
NetworkConnectivity.__module__ = "open_engine.engine"
PlayerSummary.__module__ = "open_engine.engine"
ReplayInfo.__module__ = "open_engine.engine"
