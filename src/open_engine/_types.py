"""
The string enums, the game format, and the enum and game format checks.

Reference implementation: outputs are not OPEN ratings, and ratings are
provisional (see "Status" in docs/behavior.md).
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum, StrEnum
from typing import cast

from ._errors import ConfigError, InvalidMatchError
from ._validation import _type_is, _type_name

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
    completed (see docs/behavior.md, "Match status").
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

        The format must be exactly a GameFormat (not a subclass) whose
        instance __dict__ holds only its four fields. scoring must be
        exactly a ScoringType member (a str equal to a member's value is
        rejected). points_to and win_by must be exactly ints, and cap
        exactly an int or None: a bool, a float, or an int subclass is
        rejected. The types are checked before the ranges: points_to and
        win_by positive, cap greater than points_to. The engine calls it
        through the class (GameFormat.validate(fmt)), never through the
        instance. Match.validate calls it and raises a ConfigError from it
        as InvalidMatchError with the same message.

        Raises:
            ConfigError: the format is not exactly a GameFormat with only its four
                fields, a field has the wrong type, or a value is out of range.
        """
        problem = _game_format_broken(self)
        if problem is not None:
            raise ConfigError(problem)
        if self.points_to < 1:
            raise ConfigError("points_to must be positive")
        if self.win_by < 1:
            raise ConfigError("win_by must be positive")
        if self.cap is not None and self.cap <= self.points_to:
            raise ConfigError("cap must be greater than points_to")

    def validate_game(self, score_a: int, score_b: int) -> None:
        """
        Raise InvalidMatchError when a game score cannot end a game in this format.

        Args:
            score_a: Team A's points, exactly an int.
            score_b: Team B's points, exactly an int.

        Raises:
            ConfigError: the format is not exactly a GameFormat with only its
                fields.
            InvalidMatchError: a score is not exactly an int (a bool or an int
                subclass is rejected), or the score cannot end a game in this
                format.

        Both type checks run before any score is compared.
        """
        problem = _game_format_broken(self)
        if problem is not None:
            raise ConfigError(problem)
        if type(score_a) is not int or type(score_b) is not int:
            raise InvalidMatchError("Game scores must be integers")
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


_GAME_FORMAT_FIELD_NAMES = frozenset(f.name for f in fields(GameFormat))


def _is_member(value: object, kind: type[Enum]) -> bool:
    """
    True only for a member of kind itself, compared by identity.

    An object can have exactly kind's type without being a member
    (str.__new__(RatingFormat, "singles")). Such an object passes a type
    check, but RatingFormat.team_size compares by identity and a dict
    lookup by equality, so the two would disagree about it.
    """
    return any(value is member for member in kind)


def _enum_shown(value: object, kind: type[Enum]) -> str:
    """The type name of value, or "a non-member <kind>" for a non-member of type kind."""
    if type(value) is kind:
        return f"a non-member {kind.__name__}"
    return _type_name(value)


def _safe_shown(value: object) -> str:
    """repr of an exact str, int, float, bool, or None; the type name of anything else."""
    if type(value) is int and value.bit_length() > 64:
        return "a large int"
    if _type_is(value, str, int, float, bool, type(None)):
        return repr(value)
    return _type_name(value)


def _game_format_shape_broken(fmt: object) -> str | None:
    """
    Why fmt is not exactly a GameFormat with only its fields, or None.

    A subclass can override a field with a property that runs caller code
    on every read, or override validate. An extra entry in an exact
    GameFormat's instance __dict__ could hide a method. Nothing on fmt is
    read before this check.
    """
    if type(fmt) is not GameFormat:
        return f"game_format must be a GameFormat, not {_type_name(fmt)}"
    held = vars(fmt)
    # An exact dict, so __len__ and __iter__ cannot hide an entry.
    if (
        type(held) is not dict
        or len(held) != len(_GAME_FORMAT_FIELD_NAMES)
        or not all(type(name) is str and name in _GAME_FORMAT_FIELD_NAMES for name in held)
    ):
        return "game_format has attributes that are not GameFormat fields"
    return None


def _game_format_broken(fmt: object) -> str | None:
    """Why fmt is not an exact GameFormat whose fields have their exact types, or None."""
    problem = _game_format_shape_broken(fmt)
    if problem is not None:
        return problem
    checked = cast(GameFormat, fmt)
    if not _is_member(checked.scoring, ScoringType):
        return f"scoring must be a ScoringType, not {_enum_shown(checked.scoring, ScoringType)}"
    for name, number in (("points_to", checked.points_to), ("win_by", checked.win_by)):
        if type(number) is not int:
            return f"{name} must be an int, not {_type_name(number)}"
    if checked.cap is not None and type(checked.cap) is not int:
        return f"cap must be an int or None, not {_type_name(checked.cap)}"
    return None


ALL_GAME_FORMATS: frozenset[tuple[ScoringType, int]] = frozenset(
    (scoring, points) for scoring in ScoringType for points in (11, 15, 21)
)
