"""The public string enums print as their values on every supported Python."""

from __future__ import annotations

import json
import pickle
from enum import Enum

import pytest

from open_engine import (
    Config,
    MatchType,
    RatingFormat,
    RejectionReason,
    ReliabilityBasis,
    ScoringType,
)

ENUMS: list[type[Enum]] = [
    RatingFormat,
    MatchType,
    ScoringType,
    RejectionReason,
    ReliabilityBasis,
]
MEMBERS = [member for cls in ENUMS for member in cls]


@pytest.mark.parametrize("member", MEMBERS, ids=repr)
def test_str_and_format_give_the_value(member: Enum) -> None:
    assert str(member) == member.value
    assert f"{member}" == member.value
    assert format(member, "") == member.value


@pytest.mark.parametrize("member", MEMBERS, ids=repr)
def test_other_enum_behavior_is_unchanged(member: Enum) -> None:
    cls = type(member)
    assert repr(member) == f"<{cls.__name__}.{member.name}: {member.value!r}>"
    assert pickle.loads(pickle.dumps(member)) is member
    assert json.dumps(member) == json.dumps(member.value)
    assert member == member.value
    assert cls(member.value) is member
    assert isinstance(member, str)


def test_config_fingerprint_is_unchanged() -> None:
    assert Config().fingerprint() == "53951e80d62a242c"
