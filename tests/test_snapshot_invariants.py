"""from_snapshot rejects states no engine could produce (issue #16).

A loaded snapshot sets every rating, so it must hold only states the
engine itself can reach: unplayed states with a seed RD, played states
with consistent counts and dates, unique non-blank identities, bounded
numbers, and, for a resumable snapshot, nothing dated after its replay.
All ids are synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    Match,
    RatingEngine,
    RatingFormat,
    SnapshotError,
    replay,
)

D = RatingFormat.DOUBLES
S = RatingFormat.SINGLES
AS_OF = date(2025, 3, 1)
CFG = Config()
LEDGER = [
    Match("syn-1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 4),), date(2025, 2, 1), D),
    Match("syn-2", ("syn-a", "syn-c"), ("syn-b", "syn-d"), ((11, 6),), date(2025, 2, 8), D),
]

Snap = dict[str, object]


def _engine() -> RatingEngine:
    seeder = RatingEngine()
    seeder.seed_external_rating(
        "syn-u", S, ExternalRatingSnapshot("external_standard", 4.0, date(2025, 1, 1), 60.0)
    )
    seeder.state("syn-v", S)
    seeds = [seeder.players["syn-u"], seeder.players["syn-v"]]
    return replay(LEDGER, seeds, as_of=AS_OF)


def _snapshot(engine: RatingEngine | None = None) -> Snap:
    loaded: Snap = json.loads(json.dumps((engine or _engine()).to_snapshot()))
    return loaded


def _player(snap: Snap, pid: str) -> dict[str, object]:
    players = snap["players"]
    assert isinstance(players, list)
    found: dict[str, object] = next(p for p in players if p["id"] == pid)
    return found


def _state(snap: Snap, pid: str, fmt: str) -> dict[str, object]:
    ratings = _player(snap, pid)["ratings"]
    assert isinstance(ratings, dict)
    state: dict[str, object] = ratings[fmt]
    return state


def _set(pid: str, fmt: str, **values: object) -> Callable[[Snap], None]:
    def mutate(snap: Snap) -> None:
        _state(snap, pid, fmt).update(values)

    return mutate


def _set_player(pid: str, **values: object) -> Callable[[Snap], None]:
    def mutate(snap: Snap) -> None:
        _player(snap, pid).update(values)

    return mutate


def _set_info(**values: object) -> Callable[[Snap], None]:
    def mutate(snap: Snap) -> None:
        info = snap["replay_info"]
        assert isinstance(info, dict)
        info.update(values)

    return mutate


def _set_top(**values: object) -> Callable[[Snap], None]:
    def mutate(snap: Snap) -> None:
        snap.update(values)

    return mutate


def _both(*mutations: Callable[[Snap], None]) -> Callable[[Snap], None]:
    def mutate(snap: Snap) -> None:
        for m in mutations:
            m(snap)

    return mutate


def _own_partner(snap: Snap) -> None:
    partners = _state(snap, "syn-a", "doubles")["partners"]
    assert isinstance(partners, dict)
    partners["syn-a"] = "2025-02-01"


def _own_opponent(snap: Snap) -> None:
    opponents = _state(snap, "syn-a", "doubles")["opponents"]
    assert isinstance(opponents, dict)
    opponents["syn-a"] = "2025-02-01"


def _future_partner(snap: Snap) -> None:
    partners = _state(snap, "syn-a", "doubles")["partners"]
    assert isinstance(partners, dict)
    partners["syn-b"] = "2025-03-02"


def _future_log(snap: Snap) -> None:
    snap["self_reported_log"] = [
        {"dates": ["2025-03-02"], "rating_format": "singles", "teams": [["syn-a"], ["syn-b"]]}
    ]


UNPLAYED = "syn-v"
IMPORTED = "syn-u"

TAMPERED: list[tuple[str, Callable[[Snap], None], str]] = [
    # a. an unplayed state has a seed RD, and only an import has a reliability
    (
        "unplayed low rd",
        _set(UNPLAYED, "singles", rd=CFG.rd_min),
        r"rd of an unplayed state must be new_player_rd, external_seed_rd, or at least",
    ),
    (
        "imported low rd",
        _set(IMPORTED, "singles", rd=CFG.rd_min),
        r"rd of an unplayed state must be",
    ),
    (
        "reliability without import",
        _set(UNPLAYED, "singles", initial_source_reliability=50.0),
        r"initial_source_reliability needs an initial_source_date",
    ),
    # b. played and unplayed states are consistent
    (
        "played relabeled unplayed",
        _set("syn-a", "doubles", matches_played=0),
        r"matches_played must be 0 exactly when last_played is null",
    ),
    (
        "unplayed with last_played",
        _set(UNPLAYED, "singles", last_played="2025-02-01"),
        r"matches_played must be 0 exactly when last_played is null",
    ),
    (
        "unplayed with partners",
        _set(UNPLAYED, "singles", partners={"syn-a": "2025-02-01"}),
        r"an unplayed state must have no partners",
    ),
    (
        "unplayed with opponents",
        _set(UNPLAYED, "singles", opponents={"syn-a": "2025-02-01"}),
        r"an unplayed state must have no opponents",
    ),
    (
        "live above played",
        _set("syn-a", "doubles", live_updates_since_replay=3),
        r"live_updates_since_replay must not exceed matches_played",
    ),
    ("own partner", _own_partner, r"a player cannot be their own partner"),
    ("own opponent", _own_opponent, r"a player cannot be their own opponent"),
    # c. identities are non-blank and unique
    (
        "blank account",
        _set_player("syn-a", account_user_id=""),
        r"account_user_id must not be blank",
    ),
    (
        "shared account",
        _both(
            _set_player("syn-a", account_user_id="acct-1"),
            _set_player("syn-b", account_user_id="acct-1"),
        ),
        r"account_user_id 'acct-1' is linked to more than one player",
    ),
    (
        "blank external source",
        _set_player("syn-a", external_ids={" ": "ext-1"}),
        r"external_ids must not hold a blank source or id",
    ),
    (
        "blank external id",
        _set_player("syn-a", external_ids={"ext": ""}),
        r"external_ids must not hold a blank source or id",
    ),
    (
        "shared external id",
        _both(
            _set_player("syn-a", external_ids={"ext": "same"}),
            _set_player("syn-b", external_ids={"ext": "same"}),
        ),
        r"external id 'same' from 'ext' is linked to more than one player",
    ),
    # d. a resumable snapshot holds nothing after its replay, and its counts agree
    (
        "future source date",
        _set(IMPORTED, "singles", initial_source_date="2025-03-02", rd_as_of="2025-03-02"),
        r"initial_source_date is after calculated_as_of",
    ),
    (
        "future last played",
        _set("syn-a", "doubles", last_played="2025-03-02"),
        r"last_played is after calculated_as_of",
    ),
    (
        "future rd as of",
        _set("syn-a", "doubles", rd_as_of="2025-03-02"),
        r"rd_as_of is after calculated_as_of",
    ),
    ("future partner", _future_partner, r"partners\['syn-b'\] is after calculated_as_of"),
    ("future log date", _future_log, r"dates\[0\] is after calculated_as_of"),
    (
        "considered count",
        _set_info(matches_considered=5),
        r"matches_considered must equal the number of ledger_digests",
    ),
    (
        "counted count",
        _set_info(matches_counted=1),
        r"matches_counted must equal the number of processed_match_ids",
    ),
    # e. numbers stay in bounds json.dumps can always write
    (
        "initial rating",
        _set("syn-a", "doubles", initial_rating=10**5000),
        r"initial_rating must be between",
    ),
    (
        "initial rating low",
        _set("syn-a", "doubles", initial_rating=1.0),
        r"initial_rating must be between",
    ),
    (
        "huge count",
        _set("syn-a", "doubles", matches_played=2**63),
        r"matches_played must be a nonnegative int no larger",
    ),
    (
        "huge info count",
        _both(_set_top(resumable=False), _set_info(matches_considered=2**63)),
        r"matches_considered must be a nonnegative int no larger",
    ),
]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [pytest.param(m, msg, id=name) for name, m, msg in TAMPERED],
)
def test_from_snapshot_rejects_a_state_no_engine_produces(
    mutate: Callable[[Snap], None], message: str
) -> None:
    snapshot = _snapshot()
    assert snapshot["resumable"] is True
    mutate(snapshot)
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(snapshot)


def test_the_untampered_snapshot_loads() -> None:
    snapshot = _snapshot()
    assert RatingEngine.from_snapshot(snapshot).to_snapshot() == snapshot


def test_a_live_engine_may_hold_dates_after_its_replay() -> None:
    engine = _engine()
    later = Match("syn-3", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 2),), date(2025, 4, 1), D)
    engine.record(later)
    snapshot = _snapshot(engine)
    assert snapshot["resumable"] is False
    restored = RatingEngine.from_snapshot(snapshot)
    assert restored.players["syn-a"].ratings[D].last_played == date(2025, 4, 1)


def test_a_category_state_derived_from_its_parent_loads() -> None:
    engine = _engine()
    engine.state("syn-a", RatingFormat.MIXED_DOUBLES)
    snapshot = _snapshot(engine)
    restored = RatingEngine.from_snapshot(snapshot)
    assert restored.to_snapshot() == snapshot


def test_to_snapshot_rejects_a_huge_initial_rating() -> None:
    engine = _engine()
    engine.players["syn-a"].ratings[D].initial_rating = 10**5000
    with pytest.raises(SnapshotError, match="initial_rating must be between"):
        engine.to_snapshot()
