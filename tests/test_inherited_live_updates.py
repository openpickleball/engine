"""A derived category state carries its parent's live count (issue #23 addendum, part B).

A category state derived from overall doubles copies a rating that can
hold live (provisional) movement since the last replay. Its
inherited_live_updates records how many, and player_summary's
live_updates_since_recalculation adds it to the state's own count. A
replay gives 0 everywhere. All ids are synthetic.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from open_engine import (
    Config,
    InvalidSeedError,
    Match,
    Player,
    RatingEngine,
    RatingFormat,
    RatingState,
    SnapshotError,
    replay,
)

D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
S = RatingFormat.SINGLES
CUT = date(2025, 3, 1)
LATER = date(2025, 6, 1)
TEAMS = (("syn-a", "syn-b"), ("syn-c", "syn-d"))


def _m(match_id: str, fmt: RatingFormat, on: date) -> Match:
    if fmt is S:
        return Match(match_id, ("syn-a",), ("syn-b",), ((11, 4),), on, fmt)
    return Match(match_id, TEAMS[0], TEAMS[1], ((11, 4),), on, fmt)


HISTORY = [_m("syn-h1", D, date(2025, 2, 1))]


def _live(fmt: RatingFormat, count: int, start: int = 0) -> list[Match]:
    return [
        _m(f"syn-{fmt.value}-{start + i}", fmt, date(2025, 3, 2 + start + i)) for i in range(count)
    ]


def _engine(k: int, start: str = "replayed", cfg: Config | None = None) -> RatingEngine:
    """syn-a with k live doubles matches since the last replay, or on a never-replayed engine."""
    engine = replay(HISTORY, cfg=cfg, as_of=CUT) if start == "replayed" else RatingEngine(cfg)
    for match in _live(D, k):
        assert engine.record(match).counted
    return engine


def _count(engine: RatingEngine, fmt: RatingFormat) -> int:
    return engine.player_summary("syn-a", fmt, as_of=LATER).live_updates_since_recalculation


# B1


@pytest.mark.parametrize("start", ["replayed", "never replayed"])
@pytest.mark.parametrize("k", [1, 3])
def test_a_derived_category_state_reports_the_parent_count(start: str, k: int) -> None:
    engine = _engine(k, start)
    assert _count(engine, D) == k
    # Before the first category match: unstored, then stored by a seed.
    assert engine.state("syn-a", MX).inherited_live_updates == k
    assert _count(engine, MX) == k
    engine.seed_provisional_rating("syn-a", MX)
    assert engine.players["syn-a"].ratings[MX].inherited_live_updates == k
    assert _count(engine, MX) == k
    # Each live category match adds one.
    for count, match in enumerate(_live(MX, 2, start=10), start=1):
        assert engine.record(match).counted
        assert _count(engine, MX) == k + count
        assert engine.players["syn-a"].ratings[MX].inherited_live_updates == k


def test_the_first_category_match_without_a_seed_reports_k_plus_one() -> None:
    engine = _engine(2)
    assert engine.record(_live(MX, 1)[0]).counted
    assert _count(engine, MX) == 3
    assert engine.players["syn-a"].ratings[MX].live_updates_since_replay == 1
    assert engine.players["syn-a"].ratings[MX].inherited_live_updates == 2


# B2


def test_overall_doubles_and_singles_report_their_own_count() -> None:
    engine = _engine(2)
    for match in _live(S, 3):
        assert engine.record(match).counted
    assert _count(engine, D) == 2
    assert _count(engine, S) == 3
    assert engine.players["syn-a"].ratings[D].inherited_live_updates == 0
    assert engine.players["syn-a"].ratings[S].inherited_live_updates == 0


def test_a_category_does_not_inherit_when_it_does_not_feed_its_parent() -> None:
    cfg = Config(category_feeds_parent=False)
    engine = _engine(2, cfg=cfg)
    assert engine.state("syn-a", MX).inherited_live_updates == 0
    assert _count(engine, MX) == 0
    assert engine.record(_live(MX, 1)[0]).counted
    assert _count(engine, MX) == 1


def test_a_category_derived_from_an_unplayed_unseeded_parent_inherits_nothing() -> None:
    engine = RatingEngine()
    engine.seed_provisional_rating("syn-a", D)
    engine.seed_provisional_rating("syn-a", MX)
    for match in _live(D, 2):
        assert engine.record(match).counted
    assert engine.players["syn-a"].ratings[MX].inherited_live_updates == 0
    assert _count(engine, MX) == 0


def test_parent_matches_after_derivation_do_not_change_the_inherited_count() -> None:
    engine = _engine(1)
    engine.seed_provisional_rating("syn-a", MX)
    for match in _live(D, 2, start=5):
        assert engine.record(match).counted
    assert _count(engine, D) == 3
    assert _count(engine, MX) == 1


# B3


def test_replay_gives_zero_everywhere() -> None:
    ledger = HISTORY + _live(D, 2) + _live(MX, 2, start=10)
    live = replay(HISTORY, as_of=CUT)
    for match in ledger[1:]:
        assert live.record(match).counted
    assert live.players["syn-a"].ratings[MX].inherited_live_updates == 2
    rebuilt = replay(ledger, as_of=LATER)
    for player in rebuilt.players.values():
        for state in player.ratings.values():
            assert state.inherited_live_updates == 0
            assert state.live_updates_since_replay == 0
    assert _count(rebuilt, MX) == 0
    # Live and replayed ratings agree; only the labels differ.
    for pid, player in live.players.items():
        for fmt, state in player.ratings.items():
            assert rebuilt.players[pid].ratings[fmt].rating == state.rating
            assert rebuilt.players[pid].ratings[fmt].rd == state.rd


# B4


def _json(engine: RatingEngine) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(json.dumps(engine.to_snapshot()))
    return loaded


def _live_snapshot() -> dict[str, Any]:
    engine = _engine(2)
    engine.seed_provisional_rating("syn-a", MX)
    return _json(engine)


def _state(snap: dict[str, Any], fmt: RatingFormat, pid: str = "syn-a") -> dict[str, Any]:
    player = next(p for p in snap["players"] if p["id"] == pid)
    state: dict[str, Any] = player["ratings"][fmt.value]
    return state


def test_the_snapshot_carries_the_count_and_round_trips() -> None:
    snap = _live_snapshot()
    assert snap["snapshot_version"] == 7
    assert _state(snap, MX)["inherited_live_updates"] == 2
    assert _state(snap, D)["inherited_live_updates"] == 0
    restored = RatingEngine.from_snapshot(snap)
    assert restored.players["syn-a"].ratings[MX].inherited_live_updates == 2
    assert restored.to_snapshot() == snap


def test_a_version_3_snapshot_does_not_load() -> None:
    snap = _live_snapshot()
    snap["snapshot_version"] = 3
    with pytest.raises(SnapshotError, match="snapshot_version must be 7"):
        RatingEngine.from_snapshot(snap)


def test_a_missing_key_does_not_load() -> None:
    snap = _live_snapshot()
    del _state(snap, MX)["inherited_live_updates"]
    with pytest.raises(SnapshotError, match="inherited_live_updates"):
        RatingEngine.from_snapshot(snap)


@pytest.mark.parametrize("value", [True, 1.0, -1, "1", None], ids=repr)
def test_a_wrong_type_does_not_load(value: object) -> None:
    snap = _live_snapshot()
    _state(snap, MX)["inherited_live_updates"] = value
    with pytest.raises(SnapshotError, match=r"inherited_live_updates must be a nonnegative int"):
        RatingEngine.from_snapshot(snap)


def test_a_nonzero_count_on_overall_doubles_does_not_load() -> None:
    snap = _live_snapshot()
    _state(snap, D)["inherited_live_updates"] = 1
    with pytest.raises(
        SnapshotError,
        match=r"ratings\.doubles\.inherited_live_updates must be 0 unless the state is derived "
        "from its parent",
    ):
        RatingEngine.from_snapshot(snap)


def test_a_nonzero_count_on_a_category_not_derived_does_not_load() -> None:
    snap = _live_snapshot()
    _state(snap, MX)["initial_source"] = "open_provisional"
    with pytest.raises(
        SnapshotError,
        match=r"ratings\.mixed_doubles\.inherited_live_updates must be 0 unless the state is "
        "derived from its parent",
    ):
        RatingEngine.from_snapshot(snap)


def test_a_nonzero_count_when_categories_do_not_feed_parents_does_not_load() -> None:
    cfg = Config(category_feeds_parent=False)
    engine = _engine(2, cfg=cfg)
    engine.seed_provisional_rating("syn-a", MX)
    snap = _json(engine)
    _state(snap, MX)["initial_source"] = "open_parent:doubles"
    _state(snap, MX)["inherited_live_updates"] = 1
    with pytest.raises(SnapshotError, match="must be 0 unless the state is derived"):
        RatingEngine.from_snapshot(snap, cfg)


def test_a_count_above_the_parent_count_does_not_load() -> None:
    snap = _live_snapshot()
    _state(snap, MX)["inherited_live_updates"] = 3
    with pytest.raises(
        SnapshotError,
        match=r"inherited_live_updates must not exceed the parent's live_updates_since_replay",
    ):
        RatingEngine.from_snapshot(snap)


def test_a_nonzero_count_in_a_resumable_snapshot_does_not_load() -> None:
    engine = replay(HISTORY + [_m("syn-x1", MX, date(2025, 2, 15))], as_of=CUT)
    snap = _json(engine)
    assert snap["resumable"] is True
    assert _state(snap, MX)["initial_source"] == "open_parent:doubles"
    _state(snap, D)["live_updates_since_replay"] = 1
    _state(snap, MX)["inherited_live_updates"] = 1
    with pytest.raises(
        SnapshotError, match=r"inherited_live_updates must be 0 in a resumable snapshot"
    ):
        RatingEngine.from_snapshot(snap)


def test_resume_refuses_a_snapshot_with_an_inherited_count() -> None:
    snap = _live_snapshot()
    with pytest.raises(SnapshotError, match="Cannot resume"):
        replay(HISTORY, as_of=LATER, from_snapshot=snap)


# B5


def _seed(value: object) -> Player:
    state = RatingState(rating=3.5, rd=Config().new_player_rd, inherited_live_updates=1)
    state.inherited_live_updates = value  # type: ignore[assignment]
    return Player("syn-z", {MX: state})


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (1, "inherited_live_updates must be 0"),
        (True, "inherited_live_updates must be an int"),
        (1.0, "inherited_live_updates must be an int"),
    ],
    ids=["nonzero", "bool", "float"],
)
def test_a_seed_with_an_inherited_count_is_rejected(value: object, message: str) -> None:
    with pytest.raises(InvalidSeedError, match=message):
        RatingEngine().add_player(_seed(value))
    with pytest.raises(InvalidSeedError, match=message):
        replay([], [_seed(value)], as_of=CUT)
