"""Window cutoffs near date.min are clamped instead of raising OverflowError (issue #92).

Each backward window cutoff (a match or reference date minus a configured
window) stops at date.min. The engine compares dates against a cutoff
inclusively, so a cutoff at date.min keeps every date, just as one before it
would. These tests check that ledgers near date.min and very large windows no
longer raise, and that results equal the same ledger at ordinary dates.
"""

from __future__ import annotations

import dataclasses
import random
from datetime import date, timedelta

import pytest

from open_engine import (
    Config,
    Match,
    MatchType,
    PrequentialPrediction,
    RatingEngine,
    RatingFormat,
    prequential_predictions,
    replay,
)

D = RatingFormat.DOUBLES
S = RatingFormat.SINGLES
NORMAL = date(2026, 1, 5)
PLAYERS = ("a", "b", "c", "d", "e", "f")
MAX_DAYS = (date.max - date.min).days


def _ledger(start: date) -> list[Match]:
    """A synthetic ledger every 3 days from start: doubles, self-reported repeats, singles."""
    rows: list[Match] = []
    pairings = (
        (("a", "b"), ("c", "d")),
        (("a", "c"), ("b", "e")),
        (("a", "b"), ("c", "d")),
        (("d", "f"), ("a", "e")),
        (("a", "b"), ("c", "d")),
    )
    for i in range(20):
        team_a, team_b = pairings[i % len(pairings)]
        played_on = start + timedelta(days=3 * i)
        games = ((11, 4 + i % 6),) if i % 4 else ((11, 9), (6, 11), (11, 7))
        if i % 2:
            rows.append(
                Match(
                    f"m{i:02d}",
                    team_a,
                    team_b,
                    games,
                    played_on,
                    D,
                    best_of=len(games) if len(games) > 1 else 1,
                    match_type=MatchType.SELF_REPORTED,
                    confirmed_by=frozenset({team_a[0], team_b[0]}),
                )
            )
        else:
            rows.append(
                Match(
                    f"m{i:02d}",
                    team_a,
                    team_b,
                    games,
                    played_on,
                    D,
                    best_of=len(games) if len(games) > 1 else 1,
                    match_type=MatchType.TOURNAMENT,
                )
            )
    for i in range(6):
        rows.append(
            Match(
                f"s{i:02d}",
                (PLAYERS[i % 3],),
                (PLAYERS[3 + i % 3],),
                ((11, 3 + i),),
                start + timedelta(days=5 * i + 1),
                S,
            )
        )
    return rows


def _last_day(start: date) -> date:
    return max(m.played_on for m in _ledger(start))


def _without_dates(value: object) -> object:
    """A dataclass as a dict with every date field dropped, for comparing shifted ledgers."""
    assert dataclasses.is_dataclass(value) and not isinstance(value, type)
    return {
        k: v for k, v in dataclasses.asdict(value).items() if not isinstance(v, date | type(None))
    }


def _prediction_key(p: PrequentialPrediction) -> tuple[object, ...]:
    return (p.match_id, p.players, p.expected, p.actual, p.win_probability_raw)


def test_self_reported_match_near_date_min_records() -> None:
    match = Match(
        "m1",
        ("a", "b"),
        ("c", "d"),
        ((11, 5),),
        date(1, 2, 1),
        D,
        match_type=MatchType.SELF_REPORTED,
        confirmed_by=frozenset({"a", "c"}),
    )
    assert RatingEngine(Config()).record(match).counted


def test_record_near_date_min_equals_normal_dates() -> None:
    early = RatingEngine(Config())
    normal = RatingEngine(Config())
    for low, high in zip(_ledger(date.min), _ledger(NORMAL), strict=True):
        assert _without_dates(early.record(low)) == _without_dates(normal.record(high))


@pytest.mark.parametrize("rating_format", [D, S])
def test_replay_and_summary_near_date_min_equal_normal_dates(rating_format: RatingFormat) -> None:
    early = replay(_ledger(date.min), as_of=_last_day(date.min) + timedelta(days=2))
    normal = replay(_ledger(NORMAL), as_of=_last_day(NORMAL) + timedelta(days=2))
    for pid in PLAYERS:
        assert early.state(pid, rating_format).rating == normal.state(pid, rating_format).rating
        assert early.state(pid, rating_format).rd == normal.state(pid, rating_format).rd
        summary_early = early.player_summary(pid, rating_format)
        summary_normal = normal.player_summary(pid, rating_format)
        assert _without_dates(summary_early) == _without_dates(summary_normal)


def test_player_summary_with_as_of_near_date_min() -> None:
    engine = replay(_ledger(date.min), as_of=date.min + timedelta(days=10))
    normal = replay(_ledger(NORMAL), as_of=NORMAL + timedelta(days=10))
    for days in (0, 3, 10):
        early = engine.player_summary("a", D, as_of=date.min + timedelta(days=days))
        later = normal.player_summary("a", D, as_of=NORMAL + timedelta(days=days))
        assert _without_dates(early) == _without_dates(later)


def test_large_diversity_window_summary_equals_covering_window() -> None:
    as_of = _last_day(NORMAL) + timedelta(days=2)
    huge = replay(_ledger(NORMAL), cfg=Config(diversity_window_days=800_000), as_of=as_of)
    covering = replay(_ledger(NORMAL), cfg=Config(diversity_window_days=700_000), as_of=as_of)
    for pid in PLAYERS:
        assert huge.player_summary(pid, D) == covering.player_summary(pid, D)


def test_large_repeat_window_record_equals_covering_window() -> None:
    huge = RatingEngine(Config(self_reported_repeat_window_days=MAX_DAYS))
    covering = RatingEngine(Config(self_reported_repeat_window_days=700_000))
    for match in _ledger(NORMAL):
        assert huge.record(match) == covering.record(match)


def test_large_replay_interval_scheduled_equals_covering_interval() -> None:
    huge = prequential_predictions(
        _ledger(NORMAL),
        cfg=Config(replay_interval_days=MAX_DAYS),
        mode="scheduled",
        burn_in_matches=0,
    )
    covering = prequential_predictions(
        _ledger(NORMAL),
        cfg=Config(replay_interval_days=700_000),
        mode="scheduled",
        burn_in_matches=0,
    )
    assert huge
    assert [_prediction_key(p) for p in huge] == [_prediction_key(p) for p in covering]


@pytest.mark.parametrize("anchor_days", [None, 0, 7, 30])
@pytest.mark.parametrize("interval", [1, 7, 30])
def test_scheduled_near_date_min_equals_normal_dates(
    anchor_days: int | None, interval: int
) -> None:
    cfg = Config(replay_interval_days=interval)

    def run(start: date) -> list[tuple[object, ...]]:
        anchor = None if anchor_days is None else start + timedelta(days=anchor_days)
        predictions = prequential_predictions(
            _ledger(start), cfg=cfg, mode="scheduled", burn_in_matches=0, anchor=anchor
        )
        return [_prediction_key(p) for p in predictions]

    early = run(date.min)
    assert early
    assert early == run(NORMAL)


def test_scheduled_window_near_date_min_does_not_see_its_own_match() -> None:
    """A match on date.min is predicted from the seed ratings, not after itself."""
    first = Match("x0", ("a",), ("b",), ((11, 0),), date.min, S)
    second = Match("x1", ("a",), ("b",), ((11, 0),), date.min + timedelta(days=1), S)
    predictions = prequential_predictions([first, second], mode="scheduled", burn_in_matches=0)
    assert [p.match_id for p in predictions] == ["x0", "x1"]
    assert predictions[0].expected == 0.5
    assert predictions[1].expected > 0.5


@pytest.mark.parametrize("days", [0, 1, 30, 400, MAX_DAYS])
def test_days_before_clamps_at_date_min(days: int) -> None:
    from open_engine.engine import _days_before

    assert _days_before(date.min, days) == date.min
    assert _days_before(date.min + timedelta(days=days), days) == date.min
    assert _days_before(date.max, days) == date.max - timedelta(days=days)


def test_days_before_matches_timedelta_where_it_does_not_overflow() -> None:
    from open_engine.engine import _days_before

    rng = random.Random(92)
    for _ in range(2000):
        day = date.fromordinal(rng.randint(1, date.max.toordinal()))
        days = rng.choice((rng.randint(0, 1000), rng.randint(0, MAX_DAYS)))
        expected = day - timedelta(days=days) if days < day.toordinal() else date.min
        assert _days_before(day, days) == expected


# Measured on main before the fix (01dcbf1): scheduled predictions at
# replay_interval_days=7, and replayed ratings and reliabilities, for the
# ordinary-date ledger. The fix must not move any of them.
PINNED_SCHEDULED = [
    ("m00", "0x1.0000000000000p-1"),
    ("s00", "0x1.0000000000000p-1"),
    ("m01", "0x1.ff5006bf96cf0p-2"),
    ("m02", "0x1.fe93139862bb3p-2"),
    ("s01", "0x1.0000000000000p-1"),
    ("m03", "0x1.eecf542ebaf32p-2"),
    ("s02", "0x1.0000000000000p-1"),
    ("m04", "0x1.0ccc1d264b797p-1"),
    ("m05", "0x1.0ad79483bd3d3p-1"),
    ("s03", "0x1.1a0b1a1fc45c9p-1"),
    ("m06", "0x1.04afce1455dbfp-1"),
    ("m07", "0x1.06c7bcf97d145p-1"),
    ("s04", "0x1.154b13dec0a26p-1"),
    ("m08", "0x1.fb5a268ecb792p-2"),
    ("s05", "0x1.111feadda2087p-1"),
    ("m09", "0x1.08fbca9433006p-1"),
    ("m10", "0x1.09b85a4b2678ep-1"),
    ("m11", "0x1.17aa29907333dp-1"),
    ("m12", "0x1.0e6436786277fp-1"),
    ("m13", "0x1.fb143b0cf1dffp-2"),
    ("m14", "0x1.0b74e73ed2259p-1"),
    ("m15", "0x1.13a3f76795922p-1"),
    ("m16", "0x1.161e9ad9e873ep-1"),
    ("m18", "0x1.f88535cce6ae4p-2"),
]
PINNED_REPLAY = {
    "a": ("0x1.d5b86687ad406p+1", 65.0),
    "b": ("0x1.c3fac3bc7fca8p+1", 49.8),
    "c": ("0x1.bc053c4380358p+1", 49.8),
    "d": ("0x1.bafe5d61cbf86p+1", 49.8),
    "e": ("0x1.98093d7249482p+1", 33.9),
    "f": ("0x1.d7fb5bb96d737p+1", 16.4),
}


def test_ordinary_dates_are_unchanged() -> None:
    predictions = prequential_predictions(
        _ledger(NORMAL), cfg=Config(replay_interval_days=7), mode="scheduled", burn_in_matches=0
    )
    assert [(p.match_id, p.expected.hex()) for p in predictions] == PINNED_SCHEDULED
    engine = replay(_ledger(NORMAL), as_of=NORMAL + timedelta(days=60))
    assert {
        pid: (engine.state(pid, D).rating.hex(), engine.player_summary(pid, D).reliability)
        for pid in PLAYERS
    } == PINNED_REPLAY


def test_scheduled_anchor_far_after_the_ledger_is_one_window() -> None:
    """Every match falls in one window before the anchor, replayed from nothing: live mode."""
    cfg = Config(replay_interval_days=MAX_DAYS)
    scheduled = prequential_predictions(
        _ledger(NORMAL), cfg=cfg, mode="scheduled", burn_in_matches=0, anchor=date.max
    )
    live = prequential_predictions(_ledger(NORMAL), cfg=cfg, mode="live", burn_in_matches=0)
    assert scheduled
    assert [_prediction_key(p) for p in scheduled] == [_prediction_key(p) for p in live]


def test_days_before_moving_forward_stops_at_date_max() -> None:
    from open_engine.engine import _days_before

    assert _days_before(date.max - timedelta(days=3), -10) == date.max
    assert _days_before(date.min, -10) == date.min + timedelta(days=10)
