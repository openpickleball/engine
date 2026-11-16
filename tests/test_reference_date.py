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
CLOCK_NAMES = {
    "today",
    "now",
    "utcnow",
    "fromtimestamp",
    "utcfromtimestamp",
    "time",
    "time_ns",
    "monotonic",
    "perf_counter",
    "localtime",
    "gmtime",
}


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


def _clock_reads(source: str, name: str) -> list[str]:
    """Every import of time and every call to a clock-like name, whatever its owner."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            hit = any(alias.name.split(".")[0] == "time" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            hit = node.module is not None and node.module.split(".")[0] == "time"
        elif isinstance(node, ast.Call):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            hit = called in CLOCK_NAMES
        else:
            hit = False
        if hit:
            found.append(f"{name}:{getattr(node, 'lineno', 0)} {ast.unparse(node)}")
    return found


def test_package_never_reads_the_wall_clock() -> None:
    paths = sorted(SRC.rglob("*.py"))
    assert paths
    found = [
        read
        for path in paths
        for read in _clock_reads(path.read_text(encoding="utf-8"), str(path.relative_to(SRC)))
    ]
    assert found == []


@pytest.mark.parametrize(
    "snippet",
    [
        "date.today()",
        "datetime.now()",
        "datetime.utcnow()",
        "datetime.today()",
        "datetime.datetime.now()",
        "dt.now()",
        "d.date.today()",
        "datetime.fromtimestamp(0)",
        "datetime.utcfromtimestamp(0)",
        "time.time()",
        "time.time_ns()",
        "time.monotonic()",
        "time.perf_counter()",
        "time.localtime()",
        "time.gmtime()",
        "now()",
        "perf_counter()",
        "import time",
        "import time as t",
        "from time import monotonic",
    ],
)
def test_clock_scan_catches(snippet: str) -> None:
    assert _clock_reads(snippet, "snippet") != []


@pytest.mark.parametrize("snippet", ["date(2026, 1, 1)", "x.timestamp", "import datetime"])
def test_clock_scan_ignores(snippet: str) -> None:
    assert _clock_reads(snippet, "snippet") == []


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
