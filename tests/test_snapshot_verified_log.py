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
    MatchResult,
    MatchType,
    Player,
    RatingEngine,
    RatingFormat,
    RejectionReason,
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


def test_doubles_count_must_include_category_self_reports() -> None:
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
    # Most players are imported in doubles, and some also in mixed doubles
    # with an earlier capture date, so a counted self-reported category
    # match can have every player frozen in doubles (#84).
    seeder = RatingEngine()
    for pid in [f"syn-{c}" for c in "abcdefgh"]:
        for fmt, chance, last in ((D, 0.9, 60), (MX, 0.5, 0), (S, 0.2, 60)):
            if rng.random() < chance:
                on = START + timedelta(days=rng.randint(-5, last))
                seeder.seed_external_rating(pid, fmt, ExternalRatingSnapshot("ext", 4.0, on))
    return [seeder.players[pid] for pid in sorted(seeder.players)]


CONFIGS = [
    Config(),
    Config(category_feeds_parent=False),
    Config(max_doubles_partner_gap=0.05),
    Config(reject_content_duplicates=True),
    Config(self_reported_requires_both_teams=False),
]
CONFIG_IDS = ["default", "no-feed", "partner-gap", "reject-dups", "one-team-confirms"]


def _exact_doubles_states(snap: dict[str, object], cfg: Config) -> int:
    """
    Assert the #84 rule's count on every doubles state it covers; return how many.

    The rule must never be looser than the engine: each covered state's
    unverified count equals its logged doubles and category dates.
    """
    if not cfg.category_feeds_parent or cfg.max_doubles_partner_gap is not None:
        return 0
    logged: dict[str, list[date]] = {}
    entries = snap["self_reported_log"]
    assert isinstance(entries, list)
    for entry in entries:
        fmt = RatingFormat(entry["rating_format"])
        if fmt is not D and fmt.parent is not D:
            continue
        for team in entry["teams"]:
            for pid in team:
                logged.setdefault(pid, []).extend(date.fromisoformat(on) for on in entry["dates"])
    players = snap["players"]
    assert isinstance(players, list)
    covered = 0
    for player in players:
        state = player["ratings"].get("doubles")
        if state is None:
            continue
        source_date = state["initial_source_date"]
        if source_date is not None and not snap["resumable"]:
            continue
        after = None if source_date is None else date.fromisoformat(source_date)
        dates = logged.get(player["id"], [])
        expected = sum(1 for on in dates if after is None or on > after)
        assert state["matches_played"] - state["verified_matches_played"] == expected
        covered += 1
    return covered


def _loads(engine: RatingEngine, cfg: Config) -> int:
    snap = _json(engine)
    RatingEngine.from_snapshot(snap, cfg)
    return _exact_doubles_states(snap, cfg)


def _maybe_reseed(rng: random.Random, engine: RatingEngine) -> None:
    pid = rng.choice([f"syn-{c}" for c in "abcdefgh"])
    fmt = rng.choice([S, D, MX, D])
    on = START + timedelta(days=rng.randint(-5, 120))
    snapshot = ExternalRatingSnapshot("ext", rng.choice([3.0, 4.0, 5.0]), on)
    try:
        engine.seed_external_rating(pid, fmt, snapshot, replace_unplayed_seed=True)
    except InvalidSeedError:
        pass


def _all_frozen_parent(match: Match, result: MatchResult) -> bool:
    """A counted self-reported category match whose doubles update froze every player."""
    return (
        result.counted
        and match.match_type is SR
        and result.parent is not None
        and result.parent.reason is RejectionReason.BEFORE_IMPORTED_BASELINE
    )


def _counting_replay(
    rows: list[Match],
    seeds: list[Player],
    cfg: Config,
    as_of: date,
    from_snapshot: dict[str, object] | None = None,
) -> tuple[RatingEngine, int]:
    """
    replay, also returning how many rows were counted with every player frozen
    in the doubles update (_all_frozen_parent). The spy returns what the
    engine's own per-row step returns, so the replay is unchanged.
    """
    hits = 0
    original = RatingEngine._record

    def spy(
        self: RatingEngine, match: Match, *, as_of: date | None = None
    ) -> tuple[MatchResult, dict[RatingFormat, float]]:
        nonlocal hits
        out = original(self, match, as_of=as_of)
        hits += int(_all_frozen_parent(match, out[0]))
        return out

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(RatingEngine, "_record", spy)
        engine = replay(rows, seeds, cfg=cfg, as_of=as_of, from_snapshot=from_snapshot)
    return engine, hits


def _drive(cfg: Config, seed: int) -> tuple[int, int]:
    """
    Check that every snapshot of one random run loads. Return how many
    category matches had their doubles update rejected with every player
    frozen, in the replays (resumable snapshots) and in the live engines.
    """
    rng = random.Random(seed)
    rows = _random_ledger(rng, 80)
    seeds = _random_seeds(rng)
    as_of = START + timedelta(days=130)
    covered = 0
    all_frozen = 0
    replayed, replay_frozen = _counting_replay(rows, seeds, cfg, as_of)
    covered += _loads(replayed, cfg)
    checkpoint, hits = _counting_replay(rows, seeds, cfg, START + timedelta(days=60))
    replay_frozen += hits
    covered += _loads(checkpoint, cfg)
    resumed, hits = _counting_replay(rows, [], cfg, as_of, from_snapshot=_json(checkpoint))
    replay_frozen += hits
    covered += _loads(resumed, cfg)
    unique = list({m.match_id: m for m in rows}.values())
    # Live after resuming from the checkpoint, with reseed attempts between rows.
    after_checkpoint = RatingEngine.from_snapshot(_json(checkpoint), cfg)
    for match in unique:
        if match.played_on > START + timedelta(days=60):
            if rng.random() < 0.1:
                _maybe_reseed(rng, after_checkpoint)
            all_frozen += int(_all_frozen_parent(match, after_checkpoint.record(match)))
    covered += _loads(after_checkpoint, cfg)
    # Live, in random arrival order, so many matches are late, with reseed
    # attempts (accepted and refused) between rows.
    live = RatingEngine(cfg)
    for player in seeds:
        live.add_player(player)
    shuffled = list(unique)
    rng.shuffle(shuffled)
    for match in shuffled:
        if rng.random() < 0.1:
            _maybe_reseed(rng, live)
        all_frozen += int(_all_frozen_parent(match, live.record(match)))
    covered += _loads(live, cfg)
    if cfg.category_feeds_parent and cfg.max_doubles_partner_gap is None:
        assert covered > 0
    return replay_frozen, all_frozen


@pytest.mark.parametrize("cfg", CONFIGS, ids=CONFIG_IDS)
@pytest.mark.parametrize("seed", range(15))
def test_engines_the_code_produces_load(cfg: Config, seed: int) -> None:
    _drive(cfg, seed)


def test_fuzz_reaches_a_category_match_with_every_player_frozen_in_doubles() -> None:
    # The exact rule rests on this case: the doubles update is rejected, so
    # no doubles state moves, and every player's match is on or before its
    # doubles capture date. The replays' snapshots are resumable, so the
    # exact rule covers those imported states there; the live ones get only
    # the upper bound.
    runs = [_drive(Config(), seed) for seed in range(15)]
    assert sum(replay_hits for replay_hits, _ in runs) > 0
    assert sum(live_hits for _, live_hits in runs) > 0


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


# -- category matches that fed doubles (#84) ------------------------------------


def _sr(
    match_id: str, a: tuple[str, str], b: tuple[str, str], on: date, fmt: RatingFormat
) -> Match:
    return Match(
        match_id, a, b, ((11, 7),), on, fmt, match_type=SR, confirmed_by=frozenset({a[0], b[0]})
    )


@pytest.mark.parametrize("resumable", [True, False])
def test_relabeled_category_fed_doubles_update_does_not_load(resumable: bool) -> None:
    rows = [
        _sr("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), date(2026, 1, 5), D),
        _sr("syn-m2", ("syn-a", "syn-e"), ("syn-f", "syn-g"), date(2026, 1, 6), MX),
    ]
    snap = _json(replay(rows, as_of=date(2026, 1, 10)))
    state = _state(snap, "syn-a", "doubles")
    assert (state["matches_played"], state["verified_matches_played"]) == (2, 0)
    state["verified_matches_played"] = 1
    snap["resumable"] = resumable
    with pytest.raises(
        SnapshotError,
        match=(
            r"ratings\.doubles: matches_played - verified_matches_played \(1\) must equal "
            r"the self-reported doubles and category matches logged for this player \(2\)"
        ),
    ):
        RatingEngine.from_snapshot(snap)


def test_relabeled_category_fed_update_of_a_resumable_import_does_not_load() -> None:
    seed_on = START + timedelta(days=2)
    rows = [
        # On or before the capture date: syn-a is frozen in doubles, but the
        # match is logged because the other players moved.
        _sr("syn-m0", ("syn-a", "syn-b"), ("syn-c", "syn-d"), START + timedelta(days=1), MX),
        _sr("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), START + timedelta(days=5), D),
        _sr("syn-m2", ("syn-a", "syn-e"), ("syn-f", "syn-g"), START + timedelta(days=6), MX),
    ]
    seeds = [_imported("syn-a", D, 4.0, seed_on)]
    snap = _json(replay(rows, seeds, as_of=START + timedelta(days=10)))
    assert snap["resumable"] is True
    state = _state(snap, "syn-a", "doubles")
    assert (state["matches_played"], state["verified_matches_played"]) == (2, 0)
    state["verified_matches_played"] = 1
    with pytest.raises(
        SnapshotError,
        match=(
            r"ratings\.doubles: matches_played - verified_matches_played \(1\) must equal "
            r"the self-reported doubles and category matches logged for this player \(2\)"
        ),
    ):
        RatingEngine.from_snapshot(snap)


def _feed_rows() -> list[Match]:
    return [
        _sr("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), date(2026, 1, 5), D),
        _sr("syn-m2", ("syn-a", "syn-e"), ("syn-f", "syn-g"), date(2026, 1, 6), MX),
    ]


@pytest.mark.parametrize("resumable", [True, False])
def test_doubles_count_under_a_partner_gap_is_still_a_range(resumable: bool) -> None:
    # With max_doubles_partner_gap set, the snapshot cannot tell which
    # category matches reached doubles, so only the range applies.
    cfg = Config(max_doubles_partner_gap=1.0)
    snap = _json(replay(_feed_rows(), cfg=cfg, as_of=date(2026, 1, 10)))
    state = _state(snap, "syn-a", "doubles")
    assert (state["matches_played"], state["verified_matches_played"]) == (2, 0)
    state["verified_matches_played"] = 1
    snap["resumable"] = resumable
    RatingEngine.from_snapshot(snap, cfg)


def test_live_imported_doubles_count_below_the_bound_still_loads() -> None:
    engine = RatingEngine()
    engine.add_player(_imported("syn-a", D, 4.0, date(2026, 1, 1)))
    for match in _feed_rows():
        assert engine.record(match).counted
    snap = _json(engine)
    assert snap["resumable"] is False
    state = _state(snap, "syn-a", "doubles")
    assert (state["matches_played"], state["verified_matches_played"]) == (2, 0)
    state["verified_matches_played"] = 1
    RatingEngine.from_snapshot(snap)


def test_gap_rejected_doubles_update_loads() -> None:
    cfg = Config(max_doubles_partner_gap=0.5)
    # syn-e is far from syn-a in doubles but not in mixed doubles.
    seeder = RatingEngine()
    for fmt, rating in ((D, 5.5), (MX, 3.5)):
        seeder.seed_external_rating(
            "syn-e", fmt, ExternalRatingSnapshot("ext", rating, date(2026, 1, 1))
        )
    seeds = [seeder.players["syn-e"]]
    rows = _feed_rows()
    engine = replay(rows, seeds, cfg=cfg, as_of=date(2026, 1, 10))
    state = engine.players["syn-a"].ratings[D]
    # The mixed match counted, but its doubles update was rejected for the gap.
    assert (state.matches_played, state.verified_matches_played) == (1, 0)
    assert engine.players["syn-a"].ratings[MX].matches_played == 1
    RatingEngine.from_snapshot(_json(engine), cfg)


def _doubles_counts_after_import(snap: dict[str, object]) -> dict[str, tuple[int, int]]:
    """Each doubles state's (unverified count, logged doubles and category dates after import)."""
    entries = snap["self_reported_log"]
    players = snap["players"]
    assert isinstance(entries, list) and isinstance(players, list)
    counts: dict[str, tuple[int, int]] = {}
    for player in players:
        state = player["ratings"].get("doubles")
        if state is None:
            continue
        after = state["initial_source_date"]
        logged = sum(
            1
            for entry in entries
            if RatingFormat(entry["rating_format"]) in (D, MX)
            and any(player["id"] in team for team in entry["teams"])
            for on in entry["dates"]
            if after is None or on > after
        )
        counts[player["id"]] = (state["matches_played"] - state["verified_matches_played"], logged)
    return counts


def test_category_match_with_every_player_frozen_in_doubles_loads() -> None:
    capture = START + timedelta(days=10)
    seeder = RatingEngine()
    for pid in ("syn-a", "syn-b", "syn-c", "syn-d"):
        seeder.seed_external_rating(pid, D, ExternalRatingSnapshot("ext", 4.0, capture))
    # syn-b's own mixed doubles import is earlier, so syn-b moves in mixed.
    seeder.seed_external_rating("syn-b", MX, ExternalRatingSnapshot("ext", 4.0, START))
    seeds = [seeder.players[pid] for pid in sorted(seeder.players)]
    rows = [
        _sr("syn-m0", ("syn-a", "syn-b"), ("syn-c", "syn-d"), START + timedelta(days=3), MX),
        _sr("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), START + timedelta(days=15), D),
        _sr("syn-m2", ("syn-a", "syn-c"), ("syn-b", "syn-d"), START + timedelta(days=16), MX),
    ]
    live = RatingEngine()
    for player in seeds:
        live.add_player(player)
    first = live.record(rows[0])
    assert first.counted and first.deltas["syn-b"] != 0.0
    assert first.parent is not None
    assert first.parent.reason is RejectionReason.BEFORE_IMPORTED_BASELINE
    for match in rows[1:]:
        assert live.record(match).counted
    replayed = replay(rows, seeds, as_of=START + timedelta(days=20))
    for engine, resumable in ((replayed, True), (live, False)):
        snap = _json(engine)
        assert snap["resumable"] is resumable
        RatingEngine.from_snapshot(snap)
        counts = _doubles_counts_after_import(snap)
        assert set(counts) == {"syn-a", "syn-b", "syn-c", "syn-d"}
        assert all(unverified == logged == 2 for unverified, logged in counts.values())


def test_to_snapshot_refuses_a_relabeled_category_fed_doubles_update() -> None:
    engine = RatingEngine()
    for match in _feed_rows():
        assert engine.record(match).counted
    state = engine.players["syn-a"].ratings[D]
    assert (state.matches_played, state.verified_matches_played) == (2, 0)
    state.verified_matches_played = 1
    with pytest.raises(
        SnapshotError,
        match=(
            r"ratings\.doubles: matches_played - verified_matches_played \(1\) must equal "
            r"the self-reported doubles and category matches logged for this player \(2\)"
        ),
    ):
        engine.to_snapshot()
