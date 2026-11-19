"""
Config, its tables and limits, and the helpers that check and rebuild it.

Demonstration only: not validated on real match data, outputs are not OPEN
ratings, and the API may change without notice.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, fields
from enum import Enum
from types import MemberDescriptorType
from typing import TypeVar, cast

from ._errors import ConfigError
from ._types import ALL_GAME_FORMATS, MatchType, ScoringType, _is_member
from ._validation import (
    _MAX_DAYS,
    _as_float,
    _is_config_number,
    _is_config_whole,
    _plain_number,
    _type_is,
    _type_name,
)
from ._version import __version__

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
_FORMAT_PAIRS = "allowed_game_formats entries must be (scoring type, whole number) pairs"


def _format_scoring(value: object) -> ScoringType | None:
    """An allowed_game_formats scoring item as a member: a member, or exactly a str naming one."""
    if _is_member(value, ScoringType):
        return cast(ScoringType, value)
    if type(value) is str and value in _SCORING_VALUES:
        return ScoringType(value)
    return None


def _format_points(value: object) -> int | None:
    """An allowed_game_formats points item as a plain int, judged by its real type alone."""
    real = type(value)
    if real is bool or not issubclass(real, int):
        return None
    points = int.__int__(cast(int, value))
    return points if _is_config_whole(points) else None


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


@dataclass(frozen=True, eq=False)
class Config:
    """
    Every rating parameter. Frozen, so a config cannot change during a replay.

    Each default is an uncalibrated placeholder, a declared choice, or a label
    that changes no rating, as the parameter table in docs/model.md marks it.
    fingerprint() identifies the exact parameter set behind published ratings.
    to_dict() and from_dict() store and load a config.

    Every field is checked when the config is built, and any failure
    raises ConfigError. Mapping fields are stored as read-only copies, and
    allowed_game_formats as a frozenset of (scoring type, whole number)
    pairs. Two configs are equal exactly when their fingerprint payloads are
    equal, so equal configs share a fingerprint and a hash.

    An entry point that takes a config accepts only an exact Config with
    nothing in its instance __dict__ but its fields (or None, where it
    allows one), and uses a fresh copy built from the given one's stored
    field values. Compare configs with == or fingerprint(), not is.

    Raises:
        ConfigError: a field breaks its rule.

    See docs/behavior.md, "Config validation", for every rule, and docs/model.md,
    "Parameters", for what each field means.
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
    # another value (issue #20). See "Match performance" in
    # docs/behavior.md for the full policy.
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
    # files reports. See "Likely duplicates" in docs/behavior.md.
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
        """
        Check every field and store plain values and read-only copies, in this order.

        First the type and finiteness of every scalar field, so no comparison
        in a later step can raise TypeError. Then the mapping and format
        fields, then ranges and cross-field rules. The first failing check
        raises ConfigError.
        """
        self._check_scalar_fields()
        pairs = self._read_mapping_pairs()
        self._store_game_lengths(pairs)
        self._store_game_formats()
        self._store_enum_mappings(pairs)
        self._check_mapping_values()
        self._check_rating_range()
        self._check_k_and_rd()
        self._check_step_size()
        self._check_windows_and_counts()
        self._check_labels_and_filters()

    def _check_scalar_fields(self) -> None:
        """
        Check each scalar field's type, storing every number as its plain value.

        In order: version is exactly a str; every number field is stored as
        its plain int or float; then the number, whole number, optional
        number, and bool fields, each group in its listed order.
        """

        def must_be(name: str, kind: str) -> str:
            return _CONFIG_TYPE_MESSAGES.get(name, f"{name} must be {kind}")

        # Exactly str: the snapshot loader accepts only a plain str, so a
        # subclass would give a snapshot that does not load.
        if type(self.version) is not str:
            raise ConfigError(must_be("version", "a str"))
        # Store every number as the plain int or float of its own value, so
        # every later check, fingerprint(), and rating code read the same
        # value (issue #107). A bool or a non-number is kept, and a spoofed
        # number replaced, for the type checks below to reject.
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
            # Exactly bool: an object whose __class__ claims bool passes isinstance.
            if type(getattr(self, name)) is not bool:
                raise ConfigError(must_be(name, "a bool"))

    def _read_mapping_pairs(self) -> dict[str, list[tuple[object, object]]]:
        """
        Read each mapping field once, as (key, plain value) pairs.

        For each field in _CONFIG_MAPPING_FIELDS, in order: it must be a
        mapping, then every value a finite number.
        """
        # The mapping and format helpers check types and keys and store
        # read-only copies. Keys and format entries are stored as their own
        # types (an enum member, a plain int), so the fingerprint never
        # depends on how an equal key prints or whether JSON can hold it.
        # One read of each caller's mapping, as (key, plain value) pairs;
        # every later mapping check reads these pairs or the copies built
        # from them, never the caller's mapping, and no game length key is
        # hashed before it is converted.
        pairs: dict[str, list[tuple[object, object]]] = {}
        for name in _CONFIG_MAPPING_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise ConfigError(f"{name} must be a mapping")
            pairs[name] = [(key, _plain_number(item)) for key, item in value.items()]
            if not all(_is_config_number(item) for _, item in pairs[name]):
                raise ConfigError(f"{name} values must be finite numbers")
        return pairs

    def _store_game_lengths(self, pairs: dict[str, list[tuple[object, object]]]) -> None:
        """
        Store game_length_weights from its read pairs, as a read-only copy with int keys.

        In order: every key is a whole number, no game length repeats, then
        every key is at least 1 and every value positive.
        """
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

    def _store_game_formats(self) -> None:
        """
        Store allowed_game_formats as a frozenset of (ScoringType, int) pairs.

        In order: it is a set or frozenset, then every entry is a tuple of
        two items, then no entry's first item is a ScoringType that is not a
        member, then every entry is a scoring type (a member, or exactly a
        str naming one) and a whole number (a real int, not a bool, finite
        as a float), then every whole number is at least 1. Each step checks
        every entry before the next starts, so the message does not depend
        on set order, which can change with the hash seed (issue #145).
        Every type is judged by type() alone, so no caller code runs.
        """
        if not isinstance(self.allowed_game_formats, (set, frozenset)):
            raise ConfigError("allowed_game_formats must be a set or frozenset")
        # Each entry is read once, through tuple's own methods, so a tuple
        # subclass cannot show the check one pair and the stored set another.
        entries: list[tuple[object, object]] = []
        for entry in self.allowed_game_formats:
            if not (issubclass(type(entry), tuple) and tuple.__len__(entry) == 2):
                raise ConfigError(_FORMAT_PAIRS)
            entries.append((tuple.__getitem__(entry, 0), tuple.__getitem__(entry, 1)))
        for entry_scoring, _ in entries:
            if type(entry_scoring) is ScoringType and not _is_member(entry_scoring, ScoringType):
                raise ConfigError("allowed_game_formats has a ScoringType that is not a member")
        formats: list[tuple[ScoringType, int]] = []
        for entry_scoring, entry_points in entries:
            scoring = _format_scoring(entry_scoring)
            points = _format_points(entry_points)
            if scoring is None or points is None:
                raise ConfigError(_FORMAT_PAIRS)
            formats.append((scoring, points))
        if any(points < 1 for _, points in formats):
            raise ConfigError("allowed_game_formats points must be at least 1")
        object.__setattr__(self, "allowed_game_formats", frozenset(formats))

    def _store_enum_mappings(self, pairs: dict[str, list[tuple[object, object]]]) -> None:
        """
        Store the enum-keyed mappings as read-only copies keyed by members.

        In order scale_by_scoring, win_calibration_by_scoring, then
        match_weights: each needs exactly one value for every member.
        """
        for name, kind, label in (
            ("scale_by_scoring", ScoringType, "scoring type"),
            ("win_calibration_by_scoring", ScoringType, "scoring type"),
            ("match_weights", MatchType, "match type"),
        ):
            # Each key is converted exactly once, and every check reads the
            # converted list: a key hashed twice could hash as one member
            # for a check and as another for the stored mapping.
            converted = [(_config_enum_key(key, kind, name), item) for key, item in pairs[name]]
            members = [member for member, _ in converted if member is not None]
            if len(members) != len(converted) or len(members) != len(set(members)):
                raise ConfigError(f"{name} needs a value for every {label}")
            if set(members) != set(kind):
                raise ConfigError(f"{name} needs a value for every {label}")
            keyed = {cast(Enum, member): item for member, item in converted}
            object.__setattr__(self, name, _FrozenMapping(keyed))

    def _check_mapping_values(self) -> None:
        """
        Check the stored mapping values.

        In order: scales, win calibration values, and match weights are
        positive, then every allowed game format has a game length weight,
        naming the shortest game length that has none.
        """
        if any(value <= 0 for value in self.scale_by_scoring.values()):
            raise ConfigError("scales must be positive")
        if any(value <= 0 for value in self.win_calibration_by_scoring.values()):
            raise ConfigError("win calibration values must be positive")
        if any(value <= 0 for value in self.match_weights.values()):
            raise ConfigError("match_weights values must be positive")
        # Stored points are plain ints, so sorting runs no caller code, and
        # the shortest missing length is named whatever the set order.
        for points in sorted({points for _, points in self.allowed_game_formats}):
            if points not in self.game_length_weights:
                raise ConfigError(f"No game_length_weight for games to {points}")

    def _check_rating_range(self) -> None:
        """
        Check the rating range.

        In order: each of min_rating, max_rating, and default_rating is within
        the rating limit, min_rating is below max_rating, then default_rating
        is within them.
        """
        limit = _RATING_LIMIT_TEXT
        for name in ("min_rating", "max_rating", "default_rating"):
            if not -_RATING_LIMIT <= getattr(self, name) <= _RATING_LIMIT:
                raise ConfigError(f"{name} must be between -{limit} and {limit}")
        if not self.min_rating < self.max_rating:
            raise ConfigError("min_rating must be less than max_rating")
        if not self.min_rating <= self.default_rating <= self.max_rating:
            raise ConfigError("default_rating must be within rating bounds")

    def _check_k_and_rd(self) -> None:
        """
        Check the K and RD parameters.

        In order: the K bounds, the RD bounds, rd_max against the rating
        limit, the three starting RDs within the RD bounds,
        rd_shrink_per_match, then rd_growth_per_day and its int limit.
        """
        limit = _RATING_LIMIT_TEXT
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

    def _check_step_size(self) -> None:
        """
        Check the match performance and evidence weight parameters, and the largest step.

        In order: win_weight, game_count_bonus and its int limit,
        max_information_multiplier, then the largest step size as computed
        in floats, then, for int weights, the exact int products the engine
        forms (issue #93).
        """
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

    def _check_windows_and_counts(self) -> None:
        """
        Check the day windows and match counts.

        In order: replay_interval_days, the diversity settings, the
        self-reported repeat factor and limits, the import reliability
        limits, then every day field against the largest day count.
        """
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

    def _check_labels_and_filters(self) -> None:
        """
        Check the reliability label and eligibility filter parameters.

        In order: the established threshold and minimum, the connectivity
        cap, the three connectivity minimums, then the two gap filters.
        """
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
        Build a Config from to_dict() output, also after a round trip through Python's json module.

        Args:
            data: Exactly a dict (json.loads gives one; another mapping type
                raises) with exactly the keys to_dict() gives, so a stored config
                names every parameter.

        Returns:
            The Config.

        Raises:
            ConfigError: data of another type, a missing or unknown key, a value
                that cannot be converted, or a value Config rejects.
        """
        # Exactly dict: a mapping type runs its own code on every read, so
        # the checks below and Config could read different values.
        if type(data) is not dict:
            raise ConfigError(
                f"Config data must be a mapping, exactly a dict, not {_type_name(data)}"
            )
        # Exactly str keys, checked before any set operation or lookup: a
        # key type's own __hash__ and __eq__ would run in both.
        odd = sorted({_type_name(key) for key in data if type(key) is not str})
        if odd:
            raise ConfigError(f"Config data has unknown keys: keys of type {', '.join(odd)}")
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
            return cls(**values)
        except TypeError as exc:
            raise ConfigError(f"Config data has a value of the wrong type: {exc}") from exc


_CONFIG_FIELD_NAMES = frozenset(f.name for f in fields(Config))


def _config_enum_key(key: object, kind: type[Enum], name: str) -> Enum | None:
    """
    A Config enum mapping key as its member, read once; None for an unknown plain str.

    A member stays as it is, and an exact str converts by value. A str
    subclass, or an object of kind's type that is not a member, raises
    ConfigError before anything hashes it: its own __hash__ and __eq__
    could answer differently on each call. Any other key gives None, which
    the caller reports as a missing member.
    """
    if _is_member(key, kind):
        return cast(Enum, key)
    if type(key) is kind:
        raise ConfigError(f"{name} has a {kind.__name__} key that is not a member")
    if type(key) is str:
        values = {member.value: member for member in kind}
        return values.get(key)
    if isinstance(key, str):
        raise ConfigError(f"{name} has an unknown key")
    return None


def _require_config(cfg: object) -> Config:
    """
    A rebuilt copy of cfg when it is exactly a Config with only its fields.

    Raises ConfigError otherwise, or when the rebuild does (see
    _rebuilt_config). An entry point that drains a caller iterable calls it
    only after it has read matches and seed_players (issue #122), with
    _config_shape as its early check.
    """
    return _rebuilt_config(_config_shape_checked(cfg))


def _config_shape(cfg: object) -> Config | None:
    """None for None, else cfg after the type and field checks, not rebuilt."""
    return None if cfg is None else _config_shape_checked(cfg)


def _config_shape_checked(cfg: object) -> Config:
    """
    cfg when it is exactly a Config with only its fields; ConfigError otherwise.

    A subclass can run its own methods: store a value after __post_init__
    made every number plain, or override fingerprint, _payload, or __eq__
    (issue #118). An exact Config must also hold nothing in its instance
    __dict__ but its own fields: another entry there could hide
    fingerprint, _payload, or the field list (__dataclass_fields__).
    """
    if type(cfg) is not Config:
        raise ConfigError(f"cfg must be exactly a Config, not {_type_name(cfg)}")
    held = vars(cfg)
    # An exact dict, so __len__ and __iter__ cannot hide an entry.
    if (
        type(held) is not dict
        or len(held) != len(_CONFIG_FIELD_NAMES)
        or not all(type(name) is str and name in _CONFIG_FIELD_NAMES for name in held)
    ):
        raise ConfigError("cfg has attributes that are not Config fields")
    return cfg


# The _data slot of _FrozenMapping, read without calling any method of the
# object that holds it (issue #122).
_FROZEN_DATA = cast(MemberDescriptorType, vars(_FrozenMapping)["_data"])


# The enum each enum-keyed Config mapping field is keyed by.
_CONFIG_ENUM_KEYS: dict[str, type[Enum]] = {
    "scale_by_scoring": ScoringType,
    "win_calibration_by_scoring": ScoringType,
    "match_weights": MatchType,
}


def _leaf_value(name: str, value: object) -> object:
    """
    One stored Config leaf as a plain value, judged by its real type alone.

    An exact bool, an exact str, and None are kept; a real int or float
    subclass becomes the plain int or float of its own value. Anything else
    raises ConfigError. Only type(), issubclass(), and int's and float's own
    methods run, so no caller code does (issue #122). Config(...) then
    checks the plain value as it checks a constructor argument.
    """
    real = type(value)
    if real is bool or real is str or value is None:
        return value
    if issubclass(real, int):
        return int.__int__(cast(int, value))
    if issubclass(real, float):
        return float.__float__(cast(float, value))
    raise ConfigError(f"cfg.{name} must hold a plain value, not {_type_name(value)}")


def _leaf_member(name: str, value: object, kind: type[Enum]) -> object:
    """
    A stored enum key or scoring entry: a member by identity, or an exact str.

    An exact str naming a member becomes that member; any other exact str
    is kept for Config(...) to reject. Anything else raises ConfigError
    without running its code.
    """
    if _is_member(value, kind):
        return value
    if type(value) is str:
        return {member.value: member for member in kind}.get(value, value)
    raise ConfigError(f"cfg.{name} has a key or entry of type {_type_name(value)}")


def _leaf_whole(name: str, value: object) -> int:
    """A stored game length key or format points: a real int, not a bool, as a plain int."""
    real = type(value)
    if real is bool or not issubclass(real, int):
        raise ConfigError(f"cfg.{name} must hold whole numbers, not {_type_name(value)}")
    return int.__int__(cast(int, value))


def _rebuilt_config(cfg: Config) -> Config:
    """
    A fresh Config built from cfg's stored field values (issue #122).

    cfg passed _require_config, so its instance __dict__ is an exact dict
    holding exactly its fields. object.__setattr__ can still rewrite a
    field after __post_init__ checked it, so every field value goes through
    Config(...) again: every check and the plain-value conversion (#107)
    run on it. First every leaf becomes a plain value by its real type
    alone (_leaf_value, _leaf_member, _leaf_whole), so no caller code runs
    here or in Config(...). A mapping field must hold exactly a dict, or
    exactly a _FrozenMapping whose _data slot holds exactly a dict, read
    through the slot descriptor; its keys are enum members or exact strs
    (game length keys real ints), and two keys that become one raise.
    allowed_game_formats must be exactly a frozenset or set of real tuple
    entries, read through tuple's own methods. Anything else raises
    ConfigError. The result shares nothing with cfg.
    """
    held = vars(cfg)
    values: dict[str, object] = {}
    for f in fields(Config):
        name = f.name
        value = held[name]
        if name in _CONFIG_MAPPING_FIELDS:
            values[name] = _rebuilt_mapping(name, value)
        elif name == "allowed_game_formats":
            values[name] = _rebuilt_formats(value)
        else:
            values[name] = _leaf_value(name, value)
    try:
        return Config(**values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ConfigError(f"cfg has a value of the wrong type: {exc}") from exc


def _rebuilt_mapping(name: str, value: object) -> dict[object, object]:
    """A stored Config mapping field as a new dict of plain keys and values."""
    if type(value) is _FrozenMapping:
        try:
            value = _FROZEN_DATA.__get__(value, _FrozenMapping)
        except AttributeError:
            raise ConfigError(f"cfg.{name} has no stored mapping") from None
    if type(value) is not dict:
        raise ConfigError(f"cfg.{name} must hold exactly a dict, not {_type_name(value)}")
    kind = _CONFIG_ENUM_KEYS.get(name)
    rebuilt: dict[object, object] = {}
    # dict.items of an exact dict reads no key or value method.
    for key, item in dict.items(cast("dict[object, object]", value)):
        plain = _leaf_whole(name, key) if kind is None else _leaf_member(name, key, kind)
        if plain in rebuilt:
            raise ConfigError(f"cfg.{name} repeats a key")
        rebuilt[plain] = _leaf_value(name, item)
    return rebuilt


def _rebuilt_formats(value: object) -> frozenset[tuple[object, int]]:
    """The stored allowed_game_formats as a new frozenset of plain pairs."""
    name = "allowed_game_formats"
    if type(value) is not frozenset and type(value) is not set:
        raise ConfigError(f"cfg.{name} must be exactly a frozenset or set, not {_type_name(value)}")
    rebuilt: set[tuple[object, int]] = set()
    problems: list[str] = []
    # Iterating an exact set or frozenset calls no method of its entries.
    # Every entry is checked, and the first problem in sorted order is
    # raised, so the message does not depend on set order (issue #145).
    for entry in cast("set[object]", value):
        try:
            rebuilt.add(_rebuilt_format(entry))
        except ConfigError as exc:
            problems.append(str(exc))
    if problems:
        raise ConfigError(min(problems))
    return frozenset(rebuilt)


def _rebuilt_format(entry: object) -> tuple[object, int]:
    """One stored allowed_game_formats entry as a plain pair, by its real types alone."""
    name = "allowed_game_formats"
    if not (issubclass(type(entry), tuple) and tuple.__len__(cast(tuple[object], entry)) == 2):
        raise ConfigError(f"cfg.{name} entries must be pairs, not {_type_name(entry)}")
    pair = cast(tuple[object, object], entry)
    scoring = _leaf_member(name, tuple.__getitem__(pair, 0), ScoringType)
    points = _leaf_whole(name, tuple.__getitem__(pair, 1))
    if type(scoring) is str:
        raise ConfigError(f"cfg.{name} has an unknown scoring type")
    return (scoring, points)


def _config_or_default(cfg: object) -> Config:
    """Config() for None, else cfg checked by _require_config."""
    return Config() if cfg is None else _require_config(cfg)


_E = TypeVar("_E", bound=Enum)


def _enum_keyed(value: object, kind: type[_E], name: str) -> dict[_E, object]:
    """
    A from_dict enum mapping with member keys.

    The mapping must be exactly a dict whose keys are members or exact
    strs, checked before anything hashes a key or calls its methods.
    """
    if type(value) is not dict:
        raise ConfigError(f"{name} must be a mapping")
    out: dict[_E, object] = {}
    for key, item in value.items():
        if _is_member(key, kind):
            out[cast(_E, key)] = item
            continue
        if type(key) is not str:
            raise ConfigError(f"{name} has an unknown key: {_shown(key)}")
        try:
            out[kind(key)] = item
        except ValueError as exc:
            raise ConfigError(f"{name} has an unknown key: {_shown(key)}") from exc
    return out


def _shown(value: object, show: Callable[[object], str] = repr) -> str:
    """
    value for an error message, without running caller code.

    An exact str, int, float, bool, or None is shown with show (repr by
    default), and an exact list or tuple of those by repr; anything else by
    its type name. An int over the int-to-str limit cannot print.
    """
    plain = (str, int, float, bool, type(None))
    if _type_is(value, list, tuple) and all(_type_is(item, *plain) for item in value):  # type: ignore[attr-defined]
        show = repr
    elif not _type_is(value, *plain):
        return _type_name(value)
    try:
        return show(value)
    except ValueError:
        return "a value too large to show"


def _int_keyed(value: object) -> dict[int, object]:
    """
    game_length_weights from from_dict, with int keys.

    The mapping must be exactly a dict, and each key exactly an int (not a
    bool) or exactly a str of ASCII digits, checked before anything hashes
    a key or calls its methods.
    """
    if type(value) is not dict:
        raise ConfigError("game_length_weights must be a mapping")
    out: dict[int, object] = {}
    for key, item in value.items():
        if type(key) is int:
            points = key
        elif type(key) is str and key.isascii() and key.isdigit():
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
    """
    allowed_game_formats from from_dict.

    It must be exactly a list or tuple of entries that are each exactly a
    list or tuple of two: a ScoringType member or an exact str, and exactly
    an int (not a bool).
    """
    if type(value) is not list and type(value) is not tuple:
        raise ConfigError("allowed_game_formats must be a list of [scoring, points] pairs")
    out: set[tuple[ScoringType, int]] = set()
    for item in value:
        if (type(item) is not list and type(item) is not tuple) or len(item) != 2:
            raise ConfigError(
                f"allowed_game_formats has an entry that is not a pair: {_shown(item)}"
            )
        scoring, points = item
        if type(points) is not int:
            raise ConfigError(
                f"allowed_game_formats has points that are not a whole number: {_shown(item)}"
            )
        if _is_member(scoring, ScoringType):
            out.add((cast(ScoringType, scoring), points))
            continue
        if type(scoring) is not str:
            raise ConfigError(f"allowed_game_formats has an unknown scoring type: {_shown(item)}")
        try:
            out.add((ScoringType(scoring), points))
        except ValueError as exc:
            raise ConfigError(
                f"allowed_game_formats has an unknown scoring type: {_shown(item)}"
            ) from exc
    return frozenset(out)
