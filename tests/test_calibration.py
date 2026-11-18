"""Tests for prequential predictions, evaluation modes and the scale fit."""

import math
import random
from datetime import date, timedelta

import pytest

import open_engine.engine as engine_module
from open_engine import (
    Config,
    EvaluationError,
    ExternalRatingSnapshot,
    GameFormat,
    Match,
    MatchType,
    PrequentialPrediction,
    RatingEngine,
    RatingFormat,
    ScoringType,
    calibrate_win_probability,
    evaluate,
    fit_scale,
    fit_win_calibration,
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
    # mse and bias re-frozen when each player moved by their own K (issue #14).
    assert report.matches_scored == 1159
    assert report.mse == 0.018292
    assert report.bias == 0.002051


def test_unsupported_win_by_still_scores():
    # Win probability is only modeled for win_by 1 or 2; other formats still
    # count and score on point share, with nan win probabilities.
    ledger = simulate_league(
        n_matches=300, game_format=GameFormat(ScoringType.RALLY, points_to=11, win_by=3)
    )
    report = evaluate(ledger)
    # Frozen from evaluate() at commit 02063b9. mse and bias re-frozen when
    # each player moved by their own K (issue #14).
    assert report.matches_scored == 259
    assert report.mse == 0.023467
    assert report.bias == 0.00029
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
    before_first = replay([], seeds, as_of=anchor - timedelta(days=7)).predict_point_share(*teams)
    with_first = replay([first], seeds, as_of=anchor).predict_point_share(*teams)
    with_both = replay(ledger, seeds, as_of=second.played_on).predict_point_share(*teams)

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


def _synthetic_predictions(raws: list[float], wins: list[bool]) -> list[PrequentialPrediction]:
    return [
        PrequentialPrediction(
            match_id=f"m{i}",
            played_on=START,
            players=("a", "b", "c", "d"),
            expected=0.5,
            actual=0.5,
            team_a_won=won,
            win_probability_raw=raw,
            win_probability=raw,
        )
        for i, (raw, won) in enumerate(zip(raws, wins, strict=True))
    ]


def _patch_predictions(monkeypatch: pytest.MonkeyPatch, preds: list[PrequentialPrediction]) -> None:
    monkeypatch.setattr(engine_module, "prequential_predictions", lambda *a, **k: preds)


def test_fit_win_calibration_recovers_distortion(monkeypatch):
    rng = random.Random(7)
    raws = [rng.uniform(0.05, 0.95) for _ in range(4000)]
    wins = [rng.random() < calibrate_win_probability(r, 0.5) for r in raws]
    _patch_predictions(monkeypatch, _synthetic_predictions(raws, wins))
    fit = fit_win_calibration([])
    assert 0.4 <= fit.a <= 0.6
    assert fit.brier < fit.brier_uncalibrated
    assert fit.n == 4000
    assert not fit.at_bound
    assert fit.scoring == ScoringType.SIDE_OUT.value


def test_fit_win_calibration_degenerate_is_flagged(monkeypatch):
    rng = random.Random(3)
    raws = [rng.uniform(0.49, 0.51) for _ in range(30)]
    _patch_predictions(monkeypatch, _synthetic_predictions(raws, [True] * 30))
    fit = fit_win_calibration([])
    assert fit.at_bound
    assert min(abs(fit.a - 0.2), abs(fit.a - 3.0)) <= 0.001


def test_fit_win_calibration_skips_nan(monkeypatch):
    preds = _synthetic_predictions([0.6, math.nan, 0.4], [True, True, False])
    _patch_predictions(monkeypatch, preds)
    assert fit_win_calibration([]).n == 2


def test_fit_win_calibration_empty_raises(monkeypatch):
    _patch_predictions(monkeypatch, [])
    with pytest.raises(ValueError):
        fit_win_calibration([])
    _patch_predictions(monkeypatch, _synthetic_predictions([math.nan], [True]))
    with pytest.raises(ValueError):
        fit_win_calibration([])


def test_fit_scale_profile_is_unrounded():
    ledger, true = simulate_league_with_truth()
    fit = fit_scale(ledger, true_rating_seeds(true), scoring=ScoringType.RALLY)
    assert any(abs(m * 1e6 - round(m * 1e6)) > 1e-6 for _, m in fit.profile)
    # The reports stay rounded to 6 decimals.
    assert fit.fitted.mse == round(fit.fitted.mse, 6)


def test_fit_scale_picks_lower_unrounded_mse_when_rounded_values_tie(monkeypatch):
    # Every scale gives an MSE of about 0.01; the differences (under 1e-6) sit
    # below the 6-decimal rounding, with the true minimum at scale 5.0.
    n = 1000

    def fake_predictions(
        matches: object, seeds: object = (), *, cfg: Config, **kwargs: object
    ) -> list[PrequentialPrediction]:
        scale = cfg.scale_by_scoring[ScoringType.RALLY]
        shifted = int(100 * abs(scale - 5.0))
        return [
            PrequentialPrediction(
                match_id=str(i),
                played_on=START,
                players=("a", "b", "c", "d"),
                expected=0.49999 if i < shifted else 0.5,
                actual=0.6,
                team_a_won=True,
                win_probability_raw=math.nan,
                win_probability=math.nan,
            )
            for i in range(n)
        ]

    monkeypatch.setattr(engine_module, "prequential_predictions", fake_predictions)
    cfg = Config(win_weight=0.0)
    fit = fit_scale([], cfg=cfg, scoring=ScoringType.RALLY)
    assert abs(fit.scale - 5.0) <= 0.05
    assert not fit.at_bound


BAD_TOLERANCE = "tolerance must be finite and greater than 0"

SEARCH_ARGUMENT_CASES = [
    pytest.param({"low": 3.0, "high": 0.5}, "low must be less than high", id="low-above-high"),
    pytest.param({"low": 2.0, "high": 2.0}, "low must be less than high", id="low-equals-high"),
    pytest.param({"low": math.nan}, "low and high must be finite", id="low-nan"),
    pytest.param({"high": math.inf}, "low and high must be finite", id="high-inf"),
    pytest.param({"low": -1e308, "high": 1e308}, "must be finite", id="width-overflow"),
    pytest.param({"low": 1e308, "high": 1.7e308}, "must be finite", id="sum-overflow"),
    pytest.param({"low": -1.7e308, "high": -1.0}, "must be finite", id="double-low-overflow"),
    pytest.param({"low": 1.0, "high": 1.7e308}, "must be finite", id="double-high-overflow"),
    pytest.param({"low": 1.0, "high": 1.5e307}, "must be finite", id="grid-overflow"),
    pytest.param({"low": -math.inf}, "low and high must be finite", id="low-minus-inf"),
    pytest.param({"tolerance": 0.0}, BAD_TOLERANCE, id="tol-zero"),
    pytest.param({"tolerance": -1.0}, BAD_TOLERANCE, id="tol-neg"),
    pytest.param({"tolerance": math.nan}, BAD_TOLERANCE, id="tol-nan"),
    pytest.param({"tolerance": math.inf}, BAD_TOLERANCE, id="tol-inf"),
]


def _no_prediction_work(*args: object, **kwargs: object) -> list[PrequentialPrediction]:
    raise AssertionError("the search arguments must be checked before any prediction work")


@pytest.mark.parametrize(("kwargs", "message"), SEARCH_ARGUMENT_CASES)
def test_fit_scale_rejects_bad_search_arguments(
    monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, float], message: str
) -> None:
    # tolerance <= 0 could hang the golden-section loop, and low >= high
    # could return a fit from a meaningless search range.
    monkeypatch.setattr(engine_module, "prequential_predictions", _no_prediction_work)
    with pytest.raises(EvaluationError, match=message):
        fit_scale([], scoring=ScoringType.RALLY, **kwargs)


@pytest.mark.parametrize(("kwargs", "message"), SEARCH_ARGUMENT_CASES)
def test_fit_win_calibration_rejects_bad_search_arguments(
    monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, float], message: str
) -> None:
    monkeypatch.setattr(engine_module, "prequential_predictions", _no_prediction_work)
    with pytest.raises(EvaluationError, match=message):
        fit_win_calibration([], **kwargs)


def test_fit_scale_ends_when_tolerance_is_below_float_spacing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A positive tolerance below the float spacing near the optimum could
    # hang: the bracket can stop shrinking before it gets that narrow.
    monkeypatch.setattr(engine_module, "prequential_predictions", lambda *a, **k: [])

    def fake_report(predictions: object, cfg: Config, scoring: ScoringType) -> tuple[None, float]:
        return None, (cfg.scale_by_scoring[scoring] - 3.1234567) ** 2

    monkeypatch.setattr(engine_module, "_report_from_predictions", fake_report)
    fit = fit_scale([], scoring=ScoringType.RALLY, tolerance=1e-17)
    assert fit.scale == 3.123


def test_fit_win_calibration_ends_when_tolerance_is_below_float_spacing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_predictions(monkeypatch, _synthetic_predictions([0.6], [True]))
    monkeypatch.setattr(
        engine_module, "calibrate_win_probability", lambda raw, a: a - 1.3712345 + 1.0
    )
    fit = fit_win_calibration([], tolerance=1e-17)
    assert fit.a == 1.371
