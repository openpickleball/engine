"""Config checks found in the second review: the largest K, and int and key subclasses.

Issues #68 and #70.
"""

from __future__ import annotations

import math
import re
import sys
from datetime import date

import pytest

from open_engine import (
    Config,
    ConfigError,
    GameFormat,
    Match,
    MatchType,
    RatingEngine,
    RatingFormat,
    ScoringType,
)

FMAX = sys.float_info.max
D = RatingFormat.DOUBLES
K_PRODUCT_MESSAGE = (
    "k_max times the largest match weight and max_information_multiplier must be finite"
)


# -- 1: the largest K player_k can return, not k_max -------------------------------


def _multiplier_at_the_edge(k_max: float) -> float:
    """The largest multiplier with k_max * multiplier finite."""
    multiplier = FMAX / k_max
    while math.isfinite(k_max * multiplier):
        multiplier = math.nextafter(multiplier, math.inf)
    return math.nextafter(multiplier, 0.0)


def test_k_rounded_above_k_max_cannot_overflow_the_step() -> None:
    # k_min + (k_max - k_min) * 1.0 rounds one ulp above k_max here, so the
    # step overflows although k_max times the weight does not.
    k_min, k_max = 0.08077411792107314, 1.3713882987032993
    multiplier = _multiplier_at_the_edge(k_max)
    assert k_min + (k_max - k_min) * 1.0 > k_max
    with pytest.raises(ConfigError, match=f"^{re.escape(K_PRODUCT_MESSAGE)}$"):
        Config(
            k_min=k_min,
            k_max=k_max,
            max_information_multiplier=multiplier,
            match_weights=dict.fromkeys(MatchType, 1.0),
            game_length_weights={11: FMAX, 15: FMAX, 21: FMAX},
            new_player_rd=Config().rd_max,
        )


def test_k_at_the_edge_without_rounding_is_accepted_and_stays_finite() -> None:
    k_max = 1.0
    multiplier = _multiplier_at_the_edge(k_max)
    cfg = Config(
        k_min=0.5,
        k_max=k_max,
        max_information_multiplier=multiplier,
        match_weights=dict.fromkeys(MatchType, 1.0),
        game_length_weights={11: FMAX, 15: FMAX, 21: FMAX},
    )
    result = RatingEngine(cfg).record(
        Match("e1", ("a", "b"), ("c", "d"), ((11, 9),), date(2026, 1, 1), D, best_of=1)
    )
    assert result.counted
    assert all(math.isfinite(delta) for delta in result.deltas.values())


# -- 2: int subclasses are stored by their int value, not their __int__ --------------


class _IntOver(int):
    """An int whose __int__ names another value."""

    def __int__(self) -> int:
        return 21


def _deltas(cfg: Config, points: int) -> tuple[bool, dict[str, float]]:
    result = RatingEngine(cfg).record(
        Match(
            "s1",
            ("a", "b"),
            ("c", "d"),
            ((points, points - 2),),
            date(2026, 1, 1),
            D,
            best_of=1,
            game_format=GameFormat(ScoringType.RALLY, points),
        )
    )
    return result.counted, dict(result.deltas)


def test_format_points_with_an_overridden_int_keep_their_value() -> None:
    cfg = Config(allowed_game_formats=frozenset({(ScoringType.RALLY, _IntOver(11))}))
    assert [points for _, points in cfg.allowed_game_formats] == [11]
    assert all(type(points) is int for _, points in cfg.allowed_game_formats)
    # Measured on origin/main (3845bec).
    assert cfg.fingerprint() == "afa81e2af143d673"
    assert _deltas(cfg, 11) == (True, {"a": 0.0225, "b": 0.0225, "c": -0.0225, "d": -0.0225})
    assert _deltas(cfg, 21) == (False, {})


def test_a_game_length_key_with_an_overridden_int_keeps_its_value() -> None:
    cfg = Config(
        game_length_weights={_IntOver(11): 1.5, 15: 1.1, 21: 1.2},
        allowed_game_formats=frozenset({(ScoringType.RALLY, 11)}),
    )
    assert sorted(cfg.game_length_weights) == [11, 15, 21]
    assert all(type(key) is int for key in cfg.game_length_weights)
    # Measured on origin/main (3845bec).
    assert cfg.fingerprint() == "55bb2cd0a60c56f2"
    assert _deltas(cfg, 11) == (
        True,
        {"a": 0.02925, "b": 0.02925, "c": -0.02925, "d": -0.02925},
    )


# -- 3: two game length keys for the same length raise ------------------------------


class _HashOver(int):
    """An int that hashes apart from the equal plain int."""

    def __hash__(self) -> int:
        return 12345


def test_game_length_keys_for_the_same_length_raise() -> None:
    with pytest.raises(ConfigError, match="^game_length_weights repeats the game length 11$"):
        Config(game_length_weights={11: 1.0, _HashOver(11): 9.0, 15: 1.1, 21: 1.2})


# -- the enum key conversion keeps its own error -------------------------------------


class _PickyStr(str):
    """Equal to the enum member, but not to its plain str value."""

    __hash__ = str.__hash__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ScoringType) and str.__eq__(self, other)


def test_a_key_the_enum_cannot_look_up_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="^scale_by_scoring has an unknown key$"):
        Config(scale_by_scoring={_PickyStr("rally"): 2.9, ScoringType.SIDE_OUT: 2.9})
