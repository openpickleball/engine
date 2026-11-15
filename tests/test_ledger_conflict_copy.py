"""LedgerConflictError keeps its message and match_ids through pickle and copy."""

from __future__ import annotations

import copy
import pickle
from collections.abc import Callable

import pytest

from open_engine import LedgerConflictError

ROUND_TRIPS: dict[str, Callable[[LedgerConflictError], object]] = {
    "pickle": lambda e: pickle.loads(pickle.dumps(e)),
    "copy": copy.copy,
    "deepcopy": copy.deepcopy,
}


@pytest.mark.parametrize("name", list(ROUND_TRIPS))
def test_round_trip_keeps_message_and_ids(name: str) -> None:
    original = LedgerConflictError(["m2", "m1", "m2"])
    restored = ROUND_TRIPS[name](original)
    assert type(restored) is LedgerConflictError
    assert restored.match_ids == ("m1", "m2")
    assert str(restored) == "Conflicting ledger rows for match_id: m1, m2"
    assert str(restored) == str(original)
