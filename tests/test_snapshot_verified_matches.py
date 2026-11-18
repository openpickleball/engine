"""Snapshot schema 6: RatingState.verified_matches_played (issue #59).

All player and match ids are synthetic.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from open_engine import (
    Match,
    MatchType,
    RatingEngine,
    RatingFormat,
    SnapshotError,
    replay,
)

D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
START = date(2025, 1, 6)
CHECKPOINT = date(2025, 2, 16)
AS_OF = date(2025, 4, 30)
TYPES = (
    MatchType.TOURNAMENT,
    MatchType.SELF_REPORTED,
    MatchType.LEAGUE,
    MatchType.SELF_REPORTED,
    MatchType.REC_VERIFIED,
)


def _ledger() -> list[Match]:
    rows = []
    for i in range(15):
        rows.append(
            Match(
                f"syn-v{i:02d}",
                ("syn-a", f"syn-p{i % 4}"),
                (f"syn-o{i % 5}", f"syn-o{(i + 2) % 5}"),
                ((11, 5 + i % 5),),
                START + timedelta(weeks=i),
                MX if i % 3 == 0 else D,
                match_type=TYPES[i % len(TYPES)],
                confirmed_by=frozenset({"syn-a", f"syn-o{i % 5}"}),
            )
        )
    return rows


def _snapshot() -> dict[str, object]:
    engine = replay(_ledger(), as_of=AS_OF)
    loaded: dict[str, object] = json.loads(json.dumps(engine.to_snapshot(), allow_nan=False))
    return loaded


def _state(snap: dict[str, object], pid: str = "syn-a", fmt: str = "doubles") -> dict[str, object]:
    players = snap["players"]
    assert isinstance(players, list)
    for player in players:
        if player["id"] == pid:
            state: dict[str, object] = player["ratings"][fmt]
            return state
    raise AssertionError(pid)


def test_snapshot_version_is_8() -> None:
    assert _snapshot()["snapshot_version"] == 8


def test_version_5_snapshot_is_rejected() -> None:
    snap = _snapshot()
    snap["snapshot_version"] = 5
    with pytest.raises(SnapshotError, match="snapshot_version must be 8, not 5"):
        RatingEngine.from_snapshot(snap)


def test_every_state_has_the_count() -> None:
    snap = _snapshot()
    players = snap["players"]
    assert isinstance(players, list)
    for player in players:
        for state in player["ratings"].values():
            assert type(state["verified_matches_played"]) is int


def test_count_round_trips() -> None:
    engine = replay(_ledger(), as_of=AS_OF)
    a = engine.players["syn-a"].ratings
    assert 0 < a[D].verified_matches_played < a[D].matches_played
    assert 0 < a[MX].verified_matches_played <= a[MX].matches_played
    text = json.dumps(engine.to_snapshot(), allow_nan=False)
    restored = RatingEngine.from_snapshot(json.loads(text))
    assert restored.players == engine.players


def test_resume_carries_the_count() -> None:
    full = replay(_ledger(), as_of=AS_OF)
    checkpoint = replay(_ledger(), as_of=CHECKPOINT)
    assert checkpoint.players["syn-a"].ratings[D].verified_matches_played > 0
    snap = json.loads(json.dumps(checkpoint.to_snapshot()))
    resumed = replay(_ledger(), as_of=AS_OF, from_snapshot=snap)
    assert resumed.players == full.players


@pytest.mark.parametrize("value", [-1, 1.0, True, "1", None, 2**63])
def test_count_must_be_a_nonnegative_int(value: object) -> None:
    snap = _snapshot()
    _state(snap)["verified_matches_played"] = value
    with pytest.raises(
        SnapshotError,
        match=r"ratings\.doubles\.verified_matches_played must be a nonnegative int",
    ):
        RatingEngine.from_snapshot(snap)


def test_count_must_not_exceed_matches_played() -> None:
    snap = _snapshot()
    state = _state(snap)
    played = state["matches_played"]
    assert isinstance(played, int)
    state["verified_matches_played"] = played + 1
    with pytest.raises(
        SnapshotError,
        match=r"ratings\.doubles\.verified_matches_played must not exceed matches_played",
    ):
        RatingEngine.from_snapshot(snap)


def test_count_equal_to_matches_played_loads_with_no_self_reports() -> None:
    # Relabeling a self-reported match as verified does not load (see
    # tests/test_snapshot_verified_log.py), so build a state with none.
    rows = [m for m in _ledger() if m.match_type is not MatchType.SELF_REPORTED]
    engine = replay(rows, as_of=AS_OF)
    state = engine.players["syn-a"].ratings[D]
    assert state.verified_matches_played == state.matches_played > 0
    restored = RatingEngine.from_snapshot(json.loads(json.dumps(engine.to_snapshot())))
    assert restored.players == engine.players


def test_missing_count_is_rejected() -> None:
    snap = _snapshot()
    del _state(snap)["verified_matches_played"]
    with pytest.raises(SnapshotError, match=r"missing key 'verified_matches_played'"):
        RatingEngine.from_snapshot(snap)
