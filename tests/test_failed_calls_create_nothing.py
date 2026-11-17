"""A creating call that raises leaves the engine unchanged (issue #23 addendum, part A).

link_account, attach_external_id, and seed_external_rating run every check
before they change any state, so a failed call on a new id leaves no empty
player behind and keeps the resumable mark. Error types and messages are
the same as before. All ids are synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date

import pytest

from open_engine import (
    ExternalRatingSnapshot,
    InvalidSeedError,
    Match,
    OpenEngineError,
    Player,
    PlayerError,
    RatingEngine,
    RatingFormat,
    RatingState,
    replay,
)

D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
AS_OF = date(2025, 3, 1)
CAPTURED = date(2025, 1, 1)
LEDGER = [
    Match("syn-1", ("syn-c", "syn-e"), ("syn-f", "syn-g"), ((11, 4),), date(2025, 2, 1), D),
]


def _engine() -> RatingEngine:
    """
    A replayed, resumable engine.

    syn-a holds account acct-a and identity src/ext-a, syn-d holds an
    unplayed doubles seed, and syn-c has a played doubles rating.
    """
    seeds = [
        Player("syn-a", external_ids={"src": "ext-a"}, account_user_id="acct-a"),
        Player("syn-d", {D: RatingState(rating=3.5, rd=RatingEngine().cfg.new_player_rd)}),
    ]
    return replay(LEDGER, seeds, as_of=AS_OF)


def _snap(source: str = "src", external_id: str | None = None) -> ExternalRatingSnapshot:
    return ExternalRatingSnapshot(source, 4.0, CAPTURED, None, external_id)


Call = Callable[[RatingEngine], object]

# (id of the case, call, error class, message). Each message is the one
# origin/main raises for the same call.
CASES: list[tuple[str, Call, type[OpenEngineError], str]] = [
    # A new id.
    (
        "new: link an account linked to another player",
        lambda e: e.link_account("syn-new", "acct-a"),
        PlayerError,
        "This account is already linked to another player profile",
    ),
    (
        "new: attach an identity linked to another player",
        lambda e: e.attach_external_id("syn-new", "src", "ext-a"),
        PlayerError,
        "This external identity is already linked to another player",
    ),
    (
        "new: seed with an identity linked to another player",
        lambda e: e.seed_external_rating("syn-new", D, _snap("src", "ext-a")),
        PlayerError,
        "This external identity is already linked to another player",
    ),
    (
        "new: seed with an identity and no source",
        lambda e: e.seed_external_rating("syn-new", D, _snap("", "ext-q")),
        PlayerError,
        "source and external_player_id are required",
    ),
    (
        "new: blank account",
        lambda e: e.link_account("syn-new", ""),
        PlayerError,
        "account_user_id is required",
    ),
    (
        "new: blank source",
        lambda e: e.attach_external_id("syn-new", "", "ext-q"),
        PlayerError,
        "source and external_player_id are required",
    ),
    (
        "new: blank external id",
        lambda e: e.attach_external_id("syn-new", "src", ""),
        PlayerError,
        "source and external_player_id are required",
    ),
    # A blank player id.
    (
        "blank: link_account",
        lambda e: e.link_account("  ", "acct-q"),
        PlayerError,
        "player_id is required",
    ),
    (
        "blank: link_account and blank account",
        lambda e: e.link_account("", ""),
        PlayerError,
        "account_user_id is required",
    ),
    (
        "blank: attach_external_id",
        lambda e: e.attach_external_id("  ", "src", "ext-q"),
        PlayerError,
        "player_id is required",
    ),
    (
        "blank: attach_external_id and blank source",
        lambda e: e.attach_external_id("", "", "ext-q"),
        PlayerError,
        "source and external_player_id are required",
    ),
    (
        "blank: seed_external_rating",
        lambda e: e.seed_external_rating("  ", D, _snap("src", "ext-q")),
        PlayerError,
        "player_id is required",
    ),
    (
        "blank: seed_external_rating with no source",
        lambda e: e.seed_external_rating("", D, _snap("", "ext-q")),
        PlayerError,
        "player_id is required",
    ),
    # An existing player.
    (
        "existing: link a player linked to another account",
        lambda e: e.link_account("syn-a", "acct-q"),
        PlayerError,
        "Player profile is already linked to another account",
    ),
    (
        "existing: link an account linked to another player",
        lambda e: e.link_account("syn-d", "acct-a"),
        PlayerError,
        "This account is already linked to another player profile",
    ),
    (
        "existing: attach a second id from a source",
        lambda e: e.attach_external_id("syn-a", "src", "ext-z"),
        PlayerError,
        "A different external identity is already linked",
    ),
    (
        "existing: attach an identity linked to another player",
        lambda e: e.attach_external_id("syn-d", "src", "ext-a"),
        PlayerError,
        "This external identity is already linked to another player",
    ),
    (
        "existing: seed with a second id from a source",
        lambda e: e.seed_external_rating("syn-a", D, _snap("src", "ext-z")),
        PlayerError,
        "A different external identity is already linked",
    ),
    (
        "existing: seed with an identity linked to another player",
        lambda e: e.seed_external_rating("syn-d", MX, _snap("src", "ext-a")),
        PlayerError,
        "This external identity is already linked to another player",
    ),
    (
        "existing: seed with an identity and no source",
        lambda e: e.seed_external_rating("syn-d", MX, _snap("", "ext-q")),
        PlayerError,
        "source and external_player_id are required",
    ),
    (
        "existing: seed over an unplayed seed",
        lambda e: e.seed_external_rating("syn-d", D, _snap()),
        InvalidSeedError,
        "A rating already exists; explicitly allow replacing an unplayed seed",
    ),
    (
        "existing: seed over an unplayed seed with an identity",
        lambda e: e.seed_external_rating("syn-d", D, _snap("src", "ext-q")),
        InvalidSeedError,
        "A rating already exists; explicitly allow replacing an unplayed seed",
    ),
    (
        "existing: seed over a played rating",
        lambda e: e.seed_external_rating("syn-c", D, _snap(), replace_unplayed_seed=True),
        InvalidSeedError,
        "Cannot replace an OPEN rating after OPEN matches have been processed",
    ),
]


@pytest.mark.parametrize(
    ("call", "error", "message"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_a_failed_creating_call_changes_nothing(
    call: Call, error: type[OpenEngineError], message: str
) -> None:
    engine = _engine()
    before = json.dumps(engine.to_snapshot(), sort_keys=True)
    players_before = list(engine.players)
    assert engine.to_snapshot()["resumable"] is True
    with pytest.raises(error, match=f"^{message}$"):
        call(engine)
    assert json.dumps(engine.to_snapshot(), sort_keys=True) == before
    assert list(engine.players) == players_before


def test_successful_calls_on_a_new_id_still_create() -> None:
    engine = _engine()
    engine.link_account("syn-n1", "acct-n1")
    engine.attach_external_id("syn-n2", "src", "ext-n2")
    state = engine.seed_external_rating("syn-n3", D, _snap("src", "ext-n3"))
    assert engine.players["syn-n1"].account_user_id == "acct-n1"
    assert engine.players["syn-n2"].external_ids == {"src": "ext-n2"}
    assert engine.players["syn-n3"].external_ids == {"src": "ext-n3"}
    assert engine.players["syn-n3"].ratings[D] is state
    assert state.rating == 4.0
    assert engine.to_snapshot()["resumable"] is False


def test_a_seed_that_replaces_an_unplayed_seed_still_works() -> None:
    engine = _engine()
    state = engine.seed_external_rating(
        "syn-d", D, _snap("src", "ext-d"), replace_unplayed_seed=True
    )
    assert engine.players["syn-d"].ratings[D] is state
    assert engine.players["syn-d"].external_ids == {"src": "ext-d"}
