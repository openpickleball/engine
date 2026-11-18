"""A processed seed_sensitive_rejections entry must name a counted category match (issue #99).

record adds an entry under a processed match_id only for a counted category
match whose overall doubles update it rejected. A counted match moves at
least one player, and that player's category state then records one other
named player as a partner and the other two as opponents. A snapshot whose
processed entry names a plain doubles match, or players who never played
that way, is not one the engine makes. All ids are synthetic.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    Match,
    RatingEngine,
    RatingFormat,
    RejectionReason,
    SnapshotError,
)

D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
PLAYED = date(2026, 1, 10)
EARLY = date(2026, 1, 1)
CFG = Config(max_doubles_partner_gap=1.0)
NO_CATEGORY_MATCH = r"seed_sensitive_rejections\['syn-d1'\] .*category"

Snap = dict[str, object]


def _snap(rating: float) -> ExternalRatingSnapshot:
    return ExternalRatingSnapshot("syn-ext", rating, EARLY)


def _json(engine: RatingEngine) -> Snap:
    loaded: Snap = json.loads(json.dumps(engine.to_snapshot(), allow_nan=False))
    return loaded


def _doubles_engine() -> RatingEngine:
    """A counted plain doubles match syn-d1, and four unrelated unplayed players."""
    engine = RatingEngine(CFG)
    match = Match("syn-d1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 6),), PLAYED, D)
    assert engine.record(match).counted
    for pid in ("syn-e", "syn-f", "syn-g", "syn-h"):
        engine.seed_external_rating(pid, MX, _snap(4.0))
    return engine


@pytest.mark.parametrize(
    "players",
    [
        ["syn-a", "syn-b", "syn-c", "syn-d"],
        ["syn-e", "syn-f", "syn-g", "syn-h"],
    ],
    ids=["plain doubles match", "unrelated players"],
)
def test_a_processed_entry_without_a_counted_category_match_does_not_load(
    players: list[str],
) -> None:
    snap = _json(_doubles_engine())
    rejections = snap["seed_sensitive_rejections"]
    assert isinstance(rejections, dict)
    rejections["syn-d1"] = {"players": players, "rating_format": "doubles"}
    with pytest.raises(SnapshotError, match=NO_CATEGORY_MATCH):
        RatingEngine.from_snapshot(snap, CFG)


def test_an_engine_made_processed_entry_still_loads() -> None:
    engine = RatingEngine(CFG)
    for pid in ("syn-p", "syn-q", "syn-r", "syn-s"):
        engine.seed_external_rating(pid, MX, _snap(4.0))
    engine.seed_external_rating("syn-p", D, _snap(5.0))
    engine.seed_external_rating("syn-q", D, _snap(3.0))
    match = Match("syn-mx", ("syn-p", "syn-q"), ("syn-r", "syn-s"), ((11, 6),), PLAYED, MX)
    result = engine.record(match)
    assert result.counted
    assert result.parent is not None
    assert result.parent.reason is RejectionReason.DOUBLES_PARTNER_GAP
    snap = _json(engine)
    rejections = snap["seed_sensitive_rejections"]
    assert isinstance(rejections, dict)
    assert "syn-mx" in rejections
    assert RatingEngine.from_snapshot(snap, CFG).to_snapshot() == snap
