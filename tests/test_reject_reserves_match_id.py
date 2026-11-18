"""Live record reserves the match_id of every row it rejects (issue #76).

Replay treats a second, different row with one match_id as a ledger
conflict, so it never counts it. Live recording must agree: once a row is
considered, counted or not, its match_id is taken, and a resubmission needs
a new match_id. All player and match ids are synthetic.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    GameFormat,
    LedgerConflictError,
    Match,
    MatchStatus,
    MatchType,
    Player,
    RatingEngine,
    RatingFormat,
    RejectionReason,
    ScoringType,
    ledger_order,
    replay,
)

D = RatingFormat.DOUBLES
S = RatingFormat.SINGLES
SEED_ON = date(2026, 1, 1)
BASELINE_ON = date(2026, 3, 15)
PLAYED = date(2026, 3, 10)
RESUBMITTED = date(2026, 3, 20)
AS_OF = date(2026, 4, 1)

CFG = Config(
    max_singles_rating_gap=0.5,
    max_doubles_partner_gap=0.5,
    self_reported_max_repeats=1,
    reject_content_duplicates=True,
)


def _d(
    match_id: str,
    team_a: tuple[str, str],
    team_b: tuple[str, str],
    games: tuple[tuple[int, int], ...] = ((11, 5),),
    on: date = PLAYED,
    **fields: object,
) -> Match:
    return Match(match_id, team_a, team_b, games, on, D, **fields)  # type: ignore[arg-type]


def _s(match_id: str, a: str, b: str, on: date = PLAYED) -> Match:
    return Match(match_id, (a,), (b,), ((11, 5),), on, S)


SELF = MatchType.SELF_REPORTED

# Rows the replay ledger holds, in no particular order. The comment on each
# row names the result it gets in ledger order under CFG.
LEDGER = [
    # GAME_FORMAT_NOT_ACCEPTED
    _d(
        "syn-fmt",
        ("syn-a1", "syn-a2"),
        ("syn-a3", "syn-a4"),
        ((7, 3),),
        game_format=GameFormat(ScoringType.SIDE_OUT, points_to=7),
    ),
    # Counted, then its reversed copy is POSSIBLE_DUPLICATE.
    _d("syn-orig", ("syn-c1", "syn-c2"), ("syn-c3", "syn-c4"), ((11, 4),), sequence=1),
    _d("syn-copy", ("syn-c4", "syn-c3"), ("syn-c2", "syn-c1"), ((4, 11),), sequence=2),
    # SELF_REPORTED_UNCONFIRMED
    _d("syn-unconf", ("syn-e1", "syn-e2"), ("syn-e3", "syn-e4"), match_type=SELF),
    # Counted, then the same matchup is SELF_REPORTED_REPEAT_LIMIT.
    _d(
        "syn-sr1",
        ("syn-f1", "syn-f2"),
        ("syn-f3", "syn-f4"),
        match_type=SELF,
        confirmed_by=frozenset({"syn-f1", "syn-f3"}),
        sequence=1,
    ),
    _d(
        "syn-sr2",
        ("syn-f1", "syn-f2"),
        ("syn-f3", "syn-f4"),
        ((11, 9),),
        match_type=SELF,
        confirmed_by=frozenset({"syn-f1", "syn-f3"}),
        sequence=2,
    ),
    # BEFORE_IMPORTED_BASELINE
    _d("syn-base", ("syn-b1", "syn-b2"), ("syn-b3", "syn-b4")),
    # SINGLES_RATING_GAP
    _s("syn-sgap", "syn-s1", "syn-s2"),
    # DOUBLES_PARTNER_GAP
    _d("syn-pgap", ("syn-g1", "syn-g2"), ("syn-h1", "syn-h2")),
    # MATCH_NOT_COMPLETED
    _d("syn-forfeit", ("syn-i1", "syn-i2"), ("syn-i3", "syn-i4"), (), status=MatchStatus.FORFEIT),
    # Counted; the identical copy below is DUPLICATE_MATCH_ID live and
    # collapses into one row in replay.
    _d("syn-dup", ("syn-k1", "syn-k2"), ("syn-k3", "syn-k4")),
]
IDENTICAL_COPY = _d("syn-dup", ("syn-k1", "syn-k2"), ("syn-k3", "syn-k4"))

# A resubmission of each rejected row under its match_id. Each would count
# on an engine that never saw the rejected row (see
# test_each_resubmission_would_count_on_its_own).
RESUBMISSIONS = {
    RejectionReason.GAME_FORMAT_NOT_ACCEPTED: _d(
        "syn-fmt", ("syn-a1", "syn-a2"), ("syn-a3", "syn-a4"), on=RESUBMITTED
    ),
    RejectionReason.POSSIBLE_DUPLICATE: _d(
        "syn-copy", ("syn-c1", "syn-c2"), ("syn-c3", "syn-c4"), ((11, 6),), on=RESUBMITTED
    ),
    RejectionReason.SELF_REPORTED_UNCONFIRMED: _d(
        "syn-unconf",
        ("syn-e1", "syn-e2"),
        ("syn-e3", "syn-e4"),
        on=RESUBMITTED,
        match_type=SELF,
        confirmed_by=frozenset({"syn-e1", "syn-e3"}),
    ),
    RejectionReason.SELF_REPORTED_REPEAT_LIMIT: _d(
        "syn-sr2",
        ("syn-f1", "syn-f2"),
        ("syn-f3", "syn-f4"),
        ((11, 9),),
        on=RESUBMITTED,
        match_type=MatchType.LEAGUE,
    ),
    RejectionReason.BEFORE_IMPORTED_BASELINE: _d(
        "syn-base", ("syn-b1", "syn-b2"), ("syn-b3", "syn-b4"), on=RESUBMITTED
    ),
    RejectionReason.SINGLES_RATING_GAP: _s("syn-sgap", "syn-s1", "syn-s3", on=RESUBMITTED),
    RejectionReason.DOUBLES_PARTNER_GAP: _d(
        "syn-pgap", ("syn-h1", "syn-h2"), ("syn-h3", "syn-h4"), on=RESUBMITTED
    ),
    RejectionReason.MATCH_NOT_COMPLETED: _d(
        "syn-forfeit", ("syn-i1", "syn-i2"), ("syn-i3", "syn-i4"), on=RESUBMITTED
    ),
    RejectionReason.DUPLICATE_MATCH_ID: _d(
        "syn-dup", ("syn-k1", "syn-k2"), ("syn-k3", "syn-k4"), ((11, 2),), on=RESUBMITTED
    ),
}

REJECTED_LIVE = [r for r in RESUBMISSIONS if r is not RejectionReason.DUPLICATE_MATCH_ID]


def _seeds() -> list[Player]:
    scratch = RatingEngine(CFG)
    imported = [
        ("syn-s1", S, 3.0, SEED_ON),
        ("syn-s2", S, 5.0, SEED_ON),
        ("syn-s3", S, 3.2, SEED_ON),
        ("syn-g1", D, 3.0, SEED_ON),
        ("syn-g2", D, 5.0, SEED_ON),
        ("syn-b1", D, 4.0, BASELINE_ON),
        ("syn-b2", D, 4.0, BASELINE_ON),
        ("syn-b3", D, 4.0, BASELINE_ON),
        ("syn-b4", D, 4.0, BASELINE_ON),
    ]
    for pid, fmt, rating, on in imported:
        scratch.seed_external_rating(pid, fmt, ExternalRatingSnapshot("syn-ext", rating, on))
    return [scratch.player(pid) for pid, *_ in imported]


def _live(rows: list[Match]) -> tuple[RatingEngine, list[RejectionReason | None]]:
    engine = RatingEngine(CFG)
    for seed in _seeds():
        engine.add_player(seed)
    reasons = [engine.record(match).reason for match in rows]
    return engine, reasons


def _state(engine: RatingEngine) -> dict[str, object]:
    """The snapshot, less what only a replay sets and the live update labels."""
    snap = engine.to_snapshot()
    for key in (
        "calculated_as_of",
        "replay_info",
        "ledger_digests",
        "ledger_player_ids",
        "resumable",
        "engine_version",
    ):
        snap.pop(key)
    for player in snap["players"]:  # type: ignore[attr-defined]
        for state in player["ratings"].values():
            state["live_updates_since_replay"] = 0
            state["inherited_live_updates"] = 0
    return snap


def test_issue_reproduction_rejected_id_is_reserved() -> None:
    def m(points: int, games: tuple[tuple[int, int], ...]) -> Match:
        return Match(
            "syn-m1",
            ("syn-a", "syn-b"),
            ("syn-c", "syn-d"),
            games,
            date(2026, 10, 29),
            D,
            MatchType.LEAGUE,
            game_format=GameFormat(ScoringType.SIDE_OUT, points_to=points),
        )

    engine = RatingEngine()
    first = engine.record(m(7, ((7, 3),)))
    assert first.reason is RejectionReason.GAME_FORMAT_NOT_ACCEPTED
    second = engine.record(m(11, ((11, 5),)))
    assert not second.counted
    assert second.reason is RejectionReason.DUPLICATE_MATCH_ID
    assert "syn-a" not in engine.players
    with pytest.raises(LedgerConflictError, match="syn-m1"):
        replay([m(7, ((7, 3),)), m(11, ((11, 5),))], as_of=date(2026, 11, 1))


def test_each_resubmission_would_count_on_its_own() -> None:
    rejected = {match.match_id for match in LEDGER} - {
        "syn-orig",
        "syn-sr1",
        "syn-dup",
    }
    kept = [match for match in LEDGER if match.match_id not in rejected]
    rows = sorted(kept, key=ledger_order) + [RESUBMISSIONS[reason] for reason in REJECTED_LIVE]
    _, reasons = _live(rows)
    assert reasons == [None] * len(rows)


def test_live_and_replay_agree_after_every_rejection() -> None:
    base = sorted([*LEDGER, IDENTICAL_COPY], key=ledger_order)
    resubmissions = sorted(RESUBMISSIONS.values(), key=ledger_order)
    live, reasons = _live(base + resubmissions)

    # Every reason that can occur live occurs here.
    assert set(reasons[: len(base)]) == set(RejectionReason) | {None}
    # No resubmission counts: its match_id is taken.
    assert reasons[len(base) :] == [RejectionReason.DUPLICATE_MATCH_ID] * len(resubmissions)

    replayed = replay([*LEDGER, IDENTICAL_COPY], _seeds(), cfg=CFG, as_of=AS_OF)
    assert _state(live) == _state(replayed)
    assert live.possible_duplicates == {"syn-copy": "syn-orig"}


@pytest.mark.parametrize("reason", list(RESUBMISSIONS), ids=lambda r: r.value)
def test_replay_of_a_resubmission_conflicts(reason: RejectionReason) -> None:
    resubmission = RESUBMISSIONS[reason]
    with pytest.raises(LedgerConflictError, match=resubmission.match_id):
        replay([*LEDGER, resubmission], _seeds(), cfg=CFG, as_of=AS_OF)


@pytest.mark.parametrize("reason", REJECTED_LIVE, ids=lambda r: r.value)
def test_a_resubmission_with_a_new_match_id_counts(reason: RejectionReason) -> None:
    rows = sorted(LEDGER, key=ledger_order)
    resubmission = dataclasses.replace(
        RESUBMISSIONS[reason], match_id=f"{RESUBMISSIONS[reason].match_id}-new"
    )
    _, reasons = _live([*rows, resubmission])
    assert reasons[-1] is None


@pytest.mark.parametrize("reason", REJECTED_LIVE, ids=lambda r: r.value)
def test_a_live_rejection_clears_resumable(reason: RejectionReason) -> None:
    base = sorted(LEDGER, key=ledger_order)
    original = next(m for m in base if m.match_id == RESUBMISSIONS[reason].match_id)
    rest = [m for m in base if m.match_id != original.match_id]
    engine = replay(rest, _seeds(), cfg=CFG, as_of=AS_OF)
    assert engine.to_snapshot()["resumable"] is True
    assert engine.record(original).reason is reason
    snapshot = engine.to_snapshot()
    assert snapshot["resumable"] is False
    assert original.match_id in snapshot["reserved_match_ids"]  # type: ignore[operator]


def test_live_and_replayed_snapshots_load() -> None:
    base = sorted([*LEDGER, IDENTICAL_COPY], key=ledger_order)
    live, _ = _live(base + sorted(RESUBMISSIONS.values(), key=ledger_order))
    replayed = replay([*LEDGER, IDENTICAL_COPY], _seeds(), cfg=CFG, as_of=AS_OF)
    rejected = sorted(
        {m.match_id for m in LEDGER} - {"syn-orig", "syn-sr1", "syn-dup"},
    )
    for engine in (live, replayed):
        snapshot = engine.to_snapshot()
        assert snapshot["reserved_match_ids"] == rejected
        assert RatingEngine.from_snapshot(snapshot, CFG).to_snapshot() == snapshot
    resumed = replay(
        [*LEDGER, IDENTICAL_COPY],
        cfg=CFG,
        as_of=AS_OF,
        from_snapshot=replayed.to_snapshot(),
        whole_ledger=True,
    )
    assert resumed.to_snapshot() == replayed.to_snapshot()
