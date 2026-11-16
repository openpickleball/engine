"""replay(whole_ledger=True) detects rows removed before the checkpoint (issue #16).

Given only some rows, resume cannot tell a voided row from one the caller
left out. With whole_ledger=True the caller promises the whole ledger, so
a checkpointed row that is missing must have been removed, and resume
raises. All ids are synthetic.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date, timedelta

import pytest

from open_engine import (
    LedgerConflictError,
    Match,
    RatingEngine,
    RatingFormat,
    SnapshotError,
    replay,
)

D = RatingFormat.DOUBLES
CUT = date(2025, 3, 1)
END = date(2025, 6, 1)


def _row(match_id: str, on: date) -> Match:
    return Match(match_id, ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 6),), on, D)


LEDGER = [
    _row("syn-1", CUT - timedelta(20)),
    _row("syn-2", CUT - timedelta(10)),
    _row("syn-3", CUT),
    _row("syn-4", CUT + timedelta(5)),
]


def _checkpoint() -> dict[str, object]:
    engine = replay(LEDGER[:3], as_of=CUT)
    loaded: dict[str, object] = json.loads(json.dumps(engine.to_snapshot()))
    return loaded


def _text(engine: RatingEngine) -> str:
    return json.dumps(engine.to_snapshot(), sort_keys=True)


@pytest.mark.parametrize("voided", ["syn-1", "syn-3"], ids=["before", "on the checkpoint date"])
def test_whole_ledger_rejects_a_voided_checkpointed_row(voided: str) -> None:
    rows = [m for m in LEDGER if m.match_id != voided]
    with pytest.raises(SnapshotError, match=f"missing from the ledger: {voided}$"):
        replay(rows, as_of=END, from_snapshot=_checkpoint(), whole_ledger=True)


def test_whole_ledger_names_every_missing_id_sorted() -> None:
    with pytest.raises(SnapshotError, match="missing from the ledger: syn-1, syn-2, syn-3$"):
        replay(LEDGER[3:], as_of=END, from_snapshot=_checkpoint(), whole_ledger=True)


def test_without_whole_ledger_a_voided_row_is_not_seen() -> None:
    rows = [m for m in LEDGER if m.match_id != "syn-1"]
    resumed = replay(rows, as_of=END, from_snapshot=_checkpoint())
    assert _text(resumed) == _text(replay(LEDGER, as_of=END))


def test_whole_ledger_with_the_unchanged_ledger_equals_a_full_replay() -> None:
    resumed = replay(LEDGER, as_of=END, from_snapshot=_checkpoint(), whole_ledger=True)
    assert _text(resumed) == _text(replay(LEDGER, as_of=END))


def test_whole_ledger_reports_conflicts_first() -> None:
    changed = dataclasses.replace(LEDGER[1], games=((11, 9),))
    with pytest.raises(LedgerConflictError, match="syn-2"):
        replay([changed, LEDGER[3]], as_of=END, from_snapshot=_checkpoint(), whole_ledger=True)


def test_whole_ledger_reports_late_rows_before_missing_rows() -> None:
    late = _row("syn-late", CUT - timedelta(1))
    with pytest.raises(SnapshotError, match="not in the checkpoint: syn-late"):
        replay([late, LEDGER[3]], as_of=END, from_snapshot=_checkpoint(), whole_ledger=True)


def test_whole_ledger_without_a_snapshot_raises() -> None:
    with pytest.raises(SnapshotError, match="whole_ledger needs from_snapshot"):
        replay(LEDGER, as_of=END, whole_ledger=True)


def test_whole_ledger_is_keyword_only() -> None:
    with pytest.raises(TypeError):
        replay(LEDGER, (), None, END, None, True)  # type: ignore[misc]
