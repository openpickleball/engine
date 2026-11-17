"""from_snapshot enforces the engine's invariants for possible_duplicates (issue #21).

Under the default config every copy counts, so each key is a processed match
id. Under reject_content_duplicates=True no copy counts, so no key is. A copy
never registers its own content, so a key is never a content_keys value. All
ids are synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date

import pytest

from open_engine import Config, Match, RatingEngine, RatingFormat, SnapshotError, replay

D = RatingFormat.DOUBLES
ON = date(2026, 3, 1)
AS_OF = date(2026, 3, 31)
END = date(2026, 5, 1)

M1 = Match("m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 7),), ON, D)
M2 = Match("m2", ("syn-a", "syn-c"), ("syn-b", "syn-d"), ((11, 5),), ON, D)
COPY = Match("m3", ("syn-d", "syn-c"), ("syn-b", "syn-a"), ((7, 11),), ON, D)


def _reject() -> Config:
    return Config(reject_content_duplicates=True)


CONFIGS = pytest.mark.parametrize("cfg_factory", [Config, _reject], ids=["default", "reject"])


def _snapshot(cfg: Config, ledger: list[Match]) -> dict[str, object]:
    snap: dict[str, object] = json.loads(
        json.dumps(replay(ledger, cfg=cfg, as_of=AS_OF).to_snapshot())
    )
    return snap


@CONFIGS
def test_forged_map_is_refused_on_load_and_resume(cfg_factory: Callable[[], Config]) -> None:
    cfg = cfg_factory()
    snap = _snapshot(cfg, [M1, M2])
    snap["possible_duplicates"] = {"m2": "m1", "ghost": "m1"}
    with pytest.raises(SnapshotError, match=r"snapshot\.possible_duplicates"):
        RatingEngine.from_snapshot(snap, cfg)
    with pytest.raises(SnapshotError, match=r"snapshot\.possible_duplicates"):
        replay([M1, M2], cfg=cfg, as_of=END, from_snapshot=snap)


def test_default_refuses_an_unprocessed_key() -> None:
    snap = _snapshot(Config(), [M1, M2])
    snap["possible_duplicates"] = {"ghost": "m1"}
    with pytest.raises(SnapshotError, match=r"'ghost', which is not in processed_match_ids"):
        RatingEngine.from_snapshot(snap)


def test_default_refuses_a_key_that_is_a_content_keys_value() -> None:
    snap = _snapshot(Config(), [M1, M2])
    snap["possible_duplicates"] = {"m2": "m1"}
    with pytest.raises(SnapshotError, match=r"'m2', which is a content_keys value"):
        RatingEngine.from_snapshot(snap)


def test_reject_refuses_a_processed_key() -> None:
    cfg = _reject()
    snap = _snapshot(cfg, [M1, M2])
    snap["possible_duplicates"] = {"m2": "m1"}
    with pytest.raises(SnapshotError, match=r"'m2', which is in processed_match_ids"):
        RatingEngine.from_snapshot(snap, cfg)


@CONFIGS
def test_engine_snapshots_load_and_resume(cfg_factory: Callable[[], Config]) -> None:
    cfg = cfg_factory()
    ledger = [M1, M2, COPY]
    snap = _snapshot(cfg, ledger)
    assert snap["possible_duplicates"] == {"m3": "m1"}
    assert RatingEngine.from_snapshot(snap, cfg).to_snapshot() == snap
    full = replay(ledger, cfg=cfg, as_of=END).to_snapshot()
    assert replay(ledger, cfg=cfg, as_of=END, from_snapshot=snap).to_snapshot() == full
