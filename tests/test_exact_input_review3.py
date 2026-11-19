"""Third review of the exact input type rule (issues #116, #118, #119).

A type whose __name__ is a str subclass, and a metaclass whose __eq__ runs
when a type is compared with ==, must not run caller code in a rejection.
All ids are synthetic.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest

from open_engine import (
    Config,
    ConfigError,
    EvaluationError,
    InvalidMatchError,
    Match,
    OpenEngineError,
    RatingEngine,
    RatingFormat,
    ReferenceDateError,
    evaluate,
    reliability,
    replay,
)

S = RatingFormat.SINGLES
RAN: list[str] = []


class _LoudName(str):
    def __format__(self, spec: str) -> str:
        RAN.append("format")
        raise RuntimeError("format ran")

    def __str__(self) -> str:
        RAN.append("str")
        raise RuntimeError("str ran")

    def __hash__(self) -> int:
        RAN.append("hash")
        raise RuntimeError("hash ran")

    def __eq__(self, other: object) -> bool:
        RAN.append("eq")
        raise RuntimeError("eq ran")

    def __lt__(self, other: object) -> bool:
        RAN.append("lt")
        raise RuntimeError("lt ran")


class _Named:
    pass


_Named.__name__ = _LoudName("_Named")


class _EqMeta(type):
    def __eq__(cls, other: object) -> bool:
        RAN.append("eq")
        raise RuntimeError("metaclass eq ran")

    def __ne__(cls, other: object) -> bool:
        RAN.append("ne")
        raise RuntimeError("metaclass ne ran")

    __hash__ = type.__hash__


class _Compared(metaclass=_EqMeta):
    pass


def _match(**changes: object) -> Match:
    fields: dict[str, object] = {
        "match_id": "syn-m1",
        "team_a": ("syn-a",),
        "team_b": ("syn-b",),
        "games": ((11, 5),),
        "played_on": date(2026, 3, 1),
        "rating_format": S,
    }
    fields.update(changes)
    return Match(**fields)  # type: ignore[arg-type]


def _calls(kind: type) -> list[tuple[str, type[OpenEngineError], Callable[[], object]]]:
    return [
        ("record games", InvalidMatchError, lambda: RatingEngine().record(_match(games=kind()))),
        (
            "record game",
            InvalidMatchError,
            lambda: RatingEngine().record(_match(games=(kind(),))),
        ),
        ("record team", InvalidMatchError, lambda: RatingEngine().record(_match(team_a=kind()))),
        (
            "predict team",
            InvalidMatchError,
            lambda: RatingEngine().predict_point_share(kind(), ("syn-b",), S),  # type: ignore[arg-type]
        ),
        ("replay as_of", ReferenceDateError, lambda: replay([], as_of=kind())),  # type: ignore[arg-type]
        ("from_dict key", ConfigError, lambda: Config.from_dict({kind(): 1})),  # type: ignore[dict-item]
        (
            "from_dict formats",
            ConfigError,
            lambda: Config.from_dict({**Config().to_dict(), "allowed_game_formats": [kind()]}),
        ),
        ("evaluate mode", EvaluationError, lambda: evaluate([], mode=kind())),  # type: ignore[arg-type]
        (
            "reliability state",
            EvaluationError,
            lambda: reliability(kind(), Config(), date(2026, 1, 1)),  # type: ignore[arg-type]
        ),
    ]


CASES = [("name", label, error, call) for label, error, call in _calls(_Named)] + [
    ("metaclass eq", label, error, call) for label, error, call in _calls(_Compared)
]


@pytest.mark.parametrize(
    ("kind", "label", "error", "call"),
    CASES,
    ids=[f"{kind}-{label}" for kind, label, _, _ in CASES],
)
def test_rejections_run_no_caller_code(
    kind: str, label: str, error: type[OpenEngineError], call: Callable[[], object]
) -> None:
    RAN.clear()
    with pytest.raises(error):
        call()
    assert RAN == []
