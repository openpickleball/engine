"""Snapshot schema 6: replay_info.net_rating_created (issue #42).

All player and match ids are synthetic.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

import pytest

from open_engine import (
    ExternalRatingSnapshot,
    Match,
    MatchType,
    RatingEngine,
    RatingFormat,
    SnapshotError,
    replay,
)

S = RatingFormat.SINGLES
CAPTURED = date(2026, 1, 5)
AS_OF = date(2026, 1, 25)


def _engine() -> RatingEngine:
    seeder = RatingEngine()
    seeder.seed_external_rating("syn-feed", S, ExternalRatingSnapshot("ext", 4.0, CAPTURED))
    rows = [
        Match(
            f"syn-f{i:02d}",
            ("syn-new",),
            ("syn-feed",),
            ((11, 5),),
            CAPTURED + timedelta(days=i + 1),
            S,
            match_type=MatchType.REC_VERIFIED,
        )
        for i in range(10)
    ]
    return replay(rows, [seeder.players["syn-feed"]], as_of=AS_OF)


def _snapshot(engine: RatingEngine | None = None) -> dict[str, object]:
    eng = _engine() if engine is None else engine
    loaded: dict[str, object] = json.loads(json.dumps(eng.to_snapshot(), allow_nan=False))
    return loaded


def _created(snap: dict[str, object]) -> dict[str, object]:
    info = snap["replay_info"]
    assert isinstance(info, dict)
    created: dict[str, object] = info["net_rating_created"]
    return created


def test_snapshot_has_every_format_sorted() -> None:
    snap = _snapshot()
    created = _created(snap)
    assert list(created) == sorted(fmt.value for fmt in RatingFormat)
    assert all(type(value) is float and math.isfinite(value) for value in created.values())
    assert created["singles"] > 0.2


def test_round_trip_keeps_the_exact_total() -> None:
    engine = _engine()
    restored = RatingEngine.from_snapshot(_snapshot(engine))
    assert restored.replay_info == engine.replay_info
    assert restored.replay_info is not None and engine.replay_info is not None
    got = restored.replay_info.net_rating_created[S]
    assert got.hex() == engine.replay_info.net_rating_created[S].hex()
    assert list(restored.replay_info.net_rating_created) == list(RatingFormat)


def _set(fmt: str, value: object) -> dict[str, object]:
    snap = _snapshot()
    _created(snap)[fmt] = value
    return snap


@pytest.mark.parametrize("value", [0, 1, True, math.nan, math.inf, "0.1", None])
def test_value_must_be_a_finite_float(value: object) -> None:
    with pytest.raises(
        SnapshotError, match=r"replay_info\.net_rating_created\.singles must be a finite float"
    ):
        RatingEngine.from_snapshot(_set("singles", value))


def test_missing_mapping_is_rejected() -> None:
    snap = _snapshot()
    info = snap["replay_info"]
    assert isinstance(info, dict)
    del info["net_rating_created"]
    with pytest.raises(SnapshotError, match=r"replay_info: missing key 'net_rating_created'"):
        RatingEngine.from_snapshot(snap)


@pytest.mark.parametrize("value", [[], "x", None, 0.0])
def test_mapping_must_be_an_object(value: object) -> None:
    snap = _snapshot()
    info = snap["replay_info"]
    assert isinstance(info, dict)
    info["net_rating_created"] = value
    with pytest.raises(SnapshotError, match=r"replay_info\.net_rating_created must be an object"):
        RatingEngine.from_snapshot(snap)


def test_missing_format_is_rejected() -> None:
    snap = _snapshot()
    del _created(snap)["mixed_doubles"]
    with pytest.raises(
        SnapshotError, match=r"replay_info\.net_rating_created: missing key 'mixed_doubles'"
    ):
        RatingEngine.from_snapshot(snap)


def test_unknown_format_is_rejected() -> None:
    with pytest.raises(
        SnapshotError, match=r"replay_info\.net_rating_created: unknown key 'triples'"
    ):
        RatingEngine.from_snapshot(_set("triples", 0.0))


def test_nonzero_total_needs_counted_matches() -> None:
    seeder = RatingEngine()
    seeder.seed_external_rating("syn-feed", S, ExternalRatingSnapshot("ext", 4.0, CAPTURED))
    engine = replay([], [seeder.players["syn-feed"]], as_of=AS_OF)
    snap = _snapshot(engine)
    _created(snap)["singles"] = 0.25
    with pytest.raises(
        SnapshotError,
        match=r"replay_info\.net_rating_created\.singles must be 0\.0 when matches_counted is 0",
    ):
        RatingEngine.from_snapshot(snap)


def test_nonzero_total_needs_a_played_state_in_that_format() -> None:
    with pytest.raises(
        SnapshotError,
        match=(
            r"replay_info\.net_rating_created\.doubles must be 0\.0 when no player has a "
            r"played doubles state"
        ),
    ):
        RatingEngine.from_snapshot(_set("doubles", 0.5))


def test_negative_total_loads() -> None:
    snap = _set("singles", -0.75)
    snap["resumable"] = False
    info = RatingEngine.from_snapshot(snap).replay_info
    assert info is not None
    assert info.net_rating_created[S] == -0.75
