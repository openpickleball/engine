"""player_summary measures inactivity at a reference date, never the wall clock (issue 17)."""

from __future__ import annotations

import ast
from datetime import date, timedelta
from pathlib import Path

import pytest

import open_engine
from open_engine import (
    ExternalRatingSnapshot,
    Match,
    OpenEngineError,
    RatingEngine,
    RatingFormat,
    reliability,
    replay,
)

D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
SRC = Path(__file__).resolve().parents[1] / "src" / "open_engine"
EARLY = date(2024, 3, 1)
LATE = date(2026, 11, 1)
CLOCK_CALLS = {("date", "today"), ("datetime", "now"), ("datetime", "utcnow")}
CLOCK_CALLS |= {("datetime", "today"), ("time", "time")}


def _ledger() -> list[Match]:
    """30 matches for syn-a, every 2 days from 2024-01-01, all partners and opponents new."""
    return [
        Match(
            f"syn-m{i}",
            ("syn-a", f"syn-p{i}"),
            (f"syn-o{i}", f"syn-q{i}"),
            ((11, 7),),
            date(2024, 1, 1) + timedelta(days=2 * i),
            D,
        )
        for i in range(30)
    ]


def _clock_calls(path: Path) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if isinstance(owner, ast.Name) and (owner.id, node.func.attr) in CLOCK_CALLS:
            found.append(f"{path.name}:{node.lineno} {ast.unparse(node)}")
    return found


def test_package_never_reads_the_wall_clock() -> None:
    found = [call for path in sorted(SRC.glob("*.py")) for call in _clock_calls(path)]
    assert found == []


def test_inactive_player_reliability_drops_by_default() -> None:
    early = replay(_ledger(), as_of=EARLY).player_summary("syn-a", D)
    late_engine = replay(_ledger(), as_of=LATE)
    late = late_engine.player_summary("syn-a", D)
    assert late.reliability < early.reliability
    assert late.rd > early.rd
    assert late == late_engine.player_summary("syn-a", D, as_of=LATE)


def test_resumed_checkpoint_measures_at_its_new_as_of() -> None:
    early = replay(_ledger(), as_of=EARLY)
    resumed = replay([], as_of=LATE, from_snapshot=early.to_snapshot())
    full = replay(_ledger(), as_of=LATE)
    assert resumed.player_summary("syn-a", D) == full.player_summary("syn-a", D)


def test_summary_without_reference_date_raises() -> None:
    eng = RatingEngine()
    eng.record(_ledger()[0])
    with pytest.raises(open_engine.ReferenceDateError, match="as_of"):
        eng.player_summary("syn-a", D)


def test_summary_raise_creates_no_state() -> None:
    eng = RatingEngine()
    eng.record(_ledger()[0])
    with pytest.raises(open_engine.ReferenceDateError, match="as_of"):
        eng.player_summary("syn-a", MX)
    assert MX not in eng.players["syn-a"].ratings
    with pytest.raises(open_engine.ReferenceDateError, match="as_of"):
        eng.player_summary("syn-new", D)
    assert "syn-new" not in eng.players


def test_explicit_as_of_overrides_calculated_as_of() -> None:
    early = replay(_ledger(), as_of=EARLY).player_summary("syn-a", D)
    late_engine = replay(_ledger(), as_of=LATE)
    measured = late_engine.player_summary("syn-a", D, as_of=EARLY)
    assert (measured.reliability, measured.rd) == (early.reliability, early.rd)
    assert measured.calculated_as_of == LATE


def test_reliability_requires_as_of() -> None:
    eng = replay(_ledger(), as_of=EARLY)
    state = eng.state("syn-a", D)
    with pytest.raises(TypeError):
        reliability(state, eng.cfg)  # type: ignore[call-arg]


def test_reference_date_error_is_public() -> None:
    error = open_engine.ReferenceDateError
    assert issubclass(error, OpenEngineError)
    assert issubclass(error, ValueError)
    assert "ReferenceDateError" in open_engine.__all__


def test_import_captured_after_replay_is_not_carried_by_default() -> None:
    eng = replay(_ledger(), as_of=EARLY)
    captured = EARLY + timedelta(days=5)
    snap = ExternalRatingSnapshot("external_standard", 4.0, captured, 85)
    eng.seed_external_rating("syn-z", D, snap)
    assert eng.player_summary("syn-z", D).imported_reliability is None
    assert eng.player_summary("syn-z", D, as_of=captured).imported_reliability == 85.0
