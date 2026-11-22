"""The figures docs/model.md states agree with the engine (issue #47).

Each figure is checked both ways: the engine's value rounds to the stated
figure, and the stated text appears in its section of docs/model.md. A change
to either the engine or the doc fails the test.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    GameFormat,
    Match,
    MatchResult,
    MatchType,
    RatingEngine,
    RatingFormat,
)

MODEL_DOC = Path(__file__).resolve().parents[1] / "docs" / "model.md"
DOUBLES = RatingFormat.DOUBLES


def _section(heading: str) -> str:
    """The text of docs/model.md from a "## " heading up to the next one."""
    text = MODEL_DOC.read_text(encoding="utf-8")
    start = text.index(f"\n## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


@dataclass(frozen=True)
class WorkedExample:
    first: MatchResult
    second: MatchResult
    after_first: dict[str, tuple[float, float]]
    after_second: dict[str, tuple[float, float]]


def _ratings(engine: RatingEngine) -> dict[str, tuple[float, float]]:
    states = {pid: engine.state(pid, DOUBLES) for pid in ("a1", "a2", "b1", "b2")}
    return {pid: (s.rating, s.rd) for pid, s in states.items()}


@pytest.fixture(scope="module")
def worked() -> WorkedExample:
    engine = RatingEngine(Config())
    first = engine.record(
        Match(
            match_id="worked-1",
            team_a=("a1", "a2"),
            team_b=("b1", "b2"),
            games=((11, 7),),
            played_on=date(2026, 1, 10),
            rating_format=DOUBLES,
            match_type=MatchType.TOURNAMENT,
            best_of=1,
            game_format=GameFormat(),
        )
    )
    after_first = _ratings(engine)
    second = engine.record(
        Match(
            match_id="worked-2",
            team_a=("a1", "a2"),
            team_b=("b1", "b2"),
            games=((11, 9), (8, 11), (11, 6)),
            played_on=date(2026, 3, 11),  # sixty days later
            rating_format=DOUBLES,
            match_type=MatchType.LEAGUE,
            best_of=3,
            game_format=GameFormat(),
        )
    )
    return WorkedExample(first, second, after_first, _ratings(engine))


Getter = Callable[[WorkedExample], float]

# (label, text stated in the doc, places the doc rounds to, engine value).
WORKED_FIGURES: list[tuple[str, str, int, Getter]] = [
    ("first E", "0.5", 5, lambda w: w.first.expected_a),
    ("first P", "0.61111", 5, lambda w: w.first.actual_a),
    ("first surprise", "0.11111", 5, lambda w: w.first.surprise),
    ("first weight", "1.0", 5, lambda w: w.first.effective_weight),
    ("first delta A", "+0.06667", 5, lambda w: w.first.deltas["a1"]),
    ("first delta B", "-0.06667", 5, lambda w: w.first.deltas["b1"]),
    ("first imbalance", "0.0", 5, lambda w: w.first.imbalance),
    ("first rd_new", "0.736", 5, lambda w: w.after_first["a1"][1]),
    ("first rating A", "3.56667", 5, lambda w: w.after_first["a1"][0]),
    ("first rating B", "3.43333", 5, lambda w: w.after_first["b1"][0]),
    ("second E", "0.52644", 5, lambda w: w.second.expected_a),
    ("second P", "0.53571", 5, lambda w: w.second.actual_a),
    ("second surprise", "0.00927", 5, lambda w: w.second.surprise),
    ("second weight", "1.116", 5, lambda w: w.second.effective_weight),
    ("second delta A", "+0.00580", 5, lambda w: w.second.deltas["a1"]),
    ("second delta B", "-0.00580", 5, lambda w: w.second.deltas["b1"]),
    ("second imbalance", "0.0", 5, lambda w: w.second.imbalance),
    ("second rating A", "3.57246", 5, lambda w: w.after_second["a1"][0]),
    ("second rating B", "3.42754", 5, lambda w: w.after_second["b1"][0]),
    ("second rd_new", "0.67561", 5, lambda w: w.after_second["a1"][1]),
]


@pytest.mark.parametrize(
    ("label", "stated", "places", "getter"),
    WORKED_FIGURES,
    ids=[row[0] for row in WORKED_FIGURES],
)
def test_worked_example_figure_matches_engine_and_doc(
    worked: WorkedExample, label: str, stated: str, places: int, getter: Getter
) -> None:
    assert round(getter(worked), places) == float(stated), label
    assert stated in _section("A worked example"), label


def test_worked_example_states_second_point_share_fraction(worked: WorkedExample) -> None:
    # Team A won 11 + 8 + 11 = 30 of the 56 points.
    assert round(30 / 56, 5) == worked.second.actual_a
    assert "30 / 56" in _section("A worked example")


@pytest.mark.parametrize(
    ("gap", "stated"),
    [(0.5, "83.1%"), (1.0, "97.1%")],
)
def test_gap_win_probability_matches_engine_and_doc(gap: float, stated: str) -> None:
    engine = RatingEngine(Config())
    for pid, rating in (("p1", 3.5 + gap), ("p2", 3.5)):
        engine.seed_external_rating(
            pid,
            RatingFormat.SINGLES,
            ExternalRatingSnapshot(
                source="external_standard",
                rating=rating,
                reliability=80,
                captured_on=date(2026, 1, 1),
            ),
        )
    probability = engine.predict_win_probability(
        ("p1",), ("p2",), RatingFormat.SINGLES, GameFormat(), best_of=1
    )
    assert f"{round(100 * probability, 1)}%" == stated
    assert stated in _section("Win probability")
