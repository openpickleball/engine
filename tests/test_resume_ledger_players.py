"""A seed on resume for a player the checkpoint's ledger already named (issue #16).

When every row naming a player on or before the checkpoint was rejected,
the player is not in the snapshot, yet a full replay with that player's
seed would count those rows. Resume must raise instead. All ids are
synthetic.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from datetime import date

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    Match,
    Player,
    RatingEngine,
    RatingFormat,
    SnapshotError,
    replay,
)

S = RatingFormat.SINGLES
CFG = Config(max_singles_rating_gap=0.5)
CUT = date(2025, 3, 1)
END = date(2025, 6, 1)
LEDGER = [
    Match("syn-1", ("syn-p1",), ("syn-p9",), ((11, 3),), date(2025, 2, 1), S),
    Match("syn-2", ("syn-p1",), ("syn-p9",), ((11, 3),), date(2025, 4, 1), S),
]


def _seed(pid: str, rating: float) -> Player:
    eng = RatingEngine(CFG)
    eng.seed_external_rating(
        pid, S, ExternalRatingSnapshot("external_standard", rating, date(2025, 1, 1))
    )
    return eng.players[pid]


def _checkpoint() -> dict[str, object]:
    engine = replay(LEDGER, [_seed("syn-p1", 5.0)], cfg=CFG, as_of=CUT)
    loaded: dict[str, object] = json.loads(json.dumps(engine.to_snapshot()))
    return loaded


def test_the_checkpoint_rejected_every_row_naming_the_player() -> None:
    snapshot = _checkpoint()
    players = snapshot["players"]
    assert isinstance(players, list)
    assert [p["id"] for p in players] == ["syn-p1"]
    assert snapshot["ledger_player_ids"] == ["syn-p1", "syn-p9"]


def test_resume_rejects_a_seed_for_a_player_in_a_checkpointed_row() -> None:
    with pytest.raises(SnapshotError, match="syn-p9 .*full replay"):
        replay(LEDGER, [_seed("syn-p9", 4.8)], cfg=CFG, as_of=END, from_snapshot=_checkpoint())


def test_resume_accepts_a_seed_for_a_player_first_named_after_the_checkpoint() -> None:
    later = Match("syn-3", ("syn-p1",), ("syn-p7",), ((11, 9),), date(2025, 5, 1), S)
    ledger = [*LEDGER, later]
    seed = _seed("syn-p7", 4.9)
    resumed = replay(ledger, [seed], cfg=CFG, as_of=END, from_snapshot=_checkpoint())
    full = replay(ledger, [_seed("syn-p1", 5.0), seed], cfg=CFG, as_of=END)
    assert json.dumps(resumed.to_snapshot(), sort_keys=True) == json.dumps(
        full.to_snapshot(), sort_keys=True
    )


def test_ledger_player_ids_are_cumulative_across_resumes() -> None:
    resumed = replay(LEDGER, cfg=CFG, as_of=END, from_snapshot=_checkpoint())
    full = replay(LEDGER, [_seed("syn-p1", 5.0)], cfg=CFG, as_of=END)
    assert resumed.to_snapshot()["ledger_player_ids"] == ["syn-p1", "syn-p9"]
    assert resumed.to_snapshot() == full.to_snapshot()


Snap = dict[str, object]


def _set_ids(value: object) -> Callable[[Snap], None]:
    def mutate(snap: Snap) -> None:
        snap["ledger_player_ids"] = value

    return mutate


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        pytest.param(_set_ids({}), r"ledger_player_ids must be a list", id="type"),
        pytest.param(_set_ids([" "]), r"ledger_player_ids\[0\] must be a non-blank", id="blank"),
        pytest.param(
            _set_ids(["syn-p1", "syn-p1"]), r"duplicate player id 'syn-p1'", id="duplicate"
        ),
    ],
)
def test_from_snapshot_validates_ledger_player_ids(
    mutate: Callable[[Snap], None], message: str
) -> None:
    snapshot = copy.deepcopy(_checkpoint())
    mutate(snapshot)
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(snapshot, CFG)
