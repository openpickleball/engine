"""Tests for the OPEN rating engine. Run with: python -m pytest -q"""

import random
import re
from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

import open_engine
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
    prequential_predictions,
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
    late = match(
        "aaa", ("a", "b"), ("c", "d"), on=day, played_at=datetime(2026, 10, 29, 20, tzinfo=UTC)
    )
    early = match(
        "zzz", ("a", "b"), ("c", "d"), on=day, played_at=datetime(2026, 10, 29, 18, tzinfo=UTC)
    )
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


# 11. GameFormat.validate_game edge cases --------------------------------------

STANDARD = GameFormat(points_to=11)
CAPPED = GameFormat(points_to=11, cap=15)
WIN_BY_3 = GameFormat(points_to=11, win_by=3)

VALID_GAMES = [
    pytest.param(STANDARD, 11, 9, id="standard-11-9"),
    pytest.param(STANDARD, 11, 0, id="standard-11-0"),
    pytest.param(STANDARD, 12, 10, id="standard-12-10"),
    pytest.param(STANDARD, 13, 11, id="standard-13-11"),
    pytest.param(CAPPED, 14, 12, id="capped-14-12"),
    pytest.param(CAPPED, 15, 13, id="capped-15-13"),
    pytest.param(CAPPED, 15, 14, id="capped-15-14"),
    pytest.param(WIN_BY_3, 12, 9, id="win_by_3-12-9"),
]

INVALID_GAMES = [
    pytest.param(STANDARD, 10, 8, "A game must reach its target score", id="standard-10-8"),
    pytest.param(STANDARD, 11, 10, "Game was not won by the required margin", id="standard-11-10"),
    pytest.param(
        STANDARD,
        14,
        11,
        "Extended game must end at exactly the win-by margin",
        id="standard-14-11",
    ),
    pytest.param(
        STANDARD,
        12,
        11,
        "Extended game must end at exactly the win-by margin",
        id="standard-12-11",
    ),
    pytest.param(CAPPED, 16, 14, "Game score exceeds the cap", id="capped-16-14"),
    pytest.param(CAPPED, 15, 12, "Capped game score is not reachable", id="capped-15-12"),
    pytest.param(CAPPED, 15, 11, "Capped game score is not reachable", id="capped-15-11"),
    pytest.param(WIN_BY_3, 11, 9, "Game was not won by the required margin", id="win_by_3-11-9"),
]

BOTH_ORDERS = pytest.mark.parametrize("swap", [False, True], ids=["a_wins", "b_wins"])


@BOTH_ORDERS
@pytest.mark.parametrize(("fmt", "winner", "loser"), VALID_GAMES)
def test_validate_game_accepts_possible_scores(
    fmt: GameFormat, winner: int, loser: int, swap: bool
) -> None:
    score_a, score_b = (loser, winner) if swap else (winner, loser)
    fmt.validate_game(score_a, score_b)


@BOTH_ORDERS
@pytest.mark.parametrize(("fmt", "winner", "loser", "message"), INVALID_GAMES)
def test_validate_game_rejects_impossible_scores(
    fmt: GameFormat, winner: int, loser: int, message: str, swap: bool
) -> None:
    score_a, score_b = (loser, winner) if swap else (winner, loser)
    with pytest.raises(ValueError, match=re.escape(message)):
        fmt.validate_game(score_a, score_b)


# 12. Ledger order, duplicate IDs, and time zones (issue 9) ----------------------

UTC = timezone.utc
EST = timezone(timedelta(hours=-5))
JST = timezone(timedelta(hours=9))
LEDGER_DAY = date(2026, 10, 29)
LEDGER_AS_OF = date(2026, 11, 30)


def replay_snapshot(engine: RatingEngine) -> tuple[object, ...]:
    states = tuple(
        (pid, fmt.value, state.rating, state.rd, state.matches_played)
        for pid in sorted(engine.players)
        for fmt, state in sorted(engine.player(pid).ratings.items(), key=lambda kv: kv[0].value)
    )
    return states, engine.replay_info


def mixed_ledger(seed: int = 11) -> list[Match]:
    """Synthetic ledger: doubles and singles, untimed and timed rows, three offsets."""
    rng = random.Random(seed)
    pool = [f"q{i}" for i in range(12)]
    zones = (UTC, EST, JST)
    rows: list[Match] = []
    for i in range(40):
        day = LEDGER_DAY + timedelta(days=i % 4)
        singles = i % 5 == 0
        ids = rng.sample(pool, 2 if singles else 4)
        half = len(ids) // 2
        played_at = None
        if i % 3:
            zone = zones[(i // 3) % 3]
            played_at = datetime(day.year, day.month, day.day, 8 + i % 12, tzinfo=zone)
        loser = rng.randint(0, 9)
        games = ((11, loser),) if i % 2 else ((loser, 11),)
        rows.append(
            match(
                f"x{i:02d}",
                tuple(ids[:half]),
                tuple(ids[half:]),
                games=games,
                on=day,
                fmt=RatingFormat.SINGLES if singles else D,
                played_at=played_at,
                sequence=i % 3,
            )
        )
    # Identical duplicate rows, as a platform might resend them.
    return rows + [rows[1], rows[7], rows[7], rows[20]]


def test_replay_is_permutation_invariant():
    ledger = mixed_ledger()
    unique = len({m.match_id for m in ledger})
    base = replay(ledger, as_of=LEDGER_AS_OF)
    expected = replay_snapshot(base)
    assert base.replay_info is not None
    assert base.replay_info.matches_considered == unique
    for seed in range(50):
        shuffled = list(ledger)
        random.Random(seed).shuffle(shuffled)
        assert replay_snapshot(replay(shuffled, as_of=LEDGER_AS_OF)) == expected


def dup_rows() -> tuple[Match, Match]:
    win = match("dup", ("a", "b"), ("c", "d"), games=((11, 0),))
    loss = match("dup", ("a", "b"), ("c", "d"), games=((0, 11),))
    return win, loss


@pytest.mark.parametrize("flip", [False, True], ids=["win_first", "loss_first"])
def test_conflicting_duplicate_ids_raise(flip: bool):
    win, loss = dup_rows()
    rows = [loss, win] if flip else [win, loss]
    with pytest.raises(open_engine.LedgerConflictError, match="dup") as info:
        replay(rows, as_of=LEDGER_AS_OF)
    assert isinstance(info.value, ValueError)
    assert info.value.match_ids == ("dup",)


def test_conflict_ids_sorted_whatever_the_input_order():
    win, loss = dup_rows()
    other = match("alpha", ("e", "f"), ("g", "h"), games=((11, 3),))
    other_changed = match("alpha", ("e", "f"), ("g", "h"), games=((11, 4),))
    rows = [win, other, loss, other_changed]
    seen = set()
    for order in (rows, list(reversed(rows))):
        with pytest.raises(open_engine.LedgerConflictError, match="alpha") as info:
            replay(order, as_of=LEDGER_AS_OF)
        seen.add(info.value.match_ids)
    assert seen == {("alpha", "dup")}


def test_conflicting_duplicate_after_as_of_raises():
    late = date(2026, 12, 15)
    first = match("later", ("a", "b"), ("c", "d"), games=((11, 2),), on=late)
    second = match("later", ("a", "b"), ("c", "d"), games=((2, 11),), on=late)
    with pytest.raises(open_engine.LedgerConflictError, match="later"):
        replay([first, second], as_of=LEDGER_AS_OF)


def test_identical_duplicates_collapse():
    m = match("same", ("a", "b"), ("c", "d"), games=((11, 6),))
    once = replay([m], as_of=LEDGER_AS_OF)
    twice = replay([m, m], as_of=LEDGER_AS_OF)
    assert replay_snapshot(twice) == replay_snapshot(once)
    assert twice.replay_info is not None
    assert twice.replay_info.matches_considered == 1


def test_order_follows_utc_instant():
    # 21:00 at UTC-5 is 02:00Z the next day, so it follows 22:00Z.
    m1 = match("m1", ("a", "b"), ("c", "d"), played_at=datetime(2026, 10, 29, 21, tzinfo=EST))
    m2 = match("m2", ("a", "b"), ("c", "d"), played_at=datetime(2026, 10, 29, 22, tzinfo=UTC))
    assert sorted([m1, m2], key=ledger_order) == [m2, m1]
    assert sorted([m2, m1], key=ledger_order) == [m2, m1]


class NoOffset(tzinfo):
    """A tzinfo that does not know its offset, so the datetime is naive."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return None


@pytest.mark.parametrize(
    "played_at",
    [datetime(2026, 10, 29, 19), datetime(2026, 10, 29, 19, tzinfo=NoOffset())],
    ids=["no_tzinfo", "no_offset"],
)
@pytest.mark.parametrize("check", [Match.validate, ledger_order], ids=["validate", "ledger_order"])
def test_naive_played_at_rejected(played_at: datetime, check):
    m = match("naive", ("a", "b"), ("c", "d"), played_at=played_at)
    with pytest.raises(ValueError, match="timezone-aware"):
        check(m)


def test_untimed_before_timed_same_day():
    timed = match("a", ("a", "b"), ("c", "d"), played_at=datetime(2026, 10, 29, 0, tzinfo=UTC))
    untimed_late = match("y", ("a", "b"), ("c", "d"), sequence=5)
    untimed_z = match("z", ("a", "b"), ("c", "d"), sequence=1)
    untimed_b = match("b", ("a", "b"), ("c", "d"), sequence=1)
    rows = [timed, untimed_late, untimed_z, untimed_b]
    expected = [untimed_b, untimed_z, untimed_late, timed]
    assert sorted(rows, key=ledger_order) == expected
    assert sorted(reversed(rows), key=ledger_order) == expected


def test_equal_instants_break_ties_by_sequence_then_id():
    # All three are 20:00Z. The offsets differ, so the local clock times differ.
    first = datetime(2026, 10, 29, 20, tzinfo=UTC)
    by_seq = match("z", ("a", "b"), ("c", "d"), played_at=first, sequence=0)
    by_id_a = match("a", ("a", "b"), ("c", "d"), played_at=first, sequence=2)
    by_id_b = match(
        "b", ("a", "b"), ("c", "d"), played_at=datetime(2026, 10, 29, 15, tzinfo=EST), sequence=2
    )
    rows = [by_id_b, by_id_a, by_seq]
    expected = [by_seq, by_id_a, by_id_b]
    assert sorted(rows, key=ledger_order) == expected
    assert sorted(reversed(rows), key=ledger_order) == expected


@pytest.mark.parametrize("mode", ["live", "scheduled"])
def test_prequential_is_permutation_invariant(mode):
    ledger = mixed_ledger()
    base = prequential_predictions(ledger, burn_in_matches=1, mode=mode)
    assert base
    for seed in range(20):
        shuffled = list(ledger)
        random.Random(seed).shuffle(shuffled)
        assert prequential_predictions(shuffled, burn_in_matches=1, mode=mode) == base


@pytest.mark.parametrize("mode", ["live", "scheduled"])
@pytest.mark.parametrize("flip", [False, True], ids=["win_first", "loss_first"])
def test_prequential_conflict_raises(mode, flip: bool):
    win, loss = dup_rows()
    rows = [loss, win] if flip else [win, loss]
    with pytest.raises(open_engine.LedgerConflictError, match="dup"):
        prequential_predictions(rows, mode=mode)


# 13. Duplicate rows must be interchangeable, whatever the input order ------------

FOLD_DAY = date(2026, 11, 1)  # New York clocks fall back at 02:00 local time


def new_york(cache: bool = True) -> ZoneInfo:
    try:
        return ZoneInfo("America/New_York") if cache else ZoneInfo.no_cache("America/New_York")
    except ZoneInfoNotFoundError:
        pytest.skip("tzdata for America/New_York is not installed")


def outcome(rows: list[Match]) -> object:
    """What replay gives: a snapshot, or the error type and message."""
    try:
        return replay_snapshot(replay(rows, as_of=LEDGER_AS_OF))
    except ValueError as err:
        return type(err), str(err)


@pytest.mark.parametrize("flip", [False, True], ids=["valid_first", "invalid_first"])
def test_duplicate_with_played_at_off_played_on_raises(flip: bool):
    # Same instant, so the rows compare equal, but only A falls on played_on.
    a = match("m", ("a", "b"), ("c", "d"), played_at=datetime(2026, 10, 29, 23, tzinfo=EST))
    b = match("m", ("a", "b"), ("c", "d"), played_at=datetime(2026, 10, 30, 4, tzinfo=UTC))
    with pytest.raises(ValueError, match="played_at must fall on played_on"):
        replay([b, a] if flip else [a, b], as_of=LEDGER_AS_OF)


@pytest.mark.parametrize("flip", [False, True], ids=["int_first", "float_first"])
def test_duplicate_with_float_score_raises(flip: bool):
    whole = match("m", ("a", "b"), ("c", "d"), games=((11, 3),))
    floaty = match("m", ("a", "b"), ("c", "d"), games=((11.0, 3),))
    with pytest.raises(ValueError, match="Game scores must be integers"):
        replay([floaty, whole] if flip else [whole, floaty], as_of=LEDGER_AS_OF)


def test_invalid_row_after_as_of_raises():
    late = match("late", ("a", "b"), ("c", "d"), games=((11.0, 3),), on=date(2026, 12, 15))
    with pytest.raises(ValueError, match="Game scores must be integers"):
        replay([late], as_of=LEDGER_AS_OF)


def test_fold_duplicates_conflict_in_both_orders():
    # 01:30 happens twice on FOLD_DAY: fold=0 is 05:30Z, fold=1 is 06:30Z.
    zone = new_york()
    kw = {"on": FOLD_DAY, "games": ((11, 3),)}
    first = match(
        "m", ("p1", "p2"), ("p3", "p4"), played_at=datetime(2026, 11, 1, 1, 30, tzinfo=zone), **kw
    )
    second = match(
        "m",
        ("p1", "p2"),
        ("p3", "p4"),
        played_at=datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1),
        **kw,
    )
    between = match(
        "t",
        ("p1", "p3"),
        ("p2", "p4"),
        games=((3, 11),),
        on=FOLD_DAY,
        played_at=datetime(2026, 11, 1, 6, tzinfo=UTC),
    )
    one = outcome([first, second, between])
    two = outcome([second, first, between])
    assert one == two
    assert one == (open_engine.LedgerConflictError, "Conflicting ledger rows for match_id: m")


def test_identical_fold_rows_with_distinct_zone_objects_collapse():
    rows = [
        match(
            "m",
            ("a", "b"),
            ("c", "d"),
            on=FOLD_DAY,
            played_at=datetime(2026, 11, 1, 1, 30, tzinfo=new_york(cache=False)),
        )
        for _ in range(2)
    ]
    for order in (rows, list(reversed(rows))):
        engine = replay(order, as_of=LEDGER_AS_OF)
        assert engine.replay_info is not None
        assert engine.replay_info.matches_considered == 1


def test_played_at_outside_utc_range_rejected():
    m = match(
        "early",
        ("a", "b"),
        ("c", "d"),
        on=date(1, 1, 1),
        played_at=datetime(1, 1, 1, 0, 30, tzinfo=timezone(timedelta(hours=1))),
    )
    with pytest.raises(ValueError, match="UTC"):
        m.validate()
    with pytest.raises(ValueError, match="UTC"):
        replay([m], as_of=LEDGER_AS_OF)


# 14. Duplicate rows must match in type, not only under == ------------------------

TWO_GAMES = ((11, 5), (11, 5))
LOOSE_PAIRS = [
    pytest.param(
        {"best_of": 3, "games": TWO_GAMES}, {"best_of": 3.0, "games": TWO_GAMES}, id="best_of"
    ),
    pytest.param(
        {"game_format": GameFormat(points_to=11)},
        {"game_format": GameFormat(points_to=11.0)},
        id="points_to",
    ),
    pytest.param(
        {"match_type": MatchType.SELF_REPORTED},
        {"match_type": "self_reported"},
        id="match_type",
    ),
]


def ledger_with(extra: list[Match]) -> list[Match]:
    base = [
        match(f"b{i}", ("a", "b"), ("c", "d"), games=((11, i % 9),), on=date(2026, 10, 1 + i % 5))
        for i in range(12)
    ]
    return base + extra


@pytest.mark.parametrize("flip", [False, True], ids=["strict_first", "loose_first"])
@pytest.mark.parametrize(("strict", "loose"), LOOSE_PAIRS)
def test_loosely_equal_duplicates_conflict_in_replay(strict, loose, flip: bool):
    rows = [match("x", ("a", "b"), ("c", "d"), **kw) for kw in (strict, loose)]
    assert rows[0] == rows[1]
    with pytest.raises(open_engine.LedgerConflictError, match="x"):
        replay(ledger_with(rows[::-1] if flip else rows), as_of=LEDGER_AS_OF)


@pytest.mark.parametrize("mode", ["live", "scheduled"])
@pytest.mark.parametrize("flip", [False, True], ids=["strict_first", "loose_first"])
@pytest.mark.parametrize(("strict", "loose"), LOOSE_PAIRS)
def test_loosely_equal_duplicates_conflict_in_prequential(strict, loose, flip: bool, mode):
    rows = [match("x", ("a", "b"), ("c", "d"), **kw) for kw in (strict, loose)]
    with pytest.raises(open_engine.LedgerConflictError, match="x"):
        prequential_predictions(
            ledger_with(rows[::-1] if flip else rows), burn_in_matches=0, mode=mode
        )
