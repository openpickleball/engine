"""Digests are a replay concern only, and resume never relies on assert (issue #16).

All ids are synthetic.
"""

from __future__ import annotations

import ast
from datetime import date, timedelta
from pathlib import Path

import pytest

import open_engine._replay as replay_module
import open_engine.engine as engine_module
from open_engine import Match, RatingFormat, prequential_predictions, replay

D = RatingFormat.DOUBLES
START = date(2025, 1, 1)
LEDGER = [
    Match(
        f"syn-m{i}",
        ("syn-a", "syn-b"),
        ("syn-c", "syn-d"),
        ((11, i % 9),),
        START + timedelta(days=3 * i),
        D,
    )
    for i in range(12)
]


def _no_digest(match: Match) -> str:
    raise AssertionError(f"digest computed for {match.match_id}")


@pytest.mark.parametrize("mode", ["live", "scheduled"])
def test_prequential_predictions_compute_no_digests(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    expected = prequential_predictions(LEDGER, mode=mode, burn_in_matches=0)  # type: ignore[arg-type]
    monkeypatch.setattr(replay_module, "_match_digest", _no_digest)
    got = prequential_predictions(LEDGER, mode=mode, burn_in_matches=0)  # type: ignore[arg-type]
    assert got == expected
    assert got


def test_replay_still_records_digests() -> None:
    engine = replay(LEDGER, as_of=START + timedelta(days=60))
    assert set(engine._ledger_digests) == {m.match_id for m in LEDGER}


PACKAGE_SOURCES = sorted(Path(engine_module.__file__).parent.glob("*.py"))


def test_the_assert_scan_covers_the_engine_code() -> None:
    # The resume and snapshot code lives in the private modules, not engine.py.
    names = {path.name for path in PACKAGE_SOURCES}
    assert {"engine.py", "winprob.py", "_engine.py", "_replay.py", "_snapshot.py"} <= names


@pytest.mark.parametrize("path", PACKAGE_SOURCES, ids=lambda path: path.name)
def test_engine_has_no_assert_statements(path: Path) -> None:
    # assert vanishes under python -O, so no check may rely on it.
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Assert)]
    assert lines == []
