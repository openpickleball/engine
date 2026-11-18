"""Resuming replay from a checkpoint snapshot (issue #16).

A resumed replay must equal a full replay of the same ledger, or raise.
All player and match ids are synthetic.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import random
from datetime import UTC, date, datetime, timedelta

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    InvalidSeedError,
    LedgerConflictError,
    Match,
    MatchType,
    Player,
    PlayerError,
    RatingEngine,
    RatingFormat,
    SnapshotError,
    replay,
)

D = RatingFormat.DOUBLES
S = RatingFormat.SINGLES
MX = RatingFormat.MIXED_DOUBLES
DM = RatingFormat.DOUBLES_MEN
DW = RatingFormat.DOUBLES_WOMEN
START = date(2025, 1, 1)
DAYS = 240

MEN = [f"syn-m{i}" for i in range(6)]
WOMEN = [f"syn-w{i}" for i in range(6)]
SEED_DATE = START - timedelta(days=1)


def _seeds() -> list[Player]:
    eng = RatingEngine()
    eng.seed_external_rating(
        "syn-m0", D, ExternalRatingSnapshot("external_standard", 4.3, SEED_DATE, 72.0, "ext-m0")
    )
    eng.seed_external_rating(
        "syn-w0", S, ExternalRatingSnapshot("external_standard", 3.9, SEED_DATE, 65)
    )
    # A category seed derived from the imported doubles rating.
    eng.seed_provisional_rating("syn-m0", MX)
    return [eng.players[pid] for pid in sorted(eng.players)]


def _score(rng: random.Random, best_of: int) -> tuple[tuple[int, int], ...]:
    wins_needed = best_of // 2 + 1
    games: list[tuple[int, int]] = []
    a = b = 0
    while a < wins_needed and b < wins_needed:
        loser = rng.randint(0, 9)
        if rng.random() < 0.5:
            games.append((11, loser))
            a += 1
        else:
            games.append((loser, 11))
            b += 1
    return tuple(games)


def _teams(rng: random.Random, fmt: RatingFormat) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if fmt is MX:
        men = rng.sample(MEN, 2)
        women = rng.sample(WOMEN, 2)
        return (men[0], women[0]), (men[1], women[1])
    if fmt is DM:
        ids = rng.sample(MEN, 4)
    elif fmt is DW:
        ids = rng.sample(WOMEN, 4)
    else:
        ids = rng.sample(MEN + WOMEN, 2 * fmt.team_size)
    size = fmt.team_size
    return tuple(ids[:size]), tuple(ids[size:])


def synthetic_ledger(seed: int, count: int = 160) -> list[Match]:
    """A shuffled ledger of every format and match type, with self-reported repeats."""
    rng = random.Random(seed)
    matches: list[Match] = []
    repeat: tuple[tuple[str, ...], tuple[str, ...]] = (("syn-m5",), ("syn-w5",))
    for i in range(count):
        on = START + timedelta(days=rng.randrange(DAYS))
        if i % 9 == 0:
            # The same self-reported singles matchup again and again, so
            # the repeat limit and the repeat factor both apply.
            team_a, team_b = repeat
            confirmed = frozenset(team_a + team_b) if rng.random() < 0.85 else frozenset(team_a)
            matches.append(
                Match(
                    f"syn-r{i:03d}",
                    team_a,
                    team_b,
                    _score(rng, 1),
                    on,
                    S,
                    match_type=MatchType.SELF_REPORTED,
                    confirmed_by=confirmed,
                )
            )
            continue
        fmt = rng.choice([D, D, S, MX, DM, DW])
        team_a, team_b = _teams(rng, fmt)
        match_type = rng.choice(list(MatchType))
        best_of = rng.choice([1, 1, 3])
        played_at = None
        if rng.random() < 0.3:
            played_at = datetime(on.year, on.month, on.day, rng.randrange(24), tzinfo=UTC)
        matches.append(
            Match(
                f"syn-x{i:03d}",
                team_a,
                team_b,
                _score(rng, best_of),
                on,
                fmt,
                match_type=match_type,
                best_of=best_of,
                played_at=played_at,
                sequence=rng.randrange(3),
                confirmed_by=frozenset(team_a + team_b),
            )
        )
    rng.shuffle(matches)
    return matches


def _text(engine: RatingEngine) -> str:
    return json.dumps(engine.to_snapshot(), sort_keys=True)


def _checkpoint(ledger: list[Match], cut: date, cfg: Config | None = None) -> dict[str, object]:
    """A checkpoint from the rows known at cut, through JSON as a backend would store it."""
    known = [m for m in ledger if m.played_on <= cut]
    engine = replay(known, _seeds(), cfg=cfg, as_of=cut)
    loaded: dict[str, object] = json.loads(json.dumps(engine.to_snapshot()))
    return loaded


# -- resume equals full replay -------------------------------------------------


@pytest.mark.parametrize("seed", range(30))
def test_resume_equals_full_replay(seed: int) -> None:
    ledger = synthetic_ledger(seed)
    rng = random.Random(1000 + seed)
    dates = sorted({m.played_on for m in ledger})
    # A cut on a date that has matches, so the boundary day is exercised.
    cut = rng.choice(dates[: len(dates) * 3 // 4])
    as_of = cut + timedelta(days=rng.choice([0, 1, 30, DAYS]))
    full = replay(ledger, _seeds(), as_of=as_of)
    snapshot = _checkpoint(ledger, cut)

    whole = replay(ledger, as_of=as_of, from_snapshot=snapshot)
    rest = replay([m for m in ledger if m.played_on > cut], as_of=as_of, from_snapshot=snapshot)
    checked = replay(ledger, as_of=as_of, from_snapshot=snapshot, whole_ledger=True)

    assert _text(whole) == _text(full)
    assert _text(rest) == _text(full)
    assert _text(checked) == _text(full)


def test_the_property_ledgers_exercise_what_they_should() -> None:
    engines = [
        replay(synthetic_ledger(seed), _seeds(), as_of=START + timedelta(DAYS)) for seed in range(5)
    ]
    assert all(e.self_reported_log for e in engines)
    assert all(e.players["syn-m0"].ratings[MX].matches_played for e in engines)
    assert all(e.players["syn-m1"].ratings.get(DM) for e in engines)
    assert all(e.players["syn-w1"].ratings.get(DW) for e in engines)
    info = engines[0].replay_info
    assert info is not None and info.matches_counted < info.matches_considered


@pytest.mark.parametrize("seed", range(5))
def test_chained_resumes_equal_full_replay(seed: int) -> None:
    ledger = synthetic_ledger(seed)
    first, second, end = START + timedelta(60), START + timedelta(150), START + timedelta(DAYS)
    snapshot = _checkpoint(ledger, first)
    middle = replay(ledger, as_of=second, from_snapshot=snapshot)
    last = replay(ledger, as_of=end, from_snapshot=json.loads(json.dumps(middle.to_snapshot())))
    assert _text(middle) == _text(replay(ledger, _seeds(), as_of=second))
    assert _text(last) == _text(replay(ledger, _seeds(), as_of=end))


def test_resume_counts_add_to_the_checkpoint_counts() -> None:
    ledger = synthetic_ledger(3)
    cut, end = START + timedelta(100), START + timedelta(DAYS)
    snapshot = _checkpoint(ledger, cut)
    resumed = replay(ledger, as_of=end, from_snapshot=snapshot)
    full = replay(ledger, _seeds(), as_of=end)
    assert resumed.replay_info == full.replay_info
    assert resumed.calculated_as_of == end


def test_resume_does_not_change_the_snapshot() -> None:
    ledger = synthetic_ledger(4)
    snapshot = _checkpoint(ledger, START + timedelta(100))
    before = copy.deepcopy(snapshot)
    replay(ledger, as_of=START + timedelta(DAYS), from_snapshot=snapshot)
    assert snapshot == before


# -- the checkpoint date -------------------------------------------------------

CUT = date(2025, 3, 1)


def _row(match_id: str, on: date, games: tuple[tuple[int, int], ...] = ((11, 6),)) -> Match:
    return Match(match_id, ("syn-a", "syn-b"), ("syn-c", "syn-d"), games, on, D)


BOUNDARY_LEDGER = [
    _row("syn-1", CUT - timedelta(1)),
    _row("syn-2", CUT),
    _row("syn-3", CUT + timedelta(1)),
]


def _boundary_checkpoint() -> dict[str, object]:
    engine = replay(BOUNDARY_LEDGER[:2], as_of=CUT)
    loaded: dict[str, object] = json.loads(json.dumps(engine.to_snapshot()))
    return loaded


def test_a_checkpointed_row_on_the_checkpoint_date_is_not_counted_again() -> None:
    end = CUT + timedelta(5)
    resumed = replay(BOUNDARY_LEDGER, as_of=end, from_snapshot=_boundary_checkpoint())
    full = replay(BOUNDARY_LEDGER, as_of=end)
    assert _text(resumed) == _text(full)
    assert resumed.players["syn-a"].ratings[D].matches_played == 3


def test_a_row_the_day_after_the_checkpoint_is_counted() -> None:
    end = CUT + timedelta(1)
    resumed = replay(BOUNDARY_LEDGER[2:], as_of=end, from_snapshot=_boundary_checkpoint())
    assert resumed.players["syn-a"].ratings[D].matches_played == 3
    assert _text(resumed) == _text(replay(BOUNDARY_LEDGER, as_of=end))


def test_resume_to_the_checkpoint_date_itself() -> None:
    resumed = replay(BOUNDARY_LEDGER, as_of=CUT, from_snapshot=_boundary_checkpoint())
    assert _text(resumed) == _text(replay(BOUNDARY_LEDGER, as_of=CUT))


# -- rejections ----------------------------------------------------------------


def test_resume_rejects_a_snapshot_that_is_not_from_a_replay() -> None:
    engine = RatingEngine()
    engine.record(BOUNDARY_LEDGER[0])
    with pytest.raises(SnapshotError, match="not from a replay"):
        replay(BOUNDARY_LEDGER, as_of=CUT, from_snapshot=engine.to_snapshot())


def test_resume_rejects_a_snapshot_with_live_updates() -> None:
    engine = replay(BOUNDARY_LEDGER[:1], as_of=CUT - timedelta(1))
    engine.record(BOUNDARY_LEDGER[1])
    with pytest.raises(SnapshotError, match="live update"):
        replay(BOUNDARY_LEDGER, as_of=CUT, from_snapshot=engine.to_snapshot())


def test_resume_rejects_a_live_update_count_without_a_live_match() -> None:
    snapshot = _boundary_checkpoint()
    players = snapshot["players"]
    assert isinstance(players, list)
    players[0]["ratings"]["doubles"]["live_updates_since_replay"] = 1
    with pytest.raises(
        SnapshotError, match=r"live_updates_since_replay must be 0 in a resumable snapshot"
    ):
        replay(BOUNDARY_LEDGER, as_of=CUT, from_snapshot=snapshot)


def test_resume_rejects_as_of_before_the_checkpoint() -> None:
    with pytest.raises(SnapshotError, match="as_of 2025-02-28 is before the checkpoint"):
        replay(BOUNDARY_LEDGER, as_of=CUT - timedelta(1), from_snapshot=_boundary_checkpoint())


@pytest.mark.parametrize("on", [CUT - timedelta(10), CUT], ids=["before", "on"])
def test_resume_rejects_a_late_row_on_or_before_the_checkpoint(on: date) -> None:
    late = _row("syn-late", on)
    with pytest.raises(SnapshotError, match="not in the checkpoint: syn-late"):
        replay(
            [*BOUNDARY_LEDGER, late], as_of=CUT + timedelta(1), from_snapshot=_boundary_checkpoint()
        )


def test_resume_rejects_a_changed_row_before_the_checkpoint() -> None:
    changed = dataclasses.replace(BOUNDARY_LEDGER[0], games=((11, 9),))
    with pytest.raises(LedgerConflictError, match="syn-1") as raised:
        replay(
            [changed, *BOUNDARY_LEDGER[1:]],
            as_of=CUT + timedelta(1),
            from_snapshot=_boundary_checkpoint(),
        )
    assert raised.value.match_ids == ("syn-1",)


def test_resume_rejects_a_changed_row_on_the_checkpoint_date() -> None:
    changed = dataclasses.replace(BOUNDARY_LEDGER[1], match_type=MatchType.LEAGUE)
    with pytest.raises(LedgerConflictError, match="syn-2"):
        replay([changed], as_of=CUT + timedelta(1), from_snapshot=_boundary_checkpoint())


def test_resume_rejects_a_reused_id_after_the_checkpoint() -> None:
    reused = _row("syn-1", CUT + timedelta(2))
    with pytest.raises(LedgerConflictError, match="syn-1"):
        replay([reused], as_of=CUT + timedelta(5), from_snapshot=_boundary_checkpoint())


def test_resume_reports_conflicts_before_missing_rows() -> None:
    changed = dataclasses.replace(BOUNDARY_LEDGER[0], games=((11, 9),))
    late = _row("syn-late", CUT)
    with pytest.raises(LedgerConflictError, match="syn-1"):
        replay([changed, late], as_of=CUT, from_snapshot=_boundary_checkpoint())


def test_resume_rejects_another_config() -> None:
    with pytest.raises(SnapshotError, match="config_fingerprint .* does not match"):
        replay(
            BOUNDARY_LEDGER,
            cfg=Config(k_max=0.5),
            as_of=CUT + timedelta(1),
            from_snapshot=_boundary_checkpoint(),
        )


def test_resume_rejects_a_malformed_snapshot() -> None:
    snapshot = _boundary_checkpoint()
    snapshot["processed_match_ids"] = "syn-1"
    with pytest.raises(SnapshotError, match="processed_match_ids must be a list"):
        replay(BOUNDARY_LEDGER, as_of=CUT, from_snapshot=snapshot)


def test_resume_rejects_a_seed_for_a_player_in_the_snapshot() -> None:
    seed = Player("syn-a")
    with pytest.raises(PlayerError, match="Player already exists: syn-a"):
        replay(BOUNDARY_LEDGER, [seed], as_of=CUT, from_snapshot=_boundary_checkpoint())


def test_resume_rejects_a_seed_dated_after_as_of() -> None:
    eng = RatingEngine()
    eng.seed_external_rating(
        "syn-new", D, ExternalRatingSnapshot("external_standard", 4.0, CUT + timedelta(9))
    )
    with pytest.raises(InvalidSeedError, match="must not be after as_of"):
        replay(
            BOUNDARY_LEDGER,
            [eng.players["syn-new"]],
            as_of=CUT + timedelta(1),
            from_snapshot=_boundary_checkpoint(),
        )


def test_resume_accepts_a_seed_for_a_new_player() -> None:
    eng = RatingEngine()
    eng.seed_external_rating(
        "syn-new", D, ExternalRatingSnapshot("external_standard", 4.0, CUT + timedelta(1))
    )
    seed = eng.players["syn-new"]
    later = Match(
        "syn-9", ("syn-new", "syn-a"), ("syn-c", "syn-d"), ((11, 4),), CUT + timedelta(3), D
    )
    ledger = [*BOUNDARY_LEDGER, later]
    end = CUT + timedelta(5)
    resumed = replay(ledger, [seed], as_of=end, from_snapshot=_boundary_checkpoint())
    assert _text(resumed) == _text(replay(ledger, [seed], as_of=end))
