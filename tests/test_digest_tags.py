"""Match digests take their type tags from a fixed table, not __module__ (issue #133).

A checkpoint stores a digest for every row it considered, and resume
compares them. The digest tags each value with a string for its exact
type. Those strings come from _ledger._DIGEST_TAGS, so moving a class to
another module, or a class reporting another module, leaves every digest
unchanged. All ids are synthetic.
"""

from __future__ import annotations

import typing
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from types import NoneType, UnionType

import pytest

import open_engine._ledger as ledger_module
from open_engine import (
    GameFormat,
    Match,
    MatchStatus,
    MatchType,
    RatingFormat,
    ScoringType,
)

ROW = Match(
    "syn-d1",
    ("syn-a", "syn-b"),
    ("syn-c", "syn-d"),
    [(11, 8), [6, 11], (11, 9)],
    date(2026, 3, 1),
    RatingFormat.DOUBLES,
    match_type=MatchType.SELF_REPORTED,
    best_of=3,
    game_format=GameFormat(ScoringType.RALLY, 11, 2, 15),
    played_at=datetime(2026, 3, 1, 10, tzinfo=timezone(timedelta(hours=-5))),
    sequence=2,
    confirmed_by={"syn-a", "syn-c"},
    status=MatchStatus.COMPLETED,
)

ENGINE_CLASSES = [RatingFormat, MatchType, ScoringType, MatchStatus, GameFormat]


@pytest.mark.parametrize("cls", ENGINE_CLASSES, ids=lambda cls: cls.__name__)
def test_digest_does_not_read_the_module_a_class_reports(
    cls: type, monkeypatch: pytest.MonkeyPatch
) -> None:
    ROW.validate()
    before = ledger_module._match_digest(ROW)
    # On Python 3.13 and later, setting a class's __module__ deletes its
    # __firstlineno__, which inspect.getsource reads. Registered first, so
    # undone last, this restores it after __module__ is restored.
    if "__firstlineno__" in vars(cls):
        monkeypatch.setattr(cls, "__firstlineno__", vars(cls)["__firstlineno__"])
    monkeypatch.setattr(cls, "__module__", "somewhere.else")
    assert ledger_module._match_digest(ROW) == before


# Match._validate_field_types accepts these where the annotation names
# another container: games and each game as a list as well as a tuple,
# and confirmed_by as a set as well as a frozenset.
VALIDATED_ALTERNATIVES = {list, set}

# The tag each type has always had, from the module it reported when
# checkpoints first stored digests. A change here changes stored digests.
EXPECTED_TAGS = {
    str: "builtins.str",
    int: "builtins.int",
    NoneType: "builtins.NoneType",
    tuple: "builtins.tuple",
    list: "builtins.list",
    frozenset: "builtins.frozenset",
    set: "builtins.set",
    date: "datetime.date",
    RatingFormat: "open_engine.engine.RatingFormat",
    MatchType: "open_engine.engine.MatchType",
    ScoringType: "open_engine.engine.ScoringType",
    MatchStatus: "open_engine.engine.MatchStatus",
    GameFormat: "open_engine.engine.GameFormat",
}


def _leaf_types(hint: object, found: set[type]) -> None:
    """Every class a type hint names, recursing into dataclasses and enum values."""
    args = typing.get_args(hint)
    if args:
        origin = typing.get_origin(hint)
        # A union is not a container: only its members are held.
        if origin not in (typing.Union, UnionType):
            assert isinstance(origin, type), hint
            found.add(origin)
        for arg in args:
            if arg is not Ellipsis:
                _leaf_types(arg, found)
        return
    assert isinstance(hint, type), hint
    if hint in found:
        return
    found.add(hint)
    if is_dataclass(hint):
        for name, field_hint in typing.get_type_hints(hint).items():
            assert name in {f.name for f in fields(hint)}
            _leaf_types(field_hint, found)
    if issubclass(hint, Enum):
        for member in hint:
            found.add(type(member.value))


def _digested_types() -> set[type]:
    """Every exact type a validated Match can hold in a digested field."""
    found: set[type] = set()
    for name, hint in typing.get_type_hints(Match).items():
        # played_at is digested as its UTC instant and offset, not tagged.
        if name != "played_at":
            _leaf_types(hint, found)
    return found | VALIDATED_ALTERNATIVES


def test_every_type_a_match_can_hold_has_its_digest_tag() -> None:
    digested = _digested_types()
    assert digested == set(EXPECTED_TAGS)
    table = ledger_module._DIGEST_TAGS
    assert {t: table.get(t) for t in digested} == EXPECTED_TAGS


# The table is a superset: it also tags bool and float, which a validated
# match never holds, with the strings they have always had.
SUPERSET_TAGS = {bool: "builtins.bool", float: "builtins.float"}


def test_the_table_holds_exactly_the_pinned_tags() -> None:
    assert ledger_module._DIGEST_TAGS == {**EXPECTED_TAGS, **SUPERSET_TAGS}


class _NoHashMeta(type):
    def __hash__(cls) -> int:
        raise AssertionError("hashed")

    def __eq__(cls, other: object) -> bool:
        raise AssertionError("compared with ==")


def test_a_tag_lookup_runs_no_metaclass_hash_or_eq() -> None:
    odd = _NoHashMeta("Odd", (), {})
    assert ledger_module._digest_tag(odd) is None
    assert ledger_module._digest_tag(float) == "builtins.float"
