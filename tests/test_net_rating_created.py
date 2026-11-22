"""ReplayInfo.net_rating_created: rating each replay added to or removed from a pool (#42).

All player and match ids are synthetic.
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta

import pytest

from open_engine import (
    ExternalRatingSnapshot,
    Match,
    MatchResult,
    MatchType,
    Player,
    RatingEngine,
    RatingFormat,
    replay,
)
from open_engine.engine import ReplayInfo

S = RatingFormat.SINGLES
D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
CAPTURED = date(2026, 1, 5)
FEEDER_AS_OF = date(2026, 1, 25)


def _imported(pid: str, fmt: RatingFormat, rating: float, on: date) -> Player:
    seeder = RatingEngine()
    seeder.seed_external_rating(pid, fmt, ExternalRatingSnapshot("ext", rating, on, 80.0))
    return seeder.players[pid]


def _feeder_ledger() -> list[Match]:
    return [
        Match(
            f"syn-f{i:02d}",
            ("syn-new",),
            ("syn-feed",),
            ((11, 5),),
            CAPTURED + timedelta(days=i + 1),
            S,
            match_type=MatchType.REC_VERIFIED,
        )
        for i in range(10)
    ]


def _feeder() -> RatingEngine:
    seeds = [_imported("syn-feed", S, 4.0, CAPTURED)]
    return replay(_feeder_ledger(), seeds, as_of=FEEDER_AS_OF)


def _total_change(engine: RatingEngine, fmt: RatingFormat) -> float:
    return math.fsum(
        player.ratings[fmt].rating - player.ratings[fmt].initial_rating
        for player in engine.players.values()
        if fmt in player.ratings
    )


# -- the feeder scenario -------------------------------------------------------


def test_feeder_scenario_reports_rating_created() -> None:
    engine = _feeder()
    info = engine.replay_info
    assert info is not None
    created = info.net_rating_created
    assert created[S] > 0.2
    assert created[S] == pytest.approx(_total_change(engine, S), abs=1e-12)
    new = engine.players["syn-new"].ratings[S].rating
    feed = engine.players["syn-feed"].ratings[S].rating
    assert created[S] == pytest.approx((new - 3.5) + (feed - 4.0), abs=1e-12)
    for fmt in RatingFormat:
        if fmt is not S:
            assert created[fmt] == 0.0
            assert type(created[fmt]) is float


def test_every_format_has_a_key_in_definition_order() -> None:
    info = _feeder().replay_info
    assert info is not None
    assert list(info.net_rating_created) == list(RatingFormat)


def test_mapping_is_read_only() -> None:
    info = _feeder().replay_info
    assert info is not None
    with pytest.raises(TypeError):
        info.net_rating_created[S] = 0.0  # type: ignore[index]


def test_replay_info_stays_hashable() -> None:
    info = _feeder().replay_info
    assert info is not None
    assert hash(info) == hash(_feeder().replay_info)


def test_replay_info_built_positionally_defaults_to_zero() -> None:
    info = ReplayInfo(FEEDER_AS_OF, "v", "f", None, "p", 0, 0)
    assert dict(info.net_rating_created) == dict.fromkeys(RatingFormat, 0.0)


def test_replay_info_copies_the_mapping() -> None:
    given = dict.fromkeys(RatingFormat, 0.0)
    info = ReplayInfo(FEEDER_AS_OF, "v", "f", None, "p", 1, 1, given)
    given[S] = 9.0
    assert info.net_rating_created[S] == 0.0


def test_live_updates_do_not_change_replay_info() -> None:
    engine = _feeder()
    before = engine.replay_info
    result = engine.record(
        Match("syn-live", ("syn-new",), ("syn-feed",), ((11, 2),), FEEDER_AS_OF, S)
    )
    assert result.counted and result.imbalance != 0.0
    assert engine.replay_info == before


# -- a mixed ledger ------------------------------------------------------------

START = date(2025, 3, 3)
AS_OF = date(2025, 9, 30)
CHECKPOINT = date(2025, 6, 1)
TYPES = tuple(MatchType)


def _mixed_ledger() -> list[Match]:
    """Singles, doubles, and mixed rows of every match type; some self-reported are unconfirmed."""
    rows: list[Match] = []
    for i in range(36):
        if i % 3 == 0:
            fmt, team_a, team_b = S, ("syn-a",), ("syn-c" if i % 2 else "syn-imp",)
            games = ((11, 3 + i % 7),)
        elif i % 3 == 1:
            fmt, team_a, team_b = D, ("syn-a", f"syn-p{i % 4}"), ("syn-c", "syn-imp")
            games = ((11, 4 + i % 6),)
        else:
            fmt, team_a, team_b = MX, ("syn-a", "syn-imp"), ("syn-c", f"syn-p{i % 4}")
            games = ((5 + i % 4, 11),)
        rows.append(
            Match(
                f"syn-{fmt.value}-{i:02d}",
                team_a,
                team_b,
                games,
                START + timedelta(days=5 * i),
                fmt,
                match_type=TYPES[i % len(TYPES)],
                confirmed_by=frozenset({team_a[0], team_b[0]}) if i % 7 else frozenset(),
            )
        )
    return rows


def _mixed_seeds() -> list[Player]:
    seeder = RatingEngine()
    # Captured mid-ledger, so the matches before it are frozen for syn-imp.
    on = START + timedelta(days=40)
    for fmt, rating in ((S, 4.4), (D, 4.2), (MX, 4.0)):
        seeder.seed_external_rating("syn-imp", fmt, ExternalRatingSnapshot("ext", rating, on))
    return [seeder.players["syn-imp"]]


def _live_results(rows: list[Match]) -> list[MatchResult]:
    engine = RatingEngine()
    for player in _mixed_seeds():
        engine.add_player(player)
    ordered = sorted(rows, key=lambda m: (m.played_on, m.match_id))
    return [engine.record(m, as_of=AS_OF) for m in ordered if m.played_on <= AS_OF]


def test_mixed_ledger_has_frozen_rejected_and_category_results() -> None:
    results = _live_results(_mixed_ledger())
    assert any(r.frozen for r in results)
    assert any(not r.counted for r in results)
    assert any(r.parent is not None and r.parent.counted for r in results)


def test_totals_match_the_sum_of_imbalances() -> None:
    engine = replay(_mixed_ledger(), _mixed_seeds(), as_of=AS_OF)
    info = engine.replay_info
    assert info is not None
    expected = dict.fromkeys(RatingFormat, 0.0)
    bound = dict.fromkeys(RatingFormat, 1e-9)
    for result in _live_results(_mixed_ledger()):
        for part in (result, result.parent):
            if part is None or not part.counted:
                assert part is None or part.imbalance == 0.0
                continue
            size = part.rating_format.team_size
            expected[part.rating_format] += size * part.imbalance
            bound[part.rating_format] += size * 5e-6
    assert expected[MX] != 0.0 and expected[D] != 0.0
    for fmt in RatingFormat:
        assert abs(info.net_rating_created[fmt] - expected[fmt]) <= bound[fmt]


def test_totals_match_every_players_rating_change() -> None:
    engine = replay(_mixed_ledger(), _mixed_seeds(), as_of=AS_OF)
    info = engine.replay_info
    assert info is not None
    for fmt in RatingFormat:
        assert info.net_rating_created[fmt] == pytest.approx(_total_change(engine, fmt), abs=1e-12)


def test_category_match_adds_to_its_format_and_doubles() -> None:
    rows = [
        Match("syn-x1", ("syn-a", "syn-imp"), ("syn-c", "syn-p1"), ((11, 6),), START, MX),
    ]
    seeds = [_imported("syn-imp", D, 4.6, START - timedelta(days=1))]
    engine = replay(rows, seeds, as_of=START)
    info = engine.replay_info
    assert info is not None
    live = RatingEngine()
    live.add_player(_imported("syn-imp", D, 4.6, START - timedelta(days=1)))
    result = live.record(rows[0], as_of=START)
    assert result.parent is not None and result.parent.counted
    assert result.imbalance != 0.0 and result.parent.imbalance != 0.0
    assert info.net_rating_created[MX] == pytest.approx(2 * result.imbalance, abs=1e-5)
    assert info.net_rating_created[D] == pytest.approx(2 * result.parent.imbalance, abs=1e-5)
    assert info.net_rating_created[S] == 0.0


def test_frozen_players_add_nothing() -> None:
    row = Match("syn-s1", ("syn-a",), ("syn-imp",), ((11, 4),), START, S)
    seeds = [_imported("syn-imp", S, 4.0, START)]
    engine = replay([row], seeds, as_of=START)
    info = engine.replay_info
    assert info is not None
    moved = engine.players["syn-a"].ratings[S]
    assert engine.players["syn-imp"].ratings[S].rating == 4.0
    assert info.net_rating_created[S] == moved.rating - moved.initial_rating


def test_rejected_matches_add_nothing() -> None:
    rows = [
        Match(
            f"syn-r{i}",
            ("syn-a",),
            ("syn-c",),
            ((11, 2),),
            START + timedelta(days=i),
            S,
            match_type=MatchType.SELF_REPORTED,
        )
        for i in range(3)
    ]
    info = replay(rows, as_of=AS_OF).replay_info
    assert info is not None
    assert info.matches_counted == 0
    assert dict(info.net_rating_created) == dict.fromkeys(RatingFormat, 0.0)


# -- determinism and resume ----------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_shuffled_ledger_gives_identical_replay_info(seed: int) -> None:
    rows = _mixed_ledger()
    full = replay(rows, _mixed_seeds(), as_of=AS_OF).replay_info
    shuffled = list(rows)
    random.Random(seed).shuffle(shuffled)
    again = replay(shuffled, _mixed_seeds(), as_of=AS_OF).replay_info
    assert again == full
    assert full is not None and again is not None
    for fmt in RatingFormat:
        assert again.net_rating_created[fmt].hex() == full.net_rating_created[fmt].hex()


@pytest.mark.parametrize("through_json", [False, True])
def test_resume_gives_the_full_replay_info(through_json: bool) -> None:
    rows = _mixed_ledger()
    full = replay(rows, _mixed_seeds(), as_of=AS_OF)
    checkpoint = replay(rows, _mixed_seeds(), as_of=CHECKPOINT)
    assert checkpoint.replay_info is not None
    assert any(checkpoint.replay_info.net_rating_created.values())
    snapshot: dict[str, object] = checkpoint.to_snapshot()
    if through_json:
        snapshot = json.loads(json.dumps(snapshot, allow_nan=False))
    resumed = replay(rows, as_of=AS_OF, from_snapshot=snapshot)
    assert resumed.replay_info == full.replay_info
    assert resumed.replay_info is not None and full.replay_info is not None
    for fmt in RatingFormat:
        got = resumed.replay_info.net_rating_created[fmt]
        assert got.hex() == full.replay_info.net_rating_created[fmt].hex()
