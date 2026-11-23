"""Config checks every field's type, finiteness, and range (issues #68 and #70)."""

from __future__ import annotations

import json
import math
import re
from dataclasses import fields
from datetime import date
from decimal import Decimal

import pytest

from open_engine import (
    Config,
    ConfigError,
    Match,
    MatchType,
    RatingEngine,
    RatingFormat,
    ScoringType,
)

DEFAULT_FINGERPRINT = "7334117efa23cc98"
LIMIT = 2.0**511
MAX_DAYS = (date.max - date.min).days
HUGE_INT = 10**5000

NUMBER_FIELDS = (
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
WHOLE_FIELDS = (
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
OPTIONAL_NUMBER_FIELDS = (
    "connectivity_reliability_cap",
    "max_singles_rating_gap",
    "max_doubles_partner_gap",
)
BOOL_FIELDS = (
    "self_reported_requires_both_teams",
    "reject_content_duplicates",
    "category_feeds_parent",
)
STR_FIELDS = ("version",)
OPTIONAL_STR_FIELDS = ("params_version",)
OTHER_FIELDS = (
    "scale_by_scoring",
    "win_calibration_by_scoring",
    "match_weights",
    "game_length_weights",
    "allowed_game_formats",
)

# Fields whose type failure keeps the message an earlier release already used.
KEPT_MESSAGES = {
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

BAD_NUMBERS = ["x", None, True, False, Decimal("1"), math.nan, math.inf, -math.inf, HUGE_INT]
BAD_WHOLE = ["x", None, True, 1.0, Decimal("1"), HUGE_INT]
BAD_OPTIONAL = ["x", True, Decimal("1"), math.nan, math.inf, HUGE_INT]
BAD_BOOLS = [1, 0, "yes", None]
BAD_STRS = [1, None, b"0.3.0"]
BAD_OPTIONAL_STRS = [1, b"v1", True]


def _label(value: object) -> str:
    """A test id. repr() of HUGE_INT exceeds Python's int-to-str digit limit."""
    return "huge-int" if value is HUGE_INT else repr(value)


def _cases(names: tuple[str, ...], values: list[object], kind: str) -> list[object]:
    return [
        pytest.param(
            name,
            value,
            KEPT_MESSAGES.get(name, f"{name} must be {kind}"),
            id=f"{name}-{_label(value)}",
        )
        for name in names
        for value in values
    ]


TYPE_CASES = (
    _cases(NUMBER_FIELDS, BAD_NUMBERS, "a finite number")
    + _cases(WHOLE_FIELDS, BAD_WHOLE, "a whole number")
    + _cases(OPTIONAL_NUMBER_FIELDS, BAD_OPTIONAL, "None or a finite number")
    + _cases(BOOL_FIELDS, BAD_BOOLS, "a bool")
    + _cases(STR_FIELDS, BAD_STRS, "a str")
    + _cases(OPTIONAL_STR_FIELDS, BAD_OPTIONAL_STRS, "None or a str")
)


# -- criterion 1 and 4: types and finiteness --------------------------------------


def test_every_field_has_a_kind() -> None:
    kinds = (
        NUMBER_FIELDS
        + WHOLE_FIELDS
        + OPTIONAL_NUMBER_FIELDS
        + BOOL_FIELDS
        + STR_FIELDS
        + OPTIONAL_STR_FIELDS
        + OTHER_FIELDS
    )
    assert sorted(kinds) == sorted(f.name for f in fields(Config))


@pytest.mark.parametrize(("name", "value", "message"), TYPE_CASES)
def test_wrong_type_raises_config_error(name: str, value: object, message: str) -> None:
    with pytest.raises(ConfigError, match=f"^{re.escape(message)}$"):
        Config(**{name: value})


def test_a_wrong_type_is_a_config_error_not_a_bare_type_error() -> None:
    with pytest.raises(ConfigError) as info:
        Config(k_min="x")  # type: ignore[arg-type]
    assert type(info.value) is ConfigError
    assert isinstance(info.value, ValueError)


@pytest.mark.parametrize(
    "name", ["scale_by_scoring", "win_calibration_by_scoring", "match_weights"]
)
@pytest.mark.parametrize("bad", [Decimal("1"), HUGE_INT, -math.inf], ids=_label)
def test_mapping_values_must_be_config_numbers(name: str, bad: object) -> None:
    values = dict(getattr(Config(), name))
    values[next(iter(values))] = bad
    with pytest.raises(ConfigError, match=f"^{name} values must be finite numbers$"):
        Config(**{name: values})


@pytest.mark.parametrize("bad", [Decimal("1"), HUGE_INT, math.inf], ids=_label)
def test_game_length_weight_values_must_be_config_numbers(bad: object) -> None:
    with pytest.raises(ConfigError, match="^game_length_weights values must be finite numbers$"):
        Config(game_length_weights={11: bad, 15: 1.1, 21: 1.2})


def test_a_game_length_key_too_large_for_a_float_raises() -> None:
    with pytest.raises(ConfigError, match="^game_length_weights keys must be whole numbers$"):
        Config(game_length_weights={11: 1.0, 15: 1.1, 21: 1.2, HUGE_INT: 1.3})


def test_decimal_bonus_raises_at_construction_not_at_fingerprint() -> None:
    with pytest.raises(ConfigError, match="^game_count_bonus must be a finite number$"):
        Config(game_count_bonus=Decimal("0.12"))  # type: ignore[arg-type]


# -- criterion 2: ranges --------------------------------------------------------------


def _weights(**changes: float) -> dict[MatchType, float]:
    weights = dict(Config().match_weights)
    for key, value in changes.items():
        weights[MatchType(key)] = value
    return weights


RANGE_CASES = [
    pytest.param(
        {"match_weights": _weights(tournament=0.0)},
        "match_weights values must be positive",
        id="match-weight-zero",
    ),
    pytest.param(
        {"match_weights": _weights(league=-1.0)},
        "match_weights values must be positive",
        id="match-weight-negative",
    ),
    pytest.param(
        {"game_length_weights": {11: 0.0, 15: 1.1, 21: 1.2}},
        "game_length_weights values must be positive",
        id="length-weight-zero",
    ),
    pytest.param(
        {"game_length_weights": {11: 1.0, 15: -1.0, 21: 1.2}},
        "game_length_weights values must be positive",
        id="length-weight-negative",
    ),
    pytest.param(
        {"game_length_weights": {0: 1.0, 11: 1.0, 15: 1.1, 21: 1.2}},
        "game_length_weights keys must be at least 1",
        id="length-key-zero",
    ),
    pytest.param(
        {"game_length_weights": {-11: 1.0, 11: 1.0, 15: 1.1, 21: 1.2}},
        "game_length_weights keys must be at least 1",
        id="length-key-negative",
    ),
    pytest.param(
        {"game_count_bonus": -0.01}, "game_count_bonus must be at least 0", id="bonus-negative"
    ),
    pytest.param(
        {"max_singles_rating_gap": -0.1},
        "max_singles_rating_gap must be None or at least 0",
        id="singles-gap-negative",
    ),
    pytest.param(
        {"max_doubles_partner_gap": -0.1},
        "max_doubles_partner_gap must be None or at least 0",
        id="partner-gap-negative",
    ),
    pytest.param(
        {
            "rd_min": 0.5,
            "rd_max": 0.5,
            "new_player_rd": 0.5,
            "external_seed_rd": 0.5,
            "category_seed_rd": 0.5,
        },
        "RD bounds are invalid",
        id="rd-bounds-equal",
    ),
    pytest.param({"rd_max": 1e200}, "rd_max must be at most 2**511", id="rd-max-too-large"),
    pytest.param(
        {"rd_shrink_per_match": -0.5},
        "rd_shrink_per_match must be at least 0",
        id="rd-shrink-negative",
    ),
    pytest.param(
        {"rd_growth_per_day": -0.01},
        "rd_growth_per_day must be between 0 and 2**511",
        id="rd-growth-negative",
    ),
    pytest.param(
        {"rd_growth_per_day": 1e200},
        "rd_growth_per_day must be between 0 and 2**511",
        id="rd-growth-too-large",
    ),
    pytest.param(
        {"k_max": 1.5e308},
        "k_max times the largest match weight and max_information_multiplier must be finite",
        id="k-product-overflows",
    ),
    pytest.param(
        {"k_max": 1e300, "match_weights": _weights(tournament=1e10)},
        "k_max times the largest match weight and max_information_multiplier must be finite",
        id="k-product-overflows-by-weight",
    ),
    pytest.param(
        {"max_rating": 1e308, "default_rating": 1e308},
        "max_rating must be between -2**511 and 2**511",
        id="issue-70-repro",
    ),
    pytest.param(
        {"min_rating": -1e200},
        "min_rating must be between -2**511 and 2**511",
        id="min-rating-too-small",
    ),
    pytest.param(
        {"default_rating": 1e200},
        "default_rating must be between -2**511 and 2**511",
        id="default-rating-too-large",
    ),
]

DAY_FIELDS = (
    "replay_interval_days",
    "diversity_window_days",
    "self_reported_repeat_window_days",
    "import_reliability_max_age_days",
)

RANGE_CASES += [
    pytest.param({name: value}, f"{name} must be at most {MAX_DAYS}", id=f"{name}-{value}")
    for name in DAY_FIELDS
    for value in (MAX_DAYS + 1, 10**9)
]


@pytest.mark.parametrize(("changes", "message"), RANGE_CASES)
def test_out_of_range_raises(changes: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=f"^{re.escape(message)}$"):
        Config(**changes)  # type: ignore[arg-type]


ACCEPTED = [
    pytest.param({"max_singles_rating_gap": 0.0}, id="singles-gap-zero"),
    pytest.param({"max_doubles_partner_gap": 0.0}, id="partner-gap-zero"),
    pytest.param({"game_count_bonus": 0.0}, id="bonus-zero"),
    pytest.param({"rd_shrink_per_match": 0.0}, id="rd-shrink-zero"),
    pytest.param({"rd_growth_per_day": 0.0}, id="rd-growth-zero"),
    pytest.param({"rd_growth_per_day": LIMIT}, id="rd-growth-limit"),
    pytest.param({"rd_max": LIMIT}, id="rd-max-limit"),
    pytest.param({"max_rating": LIMIT}, id="max-rating-limit"),
    pytest.param({"min_rating": -LIMIT}, id="min-rating-limit"),
    pytest.param({"k_max": 1e300}, id="k-max-large-but-finite-product"),
    pytest.param({"game_length_weights": {1: 0.5, 11: 1.0, 15: 1.1, 21: 1.2}}, id="key-one"),
    pytest.param({"import_reliability_max_age_days": 0}, id="age-zero"),
] + [pytest.param({name: MAX_DAYS}, id=f"{name}-max") for name in DAY_FIELDS]


@pytest.mark.parametrize("changes", ACCEPTED)
def test_boundaries_are_accepted(changes: dict[str, object]) -> None:
    cfg = Config(**changes)  # type: ignore[arg-type]
    for name, value in changes.items():
        assert getattr(cfg, name) == value


# -- criterion 3: defaults ---------------------------------------------------------


def test_defaults_and_fingerprint_do_not_change() -> None:
    cfg = Config()
    assert cfg.fingerprint() == DEFAULT_FINGERPRINT
    expected = {
        "min_rating": 2.0,
        "max_rating": 8.0,
        "default_rating": 3.5,
        "k_min": 0.12,
        "k_max": 0.60,
        "rd_min": 0.10,
        "rd_max": 0.80,
        "new_player_rd": 0.80,
        "external_seed_rd": 0.42,
        "category_seed_rd": 0.50,
        "rd_shrink_per_match": 0.08,
        "rd_growth_per_day": 0.012,
        "win_weight": 0.0,
        "game_count_bonus": 0.12,
        "max_information_multiplier": 1.30,
        "replay_interval_days": 7,
        "diversity_window_days": 365,
        "diversity_target": 12,
        "self_reported_requires_both_teams": True,
        "self_reported_repeat_window_days": 90,
        "self_reported_repeat_factor": 0.5,
        "self_reported_max_repeats": 4,
        "reject_content_duplicates": False,
        "import_reliability_carry_matches": 10,
        "import_reliability_max_age_days": 365,
        "established_reliability_threshold": 60.0,
        "established_min_verified_matches": 3,
        "connectivity_reliability_cap": None,
        "connectivity_min_shared_contacts": 4,
        "connectivity_min_contact_days": 3,
        "connectivity_min_outside_players": 8,
        "category_feeds_parent": True,
        "max_singles_rating_gap": None,
        "max_doubles_partner_gap": None,
    }
    for name, value in expected.items():
        assert getattr(cfg, name) == value, name
    assert dict(cfg.match_weights) == {
        MatchType.TOURNAMENT: 1.00,
        MatchType.LEAGUE: 0.90,
        MatchType.REC_VERIFIED: 0.75,
        MatchType.SELF_REPORTED: 0.40,
    }
    assert dict(cfg.game_length_weights) == {11: 1.00, 15: 1.10, 21: 1.20}
    assert dict(cfg.scale_by_scoring) == {ScoringType.SIDE_OUT: 2.9, ScoringType.RALLY: 2.9}


# -- criteria 5 and 6: the rating range ----------------------------------------------


@pytest.mark.parametrize("name", ["min_rating", "max_rating", "default_rating"])
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_rating_bounds_raise(name: str, bad: float) -> None:
    with pytest.raises(ConfigError, match=f"^{name} must be a finite number$"):
        Config(**{name: bad})


@pytest.mark.parametrize(
    ("low", "high", "rating_a", "rating_b"),
    [
        (-LIMIT, LIMIT, LIMIT, -LIMIT),
        (-LIMIT, LIMIT, -LIMIT, LIMIT),
        (2.0, LIMIT, LIMIT, 2.0),
        (2.0, LIMIT, LIMIT, LIMIT),
    ],
)
def test_a_doubles_match_at_the_largest_range_stays_finite(
    low: float, high: float, rating_a: float, rating_b: float
) -> None:
    eng = RatingEngine(Config(min_rating=low, max_rating=high, default_rating=high))
    for pid, rating in zip(
        ("a1", "a2", "b1", "b2"), (rating_a, rating_a, rating_b, rating_b), strict=True
    ):
        eng.seed_provisional_rating(pid, RatingFormat.DOUBLES).rating = rating
    match = Match(
        "m1",
        ("a1", "a2"),
        ("b1", "b2"),
        ((11, 7),),
        date(2026, 10, 1),
        RatingFormat.DOUBLES,
        best_of=1,
    )
    result = eng.record(match)
    assert result.counted
    assert math.isfinite(result.expected_a)
    assert math.isfinite(result.imbalance)


# -- criterion 8: round trip ---------------------------------------------------------

ROUND_TRIP = [
    pytest.param({}, id="default"),
    pytest.param({"min_rating": 2, "max_rating": 8, "k_min": 0, "rd_growth_per_day": 0}, id="ints"),
    pytest.param({"max_singles_rating_gap": 0.5, "max_doubles_partner_gap": 0}, id="gaps"),
    pytest.param({"game_count_bonus": 0.0, "rd_shrink_per_match": 0.0}, id="zeros"),
    pytest.param({"min_rating": -LIMIT, "max_rating": LIMIT, "rd_max": LIMIT}, id="limits"),
    pytest.param(
        {"diversity_window_days": MAX_DAYS, "connectivity_reliability_cap": 50}, id="days"
    ),
]


@pytest.mark.parametrize("changes", ROUND_TRIP)
def test_valid_configs_round_trip_through_json(changes: dict[str, object]) -> None:
    cfg = Config(**changes)  # type: ignore[arg-type]
    back = Config.from_dict(json.loads(json.dumps(cfg.to_dict())))
    assert back == cfg
    assert back.fingerprint() == cfg.fingerprint()


@pytest.mark.parametrize("value", [7.0, "7"])
def test_from_dict_rejects_a_day_count_that_is_not_an_int(value: object) -> None:
    data = Config().to_dict()
    data["replay_interval_days"] = value
    with pytest.raises(ConfigError, match="^replay_interval_days must be a whole number$"):
        Config.from_dict(data)
