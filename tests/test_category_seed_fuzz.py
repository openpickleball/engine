"""Random engine call sequences: snapshots round trip, and live equals replay (issue #112).

seed_provisional_rating no longer stores a category state under
category_feeds_parent. Random sequences of seeds (through every entry
point), seed_provisional_rating in every format, counted and rejected
matches in every format, and replays, under random configs, must leave the
engine in a state whose to_snapshot() loads with from_snapshot and gives
the same snapshot back. A second test seeds first, then records matches in
date order with seed_provisional_rating calls between them that must not
change anything, and checks that live equals a replay given the engine's
own players, taken before the first match, as seeds. All ids are
synthetic.
"""

from __future__ import annotations

import copy
import json
import random
from dataclasses import fields
from datetime import date, timedelta

import pytest

from helpers import derived_seed
from open_engine import (
    Config,
    ExternalRatingSnapshot,
    Match,
    OpenEngineError,
    Player,
    RatingEngine,
    RatingFormat,
    RatingState,
    replay,
)

FORMATS = list(RatingFormat)
CATEGORIES = [fmt for fmt in FORMATS if fmt.parent is not None]
PLAYERS = [f"syn-{c}" for c in "abcdefgh"]
START = date(2026, 1, 1)
AS_OF = date(2026, 4, 1)


def _config(rng: random.Random) -> Config:
    return Config(
        category_feeds_parent=rng.random() < 0.7,
        max_doubles_partner_gap=rng.choice([None, 0.2, 0.5]),
        max_singles_rating_gap=rng.choice([None, 0.5]),
    )


def _day(rng: random.Random) -> date:
    return START + timedelta(days=rng.randint(0, 60))


def _snap(rng: random.Random) -> ExternalRatingSnapshot:
    return ExternalRatingSnapshot("ext", round(rng.uniform(2.5, 5.0), 2), _day(rng))


def _match(rng: random.Random, match_id: str) -> Match:
    fmt = rng.choice(FORMATS)
    ids = rng.sample(PLAYERS, 2 * fmt.team_size)
    size = fmt.team_size
    loser = rng.randint(0, 9)
    games = ((11, loser),) if rng.random() < 0.5 else ((loser, 11),)
    return Match(match_id, tuple(ids[:size]), tuple(ids[size:]), games, _day(rng), fmt)


def _seed_player(rng: random.Random, pid: str, cfg: Config) -> Player:
    """A seed a backend could pass: provisional, imported, or a derived category state."""
    kind = rng.choice(["provisional", "derived", "empty"])
    if kind == "derived" and cfg.category_feeds_parent:
        return derived_seed(pid, _snap(rng), rng.choice(CATEGORIES), cfg)
    if kind == "empty":
        return Player(pid)
    fmt = rng.choice(FORMATS)
    return Player(pid, {fmt: RatingState(rating=cfg.default_rating, rd=cfg.new_player_rd)})


def _assert_round_trips(engine: RatingEngine, cfg: Config) -> None:
    snapshot = engine.to_snapshot()
    loaded = RatingEngine.from_snapshot(json.loads(json.dumps(snapshot)), cfg=cfg)
    assert loaded.to_snapshot() == snapshot


@pytest.mark.parametrize("seed", range(60))
def test_random_call_sequences_round_trip(seed: int) -> None:
    rng = random.Random(seed)
    cfg = _config(rng)
    engine = RatingEngine(cfg)
    seeds: list[Player] = []
    for pid in PLAYERS[:3]:
        player = _seed_player(rng, pid, cfg)
        engine.add_player(copy.deepcopy(player))
        seeds.append(player)
    ledger: list[Match] = []
    for step in range(40):
        op = rng.choice(["external", "provisional", "add", "record", "record", "replay"])
        pid = rng.choice(PLAYERS)
        try:
            if op == "external":
                engine.seed_external_rating(
                    pid,
                    rng.choice(FORMATS),
                    _snap(rng),
                    replace_unplayed_seed=rng.random() < 0.5,
                )
            elif op == "provisional":
                engine.seed_provisional_rating(pid, rng.choice(FORMATS))
            elif op == "add":
                engine.add_player(_seed_player(rng, f"syn-n{step}", cfg))
            elif op == "record":
                match = _match(rng, f"syn-m{step}")
                engine.record(match)
                ledger.append(match)
            else:
                engine = replay(ledger, copy.deepcopy(seeds), cfg=cfg, as_of=AS_OF)
        except OpenEngineError:
            # A refused call changes nothing; the snapshot must still load.
            pass
        _assert_round_trips(engine, cfg)


# Labeling, not rating: a live update is counted until the next replay, and a
# category state derived live carries its parent's count. A replay gives 0.
_LABEL_FIELDS = {"live_updates_since_replay", "inherited_live_updates"}


def _assert_live_equals_replay(live: RatingEngine, replayed: RatingEngine) -> None:
    assert live.processed_match_ids == replayed.processed_match_ids
    assert live._reserved_match_ids == replayed._reserved_match_ids
    assert sorted(live.players) == sorted(replayed.players)
    for pid in sorted(live.players):
        left = live.players[pid].ratings
        right = replayed.players[pid].ratings
        assert sorted(left) == sorted(right), pid
        for fmt in sorted(left):
            for field in fields(RatingState):
                if field.name in _LABEL_FIELDS:
                    continue
                assert getattr(left[fmt], field.name) == getattr(right[fmt], field.name), (
                    pid,
                    fmt,
                    field.name,
                )


def _stores_nothing(engine: RatingEngine, pid: str, fmt: RatingFormat) -> bool:
    """True when seed_provisional_rating(pid, fmt) must leave the engine as it is."""
    player = engine.players.get(pid)
    if player is None:
        return False
    return fmt in player.ratings or (fmt.parent is not None and engine.cfg.category_feeds_parent)


@pytest.mark.parametrize("seed", range(60))
def test_live_equals_replay_from_the_engine_seeds(seed: int) -> None:
    rng = random.Random(seed)
    cfg = _config(rng)
    engine = RatingEngine(cfg)
    for step in range(12):
        op = rng.choice(["external", "provisional", "provisional", "add"])
        pid = rng.choice(PLAYERS)
        try:
            if op == "external":
                engine.seed_external_rating(
                    pid,
                    rng.choice(FORMATS),
                    _snap(rng),
                    replace_unplayed_seed=rng.random() < 0.5,
                )
            elif op == "provisional":
                engine.seed_provisional_rating(pid, rng.choice(FORMATS))
            else:
                engine.add_player(_seed_player(rng, f"syn-n{step}", cfg))
        except OpenEngineError:
            pass
    seeds = [copy.deepcopy(p) for p in engine.players.values()]
    ledger: list[Match] = []
    for step in range(30):
        if rng.random() < 0.4:
            pid = rng.choice(PLAYERS)
            fmt = rng.choice(FORMATS)
            if _stores_nothing(engine, pid, fmt):
                engine.seed_provisional_rating(pid, fmt)
        match = _match(rng, f"syn-m{step:02d}")
        # One match a day, in order, so arrival order is ledger order.
        match = Match(
            match.match_id,
            match.team_a,
            match.team_b,
            match.games,
            START + timedelta(days=step),
            match.rating_format,
        )
        engine.record(match)
        ledger.append(match)
    replayed = replay(ledger, seeds, cfg=cfg, as_of=AS_OF)
    _assert_live_equals_replay(engine, replayed)
