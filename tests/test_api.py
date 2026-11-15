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
    GameFormat,
    Match,
    MatchResult,
    MatchType,
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


# -- rejection reasons -----------------------------------------------------------

S = RatingFormat.SINGLES
REJECTIONS = [
    "DUPLICATE_MATCH_ID",
    "GAME_FORMAT_NOT_ACCEPTED",
    "SELF_REPORTED_UNCONFIRMED",
    "SELF_REPORTED_REPEAT_LIMIT",
    "BEFORE_IMPORTED_BASELINE",
    "SINGLES_RATING_GAP",
    "DOUBLES_PARTNER_GAP",
]


def _seeded(cfg: Config, fmt: RatingFormat, ratings: dict[str, float]) -> RatingEngine:
    eng = RatingEngine(cfg)
    for pid, rating in ratings.items():
        eng.seed_external_rating(pid, fmt, ExternalRatingSnapshot("ext", rating, date(2026, 1, 1)))
    return eng


def _self_reported(match_id: str, confirmed_by: frozenset[str] = frozenset()) -> Match:
    return Match(
        match_id,
        ("a", "b"),
        ("c", "d"),
        ((11, 7),),
        ON,
        D,
        MatchType.SELF_REPORTED,
        confirmed_by=confirmed_by,
    )


def _duplicate() -> tuple[MatchResult, RatingFormat]:
    eng = RatingEngine()
    eng.record(_match())
    return eng.record(_match()), D


def _format_rejected() -> tuple[MatchResult, RatingFormat]:
    m = Match("m1", ("a",), ("c",), ((9, 5),), ON, S, game_format=GameFormat(points_to=9))
    return RatingEngine().record(m), S


def _unconfirmed() -> tuple[MatchResult, RatingFormat]:
    return RatingEngine().record(_self_reported("m1")), D


def _repeat_limit() -> tuple[MatchResult, RatingFormat]:
    eng = RatingEngine(Config(self_reported_max_repeats=1))
    confirmed = frozenset({"a", "c"})
    assert eng.record(_self_reported("m1", confirmed)).counted
    return eng.record(_self_reported("m2", confirmed)), D


def _before_baseline() -> tuple[MatchResult, RatingFormat]:
    eng = RatingEngine()
    for pid in ("a", "b", "c", "d"):
        eng.seed_external_rating(pid, D, ExternalRatingSnapshot("ext", 4.0, ON))
    return eng.record(_match()), D


def _singles_gap() -> tuple[MatchResult, RatingFormat]:
    eng = _seeded(Config(max_singles_rating_gap=0.5), S, {"a": 3.0, "c": 5.0})
    return eng.record(Match("m1", ("a",), ("c",), ((11, 7),), ON, S)), S


def _partner_gap() -> tuple[MatchResult, RatingFormat]:
    ratings = {"a": 3.0, "b": 5.0, "c": 4.0, "d": 4.0}
    eng = _seeded(Config(max_doubles_partner_gap=0.5), D, ratings)
    return eng.record(_match()), D


REJECTION_CASES: dict[str, Callable[[], tuple[MatchResult, RatingFormat]]] = {
    "DUPLICATE_MATCH_ID": _duplicate,
    "GAME_FORMAT_NOT_ACCEPTED": _format_rejected,
    "SELF_REPORTED_UNCONFIRMED": _unconfirmed,
    "SELF_REPORTED_REPEAT_LIMIT": _repeat_limit,
    "BEFORE_IMPORTED_BASELINE": _before_baseline,
    "SINGLES_RATING_GAP": _singles_gap,
    "DOUBLES_PARTNER_GAP": _partner_gap,
}


def test_rejection_reason_members_and_values() -> None:
    reason = open_engine.RejectionReason
    assert issubclass(reason, str)
    assert [member.name for member in reason] == REJECTIONS
    assert all(member.value == member.name.lower() for member in reason)


@pytest.mark.parametrize("name", REJECTIONS)
def test_each_rejection_reports_its_reason_and_format(name: str) -> None:
    result, fmt = REJECTION_CASES[name]()
    assert not result.counted
    assert result.reason is open_engine.RejectionReason[name]
    assert isinstance(result.rating_format, RatingFormat)
    assert result.rating_format is fmt


def test_counted_match_has_no_reason() -> None:
    result = RatingEngine().record(_match())
    assert result.counted
    assert result.reason is None
    assert isinstance(result.rating_format, RatingFormat)
    assert result.rating_format is D


def test_category_parent_result_reports_the_parent_format() -> None:
    m = Match("m1", ("a", "b"), ("c", "d"), ((11, 7),), ON, RatingFormat.MIXED_DOUBLES)
    result = RatingEngine().record(m)
    assert result.rating_format is RatingFormat.MIXED_DOUBLES
    assert result.parent is not None
    assert result.parent.reason is None
    assert result.parent.rating_format is D
