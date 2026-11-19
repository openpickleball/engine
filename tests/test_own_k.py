"""Each player moves by their own K, and every result reports the imbalance (issue #14)."""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from helpers import store_state
from open_engine import (
    Config,
    ExternalRatingSnapshot,
    Match,
    MatchResult,
    RatingEngine,
    RatingFormat,
    RejectionReason,
)
from open_engine._math import _actual_point_share
from open_engine.engine import (
    expected_point_share,
    inflated_rd,
    information_multiplier,
    player_k,
)

S = RatingFormat.SINGLES
D = RatingFormat.DOUBLES
MIXED = RatingFormat.MIXED_DOUBLES
ON = date(2026, 10, 29)
SNAP_DATE = date(2026, 10, 1)
EST_RD = 0.42
WIN = ((11, 7),)
LOSS = ((7, 11),)
OPPONENT_RDS = [0.10, 0.25, 0.42, 0.60, 0.80]


def _snapshot() -> ExternalRatingSnapshot:
    return ExternalRatingSnapshot("external_standard", 4.0, SNAP_DATE, 85, None)


def _match(
    a: tuple[str, ...],
    b: tuple[str, ...],
    fmt: RatingFormat,
    games: tuple[tuple[int, int], ...] = WIN,
    on: date = ON,
) -> Match:
    return Match("m", a, b, games, on, fmt, best_of=1)


def _engine_with_rds(fmt: RatingFormat, rds: dict[str, float]) -> RatingEngine:
    """Every player at rating 4.0 with the given RD and no inactivity before ON."""
    eng = RatingEngine()
    for pid, rd in rds.items():
        state = store_state(eng, pid, fmt)
        state.rating = 4.0
        state.rd = rd
        state.rd_as_of = ON
    return eng


def _k_before(eng: RatingEngine, pid: str, fmt: RatingFormat, on: date = ON) -> float:
    state = eng.state(pid, fmt)
    inflated = dataclasses.replace(state, rd=inflated_rd(state, on, eng.cfg))
    return player_k(inflated, eng.cfg)


def _own_deltas(eng: RatingEngine, match: Match, movers: tuple[str, ...]) -> dict[str, float]:
    """Each mover's expected delta from their own K, built from unrounded engine pieces."""
    cfg = eng.cfg
    fmt = match.rating_format
    rating_a = sum(eng.state(p, fmt).rating for p in match.team_a) / len(match.team_a)
    rating_b = sum(eng.state(p, fmt).rating for p in match.team_b) / len(match.team_b)
    scale = cfg.scale_by_scoring[match.game_format.scoring]
    actual, _ = _actual_point_share(match, cfg)
    surprise = actual - expected_point_share(rating_a, rating_b, scale)
    # Repeat factor 1.0: these matches are not self-reported.
    weight = cfg.match_weights[match.match_type] * information_multiplier(match, cfg)
    deltas: dict[str, float] = {}
    for pid in movers:
        sign = 1.0 if pid in match.team_a else -1.0
        k = _k_before(eng, pid, fmt, match.played_on)
        deltas[pid] = round(sign * k * weight * surprise, 5)
    return deltas


def _imbalance(result: MatchResult, match: Match) -> float:
    team_a = sum(result.deltas[p] for p in match.team_a) / len(match.team_a)
    team_b = sum(result.deltas[p] for p in match.team_b) / len(match.team_b)
    return team_a + team_b


def _measure_engine(fmt: RatingFormat, ids: tuple[str, ...], seeded: set[str]) -> RatingEngine:
    """The setup used to measure the equal-K values on main (see the plan's Proof)."""
    eng = RatingEngine()
    for pid in sorted(seeded):
        eng.seed_external_rating(pid, fmt, _snapshot())
    for pid in ids:
        if pid not in seeded:
            store_state(eng, pid, fmt)
    for pid in ids:
        eng.player(pid).ratings[fmt].rating = 4.0
    return eng


# Criterion 1: a new opponent never makes an established player's step larger.


@pytest.mark.parametrize("games", [WIN, LOSS], ids=["win", "loss"])
@pytest.mark.parametrize("opponent_rd", OPPONENT_RDS)
def test_singles_established_step_not_larger_against_new_opponent(
    opponent_rd: float, games: tuple[tuple[int, int], ...]
) -> None:
    eng = _engine_with_rds(S, {"est": EST_RD, "opp": opponent_rd})
    varied = eng.record(_match(("est",), ("opp",), S, games))
    same = _engine_with_rds(S, {"est": EST_RD, "opp": EST_RD})
    baseline = same.record(_match(("est",), ("opp",), S, games))
    assert abs(varied.deltas["est"]) <= abs(baseline.deltas["est"]) + 1e-9


@pytest.mark.parametrize("games", [WIN, LOSS], ids=["win", "loss"])
@pytest.mark.parametrize("opponent_rd", OPPONENT_RDS)
def test_doubles_established_step_not_larger_against_new_opponent(
    opponent_rd: float, games: tuple[tuple[int, int], ...]
) -> None:
    rds = {"e1": EST_RD, "e2": EST_RD, "opp": opponent_rd, "e3": EST_RD}
    eng = _engine_with_rds(D, rds)
    varied = eng.record(_match(("e1", "e2"), ("opp", "e3"), D, games))
    same = _engine_with_rds(D, {**rds, "opp": EST_RD})
    baseline = same.record(_match(("e1", "e2"), ("opp", "e3"), D, games))
    for pid in ("e1", "e2", "e3"):
        assert abs(varied.deltas[pid]) <= abs(baseline.deltas[pid]) + 1e-9


# Criterion 2: each player moves by their own K.


@pytest.mark.parametrize(
    ("fmt", "team_a", "team_b", "seeded"),
    [
        pytest.param(S, ("est",), ("new",), {"est"}, id="singles"),
        pytest.param(D, ("e1", "e2"), ("n1", "e4"), {"e1", "e2", "e4"}, id="doubles"),
        pytest.param(
            D, ("e1", "n2"), ("e3", "e4"), {"e1", "e3", "e4"}, id="doubles-unequal-team-k"
        ),
    ],
)
def test_each_player_moves_by_own_k(
    fmt: RatingFormat, team_a: tuple[str, ...], team_b: tuple[str, ...], seeded: set[str]
) -> None:
    eng = _measure_engine(fmt, team_a + team_b, seeded)
    ks = {pid: _k_before(eng, pid, fmt) for pid in team_a + team_b}
    mean_a = sum(ks[p] for p in team_a) / len(team_a)
    mean_b = sum(ks[p] for p in team_b) / len(team_b)
    assert abs(mean_a - mean_b) > 0.01
    m = _match(team_a, team_b, fmt)
    expected = _own_deltas(eng, m, team_a + team_b)
    r = eng.record(m)
    assert r.deltas == expected


# Criterion 3: equal K gives the deltas measured on main.


@pytest.mark.parametrize(
    ("fmt", "team_a", "team_b", "seeded", "delta"),
    [
        pytest.param(S, ("est",), ("est2",), {"est", "est2"}, 0.02856, id="singles-est"),
        pytest.param(
            D, ("e1", "e2"), ("e3", "e4"), {"e1", "e2", "e3", "e4"}, 0.02856, id="doubles-est"
        ),
        pytest.param(D, ("n1", "n2"), ("n3", "n4"), set(), 0.05, id="doubles-new"),
    ],
)
def test_equal_k_deltas_unchanged(
    fmt: RatingFormat,
    team_a: tuple[str, ...],
    team_b: tuple[str, ...],
    seeded: set[str],
    delta: float,
) -> None:
    eng = _measure_engine(fmt, team_a + team_b, seeded)
    r = eng.record(_match(team_a, team_b, fmt))
    expected = {pid: delta for pid in team_a} | {pid: -delta for pid in team_b}
    assert r.deltas == expected


# Criterion 4: every result reports the imbalance.


def test_imbalance_zero_when_k_equal() -> None:
    eng = _measure_engine(D, ("e1", "e2", "e3", "e4"), {"e1", "e2", "e3", "e4"})
    r = eng.record(_match(("e1", "e2"), ("e3", "e4"), D))
    assert r.imbalance == 0.0


def test_imbalance_follows_higher_k_team() -> None:
    eng = _measure_engine(S, ("est", "new"), {"est"})
    m = _match(("est",), ("new",), S)
    r = eng.record(m)
    assert r.imbalance == pytest.approx(_imbalance(r, m), abs=1e-5)
    # The new player has the higher K and lost, so the net is negative.
    assert r.imbalance < 0.0
    assert abs(r.deltas["new"]) > abs(r.deltas["est"])


def test_imbalance_reports_a_clamp() -> None:
    cfg = Config()
    eng = _engine_with_rds(D, {"a": 0.8, "b": 0.8, "c": 0.8, "d": 0.8})
    for pid in ("a", "b"):
        eng.state(pid, D).rating = cfg.min_rating
    for pid in ("c", "d"):
        eng.state(pid, D).rating = cfg.min_rating + 0.5
    m = _match(("a", "b"), ("c", "d"), D, games=((0, 11),))
    r = eng.record(m)
    assert set(r.clamped) == {"a", "b"}
    assert r.imbalance == pytest.approx(_imbalance(r, m), abs=1e-5)
    assert r.imbalance > 0.0


def test_imbalance_zero_on_rejected_match() -> None:
    eng = RatingEngine(Config(max_doubles_partner_gap=0.5))
    eng.seed_external_rating("a", D, ExternalRatingSnapshot("external_standard", 5.0, SNAP_DATE))
    r = eng.record(_match(("a", "b"), ("c", "d"), D))
    assert r.reason is RejectionReason.DOUBLES_PARTNER_GAP
    assert r.imbalance == 0.0


def test_category_parent_has_its_own_imbalance() -> None:
    eng = RatingEngine()
    eng.seed_external_rating("a", D, _snapshot())
    m = _match(("a", "b"), ("c", "d"), MIXED)
    r = eng.record(m)
    assert r.parent is not None and r.parent.counted
    assert r.imbalance == pytest.approx(_imbalance(r, m), abs=1e-5)
    assert r.parent.imbalance == pytest.approx(_imbalance(r.parent, m), abs=1e-5)
    assert r.parent.imbalance != 0.0


# Criterion 5: a frozen player's teammate moves by their own K only.


def test_frozen_teammate_moves_by_own_k() -> None:
    eng = RatingEngine()
    eng.seed_external_rating("seeded", D, _snapshot())
    on = date(2026, 9, 15)
    for pid in ("p1", "p2", "p3"):
        eng.seed_provisional_rating(pid, D)
    m = _match(("seeded", "p1"), ("p2", "p3"), D, on=on)
    expected = _own_deltas(eng, m, ("p1", "p2", "p3"))
    r = eng.record(m)
    assert r.frozen == ("seeded",)
    assert r.deltas == {"seeded": 0.0, **expected}
    assert r.imbalance == pytest.approx(_imbalance(r, m), abs=1e-5)
    assert r.imbalance != 0.0
