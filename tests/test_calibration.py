"""Tests for prequential predictions, evaluation modes and the scale fit."""

import math
import random
from datetime import date, timedelta

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    GameFormat,
    Match,
    MatchType,
    RatingEngine,
    RatingFormat,
    ScoringType,
    evaluate,
    fit_scale,
    prequential_predictions,
    replay,
)

D = RatingFormat.DOUBLES
RALLY_11 = GameFormat(ScoringType.RALLY, points_to=11)
START = date(2025, 1, 6)


def simulate_league_with_truth(
    true_scale: float = 2.0,
    n_players: int = 24,
    n_matches: int = 1200,
    per_day: int = 12,
    seed: int = 11,
    game_format: GameFormat = RALLY_11,
) -> tuple[list[Match], dict[str, float]]:
    """Synthetic doubles league: per-point win probability from true ratings.

    Returns the ledger and the true rating of every player.
    """
    rng = random.Random(seed)
    true = {f"p{i:02d}": rng.uniform(2.8, 5.2) for i in range(n_players)}
    ids_pool = sorted(true)
    target, win_by = game_format.points_to, game_format.win_by
    ledger: list[Match] = []
    for i in range(n_matches):
        ids = rng.sample(ids_pool, 4)
        ra = (true[ids[0]] + true[ids[1]]) / 2
        rb = (true[ids[2]] + true[ids[3]]) / 2
        p = 1 / (1 + 10 ** ((rb - ra) / true_scale))
        a = b = 0
        while not ((a >= target or b >= target) and abs(a - b) >= win_by):
            if rng.random() < p:
                a += 1
            else:
                b += 1
        ledger.append(
            Match(
                f"s{i:05d}",
                tuple(ids[:2]),
                tuple(ids[2:]),
                ((a, b),),
                START + timedelta(days=i // per_day),
                D,
                match_type=MatchType.LEAGUE,
                game_format=game_format,
                sequence=i % per_day,
            )
        )
    return ledger, true


def simulate_league(**kwargs: object) -> list[Match]:
    return simulate_league_with_truth(**kwargs)[0]


def true_rating_seeds(true: dict[str, float]) -> list:
    """Imported baselines at the given true ratings."""
    eng = RatingEngine()
    for pid, rating in true.items():
        snap = ExternalRatingSnapshot("external_standard", rating, date(2024, 12, 1), 80)
        eng.seed_external_rating(pid, D, snap)
    return list(eng.players.values())


def test_live_default_matches_old_evaluate():
    report = evaluate(simulate_league())
    # Frozen from evaluate() at commit 02063b9, before prequential_predictions.
    assert report.matches_scored == 1159
    assert report.mse == 0.01829
    assert report.bias == 0.002047


def test_unsupported_win_by_still_scores():
    # Win probability is only modeled for win_by 1 or 2; other formats still
    # count and score on point share, with nan win probabilities.
    ledger = simulate_league(
        n_matches=300, game_format=GameFormat(ScoringType.RALLY, points_to=11, win_by=3)
    )
    report = evaluate(ledger)
    # Frozen from evaluate() at commit 02063b9.
    assert report.matches_scored == 259
    assert report.mse == 0.023569
    assert report.bias == 0.000251
    preds = prequential_predictions(ledger)
    assert len(preds) == report.matches_scored
    assert all(math.isnan(p.win_probability_raw) for p in preds)
    assert all(math.isnan(p.win_probability) for p in preds)
    assert all(0 < p.expected < 1 for p in preds)


def _seeds(ids: list[str], ratings: list[float]) -> list:
    eng = RatingEngine()
    for pid, rating in zip(ids, ratings, strict=True):
        snap = ExternalRatingSnapshot("external_standard", rating, date(2024, 12, 1), 80)
        eng.seed_external_rating(pid, D, snap)
    return list(eng.players.values())


def test_scheduled_never_sees_own_day():
    anchor = date(2025, 3, 3)
    seeds = _seeds(["a", "b", "c", "d"], [4.0, 4.0, 4.0, 4.0])
    first = Match("m0", ("a", "b"), ("c", "d"), ((11, 0),), anchor, D, game_format=RALLY_11)
    second = Match(
        "m1",
        ("a", "b"),
        ("c", "d"),
        ((11, 1),),
        anchor + timedelta(days=7),
        D,
        game_format=RALLY_11,
    )
    ledger = [first, second]
    preds = prequential_predictions(ledger, seeds, mode="scheduled", anchor=anchor)
    by_id = {p.match_id: p for p in preds}
    assert set(by_id) == {"m0", "m1"}

    teams = (("a", "b"), ("c", "d"), D, ScoringType.RALLY)
    before_first = replay([], seeds, as_of=anchor - timedelta(days=7)).predict(*teams)
    with_first = replay([first], seeds, as_of=anchor).predict(*teams)
    with_both = replay(ledger, seeds, as_of=second.played_on).predict(*teams)

    assert by_id["m0"].expected == before_first
    assert by_id["m0"].expected != with_first
    assert by_id["m1"].expected == with_first
    assert by_id["m1"].expected != with_both


def test_score_from_excludes_early_matches():
    ledger = simulate_league(n_matches=600)
    cutoff = ledger[len(ledger) // 2].played_on
    everything = prequential_predictions(ledger)
    for mode in ("live", "scheduled"):
        late = prequential_predictions(ledger, mode=mode, score_from=cutoff)
        assert late
        assert all(p.played_on >= cutoff for p in late)
        assert len(late) < len(everything)


def test_prediction_fields():
    ledger = simulate_league(n_matches=400)
    by_id = {m.match_id: m for m in ledger}
    preds = prequential_predictions(ledger)
    assert preds
    for p in preds:
        m = by_id[p.match_id]
        points_a = sum(a for a, _ in m.games)
        total = sum(a + b for a, b in m.games)
        assert 0 <= p.win_probability_raw <= 1
        assert p.win_probability == pytest.approx(p.win_probability_raw, abs=1e-12)
        assert p.actual == points_a / total
        assert p.team_a_won == (m.games[0][0] > m.games[0][1])
        assert p.players == m.team_a + m.team_b


def test_win_probability_uses_calibration():
    ledger = simulate_league(n_matches=400)
    cfg = Config(win_calibration_by_scoring={ScoringType.SIDE_OUT: 1.0, ScoringType.RALLY: 2.0})
    preds = prequential_predictions(ledger, cfg=cfg)
    sharper = [p for p in preds if abs(p.win_probability_raw - 0.5) > 0.05]
    assert sharper
    assert all(abs(p.win_probability - 0.5) > abs(p.win_probability_raw - 0.5) for p in sharper)


def test_unknown_mode_rejected():
    with pytest.raises(ValueError, match="mode"):
        prequential_predictions(simulate_league(n_matches=50), mode="weekly")


def test_fit_scale_profile_and_bound():
    # Seeding at the true ratings keeps the fit from absorbing the early
    # compression of unrated players toward the default rating.
    ledger, true = simulate_league_with_truth()
    seeds = true_rating_seeds(true)
    fit = fit_scale(ledger, seeds, scoring=ScoringType.RALLY)
    scales = [s for s, _ in fit.profile]
    assert scales == sorted(scales)
    assert fit.scale in scales
    assert all(isinstance(s, float) and isinstance(m, float) for s, m in fit.profile)
    assert not fit.at_bound
    assert 1.5 <= fit.scale <= 2.6

    bounded = fit_scale(ledger, seeds, scoring=ScoringType.RALLY, low=2.5, high=8.0)
    assert bounded.at_bound


def test_fit_scale_accepts_scheduled_mode():
    ledger = simulate_league(n_matches=480)
    midpoint = ledger[len(ledger) // 2].played_on
    fit = fit_scale(ledger, scoring=ScoringType.RALLY, mode="scheduled", score_from=midpoint)
    assert fit.fitted.matches_scored > 0
