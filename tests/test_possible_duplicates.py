"""RatingEngine.possible_duplicates maps each flagged or rejected copy to its twin.

All player and match ids are synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date

import pytest

from open_engine import Config, Match, RatingEngine, RatingFormat, replay

D = RatingFormat.DOUBLES
ON = date(2026, 3, 1)
LATER = date(2026, 4, 1)
END = date(2026, 5, 1)

DUP = Match("dup", ("a", "b"), ("c", "d"), ((11, 0),), ON, D)
DUP2 = Match("dup2", ("c", "d"), ("b", "a"), ((0, 11),), ON, D)


def _reject() -> Config:
    return Config(reject_content_duplicates=True)


CONFIGS = pytest.mark.parametrize("cfg_factory", [Config, _reject], ids=["default", "reject"])


def test_new_engine_has_no_possible_duplicates() -> None:
    assert RatingEngine().possible_duplicates == {}


def test_default_replay_exposes_the_issue_repro() -> None:
    eng = replay([DUP2, DUP], as_of=LATER)
    assert eng.possible_duplicates == {"dup2": "dup"}
    assert "dup2" in eng.processed_match_ids


def test_reject_replay_exposes_the_rejected_copy() -> None:
    eng = replay([DUP2, DUP], cfg=_reject(), as_of=LATER)
    assert eng.possible_duplicates == {"dup2": "dup"}
    assert "dup2" not in eng.processed_match_ids


@CONFIGS
def test_live_record_fills_the_map(cfg_factory: Callable[[], Config]) -> None:
    eng = RatingEngine(cfg_factory())
    eng.record(DUP)
    assert eng.possible_duplicates == {}
    eng.record(DUP2)
    eng.record(Match("dup3", ("b", "a"), ("c", "d"), ((11, 0),), ON, D))
    assert eng.possible_duplicates == {"dup2": "dup", "dup3": "dup"}


@CONFIGS
def test_a_rejected_copy_clears_the_resumable_mark(cfg_factory: Callable[[], Config]) -> None:
    cfg = cfg_factory()
    checkpoint = replay([DUP], cfg=cfg, as_of=ON)
    assert checkpoint.to_snapshot()["resumable"] is True
    checkpoint.record(DUP2)
    assert checkpoint.to_snapshot()["resumable"] is False


@CONFIGS
def test_snapshot_round_trip_keeps_the_map(cfg_factory: Callable[[], Config]) -> None:
    cfg = cfg_factory()
    snapshot = json.loads(json.dumps(replay([DUP, DUP2], cfg=cfg, as_of=LATER).to_snapshot()))
    assert snapshot["possible_duplicates"] == {"dup2": "dup"}
    loaded = RatingEngine.from_snapshot(snapshot, cfg)
    assert loaded.possible_duplicates == {"dup2": "dup"}
    assert loaded.to_snapshot() == snapshot


@CONFIGS
@pytest.mark.parametrize("newer_only", [False, True], ids=["whole", "newer only"])
def test_resume_equals_a_full_replay_with_the_map(
    cfg_factory: Callable[[], Config], newer_only: bool
) -> None:
    cfg = cfg_factory()
    ledger = [
        DUP,
        DUP2,
        Match("late1", ("a", "c"), ("b", "d"), ((11, 4),), LATER, D),
        Match("late2", ("d", "b"), ("c", "a"), ((4, 11),), LATER, D),
    ]
    full = replay(ledger, cfg=cfg, as_of=END)
    checkpoint = replay(ledger, cfg=cfg, as_of=ON).to_snapshot()
    rows = ledger[2:] if newer_only else ledger
    resumed = replay(rows, cfg=cfg, as_of=END, from_snapshot=checkpoint)
    assert full.possible_duplicates == {"dup2": "dup", "late2": "late1"}
    assert resumed.possible_duplicates == full.possible_duplicates
    assert resumed.to_snapshot() == full.to_snapshot()


def test_a_rejected_id_that_later_counts_on_its_own_leaves_the_map() -> None:
    eng = RatingEngine(_reject())
    eng.record(DUP)
    eng.record(DUP2)
    assert eng.possible_duplicates == {"dup2": "dup"}
    result = eng.record(Match("dup2", ("a", "b"), ("c", "d"), ((11, 5),), LATER, D))
    assert result.counted is True
    assert eng.possible_duplicates == {}
