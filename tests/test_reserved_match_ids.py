"""A match_id rejected as not completed is reserved (issue #73).

Live, a later match with that id is a duplicate, as replay treats a
completed row that reuses the id of a non-completed row as a conflict.
All player and match ids are synthetic.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from open_engine import (
    InvalidMatchError,
    LedgerConflictError,
    Match,
    MatchStatus,
    RatingEngine,
    RatingFormat,
    RejectionReason,
    replay,
)

D = RatingFormat.DOUBLES
PLAYED = date(2026, 10, 29)
AS_OF = date(2026, 11, 1)


def _m(
    match_id: str,
    games: tuple[tuple[int, int], ...] = ((11, 5),),
    status: MatchStatus = MatchStatus.COMPLETED,
    *,
    team_a: tuple[str, ...] = ("syn-a", "syn-b"),
    team_b: tuple[str, ...] = ("syn-c", "syn-d"),
    on: date = PLAYED,
) -> Match:
    return Match(match_id, team_a, team_b, games, on, D, status=status)


FORFEIT = _m("syn-m1", (), MatchStatus.FORFEIT)

LATER = {
    "completed": _m("syn-m1"),
    "same forfeit": FORFEIT,
    "retired": Match(
        "syn-m1",
        ("syn-a", "syn-b"),
        ("syn-c", "syn-d"),
        ((11, 5),),
        PLAYED,
        D,
        best_of=3,
        status=MatchStatus.RETIRED,
    ),
    "other players": _m("syn-m1", team_a=("syn-e", "syn-f"), team_b=("syn-g", "syn-h")),
    "other date": _m("syn-m1", on=PLAYED + timedelta(days=1)),
}


def _ratings(eng: RatingEngine) -> object:
    snapshot = eng.to_snapshot()
    return snapshot["players"]


def _check_later_is_duplicate(eng: RatingEngine, later: Match) -> None:
    before_players = _ratings(eng)
    before_processed = set(eng.processed_match_ids)
    result = eng.record(later)
    assert result.counted is False
    assert result.reason is RejectionReason.DUPLICATE_MATCH_ID
    assert result.deltas == {}
    assert _ratings(eng) == before_players
    assert eng.processed_match_ids == before_processed


# -- criterion 3: live -----------------------------------------------------------


@pytest.mark.parametrize("later", list(LATER.values()), ids=list(LATER))
def test_live_reserves_not_completed_match_id(later: Match) -> None:
    eng = RatingEngine()
    first = eng.record(FORFEIT)
    assert first.reason is RejectionReason.MATCH_NOT_COMPLETED
    _check_later_is_duplicate(eng, later)


def test_live_and_replay_agree_on_reused_id() -> None:
    eng = RatingEngine()
    eng.record(FORFEIT)
    assert eng.record(_m("syn-m1")).counted is False
    with pytest.raises(LedgerConflictError):
        replay([FORFEIT, _m("syn-m1")], as_of=AS_OF)


def test_reservation_changes_no_rating_or_count() -> None:
    base = _m("syn-base", team_a=("syn-e", "syn-f"), team_b=("syn-g", "syn-h"))
    eng = replay([base], as_of=PLAYED)
    before = eng.to_snapshot()
    eng.record(FORFEIT)
    after = eng.to_snapshot()
    for key in ("players", "processed_match_ids", "content_keys", "possible_duplicates"):
        assert after[key] == before[key]
    assert after["self_reported_log"] == before["self_reported_log"]
    assert "syn-a" not in eng.players


def test_early_as_of_raises_for_a_reserved_id() -> None:
    eng = RatingEngine()
    eng.record(FORFEIT)
    before = eng.to_snapshot()
    with pytest.raises(InvalidMatchError, match="as_of cannot be earlier than the match date"):
        eng.record(_m("syn-m1"), as_of=PLAYED - timedelta(days=1))
    assert eng.to_snapshot() == before


# -- criterion 4: replayed and loaded engines -------------------------------------


@pytest.mark.parametrize("later", list(LATER.values()), ids=list(LATER))
def test_replayed_engine_keeps_the_reservation(later: Match) -> None:
    eng = replay([FORFEIT], as_of=AS_OF)
    _check_later_is_duplicate(eng, later)


@pytest.mark.parametrize("source", ["live", "replay"])
@pytest.mark.parametrize("later", list(LATER.values()), ids=list(LATER))
def test_loaded_engine_keeps_the_reservation(source: str, later: Match) -> None:
    if source == "live":
        eng = RatingEngine()
        eng.record(FORFEIT)
    else:
        eng = replay([FORFEIT], as_of=AS_OF)
    text = json.dumps(eng.to_snapshot(), allow_nan=False)
    loaded = RatingEngine.from_snapshot(json.loads(text))
    _check_later_is_duplicate(loaded, later)


# -- criterion 5: snapshot carriage ------------------------------------------------


def test_snapshot_carries_sorted_reserved_ids() -> None:
    eng = RatingEngine()
    eng.record(_m("syn-m2", (), MatchStatus.FORFEIT))
    eng.record(FORFEIT)
    snapshot = eng.to_snapshot()
    assert snapshot["reserved_match_ids"] == ["syn-m1", "syn-m2"]
    assert snapshot["snapshot_version"] == 6


def test_snapshot_of_a_fresh_engine_has_no_reserved_ids() -> None:
    assert RatingEngine().to_snapshot()["reserved_match_ids"] == []


# -- criterion 6: resumable mark and resume ----------------------------------------


def test_live_reservation_clears_resumable() -> None:
    eng = replay([_m("syn-base")], as_of=PLAYED)
    assert eng.to_snapshot()["resumable"] is True
    eng.record(FORFEIT)
    assert eng.to_snapshot()["resumable"] is False


def test_replay_reservation_keeps_resumable() -> None:
    snapshot = replay([FORFEIT, _m("syn-base")], as_of=AS_OF).to_snapshot()
    assert snapshot["resumable"] is True
    assert snapshot["reserved_match_ids"] == ["syn-m1"]


@pytest.mark.parametrize("whole", [True, False])
def test_resume_with_not_completed_rows_equals_full_replay(whole: bool) -> None:
    checkpoint_date = PLAYED + timedelta(days=3)
    rows = [
        FORFEIT,
        _m("syn-m2"),
        _m("syn-m3", (), MatchStatus.FORFEIT, on=checkpoint_date),
        _m("syn-m4", on=checkpoint_date + timedelta(days=2)),
        _m("syn-m5", (), MatchStatus.INCOMPLETE, on=checkpoint_date + timedelta(days=4)),
    ]
    end = checkpoint_date + timedelta(days=10)
    before = [row for row in rows if row.played_on <= checkpoint_date]
    snapshot = replay(before, as_of=checkpoint_date).to_snapshot()
    given = rows if whole else [row for row in rows if row.played_on > checkpoint_date]
    resumed = replay(given, as_of=end, from_snapshot=snapshot, whole_ledger=whole)
    full = replay(rows, as_of=end)
    assert resumed.to_snapshot() == full.to_snapshot()
    assert full.to_snapshot()["reserved_match_ids"] == ["syn-m1", "syn-m3", "syn-m5"]
