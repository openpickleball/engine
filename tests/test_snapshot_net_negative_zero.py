"""A negative zero total in a snapshot does not load (#42 review).

The engine never produces -0.0, and a resume from one would give snapshot
text that differs from a full replay's. All ids are synthetic.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from open_engine import Match, RatingEngine, RatingFormat, SnapshotError, replay


def _snapshot() -> dict[str, object]:
    row = Match(
        "syn-m1", ("syn-a",), ("syn-b",), ((11, 4),), date(2025, 1, 6), RatingFormat.SINGLES
    )
    engine = replay([row], as_of=date(2025, 1, 31))
    loaded: dict[str, object] = json.loads(json.dumps(engine.to_snapshot()))
    return loaded


@pytest.mark.parametrize("resumable", [True, False])
def test_negative_zero_total_does_not_load(resumable: bool) -> None:
    snap = _snapshot()
    snap["resumable"] = resumable
    info = snap["replay_info"]
    assert isinstance(info, dict)
    info["net_rating_created"]["doubles"] = -0.0
    with pytest.raises(
        SnapshotError,
        match=r"replay_info\.net_rating_created\.doubles must not be a negative zero",
    ):
        RatingEngine.from_snapshot(snap)


def test_replays_never_write_a_negative_zero() -> None:
    text = json.dumps(_snapshot())
    assert "-0.0" not in text
