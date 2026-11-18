"""Reliability sees whether a player's pool connects to the main network (issue #58)."""

from __future__ import annotations

import json
import random
from datetime import date, timedelta

import pytest

from open_engine import (
    Config,
    ConfigError,
    Match,
    MatchType,
    NetworkConnectivity,
    PlayerSummary,
    RatingEngine,
    RatingFormat,
    reliability,
    replay,
)

DOUBLES = RatingFormat.DOUBLES
START = date(2026, 1, 5)
WEEKS = 26
END = START + timedelta(weeks=WEEKS - 1)
CLUB = [f"club{i:02d}" for i in range(14)]
WIDE = [f"wide{i:03d}" for i in range(120)]
SCORES = [(11, 9), (9, 11), (11, 7), (7, 11)]


def doubles(match_id: str, players: list[str], on: date, rng: random.Random) -> Match:
    a, b, c, d = players
    return Match(
        match_id=match_id,
        team_a=(a, b),
        team_b=(c, d),
        games=(rng.choice(SCORES),),
        played_on=on,
        rating_format=DOUBLES,
        match_type=MatchType.LEAGUE,
    )


def pool_rows(prefix: str, ids: list[str], per_week: int, seed: int) -> list[Match]:
    """Weekly league doubles among ids only, with random partners and opponents."""
    rng = random.Random(seed)
    return [
        doubles(f"{prefix}{w}-{k}", rng.sample(ids, 4), START + timedelta(weeks=w), rng)
        for w in range(WEEKS)
        for k in range(per_week)
    ]


def club_rows() -> list[Match]:
    return pool_rows("c", CLUB, 4, 1)


def wide_rows() -> list[Match]:
    return pool_rows("w", WIDE, 15, 2)


def bridge_rows(count: int, *, weekly: bool) -> list[Match]:
    """Matches of two club players against two wide players."""
    rng = random.Random(3)
    rows = []
    for i in range(count):
        on = START + timedelta(weeks=i) if weekly else START + timedelta(weeks=10)
        players = rng.sample(CLUB, 2) + rng.sample(WIDE, 2)
        rows.append(doubles(f"b{i}", players, on, rng))
    return rows


def summaries(engine: RatingEngine, ids: list[str]) -> dict[str, PlayerSummary]:
    return {pid: engine.player_summary(pid, DOUBLES) for pid in ids}


# Criterion 1: a closed pool is capped, a matched connected pool is not.


def test_closed_pool_is_capped_below_established() -> None:
    cfg = Config()
    engine = replay(club_rows() + wide_rows(), as_of=END)
    for pid, summary in summaries(engine, CLUB).items():
        connectivity = summary.connectivity
        assert connectivity.connected is False, pid
        assert connectivity.in_main_network is False, pid
        assert connectivity.pool_size == 14, pid
        assert connectivity.outside_players == 0, pid
        assert connectivity.network_size == 120, pid
        assert cfg.connectivity_reliability_cap is not None
        assert summary.reliability <= cfg.connectivity_reliability_cap
        assert summary.reliability < cfg.established_reliability_threshold
        assert summary.established is False, pid
        # The cap binds: the earned value from RD and variety alone is higher.
        state = engine.state(pid, DOUBLES)
        assert reliability(state, cfg, END) > cfg.established_reliability_threshold
    for summary in summaries(engine, WIDE[:10]).values():
        assert summary.connectivity.connected is True
        assert summary.connectivity.in_main_network is True


def test_matched_pool_that_plays_the_wider_network_is_established() -> None:
    engine = replay(club_rows() + wide_rows() + bridge_rows(WEEKS, weekly=True), as_of=END)
    for pid, summary in summaries(engine, CLUB).items():
        assert summary.connectivity.connected is True, pid
        assert summary.connectivity.in_main_network is True, pid
        assert summary.established is True, pid


# Criterion 2: one cross-pool match, or one day of them, does not lift the cap.


@pytest.mark.parametrize("count", [1, 4])
def test_one_day_of_cross_pool_matches_does_not_lift_the_cap(count: int) -> None:
    cfg = Config()
    engine = replay(club_rows() + wide_rows() + bridge_rows(count, weekly=False), as_of=END)
    for pid, summary in summaries(engine, CLUB).items():
        connectivity = summary.connectivity
        assert connectivity.in_main_network is True, pid
        assert connectivity.network_size == 134, pid
        assert connectivity.pool_size == 14, pid
        assert connectivity.outside_days == 1, pid
        assert connectivity.connected is False, pid
        assert summary.established is False, pid
        assert cfg.connectivity_reliability_cap is not None
        assert summary.reliability <= cfg.connectivity_reliability_cap
    assert summaries(engine, CLUB)["club00"].connectivity.outside_players <= 2 * count


# Criterion 3: replay order, shuffles, and resume give the same values.


def test_shuffled_ledger_gives_the_same_connectivity() -> None:
    rows = club_rows() + wide_rows() + bridge_rows(1, weekly=False)
    shuffled = list(rows)
    random.Random(9).shuffle(shuffled)
    first = summaries(replay(rows, as_of=END), CLUB + WIDE)
    second = summaries(replay(shuffled, as_of=END), CLUB + WIDE)
    assert first == second


def test_resume_gives_the_same_connectivity_as_a_full_replay() -> None:
    rows = club_rows() + wide_rows() + bridge_rows(3, weekly=True)
    checkpoint = START + timedelta(weeks=12)
    first = replay(rows, as_of=checkpoint)
    snapshot = json.loads(json.dumps(first.to_snapshot()))
    resumed = replay(rows, as_of=END, from_snapshot=snapshot, whole_ledger=True)
    full = replay(rows, as_of=END)
    assert summaries(resumed, CLUB + WIDE) == summaries(full, CLUB + WIDE)


def test_live_recording_matches_replay_connectivity() -> None:
    rows = club_rows() + wide_rows() + bridge_rows(1, weekly=False)
    live = RatingEngine()
    for match in sorted(rows, key=lambda m: (m.played_on, m.match_id)):
        live.record(match)
    full = replay(rows, as_of=END)
    for pid in CLUB + WIDE[:5]:
        assert (
            live.player_summary(pid, DOUBLES, as_of=END).connectivity
            == full.player_summary(pid, DOUBLES).connectivity
        )


def test_summary_after_a_new_match_is_measured_again() -> None:
    engine = replay(club_rows() + wide_rows(), as_of=END)
    before = engine.player_summary("club00", DOUBLES, as_of=END).connectivity
    assert before.in_main_network is False
    rng = random.Random(4)
    engine.record(doubles("late", ["club00", "club01", "wide000", "wide001"], END, rng))
    after = engine.player_summary("club00", DOUBLES, as_of=END).connectivity
    assert after.in_main_network is True
    assert after.outside_players == 2


# Criterion 4: the cap set to None leaves reliability and established as before.


def test_cap_none_keeps_the_earned_reliability() -> None:
    cfg = Config(connectivity_reliability_cap=None)
    engine = replay(club_rows() + wide_rows(), cfg=cfg, as_of=END)
    for pid in CLUB + WIDE:
        summary = engine.player_summary(pid, DOUBLES)
        state = engine.state(pid, DOUBLES)
        earned = reliability(state, cfg, END)
        assert summary.reliability == earned
        assert summary.established is (
            earned >= cfg.established_reliability_threshold
            and state.verified_matches_played >= cfg.established_min_verified_matches
        )
    assert engine.player_summary("club00", DOUBLES).connectivity.connected is False


def test_the_cap_changes_no_rating() -> None:
    rows = club_rows() + wide_rows() + bridge_rows(2, weekly=False)
    capped = replay(rows, as_of=END)
    uncapped = replay(rows, cfg=Config(connectivity_reliability_cap=None), as_of=END)
    for pid in CLUB + WIDE:
        assert capped.state(pid, DOUBLES) == uncapped.state(pid, DOUBLES)


# The rule's cases.


def test_one_doubles_match_ties_no_one() -> None:
    rng = random.Random(5)
    engine = replay([doubles("m1", ["a", "b", "c", "d"], START, rng)], as_of=START)
    connectivity = engine.player_summary("a", DOUBLES).connectivity
    assert connectivity == NetworkConnectivity(
        pool_size=1,
        outside_players=3,
        outside_days=1,
        network_size=4,
        in_main_network=True,
        connected=True,
    )


def test_a_pool_that_is_the_whole_ledger_is_connected() -> None:
    engine = replay(club_rows(), as_of=END)
    for summary in summaries(engine, CLUB).values():
        assert summary.connectivity.pool_size == 14
        assert summary.connectivity.network_size == 14
        assert summary.connectivity.connected is True
        assert summary.established is True


def test_two_equal_separate_pools_have_no_main_network() -> None:
    other = [f"other{i:02d}" for i in range(14)]
    engine = replay(club_rows() + pool_rows("o", other, 4, 1), as_of=END)
    for summary in summaries(engine, CLUB + other).values():
        assert summary.connectivity.network_size == 0
        assert summary.connectivity.in_main_network is False
        assert summary.connectivity.connected is False
        assert summary.established is False


def singles(match_id: str, a: str, b: str, on: date) -> Match:
    return Match(
        match_id=match_id,
        team_a=(a,),
        team_b=(b,),
        games=((11, 9),),
        played_on=on,
        rating_format=RatingFormat.SINGLES,
        match_type=MatchType.LEAGUE,
    )


def test_a_player_with_no_state_is_measured_as_if_the_state_were_stored() -> None:
    # A summary for a missing format reports the state a seed would store,
    # so the connectivity is what storing that unplayed state would give.
    engine = replay(wide_rows() + [singles("s", "solo", "wide000", END)], as_of=END)
    summary = engine.player_summary("solo", DOUBLES)
    assert summary.connectivity == NetworkConnectivity(
        pool_size=1,
        outside_players=0,
        outside_days=0,
        network_size=120,
        in_main_network=False,
        connected=False,
    )
    engine.seed_provisional_rating("solo", DOUBLES)
    assert engine.player_summary("solo", DOUBLES) == summary


def test_a_lone_state_in_an_empty_format_is_its_own_network() -> None:
    engine = replay(club_rows(), as_of=END)
    summary = engine.player_summary("club00", RatingFormat.SINGLES)
    assert summary.connectivity == NetworkConnectivity(
        pool_size=1,
        outside_players=0,
        outside_days=0,
        network_size=1,
        in_main_network=True,
        connected=True,
    )
    engine.seed_provisional_rating("club00", RatingFormat.SINGLES)
    assert engine.player_summary("club00", RatingFormat.SINGLES) == summary


def test_contacts_outside_the_window_do_not_count() -> None:
    engine = replay(club_rows() + wide_rows(), as_of=END)
    later = END + timedelta(days=Config().diversity_window_days + 1)
    connectivity = engine.player_summary("club00", DOUBLES, as_of=later).connectivity
    assert connectivity.pool_size == 1
    assert connectivity.network_size == 0
    assert connectivity.connected is False


# Criterion 5: Config fields.


def test_defaults() -> None:
    cfg = Config()
    assert cfg.connectivity_reliability_cap == 50.0
    assert cfg.connectivity_min_shared_contacts == 4
    assert cfg.connectivity_min_contact_days == 3
    assert cfg.connectivity_min_outside_players == 8


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("connectivity_reliability_cap", None),
        ("connectivity_reliability_cap", 0.0),
        ("connectivity_reliability_cap", 59.9),
        ("connectivity_min_shared_contacts", 2),
        ("connectivity_min_contact_days", 1),
        ("connectivity_min_outside_players", 20),
    ],
)
def test_fields_are_in_the_fingerprint_and_round_trip(name: str, value: object) -> None:
    cfg = Config(**{name: value})
    assert cfg.fingerprint() != Config().fingerprint()
    assert Config.from_dict(json.loads(json.dumps(cfg.to_dict()))) == cfg


@pytest.mark.parametrize("value", [-0.1, 100.1, float("nan"), "50", True])
def test_cap_out_of_range_raises(value: object) -> None:
    with pytest.raises(
        ConfigError, match="connectivity_reliability_cap must be None or a number from 0 to 100"
    ):
        Config(connectivity_reliability_cap=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "name",
    [
        "connectivity_min_shared_contacts",
        "connectivity_min_contact_days",
        "connectivity_min_outside_players",
    ],
)
@pytest.mark.parametrize("value", [0, -1, True, 2.0, "3", None])
def test_counts_must_be_positive_ints(name: str, value: object) -> None:
    with pytest.raises(ConfigError, match=f"{name} must be a positive int"):
        Config(**{name: value})


def test_a_visitor_for_one_day_does_not_tie_into_the_pool() -> None:
    # A wide player plays four matches at the club on one day. The club
    # members reach the shared contacts on many dates, the visitor on one,
    # so the visitor is not tied in and brings no outside contacts.
    rng = random.Random(6)
    day = START + timedelta(weeks=12)
    visits = [doubles(f"v{i}", ["wide000", *rng.sample(CLUB, 3)], day, rng) for i in range(4)]
    engine = replay(club_rows() + wide_rows() + visits, as_of=END)
    for pid, summary in summaries(engine, CLUB).items():
        assert summary.connectivity.pool_size == 14, pid
        assert summary.connectivity.outside_players == 1, pid
        assert summary.connectivity.connected is False, pid
    assert engine.player_summary("wide000", DOUBLES).connectivity.connected is True
