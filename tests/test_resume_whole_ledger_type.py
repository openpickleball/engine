"""replay rejects a whole_ledger that is not a bool (issue #16).

A str such as "no" is truthy and used to turn the check on, and 0 or
None without a snapshot was accepted. All ids are synthetic.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from open_engine import Match, RatingFormat, SnapshotError, replay

D = RatingFormat.DOUBLES
CUT = date(2025, 3, 1)
LEDGER = [Match("syn-1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 6),), CUT, D)]


def _checkpoint() -> dict[str, object]:
    loaded: dict[str, object] = json.loads(json.dumps(replay(LEDGER, as_of=CUT).to_snapshot()))
    return loaded


@pytest.mark.parametrize("value", ["no", "", 0, 1, None, 1.0], ids=repr)
@pytest.mark.parametrize("with_snapshot", [True, False], ids=["resume", "no snapshot"])
def test_whole_ledger_must_be_a_bool(value: object, with_snapshot: bool) -> None:
    snapshot = _checkpoint() if with_snapshot else None
    with pytest.raises(SnapshotError, match="whole_ledger must be a bool"):
        replay(LEDGER, as_of=CUT, from_snapshot=snapshot, whole_ledger=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, False])
def test_whole_ledger_accepts_a_bool(value: bool) -> None:
    assert replay(LEDGER, as_of=CUT, from_snapshot=_checkpoint(), whole_ledger=value).replay_info
