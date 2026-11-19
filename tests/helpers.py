"""Shared test helpers. All ids are synthetic.

Under Config.category_feeds_parent, seed_provisional_rating does not store a
category state (issue #112). Tests that need a stored category state get it
here, so a setup that changes the state it stores is never silently lost.
"""

from __future__ import annotations

import copy

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    Player,
    RatingEngine,
    RatingFormat,
    RatingState,
)


def store_state(engine: RatingEngine, player_id: str, rating_format: RatingFormat) -> RatingState:
    """
    Store the player's starting state in a format, and return the stored object.

    Creates the player record when missing. For a category format under
    category_feeds_parent this simulates a state an earlier release's
    seed_provisional_rating stored: the engine no longer stores one there,
    but snapshots holding one must still load. Elsewhere it is what
    seed_provisional_rating does. Changing the returned object changes the
    engine's state.
    """
    engine.seed_provisional_rating(player_id, rating_format)
    player = engine.players[player_id]
    if rating_format not in player.ratings:
        engine._put_state(player, rating_format, engine.state(player_id, rating_format))
    return player.ratings[rating_format]


def derived_seed(
    player_id: str,
    snapshot: ExternalRatingSnapshot,
    category: RatingFormat,
    cfg: Config | None = None,
) -> Player:
    """
    A backend seed: a doubles seed from snapshot and the category state derived from it.

    The derived state has initial_source "open_parent:doubles" and passes the
    seed rules, so add_player and replay accept it.
    """
    assert category.parent is RatingFormat.DOUBLES
    scratch = RatingEngine(cfg)
    scratch.seed_external_rating(player_id, RatingFormat.DOUBLES, snapshot)
    player = copy.deepcopy(scratch.players[player_id])
    player.ratings[category] = copy.deepcopy(scratch.state(player_id, category))
    return player
