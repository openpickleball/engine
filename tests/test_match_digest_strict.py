"""The row digest agrees with the ledger's duplicate rule for lists and sets (issue #16).

Rows that repeat a match_id collapse only when every field matches in type
and value, recursively. A list or set field is compared member by member,
with each member's exact type, and the digest a checkpoint stores must
agree. All ids are synthetic.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from datetime import date

import pytest

import open_engine.engine as engine_module
from open_engine import LedgerConflictError, Match, RatingFormat, replay

S = RatingFormat.SINGLES
ON = date(2025, 2, 1)
BASE = Match("syn-d1", ("syn-a",), ("syn-b",), ((11, 1),), ON, S)


def _canonical_equal(a: Match, b: Match) -> bool:
    return engine_module._canonical(a) == engine_module._canonical(b)


class _Int(int):
    """An int subclass: a valid score, equal to its int, with another type."""


DIFFERENT = [
    pytest.param(
        dataclasses.replace(BASE, games=([11, 1],)),
        dataclasses.replace(BASE, games=([11, _Int(1)],)),
        id="list member int vs int subclass",
    ),
    pytest.param(BASE, dataclasses.replace(BASE, games=([11, 1],)), id="tuple vs list"),
    pytest.param(
        dataclasses.replace(BASE, confirmed_by=frozenset({"syn-a", "syn-b"})),
        dataclasses.replace(BASE, confirmed_by={"syn-a", "syn-b"}),
        id="frozenset vs set",
    ),
]


@pytest.mark.parametrize(("a", "b"), DIFFERENT)
def test_rows_that_differ_inside_a_list_or_set_are_separated(a: Match, b: Match) -> None:
    a.validate()
    b.validate()
    assert not _canonical_equal(a, b)
    assert engine_module._match_digest(a) != engine_module._match_digest(b)


@pytest.mark.parametrize(("a", "b"), DIFFERENT)
def test_rows_that_differ_inside_a_list_or_set_conflict(a: Match, b: Match) -> None:
    with pytest.raises(LedgerConflictError, match="syn-d1"):
        replay([a, b], as_of=ON)


SAME = [
    pytest.param(
        dataclasses.replace(BASE, games=([11, 1],)),
        dataclasses.replace(BASE, games=([11, 1],)),
        id="equal lists",
    ),
    pytest.param(
        dataclasses.replace(BASE, confirmed_by={"syn-a", "syn-b"}),
        dataclasses.replace(BASE, confirmed_by={"syn-b", "syn-a"}),
        id="equal sets",
    ),
]


@pytest.mark.parametrize(("a", "b"), SAME)
def test_equal_lists_and_sets_join(a: Match, b: Match) -> None:
    assert _canonical_equal(a, b)
    assert engine_module._match_digest(a) == engine_module._match_digest(b)
    assert replay([a, b], as_of=ON).replay_info is not None


def test_snapshot_text_does_not_depend_on_input_order_for_list_rows() -> None:
    a = dataclasses.replace(BASE, games=([11, 1],))
    b = dataclasses.replace(BASE, games=([11, 1],))
    first = json.dumps(replay([a, b], as_of=ON).to_snapshot(), sort_keys=True)
    second = json.dumps(replay([b, a], as_of=ON).to_snapshot(), sort_keys=True)
    assert first == second


def test_resume_accepts_the_same_list_row() -> None:
    row = dataclasses.replace(BASE, games=([11, 1],))
    snapshot = replay([row], as_of=ON).to_snapshot()
    again = dataclasses.replace(BASE, games=([11, 1],))
    resumed = replay([again], as_of=date(2025, 3, 1), from_snapshot=snapshot)
    assert resumed.processed_match_ids == {"syn-d1"}


DIGEST_SCRIPT = """
from datetime import date
from open_engine import Match, MatchType, RatingFormat
from open_engine.engine import _match_digest
ids = ("syn-a", "syn-b", "syn-c", "syn-d")
m = Match("syn-d1", ids[:2], ids[2:], ((11, 1),), date(2025, 2, 1),
          RatingFormat.DOUBLES, match_type=MatchType.SELF_REPORTED,
          confirmed_by=set(ids))
print(_match_digest(m))
"""


def _digest_in_process(hash_seed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": hash_seed}
    done = subprocess.run(
        [sys.executable, "-c", DIGEST_SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def test_digest_of_a_set_does_not_depend_on_the_hash_seed() -> None:
    digests = {_digest_in_process(seed) for seed in ("0", "1", "2", "3", "4", "5", "6", "7")}
    assert len(digests) == 1
