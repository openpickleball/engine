"""Tests for the OPEN rating engine. Run with: python -m pytest -q"""

import random
from datetime import date, datetime, timedelta

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
    fit_scale,
    inflated_rd,
    ledger_order,
    replay,
)

D = RatingFormat.DOUBLES
SNAP_DATE = date(2026, 10, 1)


def snapshot(rating=4.0, reliability=85, captured_on=SNAP_DATE, ext=None):
    return ExternalRatingSnapshot("external_standard", rating, captured_on, reliability, ext)


def match(mid, a, b, games=((11, 7),), on=date(2026, 10, 29), fmt=D, **kw):
    best_of = kw.pop("best_of", 1 if len(games) == 1 else 3)
    return Match(mid, a, b, games, on, fmt, best_of=best_of, **kw)


# 1. Imported RD inflates from the snapshot date ------------------------------


def test_old_import_inflates_before_first_match():
    eng = RatingEngine()
    old = eng.seed_external_rating("old", D, snapshot(captured_on=date(2024, 10, 1)))
    fresh = eng.seed_external_rating("fresh", D, snapshot())
    on = date(2026, 10, 29)
    assert inflated_rd(old, on, eng.cfg) > inflated_rd(fresh, on, eng.cfg) + 0.1
    r = eng.record(match("m1", ("old", "p1"), ("fresh", "p2")))
    assert abs(r.deltas["old"]) > abs(r.deltas["fresh"])


# 2. Imported reliability is shown separately ----------------------------------


def test_import_reliability_carried_and_labeled():
    eng = RatingEngine()
    eng.seed_external_rating("a", D, snapshot(reliability=85))
    s = eng.player_summary("a", D, as_of=date(2026, 10, 2))
    assert s["reliability"] == 85 and s["reliability_basis"] == "imported"
    assert s["open_reliability"] < 40


def test_import_reliability_expires_with_age():
    eng = RatingEngine()
    eng.seed_external_rating("a", D, snapshot(reliability=85))
    s = eng.player_summary("a", D, as_of=date(2028, 1, 1))
    assert s["reliability_basis"] == "open"


def test_import_reliability_expires_after_carry_matches():
    eng = RatingEngine(Config(import_reliability_carry_matches=2))
    eng.seed_external_rating("a", D, snapshot(reliability=85))
    for i in range(2):
        eng.record(match(f"m{i}", ("a", f"x{i}"), (f"y{i}", f"z{i}"), on=date(2026, 10, 10 + i)))
    assert eng.player_summary("a", D)["reliability_basis"] == "open"


# 3. Scale calibration ----------------------------------------------------------


def simulate_ledger(true_scale=3.6, n_players=40, n_matches=2500, seed=7):
    rng = random.Random(seed)
    true = {f"p{i}": rng.uniform(2.8, 5.2) for i in range(n_players)}
    fmt = GameFormat(ScoringType.RALLY, points_to=11)
    start = date(2025, 1, 1)
    ledger = []
    for i in range(n_matches):
        ids = rng.sample(list(true), 4)
        ra = (true[ids[0]] + true[ids[1]]) / 2
        rb = (true[ids[2]] + true[ids[3]]) / 2
        p = 1 / (1 + 10 ** ((rb - ra) / true_scale))
        a = b = 0
        while not ((a >= 11 or b >= 11) and abs(a - b) >= 2):
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
                start + timedelta(days=i // 10),
                D,
                match_type=MatchType.LEAGUE,
                game_format=fmt,
                sequence=i % 10,
            )
        )
    return ledger


def test_fit_scale_improves_on_default():
    fit = fit_scale(simulate_ledger(), scoring=ScoringType.RALLY)
    assert fit.fitted.mse <= fit.at_default.mse
    assert 1.0 <= fit.scale <= 8.0


# 4. Game format recorded and validated ------------------------------------------


@pytest.mark.parametrize(
    "score,ok",
    [
        ((11, 9), True),
        ((12, 10), True),
        ((13, 10), False),
        ((10, 8), False),
        ((11, 10), False),
    ],
)
def test_game_format_validation(score, ok):
    m = match("g", ("a", "b"), ("c", "d"), games=(score,))
    if ok:
        m.validate()
    else:
        with pytest.raises(ValueError):
            m.validate()


def test_disallowed_format_not_counted():
    cfg = Config(allowed_game_formats=frozenset({(ScoringType.SIDE_OUT, 11)}))
    eng = RatingEngine(cfg)
    m = match(
        "g",
        ("a", "b"),
        ("c", "d"),
        games=((21, 15),),
        game_format=GameFormat(ScoringType.RALLY, 21),
    )
    assert not eng.record(m).counted


def test_longer_games_carry_more_weight():
    eng = RatingEngine()
    r11 = eng.record(match("a", ("a1", "a2"), ("a3", "a4"), games=((11, 7),)))
    r21 = eng.record(
        match(
            "b", ("b1", "b2"), ("b3", "b4"), games=((21, 14),), game_format=GameFormat(points_to=21)
        )
    )
    assert r21.effective_weight > r11.effective_weight


# 5. Self-reported safeguards ----------------------------------------------------


def self_reported(mid, on, confirmed=frozenset({"a", "c"})):
    return match(
        mid,
        ("a", "b"),
        ("c", "d"),
        on=on,
        match_type=MatchType.SELF_REPORTED,
        confirmed_by=confirmed,
    )


def test_self_reported_needs_both_teams():
    eng = RatingEngine()
    r = eng.record(self_reported("m", date(2026, 10, 1), frozenset({"a", "b"})))
    assert not r.counted and "both teams" in r.reason


def test_self_reported_repeats_decay_then_stop():
    eng = RatingEngine()
    weights = []
    for i in range(5):
        r = eng.record(self_reported(f"m{i}", date(2026, 10, 1 + i)))
        weights.append(r.effective_weight if r.counted else None)
    assert weights[1] == pytest.approx(weights[0] * 0.5, rel=1e-3)
    assert weights[4] is None


# 6. Live updates are labeled until recalculation ---------------------------------


def test_live_updates_labeled_and_replay_resets():
    m = match("m", ("a", "b"), ("c", "d"))
    eng = RatingEngine()
    eng.record(m)
    assert eng.player_summary("a", D)["live_updates_since_recalculation"] == 1
    rebuilt = replay([m], as_of=date(2026, 11, 1))
    s = rebuilt.player_summary("a", D)
    assert s["live_updates_since_recalculation"] == 0
    assert s["calculated_as_of"] == "2026-11-01"
    assert rebuilt.replay_info.config_fingerprint == Config().fingerprint()


# 7. Same-day ordering follows real play ------------------------------------------


def test_same_day_order_uses_time_not_id():
    day = date(2026, 10, 29)
    late = match("aaa", ("a", "b"), ("c", "d"), on=day, played_at=datetime(2026, 10, 29, 20))
    early = match("zzz", ("a", "b"), ("c", "d"), on=day, played_at=datetime(2026, 10, 29, 18))
    assert sorted([late, early], key=ledger_order) == [early, late]


# 8. Baseline freezes only the seeded player --------------------------------------


def test_baseline_freezes_only_seeded_player():
    eng = RatingEngine()
    eng.seed_external_rating("seeded", D, snapshot())
    r = eng.record(match("m", ("seeded", "p1"), ("p2", "p3"), on=date(2026, 9, 15)))
    assert r.counted and r.frozen == ("seeded",)
    assert r.deltas["seeded"] == 0.0 and r.deltas["p1"] != 0.0
    assert eng.state("seeded", D).matches_played == 0


def test_all_frozen_not_counted():
    eng = RatingEngine()
    for pid in "abcd":
        eng.seed_external_rating(pid, D, snapshot())
    assert not eng.record(match("m", ("a", "b"), ("c", "d"), on=date(2026, 9, 1))).counted


# 9. Category matches feed overall doubles -----------------------------------------


def test_mixed_updates_mixed_and_doubles():
    eng = RatingEngine()
    eng.seed_external_rating("a", D, snapshot(rating=4.2))
    r = eng.record(match("m", ("a", "b"), ("c", "d"), fmt=RatingFormat.MIXED_DOUBLES))
    assert r.parent is not None and r.parent.counted
    assert eng.state("a", RatingFormat.MIXED_DOUBLES).initial_source == "open_parent:doubles"
    assert eng.state("a", RatingFormat.MIXED_DOUBLES).matches_played == 1
    assert eng.state("a", D).matches_played == 1


def test_category_feed_can_be_disabled():
    eng = RatingEngine(Config(category_feeds_parent=False))
    r = eng.record(match("m", ("a", "b"), ("c", "d"), fmt=RatingFormat.MIXED_DOUBLES))
    assert r.parent is None
    assert D not in eng.player("a").ratings


# 10. Clamps are reported -----------------------------------------------------------


def test_clamp_reported():
    eng = RatingEngine()
    for pid in ("a", "b"):
        eng.seed_external_rating(pid, D, snapshot(rating=2.0, captured_on=date(2026, 1, 1)))
    r = eng.record(match("m", ("a", "b"), ("c", "d"), games=((0, 11),)))
    assert set(r.clamped) == {"a", "b"}


# General ------------------------------------------------------------------------


def test_zero_sum_without_clamp_or_freeze():
    eng = RatingEngine()
    r = eng.record(match("m", ("a", "b"), ("c", "d"), games=((11, 4),)))
    assert sum(r.deltas.values()) == pytest.approx(0.0, abs=1e-4)


def test_replay_is_deterministic():
    ledger = simulate_ledger(n_matches=300)
    one = replay(ledger, as_of=date(2026, 1, 1))
    two = replay(list(reversed(ledger)), as_of=date(2026, 1, 1))
    for pid in one.players:
        assert one.state(pid, D).rating == two.state(pid, D).rating


def test_rejected_match_changes_nothing():
    eng = RatingEngine(Config(max_doubles_partner_gap=0.5))
    eng.seed_external_rating("a", D, snapshot(rating=5.0))
    before = eng.state("a", D).rd
    assert not eng.record(match("m", ("a", "b"), ("c", "d"))).counted
    assert eng.state("a", D).rd == before and "b" not in eng.players
