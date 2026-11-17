"""A resumable snapshot's possible_duplicates name only rows a replay considered (issue #21).

A replay records a copy only for a row it considered, so in a resumable
snapshot every possible_duplicates key is in ledger_digests. A live engine
can hold a rejected id no replay saw, so a live snapshot is not held to
this. All ids are synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date

import pytest

from open_engine import Config, Match, RatingEngine, RatingFormat, SnapshotError, replay

D = RatingFormat.DOUBLES
ON = date(2026, 3, 1)
AS_OF = date(2026, 3, 31)
END = date(2026, 5, 1)

M1 = Match("m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 7),), ON, D)
M2 = Match("m2", ("syn-a", "syn-c"), ("syn-b", "syn-d"), ((11, 5),), ON, D)
COPY = Match("m3", ("syn-d", "syn-c"), ("syn-b", "syn-a"), ((7, 11),), ON, D)
LATE = Match("m4", ("syn-a", "syn-d"), ("syn-b", "syn-c"), ((11, 4),), date(2026, 4, 10), D)
MESSAGE = r"snapshot\.possible_duplicates has key 'ghost', which is not in ledger_digests"


def _reject() -> Config:
    return Config(reject_content_duplicates=True)


CONFIGS = pytest.mark.parametrize("cfg_factory", [Config, _reject], ids=["default", "reject"])
# Under the default an unprocessed key is reported as not processed first
# (tests/test_snapshot_possible_duplicates.py pins that message for the
# same input), so the default case accepts either reason.
UNCONSIDERED = pytest.mark.parametrize(
    ("cfg_factory", "message"),
    [(Config, r"snapshot\.possible_duplicates has key 'ghost'"), (_reject, MESSAGE)],
    ids=["default", "reject"],
)


def _snap(engine: RatingEngine) -> dict[str, object]:
    snap: dict[str, object] = json.loads(json.dumps(engine.to_snapshot()))
    return snap


@UNCONSIDERED
def test_resumable_snapshot_refuses_an_unconsidered_key(
    cfg_factory: Callable[[], Config], message: str
) -> None:
    cfg = cfg_factory()
    snap = _snap(replay([M1, M2], cfg=cfg, as_of=AS_OF))
    assert snap["resumable"] is True
    snap["possible_duplicates"] = {"ghost": "m1"}
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(snap, cfg)
    for whole in (False, True):
        with pytest.raises(SnapshotError, match=message):
            replay([M1, M2, LATE], cfg=cfg, as_of=END, from_snapshot=snap, whole_ledger=whole)


def test_default_resumable_snapshot_refuses_a_processed_unconsidered_key() -> None:
    snap = _snap(replay([M1, M2], as_of=AS_OF))
    ids = snap["processed_match_ids"]
    info = snap["replay_info"]
    assert isinstance(ids, list) and isinstance(info, dict)
    ids.append("ghost")
    info["matches_considered"] += 1
    info["matches_counted"] += 1
    snap["possible_duplicates"] = {"ghost": "m1"}
    with pytest.raises(SnapshotError, match=MESSAGE):
        RatingEngine.from_snapshot(snap)


@CONFIGS
def test_engine_resumable_snapshot_loads_and_resumes(cfg_factory: Callable[[], Config]) -> None:
    cfg = cfg_factory()
    ledger = [M1, M2, COPY, LATE]
    snap = _snap(replay(ledger, cfg=cfg, as_of=AS_OF))
    assert snap["resumable"] is True
    assert snap["possible_duplicates"] == {"m3": "m1"}
    assert RatingEngine.from_snapshot(snap, cfg).to_snapshot() == snap
    full = replay(ledger, cfg=cfg, as_of=END).to_snapshot()
    resumed = replay(ledger, cfg=cfg, as_of=END, from_snapshot=snap, whole_ledger=True)
    assert resumed.to_snapshot() == full


@CONFIGS
def test_live_snapshot_with_an_unreplayed_copy_loads(cfg_factory: Callable[[], Config]) -> None:
    cfg = cfg_factory()
    engine = replay([M1], cfg=cfg, as_of=AS_OF)
    engine.record(COPY)
    snap = _snap(engine)
    assert snap["resumable"] is False
    assert snap["possible_duplicates"] == {"m3": "m1"}
    assert RatingEngine.from_snapshot(snap, cfg).possible_duplicates == {"m3": "m1"}
