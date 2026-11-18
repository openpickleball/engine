"""A match and its containers must be exact types (issue #109).

A Match subclass can return one value to validation and another to rating,
for example through a property that changes between reads. The engine then
holds a state its own snapshot cannot load. So record, replay, and
prequential_predictions take only a Match itself, with tuple teams, a set
or frozenset confirmed_by whose members are plain str ids, and games built
from plain tuples or lists of plain ints. Each raises InvalidMatchError
before any state changes. All ids are synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from open_engine import (
    InvalidMatchError,
    LedgerConflictError,
    Match,
    MatchType,
    RatingEngine,
    RatingFormat,
    evaluate,
    fit_scale,
    fit_win_calibration,
    ledger_order,
    prequential_predictions,
    replay,
)

D = RatingFormat.DOUBLES
AS_OF = date(2026, 11, 1)


class Blank(str):
    """A str subclass, as in the issue."""


class SubStr(str):
    """A str subclass that changes nothing."""


class AlwaysEqual(str):
    """A str subclass that equals everything."""

    def __eq__(self, other: object) -> bool:
        return True

    __hash__ = str.__hash__


class SubMatch(Match):
    """A Match subclass that changes nothing."""


class SubTuple(tuple):  # type: ignore[type-arg]
    """A tuple subclass that changes nothing."""


class SubList(list):  # type: ignore[type-arg]
    """A list subclass that changes nothing."""


class SubFrozenset(frozenset):  # type: ignore[type-arg]
    """A frozenset subclass that changes nothing."""


class SubSet(set):  # type: ignore[type-arg]
    """A set subclass that changes nothing."""


class SubInt(int):
    """An int subclass that changes nothing."""


def _plain(match_id: str = "syn-m1", day: int = 10, **kw: Any) -> Match:
    fields: dict[str, Any] = {
        "match_id": match_id,
        "team_a": ("syn-a", "syn-b"),
        "team_b": ("syn-c", "syn-d"),
        "games": ((11, 7),),
        "played_on": date(2026, 10, day),
        "rating_format": D,
    }
    fields.update(kw)
    return Match(**fields)


def _flip(match_id: str = "syn-m1", day: int = 10) -> Match:
    """The issue's match: its match_id is valid on the first read only."""
    reads = [0]

    class FlipMatch(Match):
        @property  # type: ignore[override]
        def match_id(self) -> str:
            reads[0] += 1
            return match_id if reads[0] == 1 else Blank(" ")

        @match_id.setter
        def match_id(self, value: str) -> None:
            pass

    return FlipMatch(
        match_id, ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 7),), date(2026, 10, day), D
    )


def _validates_itself(match_id: str = "syn-m1") -> Match:
    """A subclass whose own validate accepts anything."""

    class Trusting(Match):
        def validate(self) -> None:
            pass

    return Trusting(
        match_id, ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 7),), date(2026, 10, 10), D
    )


SUBCLASS_MATCHES: list[Callable[[], Match]] = [
    _flip,
    lambda: SubMatch(
        "syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 7),), date(2026, 10, 10), D
    ),
    _validates_itself,
]
SUBCLASS_IDS = ["flip", "plain-subclass", "own-validate"]


def _state(engine: RatingEngine) -> str:
    return json.dumps(engine.to_snapshot(), sort_keys=True)


def test_plain_match_snapshot_loads() -> None:
    """A plain Match records and its snapshot loads, as the baseline."""
    engine = RatingEngine()
    assert engine.record(_plain()).counted
    RatingEngine.from_snapshot(json.loads(json.dumps(engine.to_snapshot())))


@pytest.mark.parametrize("make", SUBCLASS_MATCHES, ids=SUBCLASS_IDS)
def test_record_rejects_a_match_subclass_before_any_change(make: Callable[[], Match]) -> None:
    engine = RatingEngine()
    engine.record(_plain("syn-m0", day=9))
    before = _state(engine)
    with pytest.raises(InvalidMatchError, match="match must be a Match, not"):
        engine.record(make())
    assert _state(engine) == before
    RatingEngine.from_snapshot(json.loads(json.dumps(engine.to_snapshot())))


@pytest.mark.parametrize("make", SUBCLASS_MATCHES, ids=SUBCLASS_IDS)
@pytest.mark.parametrize("position", ["first", "middle", "last", "after-as-of"])
def test_replay_rejects_a_match_subclass_anywhere(make: Callable[[], Match], position: str) -> None:
    rows = [_plain("syn-m0", day=1), _plain("syn-m2", day=20)]
    bad = make()
    if position == "first":
        ledger = [bad, *rows]
    elif position == "middle":
        ledger = [rows[0], bad, rows[1]]
    elif position == "last":
        ledger = [*rows, bad]
    else:
        ledger = [*rows, bad]
    as_of = date(2026, 10, 5) if position == "after-as-of" else AS_OF
    with pytest.raises(InvalidMatchError, match="match must be a Match, not"):
        replay(ledger, as_of=as_of)


@pytest.mark.parametrize("make", SUBCLASS_MATCHES, ids=SUBCLASS_IDS)
@pytest.mark.parametrize("mode", ["live", "scheduled"])
def test_prequential_predictions_rejects_a_match_subclass(
    make: Callable[[], Match], mode: str
) -> None:
    ledger = [_plain("syn-m0", day=1), make()]
    with pytest.raises(InvalidMatchError, match="match must be a Match, not"):
        prequential_predictions(ledger, mode=mode)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "call",
    [
        lambda rows: evaluate(rows),
        lambda rows: fit_scale(rows),
        lambda rows: fit_win_calibration(rows),
    ],
    ids=["evaluate", "fit_scale", "fit_win_calibration"],
)
def test_evaluation_entry_points_reject_a_match_subclass(
    call: Callable[[list[Match]], object],
) -> None:
    with pytest.raises(InvalidMatchError, match="match must be a Match, not"):
        call([_plain("syn-m0", day=1), _flip()])


def test_validate_rejects_a_subclass_that_keeps_it() -> None:
    with pytest.raises(InvalidMatchError, match="match must be a Match, not SubMatch"):
        SubMatch(
            "syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 7),), date(2026, 10, 10), D
        ).validate()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"team_a": SubTuple(("syn-a", "syn-b"))}, "team_a must be a tuple, not SubTuple"),
        ({"team_b": SubTuple(("syn-c", "syn-d"))}, "team_b must be a tuple, not SubTuple"),
        (
            {"confirmed_by": SubFrozenset({"syn-a"})},
            "confirmed_by must be a set or frozenset, not SubFrozenset",
        ),
        (
            {"confirmed_by": SubSet({"syn-a"})},
            "confirmed_by must be a set or frozenset, not SubSet",
        ),
        ({"games": SubList([(11, 7)])}, "games must be a tuple or list, not SubList"),
        ({"games": (SubTuple((11, 7)),)}, "Each game must be a pair of scores"),
        ({"games": (SubList([11, 7]),)}, "Each game must be a pair of scores"),
        ({"games": ((SubInt(11), 7),)}, "Game scores must be integers"),
        ({"games": ((11, SubInt(7)),)}, "Game scores must be integers"),
    ],
)
def test_container_subclasses_raise(changes: dict[str, Any], message: str) -> None:
    engine = RatingEngine()
    before = _state(engine)
    match = _plain(**changes)
    with pytest.raises(InvalidMatchError, match=message):
        match.validate()
    with pytest.raises(InvalidMatchError, match=message):
        engine.record(match)
    assert _state(engine) == before
    with pytest.raises(InvalidMatchError, match=message):
        replay([_plain("syn-m0", day=1), replace(match, match_id="syn-m9")], as_of=AS_OF)


@pytest.mark.parametrize(
    "member",
    [SubStr("syn-a"), AlwaysEqual("syn-x"), Blank("syn-a"), "", "   ", 7, None],
    ids=["str-subclass", "always-equal", "blank-subclass", "empty", "spaces", "int", "none"],
)
def test_confirmed_by_members_follow_the_player_id_rule(member: object) -> None:
    engine = RatingEngine()
    before = _state(engine)
    match = _plain(
        match_type=MatchType.SELF_REPORTED,
        confirmed_by=frozenset({"syn-c", member}),
    )
    with pytest.raises(InvalidMatchError, match="confirmed_by player ids must be non-empty"):
        match.validate()
    with pytest.raises(InvalidMatchError, match="confirmed_by player ids must be non-empty"):
        engine.record(match)
    assert _state(engine) == before


def test_plain_inputs_still_record() -> None:
    engine = RatingEngine()
    match = _plain(
        match_type=MatchType.SELF_REPORTED,
        confirmed_by={"syn-a", "syn-c"},
        games=[[11, 7]],
    )
    assert engine.record(match).counted
    assert engine.record(_plain("syn-m2", day=11, confirmed_by=frozenset())).counted


def _shadowed_validate() -> Match:
    """An exact Match whose instance dict hides validate, with a blank id."""
    match = _plain(Blank(" "))
    match.__dict__["validate"] = lambda: None
    return match


def test_record_ignores_an_instance_validate() -> None:
    engine = RatingEngine()
    before = _state(engine)
    with pytest.raises(InvalidMatchError, match="match_id is required"):
        engine.record(_shadowed_validate())
    assert _state(engine) == before


@pytest.mark.parametrize("mode", ["live", "scheduled"])
def test_ledger_entry_points_ignore_an_instance_validate(mode: str) -> None:
    ledger = [_plain("syn-m0", day=1), _shadowed_validate()]
    with pytest.raises(InvalidMatchError, match="match_id is required"):
        replay(ledger, as_of=AS_OF)
    with pytest.raises(InvalidMatchError, match="match_id is required"):
        prequential_predictions(ledger, mode=mode)  # type: ignore[arg-type]


def test_repeated_rows_compare_every_field_even_when_fields_are_shadowed() -> None:
    """Hiding a field from dataclasses.fields on the instance must not merge rows."""
    a = _plain(games=((11, 2),))
    b = _plain(games=((2, 11),))
    for match in (a, b):
        shown = {k: v for k, v in Match.__dataclass_fields__.items() if k != "games"}
        match.__dict__["__dataclass_fields__"] = shown
    with pytest.raises(LedgerConflictError, match="syn-m1"):
        replay([a, b], as_of=AS_OF)
    with pytest.raises(LedgerConflictError, match="syn-m1"):
        replay([b, a], as_of=AS_OF)


def test_ledger_order_rejects_a_match_subclass() -> None:
    with pytest.raises(InvalidMatchError, match="match must be a Match, not FlipMatch"):
        ledger_order(_flip())
    with pytest.raises(InvalidMatchError, match="match must be a Match, not str"):
        ledger_order("syn-m1")  # type: ignore[arg-type]
