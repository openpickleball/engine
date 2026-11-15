"""Win probabilities validate the game format first and raise ConfigError."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from open_engine import (
    ConfigError,
    GameFormat,
    RatingEngine,
    RatingFormat,
    game_win_probability,
    match_win_probability,
)

BAD_FORMATS = [
    (GameFormat(points_to=0), "points_to must be positive"),
    (GameFormat(cap=5), "cap must be greater than points_to"),
    (GameFormat(win_by=0), "win_by must be positive"),
]


def _game(fmt: GameFormat) -> float:
    return game_win_probability(0.6, fmt)


def _match(fmt: GameFormat) -> float:
    return match_win_probability(0.6, fmt, 3)


def _engine(fmt: GameFormat) -> float:
    return RatingEngine().predict_win_probability(("a",), ("b",), RatingFormat.SINGLES, fmt)


@pytest.mark.parametrize("call", [_game, _match, _engine], ids=["game", "match", "engine"])
@pytest.mark.parametrize(("fmt", "message"), BAD_FORMATS, ids=repr)
def test_invalid_game_format_raises_config_error(
    call: Callable[[GameFormat], float], fmt: GameFormat, message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        call(fmt)
