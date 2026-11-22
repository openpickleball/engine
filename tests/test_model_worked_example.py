"""The figures docs/model.md states agree with the engine (issue #47).

Each figure is checked both ways: the engine's value rounds to the stated
figure, and the stated figure appears exactly once, in its own context, in its
part of docs/model.md. A change to either the engine or the doc fails the test.
"""

from __future__ import annotations

import re
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
    ScoringType,
    expected_point_share,
)

MODEL_DOC = Path(__file__).resolve().parents[1] / "docs" / "model.md"
DOUBLES = RatingFormat.DOUBLES
CFG = Config()
TEAM_A = ("a1", "a2")
TEAM_B = ("b1", "b2")

# The two matches the test feeds the engine. The doc's description of each
# match is built from these same objects, so the doc's inputs and the
# engine's inputs cannot differ.
FIRST_MATCH = Match(
    match_id="worked-1",
    team_a=TEAM_A,
    team_b=TEAM_B,
    games=((11, 7),),
    played_on=date(2026, 1, 10),
    rating_format=DOUBLES,
    match_type=MatchType.TOURNAMENT,
    best_of=1,
    game_format=GameFormat(),
)
SECOND_MATCH = Match(
    match_id="worked-2",
    team_a=TEAM_A,
    team_b=TEAM_B,
    games=((11, 9), (8, 11), (11, 6)),
    played_on=date(2026, 3, 11),
    rating_format=DOUBLES,
    match_type=MatchType.LEAGUE,
    best_of=3,
    game_format=GameFormat(),
)
SECOND_GAMES = SECOND_MATCH.games

# How the doc writes the values it names.
SCORING_WORDS = {ScoringType.SIDE_OUT: "side-out", ScoringType.RALLY: "rally"}
DAY_WORDS = {30: "Thirty", 60: "Sixty", 90: "Ninety"}


def _section(heading: str) -> str:
    """The text of docs/model.md from a "## " heading up to the next one."""
    text = MODEL_DOC.read_text(encoding="utf-8")
    start = text.index(f"\n## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def _part(name: str) -> str:
    """One part of the doc a figure is checked in.

    The worked example splits at its code fences: "intro" is the prose before
    the first block, "first" and "second" are the two matches' code blocks,
    "between" the prose between them, and "after" the prose after the second
    block.
    """
    if name == "win probability":
        return _section("Win probability")
    pieces = _section("A worked example").split("```")
    assert len(pieces) == 5, "the worked example should hold exactly two code blocks"
    parts = {
        "intro": pieces[0],
        "first": pieces[1],
        "between": pieces[2],
        "second": pieces[3],
        "after": pieces[4],
    }
    return parts[name]


def _occurrences(text: str, context: str, figure: str) -> int:
    """How often context + figure appears, with the figure not followed by more digits."""
    return len(re.findall(re.escape(context + figure) + r"(?!\.?[0-9])", text))


@dataclass(frozen=True)
class WorkedExample:
    first: MatchResult
    second: MatchResult
    before_first: dict[str, float]
    after_first: dict[str, tuple[float, float]]
    after_second: dict[str, tuple[float, float]]


def _states(engine: RatingEngine) -> dict[str, tuple[float, float]]:
    states = {pid: engine.state(pid, DOUBLES) for pid in TEAM_A + TEAM_B}
    return {pid: (s.rating, s.rd) for pid, s in states.items()}


@pytest.fixture(scope="module")
def worked() -> WorkedExample:
    engine = RatingEngine(CFG)
    # The players are new, so each starts from the new player state.
    before_first = dict.fromkeys(TEAM_A + TEAM_B, CFG.default_rating)
    first = engine.record(FIRST_MATCH)
    after_first = _states(engine)
    second = engine.record(SECOND_MATCH)
    return WorkedExample(first, second, before_first, after_first, _states(engine))


def _k_first(w: WorkedExample) -> float:
    """Team A's K in the first match, from the engine's unrounded rating change."""
    raw_delta = w.after_first["a1"][0] - w.before_first["a1"]
    ((points_a, points_b),) = FIRST_MATCH.games
    surprise = points_a / (points_a + points_b) - expected_point_share(
        3.5, 3.5, CFG.scale_by_scoring[GameFormat().scoring]
    )
    return raw_delta / (w.first.effective_weight * surprise)


def _k_second(w: WorkedExample) -> float:
    """Team A's K in the second match, from the engine's unrounded rating change."""
    rating_a = w.after_first["a1"][0]
    rating_b = w.after_first["b1"][0]
    scale = CFG.scale_by_scoring[GameFormat().scoring]
    points_a = sum(a for a, _ in SECOND_GAMES)
    points_all = sum(a + b for a, b in SECOND_GAMES)
    surprise = points_a / points_all - expected_point_share(rating_a, rating_b, scale)
    raw_delta = w.after_second["a1"][0] - rating_a
    return raw_delta / (w.second.effective_weight * surprise)


def _rd_inflated(result: MatchResult, rd_new: float) -> float:
    """rd' before the update, from the engine's rd_new: rd_new = rd' * (1 - shrink * weight)."""
    return rd_new / (1 - CFG.rd_shrink_per_match * result.effective_weight)


Getter = Callable[[WorkedExample], float]

# (label, part, context, figure as the doc states it, places, engine value).
WORKED_FIGURES: list[tuple[str, str, str, str, int, Getter]] = [
    ("first R", "first", "R_A = R_B = ", "3.5", 5, lambda w: w.before_first["a1"]),
    ("first E", "first", "E = 1 / (1 + 10**0) = ", "0.5", 5, lambda w: w.first.expected_a),
    ("first P", "first", "P = 11 / 18 = ", "0.61111", 5, lambda w: w.first.actual_a),
    ("first surprise", "first", "surprise = ", "0.11111", 5, lambda w: w.first.surprise),
    (
        "first M",
        "first",
        "M = 1.00 * (1 + 0.12 * 0) = ",
        "1.0",
        5,
        lambda w: w.first.effective_weight / CFG.match_weights[MatchType.TOURNAMENT],
    ),
    (
        "first weight",
        "first",
        "weight = 1.00 * 1.0 * 1.0 = ",
        "1.0",
        5,
        lambda w: w.first.effective_weight,
    ),
    (
        "first rd'",
        "first",
        "rd' = ",
        "0.80",
        2,
        lambda w: _rd_inflated(w.first, w.after_first["a1"][1]),
    ),
    (
        "first f",
        "first",
        "f = ",
        "1",
        5,
        lambda w: (_k_first(w) - CFG.k_min) / (CFG.k_max - CFG.k_min),
    ),
    ("first K", "first", "K = ", "0.60", 5, _k_first),
    (
        "first delta A",
        "first",
        "team A delta = +0.60 * 1.0 * 0.11111 = ",
        "+0.06667",
        5,
        lambda w: w.first.deltas["a1"],
    ),
    ("first delta B", "first", "team B delta = ", "-0.06667", 5, lambda w: w.first.deltas["b1"]),
    (
        "first rd_new",
        "first",
        "rd_new = max(0.10, 0.80 * (1 - 0.08 * 1.0)) = ",
        "0.736",
        5,
        lambda w: w.after_first["a1"][1],
    ),
    ("first imbalance", "first", "imbalance = ", "0.0", 5, lambda w: w.first.imbalance),
    ("first after A", "first", "team A after = ", "3.56667", 5, lambda w: w.after_first["a1"][0]),
    ("first after B", "first", "team B after = ", "3.43333", 5, lambda w: w.after_first["b1"][0]),
    (
        "second rd' (prose)",
        "between",
        "rd' = sqrt(0.736**2 + 0.012**2 * 60) = ",
        "0.7418463",
        7,
        lambda w: _rd_inflated(w.second, w.after_second["a1"][1]),
    ),
    (
        "second K (prose)",
        "between",
        "K = 0.12 + 0.48 * (rd' - 0.10) / 0.70 = ",
        "0.56012",
        5,
        _k_second,
    ),
    (
        "second weight (prose)",
        "between",
        "0.90 * 1.0 * min(1.30, 1.00 * (1 + 0.12 * 2)) = ",
        "1.116",
        5,
        lambda w: w.second.effective_weight,
    ),
    ("second R_A", "second", "R_A = ", "3.56667", 5, lambda w: w.after_first["a1"][0]),
    ("second R_B", "second", "R_B = ", "3.43333", 5, lambda w: w.after_first["b1"][0]),
    (
        "second gap",
        "second",
        "E = 1 / (1 + 10**(",
        "-0.13333",
        5,
        lambda w: w.after_first["b1"][0] - w.after_first["a1"][0],
    ),
    ("second E", "second", "/ 2.9)) = ", "0.52644", 5, lambda w: w.second.expected_a),
    ("second P", "second", "P = 30 / 56 = ", "0.53571", 5, lambda w: w.second.actual_a),
    ("second surprise", "second", "surprise = ", "0.00927", 5, lambda w: w.second.surprise),
    (
        "second rd'",
        "second",
        "rd' = ",
        "0.7418463",
        7,
        lambda w: _rd_inflated(w.second, w.after_second["a1"][1]),
    ),
    ("second K", "second", "K = ", "0.56012", 5, _k_second),
    ("second weight", "second", "weight = ", "1.116", 5, lambda w: w.second.effective_weight),
    (
        "second delta A",
        "second",
        "team A delta = +0.56012 * 1.116 * 0.00927 = ",
        "+0.00580",
        5,
        lambda w: w.second.deltas["a1"],
    ),
    ("second delta B", "second", "team B delta = ", "-0.00580", 5, lambda w: w.second.deltas["b1"]),
    (
        "second rd_new",
        "second",
        "rd_new = max(0.10, 0.7418463 * (1 - 0.08 * 1.116)) = ",
        "0.67561",
        5,
        lambda w: w.after_second["a1"][1],
    ),
    ("second imbalance", "second", "imbalance = ", "0.0", 5, lambda w: w.second.imbalance),
    (
        "second after A",
        "second",
        "team A after = ",
        "3.57246",
        5,
        lambda w: w.after_second["a1"][0],
    ),
    (
        "second after B",
        "second",
        "team B after = ",
        "3.42754",
        5,
        lambda w: w.after_second["b1"][0],
    ),
    (
        "second E as a percent",
        "after",
        "the ",
        "52.6",
        1,
        lambda w: 100 * w.second.expected_a,
    ),
]


@pytest.mark.parametrize(
    ("label", "part", "context", "stated", "places", "getter"),
    WORKED_FIGURES,
    ids=[row[0] for row in WORKED_FIGURES],
)
def test_worked_example_figure_matches_engine_and_doc(
    worked: WorkedExample,
    label: str,
    part: str,
    context: str,
    stated: str,
    places: int,
    getter: Getter,
) -> None:
    assert round(getter(worked), places) == float(stated), label
    assert _occurrences(_part(part), context, stated) == 1, label


def test_worked_example_states_second_point_totals() -> None:
    points_a = sum(a for a, _ in SECOND_GAMES)
    points_all = sum(a + b for a, b in SECOND_GAMES)
    assert (points_a, points_all) == (30, 56)
    assert _occurrences(_part("second"), "P = ", "30 / 56") == 1
    assert _occurrences(_part("after"), "Team A won ", "30 of the 56 points") == 1


def _games_text(match: Match) -> str:
    return ", ".join(f"{a}-{b}" for a, b in match.games)


def test_worked_example_states_the_engine_inputs() -> None:
    first, second = FIRST_MATCH, SECOND_MATCH
    # One game, so "one ... game" in the doc is the first match's best_of.
    assert first.best_of == 1
    assert len(first.games) == 1
    first_text = (
        f"one {first.match_type.value} game of {SCORING_WORDS[first.game_format.scoring]}"
        f" scoring to {first.game_format.points_to}, won {_games_text(first)} by team A."
    )
    assert _occurrences(_part("intro"), "", first_text) == 1
    start_text = f"starts at rating {CFG.default_rating} and RD {CFG.new_player_rd:.2f},"
    assert _occurrences(_part("intro"), "", start_text) == 1
    days = (second.played_on - first.played_on).days
    assert second.game_format == first.game_format
    second_text = (
        f"{DAY_WORDS[days]} days later the same four players meet in a"
        f" {second.match_type.value} best of {second.best_of} ({_games_text(second)})."
    )
    assert _occurrences(_part("between"), "", second_text) == 1


@pytest.mark.parametrize("after", ["after_first", "after_second"])
def test_teammates_move_together(worked: WorkedExample, after: str) -> None:
    # The doc states one figure per team, so both players on a team must have it.
    states = getattr(worked, after)
    for team in (TEAM_A, TEAM_B):
        assert states[team[0]] == states[team[1]]
    result = worked.first if after == "after_first" else worked.second
    for team in (TEAM_A, TEAM_B):
        assert result.deltas[team[0]] == result.deltas[team[1]]
    assert result.deltas[TEAM_A[0]] == -result.deltas[TEAM_B[0]]


WIN_FORMAT = GameFormat()
WIN_BEST_OF = 1


def test_win_probability_example_states_its_inputs() -> None:
    fmt = WIN_FORMAT
    assert fmt.cap is None
    text = (
        f"Under the default config (scale {CFG.scale_by_scoring[fmt.scoring]},"
        f" `a = {CFG.win_calibration_by_scoring[fmt.scoring]}`), take one"
        f" {SCORING_WORDS[fmt.scoring]} game to {fmt.points_to}, win by {fmt.win_by},"
        f" with no cap (`GameFormat()`), best of {WIN_BEST_OF}."
    )
    assert _occurrences(_part("win probability"), "", text) == 1


def _seeded_singles(gap: float) -> RatingEngine:
    engine = RatingEngine(CFG)
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
    return engine


# (gap, the doc's sentence with {gap}, {share}, and {win} for the figures,
# point share, win probability), each figure a percent as the doc states it.
GAP_FIGURES = [
    (
        0.5,
        "A team rating gap of {gap} gives the higher team an expected point share"
        " of about {share}% and about {win}% to win the game.",
        "59.8",
        "83.1",
    ),
    (
        1.0,
        "A gap of {gap} gives about {share}% of the points and about {win}% to win.",
        "68.9",
        "97.1",
    ),
]


@pytest.mark.parametrize(
    ("gap", "sentence", "share", "win"), GAP_FIGURES, ids=["gap 0.5", "gap 1.0"]
)
def test_gap_win_probability_matches_engine_and_doc(
    gap: float, sentence: str, share: str, win: str
) -> None:
    engine = _seeded_singles(gap)
    point_share = engine.predict_point_share(
        ("p1",), ("p2",), RatingFormat.SINGLES, WIN_FORMAT.scoring
    )
    probability = engine.predict_win_probability(
        ("p1",), ("p2",), RatingFormat.SINGLES, WIN_FORMAT, best_of=WIN_BEST_OF
    )
    assert round(100 * point_share, 1) == float(share)
    assert round(100 * probability, 1) == float(win)
    stated = sentence.format(gap=gap, share=share, win=win)
    assert _occurrences(_part("win probability"), "", stated) == 1
