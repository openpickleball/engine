"""The int step weight check runs only when the int multiplier is reachable (issue #93).

information_multiplier returns max_information_multiplier itself only when
length * count is not below it, for some allowed format's length weight and
some game count. Otherwise it returns a float, the self-reported step weight
is a float, and the exact int product never forms. So a config whose int
multiplier no match can reach builds and rates as before, and one whose
multiplier only a longer match reaches is still rejected.
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
    RatingEngine,
    RatingFormat,
    replay,
)

D = RatingFormat.DOUBLES
SR = MatchType.SELF_REPORTED
PLAYERS = ("a", "b", "c", "d")
ON = date(2026, 1, 5)
RD_MESSAGE = (
    "rd_shrink_per_match times the largest match weight and max_information_multiplier "
    "must be finite"
)

# Each pair would overflow in exact int arithmetic if the engine formed w * m.
UNREACHED = [
    pytest.param(2**511, 2**511, {"rd_shrink_per_match": 4}, id="rd-shrink"),
    pytest.param(
        2**511 + 2**458 - 1, 2**513 - 2**459 - 1, {"rd_shrink_per_match": 0}, id="no-conversion"
    ),
    pytest.param(
        math.isqrt(6 * 2**1024 // 10),
        math.isqrt(6 * 2**1024 // 10),
        {"rd_shrink_per_match": 2},
        id="issue-values",
    ),
]


def _cfg(w: int, m: int, **overrides: object) -> Config:
    fields: dict[str, object] = {
        "k_min": 0,
        "k_max": 1,
        "match_weights": dict.fromkeys(MatchType, w),
        "max_information_multiplier": m,
        "self_reported_repeat_factor": 1,
    }
    fields.update(overrides)
    return Config(**fields)  # type: ignore[arg-type]


def _ledger() -> list[Match]:
    def sr(match_id: str, games: tuple[tuple[int, int], ...], day: int) -> Match:
        return Match(
            match_id,
            ("a", "b"),
            ("c", "d"),
            games,
            date(2026, 1, day),
            D,
            best_of=len(games) if len(games) > 1 else 1,
            match_type=SR,
            confirmed_by=frozenset({"a", "c"}),
        )

    return [
        sr("s1", ((11, 7),), 5),
        sr("s2", ((11, 9), (0, 11), (11, 2)), 6),
        sr("s3", ((11, 9), (9, 11), (11, 7), (5, 11), (11, 3)), 7),
        Match("t1", ("a", "c"), ("b", "d"), ((11, 4),), date(2026, 1, 8), D),
    ]


@pytest.mark.parametrize(("w", "m", "overrides"), UNREACHED)
def test_unreachable_int_multiplier_builds_and_records(
    w: int, m: int, overrides: dict[str, object]
) -> None:
    # Default game_length_weights and game_count_bonus: length * count stays
    # far below m for every format and game count.
    cfg = _cfg(w, m, **overrides)
    eng = RatingEngine(cfg)
    for match in _ledger():
        result = eng.record(match)
        assert result.counted
        assert isinstance(result.effective_weight, float)
    replayed = replay(_ledger(), cfg=cfg, as_of=date(2026, 1, 8))
    for engine in (eng, replayed):
        for pid in PLAYERS:
            state = engine.state(pid, D)
            assert math.isfinite(state.rating)
            assert math.isfinite(state.rd)


REACHED = [
    # The second game reaches m: 1.0 * (1.0 + 2**511) is not below 2**511.
    pytest.param({"game_count_bonus": 2**511}, id="second-game"),
    # Only a match with about 2**62 games reaches m.
    pytest.param({"game_count_bonus": 2.0**450}, id="largest-game-count"),
    # A length weight for another allowed format reaches m in one game.
    pytest.param(
        {"game_length_weights": {11: 1.0, 15: 1.1, 21: int(sys.float_info.max)}},
        id="other-format",
    ),
    # A tie returns the multiplier itself.
    pytest.param(
        {"game_length_weights": {11: 2**511, 15: 1.1, 21: 1.2}, "game_count_bonus": 0},
        id="tie",
    ),
]


@pytest.mark.parametrize("overrides", REACHED)
def test_int_multiplier_reached_by_some_match_is_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ConfigError, match=RD_MESSAGE):
        _cfg(2**511, 2**511, rd_shrink_per_match=4, **overrides)


def test_multiplier_reached_at_second_game_overflowed_before() -> None:
    # The second-game config with a float shrink builds, and a two-game
    # self-reported match forms the exact int step weight 2**1022.
    cfg = _cfg(2**511, 2**511, rd_shrink_per_match=4.0, game_count_bonus=2**511)
    eng = RatingEngine(cfg)
    assert isinstance(eng.record(_ledger()[0]).effective_weight, float)
    result = eng.record(
        Match(
            "s4",
            ("a", "b"),
            ("c", "d"),
            ((11, 9), (11, 2)),
            date(2026, 1, 9),
            D,
            best_of=3,
            match_type=SR,
            confirmed_by=frozenset({"a", "c"}),
        )
    )
    assert result.effective_weight == 2**1022
    assert isinstance(result.effective_weight, int)
