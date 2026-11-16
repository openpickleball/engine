"""Replay and live recording use one update rule (issue #13).

A replay must give the same ratings as recording the same ledger live, in
ledger order. The replay date only labels the result; it never shrinks or
discounts an old match's update. All player ids are synthetic.
"""

from __future__ import annotations

import dataclasses
import random
from datetime import date, timedelta

import pytest

import open_engine.engine as engine_module
from open_engine import (
    Config,
    Match,
    RatingEngine,
    RatingFormat,
    evaluate,
    ledger_order,
    prequential_predictions,
    replay,
)

D = RatingFormat.DOUBLES
S = RatingFormat.SINGLES
MX = RatingFormat.MIXED_DOUBLES
START = date(2023, 1, 1)

COMPARED_FIELDS = (
    "rating",
    "rd",
    "matches_played",
    "last_played",
    "rd_as_of",
    "partners",
    "opponents",
)


def _score(rng: random.Random) -> tuple[tuple[int, int], ...]:
    loser = rng.randint(0, 9)
    return ((11, loser),) if rng.random() < 0.5 else ((loser, 11),)


def multi_year_ledger(seed: int = 13, count: int = 130) -> list[Match]:
    """About three years of synthetic doubles, singles and mixed doubles."""
    rng = random.Random(seed)
    pool = [f"syn-p{i:02d}" for i in range(16)]
    matches: list[Match] = []
    for i in range(count):
        on = START + timedelta(days=8 * i)
        kind = (D, S, MX)[i % 3]
        size = kind.team_size
        ids = rng.sample(pool, 2 * size)
        matches.append(
            Match(
                f"syn-m{i:03d}",
                tuple(ids[:size]),
                tuple(ids[size:]),
                _score(rng),
                on,
                kind,
            )
        )
    # Shuffle so the tests also cover the ledger's own ordering.
    rng.shuffle(matches)
    return matches


def record_live(matches: list[Match], cfg: Config | None = None) -> RatingEngine:
    engine = RatingEngine(cfg)
    for match in sorted(matches, key=ledger_order):
        engine.record(match)
    return engine


def _states(engine: RatingEngine) -> dict[tuple[str, RatingFormat], engine_module.RatingState]:
    return {
        (pid, fmt): state
        for pid, player in engine.players.items()
        for fmt, state in player.ratings.items()
    }


def test_replay_matches_live_recording() -> None:
    ledger = multi_year_ledger()
    last = max(m.played_on for m in ledger)
    live = record_live(ledger)
    replayed = replay(ledger, as_of=last + timedelta(days=365))

    live_states = _states(live)
    replay_states = _states(replayed)
    assert set(live_states) == set(replay_states)
    assert any(fmt is MX for _, fmt in live_states)
    assert any(fmt is D for _, fmt in live_states)
    assert any(fmt is S for _, fmt in live_states)
    for key, live_state in live_states.items():
        replay_state = replay_states[key]
        for name in COMPARED_FIELDS:
            assert getattr(replay_state, name) == getattr(live_state, name), (key, name)
        if live_state.matches_played:
            assert live_state.live_updates_since_replay > 0
        assert replay_state.live_updates_since_replay == 0


def test_old_match_moves_ratings_as_much_in_replay() -> None:
    played_on = date(2023, 11, 1)
    old = Match("syn-old", ("syn-a1", "syn-a2"), ("syn-b1", "syn-b2"), ((11, 2),), played_on, D)
    default = Config().default_rating

    live = record_live([old])
    replayed = replay([old], as_of=date(2026, 11, 1))
    for pid in ("syn-a1", "syn-a2"):
        live_state = live.players[pid].ratings[D]
        replay_state = replayed.players[pid].ratings[D]
        assert round(live_state.rating - default, 5) == pytest.approx(0.15577)
        assert round(replay_state.rating - default, 5) == pytest.approx(0.15577)
        assert replay_state.rating == live_state.rating
        assert round(replay_state.rd, 4) == pytest.approx(0.752, abs=5e-4)
        assert replay_state.rd == live_state.rd


def test_player_with_only_old_matches_keeps_their_rating() -> None:
    rng = random.Random(7)
    others = [f"syn-o{i:02d}" for i in range(12)]
    ledger: list[Match] = []
    # syn-early wins every match in the first year only.
    for i in range(20):
        partner, *opponents = rng.sample(others, 3)
        ledger.append(
            Match(
                f"syn-e{i:02d}",
                ("syn-early", partner),
                tuple(opponents),
                ((11, rng.randint(0, 5)),),
                START + timedelta(days=15 * i),
                D,
            )
        )
    # Everyone else keeps playing for two more years.
    for i in range(60):
        ids = rng.sample(others, 4)
        ledger.append(
            Match(
                f"syn-l{i:02d}",
                tuple(ids[:2]),
                tuple(ids[2:]),
                _score(rng),
                date(2024, 1, 1) + timedelta(days=12 * i),
                D,
            )
        )
    last = max(m.played_on for m in ledger)
    live = record_live(ledger)
    replayed = replay(ledger, as_of=last + timedelta(days=180))

    live_rating = live.players["syn-early"].ratings[D].rating
    replay_rating = replayed.players["syn-early"].ratings[D].rating
    assert replay_rating == live_rating
    assert replay_rating >= Config().default_rating + 0.3


def test_replay_does_not_depend_on_as_of() -> None:
    ledger = multi_year_ledger()
    last = max(m.played_on for m in ledger)
    baseline = _states(replay(ledger, as_of=last))
    for offset in (1, 7, 730):
        other = _states(replay(ledger, as_of=last + timedelta(days=offset)))
        assert set(other) == set(baseline)
        for key, state in baseline.items():
            assert other[key].rating == state.rating, (offset, key)
            assert other[key].rd == state.rd, (offset, key)


def test_live_and_scheduled_evaluation_agree() -> None:
    ledger = multi_year_ledger()
    live_report = evaluate(ledger, mode="live")
    scheduled_report = evaluate(ledger, mode="scheduled")
    assert live_report.matches_scored > 0
    assert live_report == scheduled_report

    live_predictions = prequential_predictions(ledger, mode="live")
    scheduled_predictions = prequential_predictions(ledger, mode="scheduled")
    assert [p.match_id for p in live_predictions] == [p.match_id for p in scheduled_predictions]
    assert [p.expected for p in live_predictions] == [p.expected for p in scheduled_predictions]


def test_recency_option_is_gone() -> None:
    assert "recency_half_life_days" not in {f.name for f in dataclasses.fields(Config)}
    with pytest.raises(TypeError):
        Config(recency_half_life_days=365)  # type: ignore[call-arg]
    assert not hasattr(engine_module, "recency_multiplier")
