"""Follow-up checks for read-only player lookups (issue #23).

A read of a missing category format no longer stores a state frozen at
the read, so the first category match rates from the current parent, as a
replay does. predict_win_probability changes nothing. An id named only in
uncounted rows or rows after as_of has no player record. All ids are
synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date

import pytest

from open_engine import (
    GameFormat,
    Match,
    MatchStatus,
    RatingEngine,
    RatingFormat,
    UnknownPlayerError,
    replay,
)

D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
S = RatingFormat.SINGLES
CUT = date(2025, 3, 1)


def _m(match_id: str, a: tuple[str, ...], b: tuple[str, ...], fmt: RatingFormat, on: date) -> Match:
    return Match(match_id, a, b, ((11, 4),), on, fmt)


# Played before CUT and replayed: one match that sets syn-a's doubles rating,
# or, for the variant, a singles match so syn-a is known with no doubles.
HISTORY: dict[str, list[Match]] = {
    "read after a parent match": [
        _m("syn-h1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), D, date(2025, 2, 1))
    ],
    "read before any parent match": [_m("syn-h1", ("syn-a",), ("syn-b",), S, date(2025, 2, 1))],
}
LIVE = [
    _m("syn-l1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), D, date(2025, 3, 5)),
    _m("syn-l2", ("syn-a", "syn-c"), ("syn-b", "syn-d"), D, date(2025, 3, 12)),
    _m("syn-l3", ("syn-a", "syn-d"), ("syn-b", "syn-c"), D, date(2025, 3, 19)),
    _m("syn-l4", ("syn-a", "syn-b"), ("syn-c", "syn-d"), MX, date(2025, 3, 26)),
]


def _live_mixed_rating(history: list[Match], read: bool) -> float:
    engine = replay(history, as_of=CUT)
    if read:
        engine.player_summary("syn-a", MX, as_of=CUT)
        engine.state("syn-a", MX)
    for match in LIVE:
        assert engine.record(match).counted
    return engine.players["syn-a"].ratings[MX].rating


@pytest.mark.parametrize("history", list(HISTORY.values()), ids=list(HISTORY))
def test_a_read_of_a_missing_category_does_not_change_its_first_match(
    history: list[Match],
) -> None:
    with_read = _live_mixed_rating(history, read=True)
    without_read = _live_mixed_rating(history, read=False)
    full = replay(history + LIVE, as_of=LIVE[-1].played_on)
    assert with_read == without_read
    assert with_read == full.players["syn-a"].ratings[MX].rating


def _replayed() -> RatingEngine:
    return replay(HISTORY["read after a parent match"], as_of=CUT)


WIN_PROBABILITY_CASES: dict[str, Callable[[RatingEngine], object]] = {
    "known player": lambda e: e.predict_win_probability(
        ("syn-a", "syn-b"), ("syn-c", "syn-d"), D, GameFormat()
    ),
    "missing category state": lambda e: e.predict_win_probability(
        ("syn-a", "syn-b"), ("syn-c", "syn-d"), MX, GameFormat()
    ),
    "unknown id": lambda e: e.predict_win_probability(
        ("syn-ghost", "syn-b"), ("syn-c", "syn-d"), D, GameFormat()
    ),
}


@pytest.mark.parametrize(
    "read", list(WIN_PROBABILITY_CASES.values()), ids=list(WIN_PROBABILITY_CASES)
)
def test_predict_win_probability_changes_nothing(read: Callable[[RatingEngine], object]) -> None:
    engine = _replayed()
    before = json.dumps(engine.to_snapshot(), sort_keys=True)
    players_before = list(engine.players)
    read(engine)
    assert json.dumps(engine.to_snapshot(), sort_keys=True) == before
    assert list(engine.players) == players_before
    assert engine.to_snapshot()["resumable"] is True


def test_an_id_only_in_uncounted_or_later_rows_has_no_player_record() -> None:
    ledger = [
        _m("syn-1", ("syn-a",), ("syn-b",), S, date(2025, 2, 1)),
        Match(
            "syn-2",
            ("syn-a",),
            ("syn-forfeit",),
            (),
            date(2025, 2, 2),
            S,
            status=MatchStatus.FORFEIT,
        ),
        _m("syn-3", ("syn-a",), ("syn-later",), S, date(2025, 4, 1)),
    ]
    engine = replay(ledger, as_of=CUT)
    for pid in ("syn-forfeit", "syn-later"):
        with pytest.raises(UnknownPlayerError, match=f"Unknown player: {pid}"):
            engine.player_summary(pid, S)
        assert pid not in engine.players
