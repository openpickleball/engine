"""from_snapshot checks reserved_match_ids (issue #73).

All player and match ids are synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any

import pytest

from open_engine import (
    Config,
    Match,
    MatchStatus,
    RatingEngine,
    RatingFormat,
    RejectionReason,
    SnapshotError,
    replay,
)

D = RatingFormat.DOUBLES
PLAYED = date(2026, 10, 29)
AS_OF = date(2026, 11, 1)
REJECT = Config(reject_content_duplicates=True)

Snap = dict[str, Any]


def _m(
    match_id: str,
    games: tuple[tuple[int, int], ...] = ((11, 5),),
    status: MatchStatus = MatchStatus.COMPLETED,
) -> Match:
    return Match(match_id, ("syn-a", "syn-b"), ("syn-c", "syn-d"), games, PLAYED, D, status=status)


def _json(snapshot: dict[str, object]) -> Snap:
    loaded: Snap = json.loads(json.dumps(snapshot, allow_nan=False))
    return loaded


def _live() -> Snap:
    eng = RatingEngine()
    eng.record(_m("syn-m1"))
    eng.record(_m("syn-x1", (), MatchStatus.FORFEIT))
    return _json(eng.to_snapshot())


def _resumable() -> Snap:
    ledger = [_m("syn-m1"), _m("syn-x1", (), MatchStatus.FORFEIT)]
    return _json(replay(ledger, as_of=AS_OF).to_snapshot())


def _set(value: object) -> Callable[[Snap], None]:
    def change(snap: Snap) -> None:
        snap["reserved_match_ids"] = value

    return change


MALFORMED = {
    "not a list": (_set("syn-x1"), r"snapshot.reserved_match_ids must be a list"),
    "object": (_set({"syn-x1": 1}), r"snapshot.reserved_match_ids must be a list"),
    "blank": (_set(["syn-x1", " "]), r"reserved_match_ids\[1\] must be a non-blank str"),
    "not a str": (_set([3]), r"reserved_match_ids\[0\] must be a non-blank str"),
    "repeat": (
        _set(["syn-x1", "syn-x1"]),
        r"reserved_match_ids\[1\]: duplicate match id 'syn-x1'",
    ),
    "processed": (
        _set(["syn-m1", "syn-x1"]),
        r"reserved_match_ids names 'syn-m1', which is in processed_match_ids",
    ),
}


@pytest.mark.parametrize("source", [_live, _resumable], ids=["live", "resumable"])
@pytest.mark.parametrize(("change", "message"), list(MALFORMED.values()), ids=list(MALFORMED))
def test_from_snapshot_rejects_bad_reserved_ids(
    source: Callable[[], Snap], change: Callable[[Snap], None], message: str
) -> None:
    snap = source()
    change(snap)
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(snap)


def test_missing_reserved_ids_key_does_not_load() -> None:
    snap = _live()
    del snap["reserved_match_ids"]
    with pytest.raises(SnapshotError, match=r"missing key 'reserved_match_ids'"):
        RatingEngine.from_snapshot(snap)


def test_resumable_reserved_id_must_be_a_considered_row() -> None:
    snap = _resumable()
    snap["reserved_match_ids"] = ["syn-x1", "syn-zz"]
    with pytest.raises(
        SnapshotError, match=r"reserved_match_ids names 'syn-zz', which is not in ledger_digests"
    ):
        RatingEngine.from_snapshot(snap)


def test_live_reserved_id_need_not_be_a_considered_row() -> None:
    snap = _live()
    assert snap["ledger_digests"] == {}
    assert RatingEngine.from_snapshot(snap).to_snapshot() == snap


def _copy(match_id: str = "syn-m2") -> Match:
    return Match(match_id, ("syn-b", "syn-a"), ("syn-c", "syn-d"), ((11, 5),), PLAYED, D)


def _reject_live() -> Snap:
    eng = RatingEngine(REJECT)
    eng.record(_m("syn-m1"))
    assert eng.record(_copy()).reason is RejectionReason.POSSIBLE_DUPLICATE
    return _json(eng.to_snapshot())


def _reject_resumable() -> Snap:
    return _json(replay([_m("syn-m1"), _copy()], cfg=REJECT, as_of=AS_OF).to_snapshot())


@pytest.mark.parametrize("source", [_reject_live, _reject_resumable], ids=["live", "resumable"])
def test_a_rejected_copy_is_reserved_and_loads(source: Callable[[], Snap]) -> None:
    # Issue #76: under reject_content_duplicates a rejected copy reserves its id.
    snap = source()
    assert snap["possible_duplicates"] == {"syn-m2": "syn-m1"}
    assert snap["reserved_match_ids"] == ["syn-m2"]
    loaded = RatingEngine.from_snapshot(snap, REJECT)
    assert loaded.to_snapshot() == snap
    assert loaded.record(_m("syn-m2", ((11, 3),))).reason is RejectionReason.DUPLICATE_MATCH_ID


@pytest.mark.parametrize("source", [_reject_live, _reject_resumable], ids=["live", "resumable"])
def test_a_rejected_copy_must_be_reserved(source: Callable[[], Snap]) -> None:
    snap = source()
    snap["reserved_match_ids"] = []
    with pytest.raises(
        SnapshotError,
        match=r"possible_duplicates has key 'syn-m2', which is not in reserved_match_ids",
    ):
        RatingEngine.from_snapshot(snap, REJECT)


def test_a_forfeit_reusing_a_rejected_copy_id_is_a_duplicate() -> None:
    eng = RatingEngine(REJECT)
    eng.record(_m("syn-m1"))
    assert eng.record(_copy()).reason is RejectionReason.POSSIBLE_DUPLICATE
    forfeit = _m("syn-m2", (), MatchStatus.FORFEIT)
    assert eng.record(forfeit).reason is RejectionReason.DUPLICATE_MATCH_ID


def _live_after_replay() -> Snap:
    eng = replay([_m("syn-m1"), _m("syn-x1", (), MatchStatus.FORFEIT)], as_of=AS_OF)
    eng.record(_m("syn-m3"))
    snap = _json(eng.to_snapshot())
    assert snap["resumable"] is False
    return snap


@pytest.mark.parametrize("source", [_live_after_replay, _resumable], ids=["live", "resumable"])
def test_considered_row_must_be_processed_or_reserved(source: Callable[[], Snap]) -> None:
    # A replay counts or reserves every row it digests, and live recording
    # never frees an id, so a live snapshot is held to this too.
    snap = source()
    assert snap["reserved_match_ids"] == ["syn-x1"]
    snap["reserved_match_ids"] = []
    with pytest.raises(
        SnapshotError,
        match=r"ledger_digests names 'syn-x1', which is in neither processed_match_ids "
        r"nor reserved_match_ids",
    ):
        RatingEngine.from_snapshot(snap)
    loaded = RatingEngine.from_snapshot(source())
    assert loaded.record(_m("syn-x1")).reason is RejectionReason.DUPLICATE_MATCH_ID


def test_version_6_snapshot_is_refused() -> None:
    snap = _live()
    snap["snapshot_version"] = 6
    with pytest.raises(SnapshotError, match=r"snapshot_version must be 7, not 6"):
        RatingEngine.from_snapshot(snap)
