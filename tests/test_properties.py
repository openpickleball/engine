"""
Seeded property tests over many synthetic cases (issue #26).

Every case comes from random.Random with a fixed seed, so a failure always
reproduces. Player ids are invented. A state built by setting RatingState
fields directly is fine for record, but to_snapshot rejects it, so tests that
compare snapshots build their engines by recording or replaying a generated
ledger.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    GameFormat,
    Match,
    MatchResult,
    MatchStatus,
    MatchType,
    Player,
    RatingEngine,
    RatingFormat,
    RejectionReason,
    ScoringType,
    expected_point_share,
    reliability,
    replay,
)
from open_engine.engine import inflated_rd, information_multiplier, player_k

S = RatingFormat.SINGLES
D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
DAY0 = date(2026, 3, 2)
ON = DAY0 + timedelta(days=30)
AS_OF = DAY0 + timedelta(days=25)
DEFAULT = Config()
GAME_FORMATS = (
    GameFormat(ScoringType.SIDE_OUT, 11),
    GameFormat(ScoringType.RALLY, 11),
    GameFormat(ScoringType.SIDE_OUT, 15),
    GameFormat(ScoringType.RALLY, 21),
)
ZONES = (UTC, timezone(timedelta(hours=-5)), timezone(timedelta(hours=9)))
VERIFIED = (MatchType.TOURNAMENT, MatchType.LEAGUE, MatchType.REC_VERIFIED)


# -- generators -------------------------------------------------------------------


def game_score(rng: random.Random, fmt: GameFormat, a_wins: bool) -> tuple[int, int]:
    """A valid final score in fmt: the target reached, or a deuce game won by win_by."""
    if rng.random() < 0.25:
        low = rng.randint(fmt.points_to - 1, fmt.points_to + 4)
        high = low + fmt.win_by
    else:
        high = fmt.points_to
        low = rng.randint(0, fmt.points_to - fmt.win_by)
    return (high, low) if a_wins else (low, high)


def winners(rng: random.Random, best_of: int) -> tuple[bool, ...]:
    """Game winners (True for team A) until one team has won the match."""
    need = best_of // 2 + 1
    seq: list[bool] = []
    while seq.count(True) < need and seq.count(False) < need:
        seq.append(rng.random() < 0.5)
    return tuple(seq)


def games_for(
    rng: random.Random, fmt: GameFormat, pattern: tuple[bool, ...]
) -> tuple[tuple[int, int], ...]:
    """Game scores that keep the given game winners."""
    return tuple(game_score(rng, fmt, a_wins) for a_wins in pattern)


def point_share(games: tuple[tuple[int, int], ...]) -> float:
    points_a = sum(a for a, _ in games)
    return points_a / (points_a + sum(b for _, b in games))


def mirror(match: Match) -> Match:
    """The same match with the teams swapped and every game score flipped."""
    return replace(
        match,
        team_a=match.team_b,
        team_b=match.team_a,
        games=tuple((b, a) for a, b in match.games),
    )


@dataclass(frozen=True)
class Seat:
    """One player's starting state for a single-match case."""

    pid: str
    rating: float
    rd: float
    last_played: date | None = None
    import_on: date | None = None  # an imported baseline; frozen when on or after the match
    parent: Seat | None = None  # the overall doubles state, for a category match


@dataclass(frozen=True)
class Case:
    cfg: Config
    seats: tuple[Seat, ...]
    match: Match

    def build(self) -> RatingEngine:
        """A fresh engine holding the seats' states. Fine for record, not for to_snapshot."""
        engine = RatingEngine(self.cfg)
        fmt = self.match.rating_format
        for seat in self.seats:
            if seat.parent is not None and fmt.parent is not None:
                put_seat(engine, seat.parent, fmt.parent)
            put_seat(engine, seat, fmt)
        return engine


def put_seat(engine: RatingEngine, seat: Seat, fmt: RatingFormat) -> None:
    if seat.import_on is not None:
        snap = ExternalRatingSnapshot("external_standard", seat.rating, seat.import_on)
        engine.seed_external_rating(seat.pid, fmt, snap)
        return
    state = engine.seed_provisional_rating(seat.pid, fmt)
    state.rating = seat.rating
    state.rd = seat.rd
    if seat.last_played is not None:
        state.last_played = seat.last_played
        state.rd_as_of = seat.last_played
        state.matches_played = 1


def random_seat(
    rng: random.Random,
    pid: str,
    ratings: tuple[float, float],
    *,
    frozen: bool,
    late: bool,
    category: bool = False,
) -> Seat:
    parent = None
    if category:
        parent = random_seat(rng, pid, ratings, frozen=frozen, late=late)
    rating = round(rng.uniform(*ratings), 3)
    rd = round(rng.uniform(DEFAULT.rd_min, DEFAULT.rd_max), 3)
    roll = rng.random()
    if frozen and roll < 0.2:
        return Seat(
            pid, rating, rd, import_on=ON + timedelta(days=rng.randint(-10, 5)), parent=parent
        )
    if late and roll < 0.5:
        last_played = ON + timedelta(days=rng.randint(-60, 10))
        return Seat(pid, rating, rd, last_played=last_played, parent=parent)
    return Seat(pid, rating, rd, parent=parent)


RATING_BANDS = ((2.0, 8.0), (2.0, 2.4), (7.6, 8.0), (3.0, 7.0))


def random_case(
    rng: random.Random,
    fmt: RatingFormat,
    *,
    cfg: Config = DEFAULT,
    frozen: bool = True,
    late: bool = True,
    pattern: tuple[bool, ...] | None = None,
    game_format: GameFormat | None = None,
) -> Case:
    size = fmt.team_size
    band = rng.choice(RATING_BANDS)
    seats = tuple(
        random_seat(rng, f"syn-{i}", band, frozen=frozen, late=late, category=bool(fmt.parent))
        for i in range(2 * size)
    )
    gf = game_format or rng.choice(GAME_FORMATS)
    pattern = pattern or winners(rng, rng.choice((1, 3)))
    best_of = 1 if len(pattern) == 1 else 3
    match = Match(
        "syn-m",
        tuple(s.pid for s in seats[:size]),
        tuple(s.pid for s in seats[size:]),
        games_for(rng, gf, pattern),
        ON,
        fmt,
        rng.choice(VERIFIED),
        best_of=best_of,
        game_format=gf,
    )
    return Case(cfg, seats, match)


def ratings_of(engine: RatingEngine, ids: tuple[str, ...], fmt: RatingFormat) -> dict[str, float]:
    return {pid: engine.state(pid, fmt).rating for pid in ids}


def changes(before: dict[str, float], engine: RatingEngine, fmt: RatingFormat) -> dict[str, float]:
    return {pid: engine.state(pid, fmt).rating - old for pid, old in before.items()}


# -- conservation -----------------------------------------------------------------


def check_conservation(result: MatchResult, match: Match, change: dict[str, float]) -> None:
    mean_a = sum(change[pid] for pid in match.team_a) / len(match.team_a)
    mean_b = sum(change[pid] for pid in match.team_b) / len(match.team_b)
    assert abs(result.imbalance - (mean_a + mean_b)) <= 5e-6 + 1e-12


def raw_steps(
    engine: RatingEngine, match: Match, fmt: RatingFormat, frozen: set[str]
) -> dict[str, float]:
    """
    Each player's unclamped step before the match: sign * K * effective_weight * surprise,
    with K from the RD after inactivity (no inactivity for a frozen player).
    """
    cfg = engine.cfg
    states = {pid: engine.state(pid, fmt) for pid in match.team_a + match.team_b}
    rating_a = math.fsum(states[pid].rating for pid in match.team_a) / len(match.team_a)
    rating_b = math.fsum(states[pid].rating for pid in match.team_b) / len(match.team_b)
    expected = expected_point_share(
        rating_a, rating_b, cfg.scale_by_scoring[match.game_format.scoring]
    )
    a_won = sum(a > b for a, b in match.games) * 2 > len(match.games)
    actual = (1.0 - cfg.win_weight) * point_share(match.games) + cfg.win_weight * float(a_won)
    weight = cfg.match_weights[match.match_type] * 1.0 * information_multiplier(match, cfg)
    steps = {}
    for team, sign in ((match.team_a, 1.0), (match.team_b, -1.0)):
        for pid in team:
            state = states[pid]
            if pid not in frozen:
                state = replace(state, rd=inflated_rd(state, match.played_on, cfg))
            steps[pid] = sign * player_k(state, cfg) * weight * (actual - expected)
    return steps


def check_clamped(
    engine: RatingEngine,
    result: MatchResult,
    before: dict[str, float],
    steps: dict[str, float],
    change: dict[str, float],
) -> int:
    """
    A player is clamped exactly when the unclamped step would leave the rating range,
    and every other player that is not frozen moves by that step. Returns the clamp count.
    """
    cfg = engine.cfg
    for pid, step in steps.items():
        if pid in result.frozen:
            assert pid not in result.clamped and change[pid] == 0.0
            continue
        target = before[pid] + step
        if abs(target - cfg.min_rating) < 1e-9 or abs(target - cfg.max_rating) < 1e-9:
            continue  # too close to a bound to call either way
        outside = not cfg.min_rating <= target <= cfg.max_rating
        assert (pid in result.clamped) == outside
        if outside:
            assert before[pid] + change[pid] == min(cfg.max_rating, max(cfg.min_rating, target))
        else:
            assert abs(change[pid] - step) <= 1e-12
    return len(result.clamped)


@pytest.mark.parametrize("fmt", [S, D, MX])
def test_imbalance_is_the_net_team_mean_change(fmt: RatingFormat) -> None:
    rng = random.Random(2601)
    counted = clamps = parent_nonzero = 0
    for _ in range(150):
        case = random_case(rng, fmt)
        engine = case.build()
        match = case.match
        ids = match.team_a + match.team_b
        before = ratings_of(engine, ids, fmt)
        parent_before = ratings_of(engine, ids, D) if fmt.parent else {}
        frozen = {
            pid
            for pid in ids
            if (baseline := engine.state(pid, fmt).initial_source_date) is not None
            and match.played_on <= baseline
        }
        steps = raw_steps(engine, match, fmt, frozen)
        parent_steps = raw_steps(engine, match, D, set()) if fmt.parent else {}
        result = engine.record(match)
        if not result.counted:
            assert result.reason is RejectionReason.BEFORE_IMPORTED_BASELINE
            continue
        counted += 1
        assert set(result.frozen) == frozen
        change = changes(before, engine, fmt)
        check_conservation(result, match, change)
        clamps += check_clamped(engine, result, before, steps, change)
        if fmt.parent:
            assert result.parent is not None and result.parent.counted
            parent_change = changes(parent_before, engine, D)
            check_conservation(result.parent, match, parent_change)
            clamps += check_clamped(
                engine, result.parent, parent_before, parent_steps, parent_change
            )
            parent_nonzero += result.parent.imbalance != 0.0
    assert counted > 100
    assert clamps > 10
    if fmt.parent:
        assert parent_nonzero > 50


def equal_k_case(rng: random.Random, fmt: RatingFormat) -> Case:
    """Mid-range ratings, no inactivity, and team B's RDs with the same mean as team A's."""
    lo, hi = DEFAULT.rd_min, DEFAULT.rd_max
    if fmt.team_size == 1:
        rd = round(rng.uniform(lo, hi), 3)
        rds_a, rds_b = [rd], [rd]
    else:
        rds_a = [round(rng.uniform(lo + 0.1, hi - 0.1), 3) for _ in range(2)]
        if rng.random() < 0.5:
            rds_b = rds_a[::-1]
        else:
            shift = round(rng.uniform(-0.09, 0.09), 3)
            rds_b = [rds_a[0] + shift, rds_a[1] - shift]
    seats = tuple(
        Seat(f"syn-{i}", round(rng.uniform(3.0, 7.0), 3), rd) for i, rd in enumerate(rds_a + rds_b)
    )
    base = random_case(rng, fmt, frozen=False, late=False)
    return Case(DEFAULT, seats, base.match)


@pytest.mark.parametrize("fmt", [S, D, MX])
def test_imbalance_is_zero_for_equal_mean_k(fmt: RatingFormat) -> None:
    rng = random.Random(2602)
    unequal_partners = 0
    for _ in range(150):
        case = equal_k_case(rng, fmt)
        rds = [seat.rd for seat in case.seats]
        if fmt.team_size == 2 and rds[0] != rds[1]:
            unequal_partners += 1
        result = case.build().record(case.match)
        assert result.counted
        assert result.clamped == () and result.frozen == ()
        assert result.imbalance == 0.0
    if fmt.team_size == 2:
        assert unequal_partners > 140


# -- symmetry ---------------------------------------------------------------------

# Two values rounded to 5 places on their own can differ by one unit in the last place.
ROUNDED = 1e-5 + 1e-12


def check_mirrored(
    result: MatchResult,
    flipped: MatchResult,
    change: dict[str, float],
    flipped_change: dict[str, float],
) -> None:
    assert flipped.counted == result.counted
    assert flipped.reason == result.reason
    assert flipped.late == result.late
    assert flipped.frozen == result.frozen
    assert sorted(flipped.clamped) == sorted(result.clamped)
    assert abs(flipped.imbalance - result.imbalance) <= ROUNDED
    assert abs(flipped.effective_weight - result.effective_weight) <= ROUNDED
    if result.counted:
        assert abs(flipped.expected_a - (1.0 - result.expected_a)) <= ROUNDED
        assert abs(flipped.actual_a - (1.0 - result.actual_a)) <= ROUNDED
        assert abs(flipped.surprise + result.surprise) <= ROUNDED
    assert flipped.deltas.keys() == result.deltas.keys()
    for pid, delta in result.deltas.items():
        assert abs(flipped.deltas[pid] - delta) <= ROUNDED
    for pid, step in change.items():
        assert abs(flipped_change[pid] - step) <= 1e-12


@pytest.mark.parametrize("fmt", [S, D, MX])
def test_swapping_teams_mirrors_the_update(fmt: RatingFormat) -> None:
    rng = random.Random(2603)
    configs = (DEFAULT, Config(win_weight=0.3), Config(win_weight=1.0))
    parent_moves = 0
    for _ in range(150):
        case = random_case(rng, fmt, cfg=rng.choice(configs))
        ids = case.match.team_a + case.match.team_b
        engine, flipped_engine = case.build(), case.build()
        before = ratings_of(engine, ids, fmt)
        parent_before = ratings_of(engine, ids, D) if fmt.parent else {}
        result = engine.record(case.match)
        flipped = flipped_engine.record(mirror(case.match))
        check_mirrored(
            result, flipped, changes(before, engine, fmt), changes(before, flipped_engine, fmt)
        )
        if fmt.parent and result.counted:
            assert result.parent is not None and flipped.parent is not None
            parent_moves += result.parent.imbalance != 0.0
            check_mirrored(
                result.parent,
                flipped.parent,
                changes(parent_before, engine, D),
                changes(parent_before, flipped_engine, D),
            )
    if fmt.parent:
        assert parent_moves > 50


# -- margin -----------------------------------------------------------------------


@pytest.mark.parametrize("win_weight", [0.0, 0.25, 0.75, 1.0])
@pytest.mark.parametrize("game_format", GAME_FORMATS, ids=lambda f: f"{f.scoring}-{f.points_to}")
@pytest.mark.parametrize("fmt", [S, D])
def test_wider_point_share_never_costs_rating(
    fmt: RatingFormat, game_format: GameFormat, win_weight: float
) -> None:
    rng = random.Random(f"margin-{fmt}-{game_format.scoring}-{game_format.points_to}-{win_weight}")
    cfg = Config(win_weight=win_weight)
    compared = 0
    for _ in range(25):
        pattern = winners(rng, rng.choice((1, 3)))
        case = random_case(rng, fmt, cfg=cfg, pattern=pattern, game_format=game_format)
        other = games_for(rng, game_format, pattern)
        if point_share(other) == point_share(case.match.games):
            continue
        low, high = sorted((case.match.games, other), key=point_share)
        ids = case.match.team_a + case.match.team_b
        low_engine, high_engine = case.build(), case.build()
        before = ratings_of(low_engine, ids, fmt)
        low_result = low_engine.record(replace(case.match, games=low))
        high_result = high_engine.record(replace(case.match, games=high))
        assert low_result.counted == high_result.counted
        low_change = changes(before, low_engine, fmt)
        high_change = changes(before, high_engine, fmt)
        for pid in case.match.team_a:
            assert high_change[pid] >= low_change[pid]
        for pid in case.match.team_b:
            assert high_change[pid] <= low_change[pid]
        compared += 1
    assert compared >= 20


# -- generated ledgers ------------------------------------------------------------


@dataclass(frozen=True)
class Ledger:
    rows: tuple[Match, ...]
    seeds: tuple[tuple[str, RatingFormat, float, date], ...]

    def seed_players(self) -> list[Player]:
        """Fresh Player seeds, built through seed_external_rating."""
        builder = RatingEngine()
        for pid, fmt, rating, captured_on in self.seeds:
            snap = ExternalRatingSnapshot("external_standard", rating, captured_on)
            builder.seed_external_rating(pid, fmt, snap)
        return list(builder.players.values())

    def live_engine(self, cfg: Config, rows: list[Match]) -> RatingEngine:
        engine = RatingEngine(cfg)
        for player in self.seed_players():
            engine.add_player(player)
        for match in rows:
            engine.record(match)
        return engine


def random_row(
    rng: random.Random, match_id: str, pool: list[str], formats: tuple[RatingFormat, ...]
) -> Match:
    fmt = rng.choice(formats)
    ids = rng.sample(pool, 2 * fmt.team_size)
    half = fmt.team_size
    day = DAY0 + timedelta(days=rng.randint(0, 20))
    gf = rng.choice(GAME_FORMATS)
    best_of = rng.choice((1, 1, 3))
    match_type = rng.choice(list(MatchType))
    confirmed: frozenset[str] = frozenset()
    if match_type is MatchType.SELF_REPORTED and rng.random() < 0.85:
        confirmed = frozenset({ids[0], ids[-1]})
    played_at = None
    if rng.random() < 0.5:
        played_at = datetime(
            day.year,
            day.month,
            day.day,
            rng.randint(7, 20),
            rng.choice((0, 30)),
            tzinfo=rng.choice(ZONES),
        )
    return Match(
        match_id,
        tuple(ids[:half]),
        tuple(ids[half:]),
        games_for(rng, gf, winners(rng, best_of)),
        day,
        fmt,
        match_type,
        best_of=best_of,
        game_format=gf,
        played_at=played_at,
        sequence=rng.randint(0, 2),
        confirmed_by=confirmed,
    )


def random_ledger(
    rng: random.Random,
    formats: tuple[RatingFormat, ...] = (S, D, D, MX),
    n_rows: int = 36,
    seed_days: tuple[int, int] = (-30, 10),
    n_seeds: int = 3,
) -> Ledger:
    """
    A ledger with singles, doubles, a category, timed and untimed rows, self-reported
    rows, identical repeated rows, rows that are not completed, content duplicates
    under other ids, and imported seeds.
    """
    pool = [f"syn-{i:02d}" for i in range(10)]
    rows = [random_row(rng, f"row-{i:02d}", pool, formats) for i in range(n_rows)]
    for i, source in enumerate(rng.sample(rows, 4)):
        copy = replace(source, match_id=f"copy-{i}", played_at=None, sequence=i)
        if rng.random() < 0.5:
            copy = mirror(copy)
        if copy.match_type is MatchType.SELF_REPORTED:
            copy = replace(copy, match_type=MatchType.LEAGUE)
        rows.append(copy)
    for i in range(2):
        source = rng.choice(rows)
        if rng.random() < 0.5:
            unfinished = replace(source, games=(), best_of=1, status=MatchStatus.FORFEIT)
        else:
            pattern = (True, False)
            unfinished = replace(
                source,
                games=games_for(rng, source.game_format, pattern),
                best_of=3,
                status=MatchStatus.RETIRED,
            )
        rows.append(replace(unfinished, match_id=f"open-{i}"))
    rows.extend(rng.sample(rows, 3))  # identical repeats, as a platform might resend them
    seeds = tuple(
        (
            pid,
            rng.choice(formats[:2]),
            round(rng.uniform(3.0, 6.0), 2),
            DAY0 + timedelta(days=rng.randint(*seed_days)),
        )
        for pid in rng.sample(pool, n_seeds)
    )
    return Ledger(tuple(rows), seeds)


@pytest.mark.parametrize("cfg", [DEFAULT, Config(reject_content_duplicates=True)])
def test_replay_ignores_row_and_seed_order(cfg: Config) -> None:
    rng = random.Random(2604)
    for _ in range(12):
        ledger = random_ledger(rng)
        expected = replay(ledger.rows, ledger.seed_players(), cfg=cfg, as_of=AS_OF).to_snapshot()
        for _ in range(4):
            rows, seeds = list(ledger.rows), ledger.seed_players()
            rng.shuffle(rows)
            rng.shuffle(seeds)
            assert replay(rows, seeds, cfg=cfg, as_of=AS_OF).to_snapshot() == expected


@pytest.mark.parametrize("cfg", [DEFAULT, Config(reject_content_duplicates=True)])
def test_live_rerecord_of_a_processed_match_changes_nothing(cfg: Config) -> None:
    rng = random.Random(2605)
    for _ in range(6):
        ledger = random_ledger(rng)
        rows = list(ledger.rows)
        rng.shuffle(rows)
        engine = ledger.live_engine(cfg, rows)
        snapshot = engine.to_snapshot()
        processed = [m for m in ledger.rows if m.match_id in engine.processed_match_ids]
        reserved = [m for m in ledger.rows if m.status is not MatchStatus.COMPLETED]
        assert processed and reserved
        for match in processed + reserved:
            one_game = (game_score(rng, match.game_format, rng.random() < 0.5),)
            changed = replace(match, games=one_game, best_of=1, status=MatchStatus.COMPLETED)
            for again in (match, mirror(match), changed):
                result = engine.record(again)
                assert not result.counted
                assert result.reason is RejectionReason.DUPLICATE_MATCH_ID
                assert engine.to_snapshot() == snapshot


@pytest.mark.parametrize("formats", [(S,), (D,), (S, D)], ids=["singles", "doubles", "both"])
def test_live_late_flag_matches_the_dates_already_reflected(
    formats: tuple[RatingFormat, ...],
) -> None:
    rng = random.Random(2606)
    seen = {True: 0, False: 0}
    frozen_only = 0  # late only because of a frozen player
    for _ in range(15):
        # Seeds captured mid-ledger, so a seeded player's later match can count before an
        # earlier one that is on or before the capture date (frozen for that player) arrives.
        ledger = random_ledger(rng, formats=formats * 2, seed_days=(4, 16), n_seeds=6)
        rows = list(ledger.rows)
        rng.shuffle(rows)
        engine = ledger.live_engine(DEFAULT, [])
        reflected: dict[tuple[str, RatingFormat], date] = {}
        for match in rows:
            result = engine.record(match)
            ids = match.team_a + match.team_b
            fmt = match.rating_format
            expected = result.counted and any(
                reflected.get((pid, fmt), match.played_on) > match.played_on for pid in ids
            )
            assert result.late == expected
            seen[result.late] += result.counted
            frozen_only += result.late and not any(
                reflected.get((pid, fmt), match.played_on) > match.played_on
                for pid in ids
                if pid not in result.frozen
            )
            if result.counted:
                for pid in ids:
                    if pid not in result.frozen:
                        key = (pid, fmt)
                        reflected[key] = max(reflected.get(key, match.played_on), match.played_on)
    assert seen[True] > 20 and seen[False] > 20
    assert frozen_only >= 3


# -- reliability and RD -----------------------------------------------------------


def pre_match_rd(engine: RatingEngine, pid: str, fmt: RatingFormat, on: date) -> float:
    """The RD a match on `on` starts from: the state's RD after inactivity."""
    if pid not in engine.players:
        return engine.cfg.new_player_rd
    return inflated_rd(engine.state(pid, fmt), on, engine.cfg)


def check_rd_step(
    engine: RatingEngine,
    result: MatchResult | None,
    match: Match,
    fmt: RatingFormat,
    before: dict[str, float],
) -> None:
    if result is None or not result.counted:
        return
    for pid, rd_before in before.items():
        if pid in result.frozen:
            continue
        rd_after = engine.state(pid, fmt).rd
        assert engine.cfg.rd_min <= rd_after <= rd_before


def test_rd_never_rises_across_a_counted_match() -> None:
    rng = random.Random(2607)
    steps = 0
    for _ in range(10):
        ledger = random_ledger(rng)
        rows = list(ledger.rows)
        rng.shuffle(rows)
        engine = ledger.live_engine(DEFAULT, [])
        for match in rows:
            ids = match.team_a + match.team_b
            fmt = match.rating_format
            before = {pid: pre_match_rd(engine, pid, fmt, match.played_on) for pid in ids}
            parent_before = {pid: pre_match_rd(engine, pid, D, match.played_on) for pid in ids}
            result = engine.record(match)
            check_rd_step(engine, result, match, fmt, before)
            if fmt.parent:
                check_rd_step(engine, result.parent, match, D, parent_before)
            steps += result.counted
    assert steps > 250


def test_inactivity_never_lowers_rd_or_raises_reliability() -> None:
    rng = random.Random(2608)
    offsets = (0, 1, 7, 30, 90, 200, 365, 800, 2000, 10000)
    for _ in range(4):
        ledger = random_ledger(rng)
        engine = replay(ledger.rows, ledger.seed_players(), as_of=AS_OF)
        for pid, player in sorted(engine.players.items()):
            for fmt, state in sorted(player.ratings.items()):
                start = state.last_played or state.rd_as_of or AS_OF
                rds: list[float] = []
                earned: list[float] = []
                for offset in offsets:
                    on = start + timedelta(days=offset)
                    summary = engine.player_summary(pid, fmt, as_of=on)
                    rds.append(inflated_rd(state, on, engine.cfg))
                    earned.append(reliability(state, engine.cfg, on))
                    assert summary.reliability == earned[-1]
                assert rds == sorted(rds)
                assert earned == sorted(earned, reverse=True)
                assert max(rds) <= engine.cfg.rd_max
                assert rds[-1] == engine.cfg.rd_max


def weekly_schedule(rng: random.Random, fmt: RatingFormat, self_reported: bool) -> list[Match]:
    """
    One player, one match a week, partners and opponents drawn from a large pool.

    Verified schedules mix match types, game formats, and best of 1 and 3.
    A self-reported schedule keeps one weight: after a run of heavier matches, one
    lighter match can shrink RD by less than a week of inactivity grew it.
    """
    pool = [f"syn-{i:03d}" for i in range(200)]
    rows = []
    for week in range(40):
        others = rng.sample(pool, 2 * fmt.team_size - 1)
        team_a = ("syn-focus", *others[: fmt.team_size - 1])
        team_b = tuple(others[fmt.team_size - 1 :])
        if self_reported:
            gf, best_of, match_type = GAME_FORMATS[0], 1, MatchType.SELF_REPORTED
        else:
            gf, best_of = rng.choice(GAME_FORMATS), rng.choice((1, 3))
            match_type = rng.choice(VERIFIED)
        rows.append(
            Match(
                f"week-{week:02d}",
                team_a,
                team_b,
                games_for(rng, gf, winners(rng, best_of)),
                DAY0 + timedelta(weeks=week),
                fmt,
                match_type,
                best_of=best_of,
                game_format=gf,
                confirmed_by=frozenset({team_a[0], team_b[0]}),
            )
        )
    return rows


@pytest.mark.parametrize("self_reported", [False, True], ids=["verified", "self_reported"])
@pytest.mark.parametrize("fmt", [S, D])
def test_reliability_never_falls_across_a_weekly_schedule(
    fmt: RatingFormat, self_reported: bool
) -> None:
    rng = random.Random(2609)
    for _ in range(5):
        engine = RatingEngine()
        earned = []
        for match in weekly_schedule(rng, fmt, self_reported):
            assert engine.record(match).counted
            earned.append(engine.player_summary("syn-focus", fmt, as_of=match.played_on))
            assert engine.state("syn-focus", fmt).rd >= engine.cfg.rd_min
        values = [summary.reliability for summary in earned]
        assert values == sorted(values)
        assert values[-1] > values[0]
        if not self_reported:
            # Verified matches drive RD down to the floor, and it stays there.
            assert engine.state("syn-focus", fmt).rd == engine.cfg.rd_min


# -- self-reported repeat window --------------------------------------------------


def self_reported(match_id: str, on: date, score: int = 7) -> Match:
    return Match(
        match_id,
        ("syn-a", "syn-b"),
        ("syn-c", "syn-d"),
        ((11, score),),
        on,
        D,
        MatchType.SELF_REPORTED,
        confirmed_by=frozenset({"syn-a", "syn-c"}),
    )


WINDOW = DEFAULT.self_reported_repeat_window_days
BASE_WEIGHT = DEFAULT.match_weights[MatchType.SELF_REPORTED]


@pytest.mark.parametrize(
    ("gap", "factor"),
    [(1, 0.5), (WINDOW - 1, 0.5), (WINDOW, 0.5), (WINDOW + 1, 1.0), (3 * WINDOW, 1.0)],
)
def test_repeat_discount_resets_once_the_earlier_match_leaves_the_window(
    gap: int, factor: float
) -> None:
    engine = RatingEngine()
    assert engine.record(self_reported("sr-0", DAY0)).counted
    result = engine.record(self_reported("sr-1", DAY0 + timedelta(days=gap), score=5))
    assert result.counted
    assert result.effective_weight == pytest.approx(BASE_WEIGHT * factor, abs=1e-5)


LIMIT = DEFAULT.self_reported_max_repeats


@pytest.mark.parametrize(
    ("gap", "expired"),
    [(WINDOW, 0), (WINDOW + 1, 1), (WINDOW + 2, 2), (WINDOW + LIMIT, LIMIT)],
)
def test_repeat_limit_lifts_as_earlier_matches_leave_the_window(gap: int, expired: int) -> None:
    """LIMIT matches on consecutive days, then one more `gap` days after the first."""
    engine = RatingEngine()
    for i in range(LIMIT):
        assert engine.record(self_reported(f"sr-{i}", DAY0 + timedelta(days=i), score=i)).counted
    result = engine.record(self_reported("sr-late", DAY0 + timedelta(days=gap), score=9))
    if expired == 0:
        assert not result.counted
        assert result.reason is RejectionReason.SELF_REPORTED_REPEAT_LIMIT
        return
    assert result.counted
    prior = LIMIT - expired
    expected = BASE_WEIGHT * DEFAULT.self_reported_repeat_factor**prior
    assert result.effective_weight == pytest.approx(expected, abs=1e-5)
