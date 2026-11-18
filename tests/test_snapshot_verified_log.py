"""A snapshot's verified_matches_played must agree with the self-reported log (#59).

A stored snapshot sets every label when loaded, so a snapshot that relabels
self-reported matches as verified must not load. All ids are synthetic.
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    InvalidSeedError,
    Match,
    MatchType,
    Player,
    RatingEngine,
    RatingFormat,
    SnapshotError,
    replay,
)

S = RatingFormat.SINGLES
D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
START = date(2026, 1, 5)
SR = MatchType.SELF_REPORTED


def _sr_doubles(count: int) -> list[Match]:
    return [
        Match(
            f"syn-sr{i:02d}",
            ("syn-p", f"syn-q{i}"),
            (f"syn-r{i}", f"syn-s{i}"),
            ((11, 9),),
            START + timedelta(weeks=i),
            D,
            match_type=SR,
            confirmed_by=frozenset({"syn-p", f"syn-r{i}"}),
        )
        for i in range(count)
    ]


def _json(engine: RatingEngine) -> dict[str, object]:
    loaded: dict[str, object] = json.loads(json.dumps(engine.to_snapshot()))
    return loaded


def _state(snap: dict[str, object], pid: str, fmt: str) -> dict[str, object]:
    players = snap["players"]
    assert isinstance(players, list)
    for player in players:
        if player["id"] == pid:
            state: dict[str, object] = player["ratings"][fmt]
            return state
    raise AssertionError(pid)


def _imported(pid: str, fmt: RatingFormat, rating: float, on: date) -> Player:
    seeder = RatingEngine()
    seeder.seed_external_rating(pid, fmt, ExternalRatingSnapshot("ext", rating, on))
    return seeder.players[pid]


# -- the attack ----------------------------------------------------------------


@pytest.mark.parametrize("resumable", [True, False])
def test_relabeled_self_reported_matches_do_not_load(resumable: bool) -> None:
    rows = _sr_doubles(30)
    snap = _json(replay(rows, as_of=rows[-1].played_on))
    snap["resumable"] = resumable
    _state(snap, "syn-p", "doubles")["verified_matches_played"] = 30
    with pytest.raises(
        SnapshotError,
        match=(
            r"ratings\.doubles: matches_played - verified_matches_played \(0\) must be at "
            r"least the self-reported doubles matches logged for this player \(30\)"
        ),
    ):
        RatingEngine.from_snapshot(snap)


def test_relabeled_imported_player_does_not_load_from_a_replay() -> None:
    rows = _sr_doubles(30)
    seeds = [_imported("syn-p", D, 4.0, START - timedelta(days=1))]
    snap = _json(replay(rows, seeds, as_of=rows[-1].played_on))
    _state(snap, "syn-p", "doubles")["verified_matches_played"] = 30
    with pytest.raises(SnapshotError, match=r"ratings\.doubles: matches_played - verified"):
        RatingEngine.from_snapshot(snap)


def _singles(pid: str, other: str, count: int, kind: MatchType, offset: int = 0) -> list[Match]:
    return [
        Match(
            f"syn-{pid}-{kind.value}-{i}",
            (pid,),
            (other,),
            ((11, 6),),
            START + timedelta(days=100 * i + offset),
            S,
            match_type=kind,
            confirmed_by=frozenset({pid, other}),
        )
        for i in range(count)
    ]


def _singles_snapshot(resumable: bool) -> dict[str, object]:
    rows = _singles("syn-a", "syn-b", 2, SR) + _singles("syn-a", "syn-b", 1, MatchType.LEAGUE, 3)
    snap = _json(replay(rows, as_of=START + timedelta(days=400)))
    state = _state(snap, "syn-a", "singles")
    assert (state["matches_played"], state["verified_matches_played"]) == (3, 1)
    snap["resumable"] = resumable
    return snap


@pytest.mark.parametrize("resumable", [True, False])
@pytest.mark.parametrize(("verified", "unverified"), [(0, 3), (2, 1), (3, 0)])
def test_provisional_singles_count_must_equal_the_log(
    resumable: bool, verified: int, unverified: int
) -> None:
    snap = _singles_snapshot(resumable)
    _state(snap, "syn-a", "singles")["verified_matches_played"] = verified
    with pytest.raises(
        SnapshotError,
        match=(
            rf"ratings\.singles: matches_played - verified_matches_played \({unverified}\) "
            r"must equal the self-reported singles matches logged for this player \(2\)"
        ),
    ):
        RatingEngine.from_snapshot(snap)


def _imported_live_snapshot() -> dict[str, object]:
    engine = RatingEngine()
    engine.add_player(_imported("syn-a", S, 4.0, START - timedelta(days=1)))
    for match in _singles("syn-a", "syn-b", 2, SR):
        assert engine.record(match).counted
    snap = _json(engine)
    state = _state(snap, "syn-a", "singles")
    assert (state["matches_played"], state["verified_matches_played"]) == (2, 0)
    return snap


def test_imported_state_above_the_log_does_not_load() -> None:
    snap = _imported_live_snapshot()
    state = _state(snap, "syn-a", "singles")
    state["matches_played"] = 3
    state["live_updates_since_replay"] = 3
    with pytest.raises(
        SnapshotError,
        match=(
            r"ratings\.singles: matches_played - verified_matches_played \(3\) must not "
            r"exceed the self-reported singles matches logged for this player \(2\)"
        ),
    ):
        RatingEngine.from_snapshot(snap)


def test_imported_live_state_below_the_log_loads() -> None:
    # A live imported state can be reseeded while unplayed, so a logged match
    # can be one in which the player was frozen: only the upper bound holds.
    snap = _imported_live_snapshot()
    _state(snap, "syn-a", "singles")["verified_matches_played"] = 2
    RatingEngine.from_snapshot(snap)


def _category_snapshot(feeds: bool) -> dict[str, object]:
    cfg = Config(category_feeds_parent=feeds)
    rows = [
        Match(
            f"syn-x{i}",
            ("syn-a", "syn-b"),
            ("syn-c", f"syn-d{i}"),
            ((11, 7),),
            START + timedelta(days=i),
            MX,
            match_type=SR,
            confirmed_by=frozenset({"syn-a", "syn-c"}),
        )
        for i in range(2)
    ] + [
        Match(
            "syn-dbl",
            ("syn-a", "syn-e"),
            ("syn-c", "syn-f"),
            ((11, 7),),
            START + timedelta(days=5),
            D,
            match_type=SR,
            confirmed_by=frozenset({"syn-a", "syn-c"}),
        )
    ]
    return _json(replay(rows, cfg=cfg, as_of=START + timedelta(days=10)))


def test_doubles_count_may_include_category_self_reports() -> None:
    snap = _category_snapshot(True)
    state = _state(snap, "syn-a", "doubles")
    assert (state["matches_played"], state["verified_matches_played"]) == (3, 0)
    RatingEngine.from_snapshot(snap, Config(category_feeds_parent=True))


def test_doubles_count_above_the_category_bound_does_not_load() -> None:
    snap = _category_snapshot(True)
    state = _state(snap, "syn-a", "doubles")
    state["matches_played"] = 4
    snap["resumable"] = False
    with pytest.raises(
        SnapshotError,
        match=r"ratings\.doubles: matches_played - verified_matches_played \(4\) must not exceed",
    ):
        RatingEngine.from_snapshot(snap)


def test_doubles_count_without_category_feed_must_equal_the_doubles_log() -> None:
    cfg = Config(category_feeds_parent=False)
    snap = _category_snapshot(False)
    state = _state(snap, "syn-a", "doubles")
    assert (state["matches_played"], state["verified_matches_played"]) == (1, 0)
    RatingEngine.from_snapshot(snap, cfg)
    state["matches_played"] = 2
    snap["resumable"] = False
    with pytest.raises(SnapshotError, match=r"ratings\.doubles: matches_played - verified"):
        RatingEngine.from_snapshot(snap, cfg)


# -- every engine the code produces loads ---------------------------------------


def _random_ledger(rng: random.Random, count: int) -> list[Match]:
    people = [f"syn-{c}" for c in "abcdefgh"]
    rows: list[Match] = []
    for i in range(count):
        fmt = rng.choice([S, D, MX, MX, D])
        size = fmt.team_size
        chosen = rng.sample(people, 2 * size)
        a, b = tuple(chosen[:size]), tuple(chosen[size:])
        won = rng.randint(0, 9)
        games = ((11, won),) if rng.random() < 0.5 else ((won, 11),)
        kind = rng.choice(list(MatchType) + [SR, SR])
        confirmed = frozenset({a[0], b[0]}) if rng.random() < 0.9 else frozenset({a[0]})
        on = START + timedelta(days=rng.randint(0, 120))
        rows.append(
            Match(f"syn-m{i:03d}", a, b, games, on, fmt, match_type=kind, confirmed_by=confirmed)
        )
        if rng.random() < 0.1:
            # The same content under another id: a possible duplicate.
            rows.append(
                Match(
                    f"syn-m{i:03d}-copy",
                    a,
                    b,
                    games,
                    on,
                    fmt,
                    match_type=kind,
                    confirmed_by=confirmed,
                )
            )
    return rows


def _random_seeds(rng: random.Random) -> list[Player]:
    seeder = RatingEngine()
    for pid, fmt in (("syn-a", D), ("syn-b", S), ("syn-c", MX), ("syn-d", D)):
        on = START + timedelta(days=rng.randint(-5, 60))
        seeder.seed_external_rating(pid, fmt, ExternalRatingSnapshot("ext", 4.0, on))
    return [seeder.players[pid] for pid in sorted(seeder.players)]


CONFIGS = [
    Config(),
    Config(category_feeds_parent=False),
    Config(max_doubles_partner_gap=0.05),
    Config(reject_content_duplicates=True),
]


@pytest.mark.parametrize("cfg", CONFIGS, ids=["default", "no-feed", "partner-gap", "reject-dups"])
@pytest.mark.parametrize("seed", range(6))
def test_engines_the_code_produces_load(cfg: Config, seed: int) -> None:
    rng = random.Random(seed)
    rows = _random_ledger(rng, 80)
    seeds = _random_seeds(rng)
    as_of = START + timedelta(days=130)
    replayed = replay(rows, seeds, cfg=cfg, as_of=as_of)
    RatingEngine.from_snapshot(_json(replayed), cfg)
    checkpoint = replay(rows, seeds, cfg=cfg, as_of=START + timedelta(days=60))
    resumed = replay(rows, cfg=cfg, as_of=as_of, from_snapshot=_json(checkpoint))
    RatingEngine.from_snapshot(_json(resumed), cfg)
    # Live, in random arrival order, so many matches are late.
    live = RatingEngine(cfg)
    for player in seeds:
        live.add_player(player)
    shuffled = list({m.match_id: m for m in rows}.values())
    rng.shuffle(shuffled)
    for match in shuffled:
        live.record(match)
    RatingEngine.from_snapshot(_json(live), cfg)


def test_live_import_with_frozen_logged_matches_loads_and_is_not_reseeded() -> None:
    engine = RatingEngine()
    engine.seed_external_rating("syn-a", S, ExternalRatingSnapshot("ext", 4.0, START))
    # Frozen for syn-a: on its capture date. Logged, since syn-b moved.
    frozen = _singles("syn-a", "syn-b", 1, SR)
    assert engine.record(frozen[0]).frozen == ("syn-a",)
    # A reseed with an earlier capture date is refused (issue #79): the
    # counted match would then move syn-a in a replay.
    with pytest.raises(InvalidSeedError, match="OPEN matches have counted"):
        engine.seed_external_rating(
            "syn-a",
            S,
            ExternalRatingSnapshot("ext", 4.0, START - timedelta(days=10)),
            replace_unplayed_seed=True,
        )
    RatingEngine.from_snapshot(_json(engine))
