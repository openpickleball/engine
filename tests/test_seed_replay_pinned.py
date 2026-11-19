"""Replaying engine-made seeds gives the ratings it gave before seed validation."""

from __future__ import annotations

import copy
from datetime import date

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    Match,
    Player,
    RatingEngine,
    RatingFormat,
    replay,
)

D = RatingFormat.DOUBLES
MIXED = RatingFormat.MIXED_DOUBLES
S = RatingFormat.SINGLES


def _seeds(cfg: Config) -> list[Player]:
    eng = RatingEngine(cfg)
    eng.seed_external_rating(
        "imp", D, ExternalRatingSnapshot("external_standard", 4.2, date(2026, 9, 1), 85)
    )
    eng.seed_external_rating(
        "noreli", D, ExternalRatingSnapshot("external_standard", 3.8, date(2026, 9, 15))
    )
    eng.seed_provisional_rating("prov", D)
    eng.seed_external_rating(
        "single", S, ExternalRatingSnapshot("external_standard", 4.5, date(2026, 9, 1), 70)
    )
    eng.seed_provisional_rating("other", D)
    eng.seed_provisional_rating("other", S)
    seeds = [copy.deepcopy(eng.players[pid]) for pid in sorted(eng.players)]
    # The engine stores no category starting state (issue #112), so the
    # seeds carry the one state reports, as a backend can.
    for seed in seeds:
        if seed.id in ("imp", "noreli", "prov"):
            seed.ratings[MIXED] = copy.deepcopy(eng.state(seed.id, MIXED))
    return seeds


LEDGER = [
    Match("m1", ("imp", "prov"), ("noreli", "other"), ((11, 7),), date(2026, 10, 3), MIXED),
    Match(
        "m2",
        ("imp", "noreli"),
        ("prov", "other"),
        ((11, 9), (8, 11), (11, 6)),
        date(2026, 10, 10),
        D,
        best_of=3,
    ),
    Match("m3", ("single",), ("other",), ((11, 4),), date(2026, 10, 12), S),
    Match("m4", ("prov", "noreli"), ("imp", "other"), ((7, 11),), date(2026, 10, 20), D),
]

# (rating, rd) by (player, format), computed on origin/main (214674b).
EXPECTED = {
    "default": {
        ("imp", "doubles"): (4.216607204582601, 0.3508715452911436),
        ("imp", "mixed_doubles"): (4.221310626236882, 0.47431174221180733),
        ("noreli", "doubles"): (3.7458340279179576, 0.34894462094794293),
        ("noreli", "mixed_doubles"): (3.7787629714850173, 0.4724301971720266),
        ("other", "doubles"): (3.5301875112899967, 0.655841753700352),
        ("other", "mixed_doubles"): (3.467827437036002, 0.752),
        ("other", "singles"): (3.4799098070541072, 0.752),
        ("prov", "doubles"): (3.5355363568439495, 0.655841753700352),
        ("prov", "mixed_doubles"): (3.532172562963998, 0.752),
        ("single", "singles"): (4.511525358535171, 0.4013524814922663),
    },
    "external_rd_above_category": {
        ("imp", "doubles"): (4.222011520439452, 0.49590322402709835),
        ("imp", "mixed_doubles"): (4.224959576932707, 0.5675981226184597),
        ("noreli", "doubles"): (3.7270705013712186, 0.49454172199520124),
        ("noreli", "mixed_doubles"): (3.7751018878879163, 0.5660267583780824),
        ("other", "doubles"): (3.529669723012258, 0.655841753700352),
        ("other", "mixed_doubles"): (3.467827437036002, 0.752),
        ("other", "singles"): (3.4799098070541072, 0.752),
        ("prov", "doubles"): (3.5360515364093925, 0.655841753700352),
        ("prov", "mixed_doubles"): (3.532172562963998, 0.752),
        ("single", "singles"): (4.5156106537346865, 0.5686059922301205),
    },
}
CONFIGS = {
    "default": Config(),
    "external_rd_above_category": Config(external_seed_rd=0.6, category_seed_rd=0.5),
}


@pytest.mark.parametrize("name", list(CONFIGS))
def test_replay_of_engine_made_seeds_is_unchanged(name: str) -> None:
    cfg = CONFIGS[name]
    engine = replay(LEDGER, _seeds(cfg), cfg=cfg, as_of=date(2026, 11, 1))
    got = {
        (pid, fmt.value): (state.rating, state.rd)
        for pid, player in engine.players.items()
        for fmt, state in player.ratings.items()
    }
    assert got == EXPECTED[name]
