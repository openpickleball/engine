"""Snapshots the engine's own methods produce still load (issue #61).

from_snapshot rejects states no engine method produces. These tests
show it still loads replays with rejected rows, resumes from them, and
an engine seeded from an external rating with no external id. All ids
are synthetic.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    Match,
    MatchStatus,
    MatchType,
    RatingEngine,
    RatingFormat,
    replay,
)

D = RatingFormat.DOUBLES
S = RatingFormat.SINGLES
AS_OF = date(2025, 3, 1)

Snap = dict[str, object]

LEDGER = [
    Match("syn-1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 4),), date(2025, 2, 1), D),
    Match(
        "syn-2",
        ("syn-a", "syn-c"),
        ("syn-b", "syn-d"),
        (),
        date(2025, 2, 2),
        D,
        status=MatchStatus.FORFEIT,
    ),
    Match(
        "syn-3",
        ("syn-e",),
        ("syn-f",),
        ((11, 7),),
        date(2025, 2, 3),
        S,
        match_type=MatchType.SELF_REPORTED,
    ),
    Match("syn-4", ("syn-c", "syn-d"), ("syn-a", "syn-b"), ((4, 11),), date(2025, 2, 1), D),
]


def _round_trips(engine: RatingEngine) -> Snap:
    snapshot: Snap = json.loads(json.dumps(engine.to_snapshot()))
    assert RatingEngine.from_snapshot(snapshot, engine.cfg).to_snapshot() == snapshot
    return snapshot


@pytest.mark.parametrize("reject", [False, True], ids=["flag copies", "reject copies"])
def test_a_replay_with_rejected_rows_loads_and_resumes(reject: bool) -> None:
    cfg = Config(reject_content_duplicates=reject)
    engine = replay(LEDGER, cfg=cfg, as_of=AS_OF)
    counted = {"syn-1"} if reject else {"syn-1", "syn-4"}
    assert engine.processed_match_ids == counted
    assert engine.possible_duplicates == {"syn-4": "syn-1"}
    checkpoint = _round_trips(engine)
    assert checkpoint["resumable"] is True
    later = Match("syn-5", ("syn-a", "syn-e"), ("syn-b", "syn-f"), ((11, 6),), date(2025, 3, 5), D)
    resumed = replay([*LEDGER, later], cfg=cfg, as_of=date(2025, 3, 10), from_snapshot=checkpoint)
    full = replay([*LEDGER, later], cfg=cfg, as_of=date(2025, 3, 10))
    assert _round_trips(resumed) == _round_trips(full)


def test_an_external_seed_without_an_external_id_loads() -> None:
    engine = RatingEngine()
    engine.seed_external_rating(
        "syn-g", S, ExternalRatingSnapshot("external_standard", 4.0, date(2025, 1, 1), 60.0)
    )
    snapshot = _round_trips(engine)
    players = snapshot["players"]
    assert isinstance(players, list)
    assert players[0]["external_ids"] == {}
