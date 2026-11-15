"""Tests for the public API contract: exports, exceptions, result types, docs."""

from __future__ import annotations

import ast
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

import open_engine
import open_engine.engine as engine_module
from open_engine import (
    Config,
    ExternalRatingSnapshot,
    Match,
    RatingEngine,
    RatingFormat,
    prequential_predictions,
    replay,
)

SRC = Path(open_engine.__file__).parent
D = RatingFormat.DOUBLES
ON = date(2026, 10, 29)

ERROR_NAMES = [
    "ConfigError",
    "InvalidMatchError",
    "InvalidSeedError",
    "PlayerError",
    "EvaluationError",
    "LedgerConflictError",
]


def _match(match_id: str = "m1", games: tuple[tuple[int, int], ...] = ((11, 7),)) -> Match:
    return Match(match_id, ("a", "b"), ("c", "d"), games, ON, D)


# -- exceptions ----------------------------------------------------------------


def test_open_engine_error_is_a_value_error() -> None:
    assert issubclass(open_engine.OpenEngineError, ValueError)


@pytest.mark.parametrize("name", ERROR_NAMES)
def test_error_classes_share_the_base(name: str) -> None:
    cls = getattr(open_engine, name)
    assert issubclass(cls, open_engine.OpenEngineError)
    assert issubclass(cls, ValueError)


def _bad_config() -> None:
    Config(min_rating=9.0)


def _bad_match() -> None:
    _match(match_id=" ").validate()


def _bad_seed() -> None:
    RatingEngine().seed_external_rating("a", D, ExternalRatingSnapshot("ext", 99.0, ON))


def _bad_player() -> None:
    RatingEngine().player(" ")


def _bad_mode() -> None:
    prequential_predictions([], mode="sometimes")  # type: ignore[arg-type]


def _conflict() -> None:
    replay([_match(), _match(games=((11, 9),))], as_of=ON)


@pytest.mark.parametrize(
    ("name", "action", "message"),
    [
        ("ConfigError", _bad_config, "min_rating must be less than max_rating"),
        ("InvalidMatchError", _bad_match, "match_id is required"),
        ("InvalidSeedError", _bad_seed, "outside the configured scale"),
        ("PlayerError", _bad_player, "player_id is required"),
        ("EvaluationError", _bad_mode, "mode must be"),
        ("LedgerConflictError", _conflict, "Conflicting ledger rows"),
    ],
)
def test_each_error_class_is_raised(name: str, action: Callable[[], None], message: str) -> None:
    with pytest.raises(getattr(open_engine, name), match=message):
        action()


def _raised_names(path: Path) -> list[tuple[int, str]]:
    """Name of the class in every raise statement, with its line number."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        found.append((node.lineno, ast.unparse(target)))
    return found


@pytest.mark.parametrize("filename", ["engine.py", "winprob.py"])
def test_every_raise_uses_an_engine_error(filename: str) -> None:
    raised = _raised_names(SRC / filename)
    assert raised
    bad = [
        (line, name)
        for line, name in raised
        if not (
            isinstance(cls := getattr(engine_module, name, None), type)
            and issubclass(cls, open_engine.OpenEngineError)
        )
    ]
    assert bad == []
