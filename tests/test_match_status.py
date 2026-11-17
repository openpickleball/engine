"""Forfeits, retirements, and incomplete matches (issue #22).

A match whose status is not completed is a valid ledger row that never
counts. All player and match ids are synthetic.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import random
from datetime import date, datetime, timedelta

import pytest

import open_engine
from open_engine import (
    InvalidMatchError,
    LedgerConflictError,
    Match,
    MatchStatus,
    RatingEngine,
    RatingFormat,
    RejectionReason,
    SnapshotError,
    evaluate,
    prequential_predictions,
    replay,
)

S = RatingFormat.SINGLES
START = date(2026, 1, 5)
PLAYERS = ["syn-p1", "syn-p2", "syn-p3", "syn-p4"]
NOT_COMPLETED = [MatchStatus.FORFEIT, MatchStatus.RETIRED, MatchStatus.INCOMPLETE]


def _m(
    match_id: str,
    games: tuple[tuple[int, int], ...] = ((11, 5),),
    status: object = MatchStatus.COMPLETED,
    *,
    team_a: tuple[str, ...] = ("syn-p1",),
    team_b: tuple[str, ...] = ("syn-p2",),
    on: date = START,
    fmt: RatingFormat = S,
    **kwargs: object,
) -> Match:
    return Match(  # type: ignore[arg-type]
        match_id, team_a, team_b, games, on, fmt, status=status, **kwargs
    )


# -- criterion 1: the enum -------------------------------------------------------


def test_match_status_members_and_values() -> None:
    assert [(m.name, m.value) for m in MatchStatus] == [
        ("COMPLETED", "completed"),
        ("FORFEIT", "forfeit"),
        ("RETIRED", "retired"),
        ("INCOMPLETE", "incomplete"),
    ]
    assert issubclass(MatchStatus, str)
    assert str(MatchStatus.FORFEIT) == "forfeit"
    assert f"{MatchStatus.RETIRED}" == "retired"


def test_match_status_is_public_and_documented() -> None:
    assert "MatchStatus" in open_engine.__all__
    assert open_engine.MatchStatus is MatchStatus
    assert MatchStatus.__doc__


# -- criterion 2: the field ------------------------------------------------------


def test_status_is_the_last_field_and_defaults_to_completed() -> None:
    assert dataclasses.fields(Match)[-1].name == "status"
    match = Match("syn-m1", ("syn-p1",), ("syn-p2",), ((11, 5),), START, S)
    assert match.status is MatchStatus.COMPLETED


# -- criterion 3: validation ------------------------------------------------------

INVALID: list[tuple[str, Match, str]] = [
    ("str status", _m("syn-m1", (), "forfeit"), r"status must be a MatchStatus"),
    ("str completed", _m("syn-m1", ((11, 5),), "completed"), r"status must be a MatchStatus"),
    ("None status", _m("syn-m1", (), None), r"status must be a MatchStatus"),
    ("forfeit with a game", _m("syn-m1", ((11, 5),), MatchStatus.FORFEIT), r"forfeit"),
    (
        "forfeit with a game in a best of 3",
        _m("syn-m1", ((11, 5),), MatchStatus.FORFEIT, best_of=3),
        r"forfeit",
    ),
    (
        "retired with a decided winner",
        _m("syn-m1", ((11, 5), (11, 7)), MatchStatus.RETIRED, best_of=3),
        r"decided winner",
    ),
    (
        "incomplete with a decided winner",
        _m("syn-m1", ((11, 5),), MatchStatus.INCOMPLETE),
        r"decided winner",
    ),
    (
        "retired with a partial game",
        _m("syn-m1", ((7, 4),), MatchStatus.RETIRED, best_of=3),
        r"A game must reach its target score",
    ),
    (
        "incomplete with a partial second game",
        _m("syn-m1", ((11, 5), (9, 4)), MatchStatus.INCOMPLETE, best_of=3),
        r"A game must reach its target score",
    ),
    (
        "retired with more games than best_of",
        _m("syn-m1", ((11, 5), (5, 11)), MatchStatus.RETIRED),
        r"Games played cannot exceed best_of",
    ),
    (
        "forfeit with a blank match_id",
        _m(" ", (), MatchStatus.FORFEIT),
        r"match_id is required",
    ),
    (
        "retired with a repeated player",
        _m("syn-m1", (), MatchStatus.RETIRED, team_b=("syn-p1",)),
        r"cannot appear more than once",
    ),
    (
        "incomplete with an even best_of",
        _m("syn-m1", (), MatchStatus.INCOMPLETE, best_of=2),
        r"best_of must be a positive odd number",
    ),
    (
        "forfeit with an outside confirmer",
        _m("syn-m1", (), MatchStatus.FORFEIT, confirmed_by=frozenset({"syn-p9"})),
        r"confirmed_by may only contain match participants",
    ),
    (
        "forfeit with a negative sequence",
        _m("syn-m1", (), MatchStatus.FORFEIT, sequence=-1),
        r"sequence must be nonnegative",
    ),
    (
        "retired with a naive played_at",
        _m("syn-m1", (), MatchStatus.RETIRED, played_at=datetime(2026, 1, 5, 10)),
        r"played_at must be timezone-aware",
    ),
    (
        "completed with no games",
        _m("syn-m1", (), MatchStatus.COMPLETED),
        r"At least one game score is required",
    ),
    (
        "completed with no winner",
        _m("syn-m1", ((11, 5),), MatchStatus.COMPLETED, best_of=3),
        r"Match score does not contain a completed winner",
    ),
]


@pytest.mark.parametrize(
    ("match", "message"), [c[1:] for c in INVALID], ids=[c[0] for c in INVALID]
)
def test_invalid_matches_raise(match: Match, message: str) -> None:
    with pytest.raises(InvalidMatchError, match=message):
        match.validate()


VALID: list[tuple[str, Match]] = [
    ("forfeit with no games", _m("syn-m1", (), MatchStatus.FORFEIT)),
    ("forfeit best of 3", _m("syn-m1", (), MatchStatus.FORFEIT, best_of=3)),
    ("retired with no games", _m("syn-m1", (), MatchStatus.RETIRED)),
    ("incomplete with no games", _m("syn-m1", (), MatchStatus.INCOMPLETE)),
    ("retired after one game", _m("syn-m1", ((11, 5),), MatchStatus.RETIRED, best_of=3)),
    (
        "incomplete at one game all",
        _m("syn-m1", ((11, 5), (5, 11)), MatchStatus.INCOMPLETE, best_of=3),
    ),
    (
        "retired two games up in a best of 5",
        _m("syn-m1", ((11, 5), (11, 6)), MatchStatus.RETIRED, best_of=5),
    ),
]


@pytest.mark.parametrize("match", [c[1] for c in VALID], ids=[c[0] for c in VALID])
def test_valid_non_completed_matches(match: Match) -> None:
    match.validate()


# -- criterion 4: record ----------------------------------------------------------


def _played_engine() -> RatingEngine:
    ledger = [
        _m("syn-base1", ((11, 5),)),
        _m("syn-base2", ((7, 11),), team_a=("syn-p3",), team_b=("syn-p4",)),
    ]
    return replay(ledger, as_of=START)


NON_COMPLETED_ROWS = {
    MatchStatus.FORFEIT: ((), 1),
    MatchStatus.RETIRED: (((11, 5),), 3),
    MatchStatus.INCOMPLETE: (((11, 5), (5, 11)), 3),
}


@pytest.mark.parametrize("status", NOT_COMPLETED, ids=str)
@pytest.mark.parametrize("live", [True, False], ids=["live", "as_of"])
def test_record_does_not_count_a_non_completed_match(status: MatchStatus, live: bool) -> None:
    eng = _played_engine()
    before = eng.to_snapshot()
    assert before["resumable"] is True
    games, best_of = NON_COMPLETED_ROWS[status]
    match = _m("syn-x1", games, status, team_a=("syn-p1",), team_b=("syn-new",), best_of=best_of)
    result = eng.record(match) if live else eng.record(match, as_of=START + timedelta(days=1))
    assert result.counted is False
    assert result.reason is RejectionReason.MATCH_NOT_COMPLETED
    assert result.rating_format is S
    assert result.possible_duplicate_of is None
    assert result.deltas == {}
    assert eng.to_snapshot() == before
    assert "syn-new" not in eng.players


def test_a_later_completed_match_with_the_same_id_counts() -> None:
    eng = _played_engine()
    eng.record(_m("syn-x1", (), MatchStatus.FORFEIT))
    result = eng.record(_m("syn-x1", ((11, 5),)))
    assert result.counted is True
    assert "syn-x1" in eng.processed_match_ids


def test_a_non_completed_match_with_a_processed_id_is_a_duplicate() -> None:
    eng = _played_engine()
    result = eng.record(_m("syn-base1", (), MatchStatus.FORFEIT))
    assert result.reason is RejectionReason.DUPLICATE_MATCH_ID


def test_match_not_completed_is_a_rejection_reason() -> None:
    assert RejectionReason.MATCH_NOT_COMPLETED.value == "match_not_completed"


# -- criterion 5: replay ----------------------------------------------------------


def _completed_ledger(n: int = 40, seed: int = 7) -> list[Match]:
    rng = random.Random(seed)
    rows: list[Match] = []
    for i in range(n):
        a, b = rng.sample(PLAYERS, 2)
        loser = rng.randint(0, 9)
        games = ((11, loser),) if rng.random() < 0.5 else ((loser, 11),)
        on = START + timedelta(days=3 * i)
        rows.append(_m(f"syn-c{i:02d}", games, team_a=(a,), team_b=(b,), on=on))
    return rows


def _non_completed_rows() -> list[Match]:
    return [
        _m(
            "syn-f1",
            (),
            MatchStatus.FORFEIT,
            team_a=("syn-p1",),
            team_b=("syn-p2",),
            on=START + timedelta(days=10),
        ),
        _m(
            "syn-r1",
            ((11, 5),),
            MatchStatus.RETIRED,
            team_a=("syn-p3",),
            team_b=("syn-p4",),
            on=START + timedelta(days=31),
            best_of=3,
        ),
        _m(
            "syn-i1",
            (),
            MatchStatus.INCOMPLETE,
            team_a=("syn-p2",),
            team_b=("syn-ghost",),
            on=START + timedelta(days=65),
        ),
        _m(
            "syn-r2",
            ((5, 11), (11, 9)),
            MatchStatus.RETIRED,
            team_a=("syn-p4",),
            team_b=("syn-p1",),
            on=START + timedelta(days=95),
            best_of=3,
        ),
        _m(
            "syn-f2",
            (),
            MatchStatus.FORFEIT,
            team_a=("syn-p3",),
            team_b=("syn-p2",),
            on=START + timedelta(days=110),
        ),
    ]


AS_OF = START + timedelta(days=200)


def test_replay_ratings_ignore_non_completed_rows() -> None:
    completed = _completed_ledger()
    extra = _non_completed_rows()
    without = replay(completed, as_of=AS_OF)
    mixed = replay(completed + extra, as_of=AS_OF)
    assert mixed.to_snapshot()["players"] == without.to_snapshot()["players"]
    assert mixed.replay_info is not None and without.replay_info is not None
    assert mixed.replay_info.matches_considered == without.replay_info.matches_considered + 5
    assert mixed.replay_info.matches_counted == without.replay_info.matches_counted
    assert mixed.processed_match_ids == without.processed_match_ids
    assert "syn-ghost" not in mixed.players


def test_replay_with_non_completed_rows_is_order_independent() -> None:
    rows = _completed_ledger() + _non_completed_rows()
    expected = replay(rows, as_of=AS_OF).to_snapshot()
    rng = random.Random(3)
    for _ in range(5):
        shuffled = rows[:]
        rng.shuffle(shuffled)
        assert replay(shuffled, as_of=AS_OF).to_snapshot() == expected


def test_non_completed_and_completed_rows_with_one_id_conflict() -> None:
    rows = [_m("syn-x1", (), MatchStatus.FORFEIT), _m("syn-x1", ((11, 5),))]
    with pytest.raises(LedgerConflictError, match="syn-x1"):
        replay(rows, as_of=AS_OF)


@pytest.mark.parametrize("whole", [True, False], ids=["whole ledger", "newer rows"])
def test_resume_with_non_completed_rows_equals_full_replay(whole: bool) -> None:
    rows = _completed_ledger() + _non_completed_rows()
    checkpoint_date = START + timedelta(days=70)
    checkpoint = replay([m for m in rows if m.played_on <= checkpoint_date], as_of=checkpoint_date)
    snapshot = json.loads(json.dumps(checkpoint.to_snapshot()))
    given = rows if whole else [m for m in rows if m.played_on > checkpoint_date]
    resumed = replay(given, as_of=AS_OF, from_snapshot=snapshot, whole_ledger=whole)
    assert resumed.to_snapshot() == replay(rows, as_of=AS_OF).to_snapshot()


# -- criterion 6: prequential and evaluate ----------------------------------------


def _established_forfeits() -> list[Match]:
    late = START + timedelta(days=150)
    return [
        _m("syn-f9", (), MatchStatus.FORFEIT, team_a=("syn-p1",), team_b=("syn-p2",), on=late),
        _m(
            "syn-r9",
            ((11, 5),),
            MatchStatus.RETIRED,
            team_a=("syn-p3",),
            team_b=("syn-p4",),
            on=late,
            best_of=3,
        ),
        _m(
            "syn-i9",
            (),
            MatchStatus.INCOMPLETE,
            team_a=("syn-p2",),
            team_b=("syn-p3",),
            on=late + timedelta(days=1),
        ),
    ]


@pytest.mark.parametrize("mode", ["live", "scheduled"])
def test_prequential_skips_non_completed_rows(mode: str) -> None:
    completed = _completed_ledger()
    mixed = completed + _non_completed_rows() + _established_forfeits()
    without = prequential_predictions(completed, burn_in_matches=2, mode=mode)  # type: ignore[arg-type]
    with_rows = prequential_predictions(mixed, burn_in_matches=2, mode=mode)  # type: ignore[arg-type]
    assert without
    assert with_rows == without


@pytest.mark.parametrize("mode", ["live", "scheduled"])
def test_evaluate_with_a_zero_game_forfeit_between_established_players(mode: str) -> None:
    completed = _completed_ledger()
    mixed = completed + _established_forfeits()
    without = evaluate(completed, burn_in_matches=2, mode=mode)  # type: ignore[arg-type]
    assert evaluate(mixed, burn_in_matches=2, mode=mode) == without  # type: ignore[arg-type]


# -- criterion 7: snapshot version ------------------------------------------------


def test_snapshot_version_is_4() -> None:
    assert _played_engine().to_snapshot()["snapshot_version"] == 4


def test_version_2_snapshot_is_refused() -> None:
    snapshot = copy.deepcopy(_played_engine().to_snapshot())
    snapshot["snapshot_version"] = 2
    with pytest.raises(SnapshotError, match="snapshot_version must be 4"):
        RatingEngine.from_snapshot(snapshot)
