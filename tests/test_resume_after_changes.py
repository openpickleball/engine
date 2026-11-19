"""Resume refuses a snapshot of an engine changed after its replay (issue #16).

Any change outside replay (a new player or rating state, a seed, an
identity link, a live match) can make a resumed replay differ from a full
one, or accept what a full replay rejects. A read that creates nothing
keeps the engine resumable, and so does a read of an unknown player,
which raises. All ids are synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, timedelta

import pytest

from open_engine import (
    ExternalRatingSnapshot,
    Match,
    Player,
    RatingEngine,
    RatingFormat,
    SnapshotError,
    UnknownPlayerError,
    replay,
)

D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
CUT = date(2025, 3, 1)
END = date(2025, 6, 1)
LEDGER = [
    Match("syn-1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 4),), date(2025, 2, 1), D),
    Match("syn-2", ("syn-a", "syn-c"), ("syn-b", "syn-d"), ((11, 6),), date(2025, 4, 1), MX),
]


def _engine() -> RatingEngine:
    eng = RatingEngine()
    eng.seed_external_rating(
        "syn-s", D, ExternalRatingSnapshot("external_standard", 4.0, date(2025, 1, 1))
    )
    return replay(LEDGER, [eng.players["syn-s"]], as_of=CUT)


def _snapshot(engine: RatingEngine) -> dict[str, object]:
    loaded: dict[str, object] = json.loads(json.dumps(engine.to_snapshot()))
    return loaded


def _seed_new(engine: RatingEngine) -> None:
    engine.seed_external_rating(
        "syn-n", D, ExternalRatingSnapshot("external_standard", 4.4, date(2025, 2, 1))
    )


def _seed_future(engine: RatingEngine) -> None:
    engine.seed_external_rating(
        "syn-n", D, ExternalRatingSnapshot("external_standard", 4.4, END + timedelta(30))
    )


def _replace_seed(engine: RatingEngine) -> None:
    engine.seed_external_rating(
        "syn-s",
        D,
        ExternalRatingSnapshot("external_standard", 5.0, date(2025, 2, 1)),
        replace_unplayed_seed=True,
    )


CHANGES: dict[str, Callable[[RatingEngine], object]] = {
    "seed_provisional_rating creates a state": lambda e: e.seed_provisional_rating(
        "syn-b", RatingFormat.SINGLES
    ),
    "seed_provisional_rating creates a player": lambda e: e.seed_provisional_rating("syn-n", MX),
    "add_player": lambda e: e.add_player(Player("syn-n")),
    "seed_external_rating": _seed_new,
    "future seed_external_rating": _seed_future,
    "replace_unplayed_seed": _replace_seed,
    "link_account": lambda e: e.link_account("syn-a", "acct-1"),
    "attach_external_id": lambda e: e.attach_external_id("syn-a", "external_standard", "ext-1"),
}


@pytest.mark.parametrize("change", list(CHANGES.values()), ids=list(CHANGES))
def test_resume_rejects_a_snapshot_of_a_changed_engine(
    change: Callable[[RatingEngine], object],
) -> None:
    engine = _engine()
    change(engine)
    snapshot = _snapshot(engine)
    assert snapshot["resumable"] is False
    with pytest.raises(SnapshotError, match="snapshot right after"):
        replay(LEDGER, as_of=END, from_snapshot=snapshot)


def test_a_counted_live_match_clears_the_mark() -> None:
    engine = _engine()
    engine.record(Match("syn-9", ("syn-a",), ("syn-b",), ((11, 2),), CUT, RatingFormat.SINGLES))
    assert _snapshot(engine)["resumable"] is False


READS: dict[str, Callable[[RatingEngine], object]] = {
    "player_summary of an existing state": lambda e: e.player_summary("syn-a", D, as_of=CUT),
    "state of an existing state": lambda e: e.state("syn-a", D),
    "player of an existing player": lambda e: e.player("syn-a"),
    "player_summary of a missing category state": lambda e: e.player_summary("syn-a", MX),
    "state of a missing category state": lambda e: e.state("syn-a", MX),
    # It stores no category starting state (issue #112).
    "seed_provisional_rating of a missing category state": lambda e: e.seed_provisional_rating(
        "syn-b", MX
    ),
    "predict_point_share": lambda e: e.predict_point_share(
        ("syn-a", "syn-x"), ("syn-b", "syn-c"), MX
    ),
    "to_snapshot": lambda e: e.to_snapshot(),
}


@pytest.mark.parametrize("read", list(READS.values()), ids=list(READS))
def test_a_read_that_creates_nothing_keeps_the_engine_resumable(
    read: Callable[[RatingEngine], object],
) -> None:
    engine = _engine()
    read(engine)
    snapshot = _snapshot(engine)
    assert snapshot["resumable"] is True
    resumed = replay(LEDGER, as_of=END, from_snapshot=snapshot)
    full = replay(LEDGER, [_engine().players["syn-s"]], as_of=END)
    assert resumed.to_snapshot() == full.to_snapshot()


def test_player_of_an_unknown_id_raises_and_keeps_the_engine_resumable() -> None:
    engine = _engine()
    with pytest.raises(UnknownPlayerError, match="Unknown player: syn-n"):
        engine.player("syn-n")
    snapshot = _snapshot(engine)
    assert snapshot["resumable"] is True
    assert "syn-n" not in engine.players
    resumed = replay(LEDGER, as_of=END, from_snapshot=snapshot)
    full = replay(LEDGER, [_engine().players["syn-s"]], as_of=END)
    assert resumed.to_snapshot() == full.to_snapshot()


def test_the_mark_round_trips_and_follows_replay() -> None:
    assert RatingEngine().to_snapshot()["resumable"] is False
    engine = _engine()
    assert engine.to_snapshot()["resumable"] is True
    restored = RatingEngine.from_snapshot(_snapshot(engine))
    assert restored.to_snapshot()["resumable"] is True
    resumed = replay(LEDGER, as_of=END, from_snapshot=_snapshot(engine))
    assert resumed.to_snapshot()["resumable"] is True
    restored.link_account("syn-a", "acct-1")
    assert restored.to_snapshot()["resumable"] is False


@pytest.mark.parametrize(
    ("value", "calculated", "message"),
    [
        pytest.param(1, "keep", r"resumable must be a bool", id="int"),
        pytest.param(None, "keep", r"resumable must be a bool", id="null"),
        pytest.param(True, None, r"resumable must be false", id="no replay"),
    ],
)
def test_from_snapshot_validates_the_mark(value: object, calculated: object, message: str) -> None:
    snapshot = _snapshot(_engine())
    snapshot["resumable"] = value
    if calculated is None:
        snapshot["calculated_as_of"] = None
        snapshot["replay_info"] = None
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(snapshot)
