"""Every snapshot of a random sequence of engine calls loads and round trips (issue #112).

seed_provisional_rating no longer stores a category state under
category_feeds_parent. Random sequences of seeds (through every entry
point), seed_provisional_rating in every format, counted and rejected
matches in every format, and replays, under random configs, must leave the
engine in a state whose to_snapshot() loads with from_snapshot and gives
the same snapshot back. All ids are synthetic.
"""

from __future__ import annotations

import copy
import json
import random
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
