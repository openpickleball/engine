"""Int config values can no longer overflow exact int arithmetic in rating code (issue #93).

With int match weights, an int max_information_multiplier, and an int
self_reported_repeat_factor of 1, a self-reported match's step weight can be
an exact int (when information_multiplier returns max_information_multiplier).
Rating code then converts it to a float (OverflowError when it is too large,
or above the float product the K check saw), and an int
rd_shrink_per_match times it is another exact int. Config now forms those
products the way the engine does and raises ConfigError when one would
overflow. Configs that still build rate exactly as before.
"""

from __future__ import annotations

import math
import sys
from datetime import date

import pytest

from open_engine import (
    Config,
    ConfigError,
    Match,
    MatchType,
    OpenEngineError,
    RatingEngine,
    RatingFormat,
    replay,
)

D = RatingFormat.DOUBLES
SR = MatchType.SELF_REPORTED
PLAYERS = ("a", "b", "c", "d")
ON = date(2026, 1, 5)
K_MESSAGE = "k_max times the largest match weight and max_information_multiplier must be finite"
RD_MESSAGE = (
    "rd_shrink_per_match times the largest match weight and max_information_multiplier "
    "must be finite"
)
# A length weight so large that information_multiplier always returns
# max_information_multiplier itself.
HUGE_LENGTHS = {11: int(sys.float_info.max), 15: 1.1, 21: 1.2}

# The issue's repro: w * m converts (about 1.0786e308), but the int
# rd_shrink_per_match of 2 times it does not.
ISSUE_W = math.isqrt(6 * 2**1024 // 10)
# Each rounds down to a float (2**511 and 2**513 - 2**460) whose product is
# the largest finite float, but the exact product is too large to convert.
CONVERT_W = 2**511 + 2**458 - 1
CONVERT_M = 2**513 - 2**459 - 1
# float(ULP_W * ULP_W) is one ulp above float(ULP_W) ** 2.
ULP_W = 2**511 + 2**457


def _largest_finite_k(product: float) -> float:
    """The largest float K with K * product finite."""
    k = sys.float_info.max / product
    while math.isfinite(math.nextafter(k, math.inf) * product):
        k = math.nextafter(k, math.inf)
    while not math.isfinite(k * product):
        k = math.nextafter(k, 0.0)
    return k


ULP_K = _largest_finite_k(float(ULP_W) * float(ULP_W))


def _weights(value: float, self_reported: float | None = None) -> dict[MatchType, float]:
    return {
        t: (self_reported if self_reported is not None and t is SR else value) for t in MatchType
    }


def _cfg(
    weight: float,
    multiplier: float,
    *,
    self_reported_weight: float | None = None,
    **overrides: object,
) -> Config:
    fields: dict[str, object] = {
        "match_weights": _weights(weight, self_reported_weight),
        "max_information_multiplier": multiplier,
        "game_length_weights": HUGE_LENGTHS,
        "self_reported_repeat_factor": 1,
    }
    fields.update(overrides)
    return Config(**fields)  # type: ignore[arg-type]


def _match(match_id: str, match_type: MatchType, games: tuple[tuple[int, int], ...]) -> Match:
    return Match(
        match_id,
        ("a", "b"),
        ("c", "d"),
        games,
        ON,
        D,
        best_of=len(games),
        match_type=match_type,
        confirmed_by=frozenset({"a", "c"}) if match_type is SR else frozenset(),
    )


EVEN_GAMES = ((11, 9), (0, 11), (11, 2))  # 22 points each, so the surprise can be 0

REPRO = {"k_min": 0, "k_max": 1, "rd_shrink_per_match": 2}
CONVERT = {"k_min": 0, "k_max": 1, "rd_shrink_per_match": 0}
ULP = {"k_min": ULP_K, "k_max": ULP_K, "rd_shrink_per_match": 0, "win_weight": 0}
RD = {"k_min": 0, "k_max": 1, "rd_shrink_per_match": 4}

REJECTED = [
    pytest.param(ISSUE_W, ISSUE_W, REPRO, RD_MESSAGE, id="issue-repro"),
    pytest.param(CONVERT_W, CONVERT_M, CONVERT, K_MESSAGE, id="no-float-conversion"),
    pytest.param(ULP_W, ULP_W, ULP, K_MESSAGE, id="ulp"),
    pytest.param(2**511, 2**511, RD, RD_MESSAGE, id="rd-shrink"),
]


@pytest.mark.parametrize(("w", "m", "overrides", "message"), REJECTED)
@pytest.mark.parametrize("only_self_reported", [False, True], ids=["all-int", "sr-int"])
def test_overflowing_int_config_is_rejected(
    w: int, m: int, overrides: dict[str, object], message: str, only_self_reported: bool
) -> None:
    weight: float = 1.0 if only_self_reported else w
    with pytest.raises(ConfigError, match=message) as info:
        _cfg(weight, m, self_reported_weight=w, **overrides)
    assert isinstance(info.value, OpenEngineError)
    assert not isinstance(info.value, OverflowError)


def _assert_rates_finitely(cfg: Config) -> None:
    ledger = [
        _match("sr", SR, EVEN_GAMES),
        _match("t", MatchType.TOURNAMENT, ((11, 7),)),
    ]
    eng = RatingEngine(cfg)
    for match in ledger:
        assert eng.record(match).counted
    replayed = replay(ledger, cfg=cfg, as_of=ON)
    for engine in (eng, replayed):
        for pid in PLAYERS:
            state = engine.state(pid, D)
            assert math.isfinite(state.rating)
            assert math.isfinite(state.rd)


ACCEPTED = [
    pytest.param(
        lambda: _cfg(2**511, 2**511, k_min=0, k_max=1, rd_shrink_per_match=1), id="at-bound"
    ),
    pytest.param(
        lambda: _cfg(ISSUE_W, ISSUE_W, self_reported_repeat_factor=0.5, **REPRO),
        id="float-repeat-factor",
    ),
    pytest.param(
        lambda: _cfg(ISSUE_W, ISSUE_W, self_reported_weight=1.0, **REPRO),
        id="float-self-reported-weight",
    ),
    pytest.param(lambda: Config(rd_shrink_per_match=2), id="int-shrink-float-weights"),
    pytest.param(Config, id="default"),
]


@pytest.mark.parametrize("build", ACCEPTED)
def test_config_without_overflow_still_builds_and_rates(build: object) -> None:
    assert callable(build)
    cfg = build()
    assert isinstance(cfg, Config)
    _assert_rates_finitely(cfg)


def test_default_fingerprint_is_unchanged() -> None:
    # Re-pinned in issue #155 when params_version joined the fingerprint payload,
    # confirmed equal to the old pin without that key.
    assert Config().fingerprint() == "30dc01fa90e94c97"


# Configs that build both before and after the fix, int and float.
GRID = {
    "default": lambda: Config(),
    "int-all": lambda: Config(
        match_weights={t: i + 1 for i, t in enumerate(MatchType)},
        max_information_multiplier=2,
        game_length_weights=HUGE_LENGTHS,
        self_reported_repeat_factor=1,
        rd_shrink_per_match=0,
    ),
    "int-weights-float-factor": lambda: Config(
        match_weights={t: 2 for t in MatchType},
        max_information_multiplier=3,
        self_reported_repeat_factor=0.5,
    ),
    "float-weights-int-multiplier": lambda: Config(
        match_weights={t: 0.7 for t in MatchType},
        max_information_multiplier=2,
        rd_shrink_per_match=0.01,
    ),
    "int-at-bound": lambda: _cfg(2**511, 2**511, k_min=0, k_max=1, rd_shrink_per_match=1),
}

GRID_LEDGER = [
    Match("g1", ("a", "b"), ("c", "d"), ((11, 7),), date(2026, 3, 1), D),
    Match(
        "g2",
        ("a", "c"),
        ("b", "d"),
        ((11, 9), (6, 11), (11, 4)),
        date(2026, 3, 8),
        D,
        best_of=3,
        match_type=SR,
        confirmed_by=frozenset({"a", "b"}),
    ),
    Match(
        "g3",
        ("a", "c"),
        ("b", "d"),
        ((9, 11),),
        date(2026, 3, 9),
        D,
        match_type=SR,
        confirmed_by=frozenset({"c", "d"}),
    ),
    Match(
        "g4",
        ("a", "d"),
        ("b", "c"),
        ((11, 5), (11, 8)),
        date(2026, 3, 20),
        D,
        best_of=3,
        match_type=MatchType.LEAGUE,
    ),
]

# Computed on unmodified code (c3d1f37, before the fix): per match the
# effective weight, then the final (rating, rd) per player.
PINNED: dict[str, tuple[list[float], dict[str, tuple[float, float]]]] = {
    "default": (
        [0.75, 0.496, 0.2, 1.008],
        {
            "a": (3.62486486306851, 0.6549977068054611),
            "b": (3.4751351369314896, 0.6549977068054611),
            "c": (3.3849058127945493, 0.6549977068054611),
            "d": (3.515094187205451, 0.6549977068054611),
        },
    ),
    "float-weights-int-multiplier": (
        [0.7, 0.868, 0.35, 0.784],
        {
            "a": (3.614132116939152, 0.7803075747522376),
            "b": (3.479201216394182, 0.7803075747522376),
            "c": (3.401708683204926, 0.7803075747522376),
            "d": (3.50495798346174, 0.7803075747522376),
        },
    ),
    "int-all": (
        [6.0, 8, 8, 4.0],
        {
            "a": (3.8038842323354594, 0.8),
            "b": (3.9961157676645414, 0.8),
            "c": (2.3867413751926008, 0.8),
            "d": (3.8132586248073985, 0.8),
        },
    ),
    "int-at-bound": (
        [4.49423283715579e307, 2**1022, 2**1022, 4.49423283715579e307],
        {"a": (8.0, 0.1), "b": (2.0, 0.1), "c": (2.0, 0.1), "d": (8.0, 0.1)},
    ),
    "int-weights-float-factor": (
        [2.0, 2.48, 1.0, 2.24],
        {
            "a": (3.7661292032063334, 0.4086354324256164),
            "b": (3.5005374634603332, 0.4086354324256164),
            "c": (3.273203080334859, 0.4086354324256164),
            "d": (3.4601302529984745, 0.4086354324256164),
        },
    ),
}


@pytest.mark.parametrize("name", sorted(GRID))
def test_valid_config_ratings_are_unchanged(name: str) -> None:
    cfg = GRID[name]()
    eng = RatingEngine(cfg)
    weights = []
    for match in GRID_LEDGER:
        result = eng.record(match)
        assert result.counted
        weights.append(result.effective_weight)
    states = {pid: (eng.state(pid, D).rating, eng.state(pid, D).rd) for pid in PLAYERS}
    expected_weights, expected_states = PINNED[name]
    assert weights == expected_weights
    assert states == expected_states
    replayed = replay(GRID_LEDGER, cfg=cfg, as_of=date(2026, 3, 20))
    assert {
        pid: (replayed.state(pid, D).rating, replayed.state(pid, D).rd) for pid in PLAYERS
    } == expected_states
