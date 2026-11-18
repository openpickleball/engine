"""Identity and provenance values must be plain str and dict (issue #96).

A str or dict subclass can override __eq__, __hash__, strip, or get and so
pass a check that the plain value would fail. The engine then holds a
state its own snapshot cannot load. Ids, sources, external ids, account
ids, seed provenance strings, and external_ids must be exactly str and
dict. A subclass raises before anything changes. All ids are synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from open_engine import (
    ExternalRatingSnapshot,
    InvalidMatchError,
    InvalidSeedError,
    Match,
    Player,
    PlayerError,
    RatingEngine,
    RatingFormat,
    SnapshotError,
    prequential_predictions,
    replay,
)

D = RatingFormat.DOUBLES
S = RatingFormat.SINGLES
AS_OF = date(2026, 11, 1)
CAPTURED = date(2026, 10, 1)

Snap = dict[str, Any]


class SubStr(str):
    """A str subclass that changes nothing."""


class SubDict(dict):  # type: ignore[type-arg]
    """A dict subclass that changes nothing."""


class AlwaysEqual(str):
    """The issue's str subclass that equals everything."""

    def __eq__(self, other: object) -> bool:
        return True

    __hash__ = str.__hash__


class Strip(str):
    """The issue's str subclass whose strip is never blank."""

    def strip(self, chars: str | None = None) -> str:
        return "x"


class LyingGet(dict):  # type: ignore[type-arg]
    """A dict subclass whose get always finds the asked-for link."""

    def get(self, key: object, default: object = None) -> object:
        return "X1"


def _provisional(**changes: Any) -> Any:
    state = RatingEngine().seed_provisional_rating("syn-x", D)
    return replace(state, **changes)


def _imported(**changes: Any) -> Any:
    snapshot = ExternalRatingSnapshot("ext", 4.0, CAPTURED, external_player_id="X1")
    state = RatingEngine().seed_external_rating("syn-x", D, snapshot)
    return replace(state, **changes)


# -- seeds: every seed entry point -------------------------------------------

SEED_ROWS: list[tuple[str, Callable[[], Player], str]] = [
    ("id", lambda: Player(SubStr("syn-a")), r"^Seed player id must be a str, not SubStr$"),
    (
        "account_user_id",
        lambda: Player("syn-a", account_user_id=SubStr("acct-1")),
        r"^Seed for syn-a: account_user_id must be a str or None$",
    ),
    (
        "external_ids_dict",
        lambda: Player("syn-a", external_ids=SubDict({"ext": "X1"})),
        r"^Seed for syn-a: external_ids must be a dict$",
    ),
    (
        "external_ids_lying_get",
        lambda: Player("syn-a", {D: _imported()}, external_ids=LyingGet({"ext": "Q"})),
        r"^Seed for syn-a: external_ids must be a dict$",
    ),
    (
        "external_ids_key",
        lambda: Player("syn-a", external_ids={SubStr("ext"): "X1"}),
        r"^Seed for syn-a: external_ids must map str to str$",
    ),
    (
        "external_ids_value",
        lambda: Player("syn-a", external_ids={"ext": SubStr("X1")}),
        r"^Seed for syn-a: external_ids must map str to str$",
    ),
    (
        "ratings_dict",
        lambda: Player("syn-a", SubDict({D: _provisional()})),
        r"^Seed for syn-a: ratings must be a dict$",
    ),
    (
        "initial_source",
        lambda: Player("syn-a", {D: _provisional(initial_source=SubStr("open_provisional"))}),
        r"^Seed for syn-a in doubles: initial_source must be a str$",
    ),
    (
        "initial_source_strip",
        lambda: Player("syn-a", {D: _provisional(initial_source=Strip("  "))}),
        r"^Seed for syn-a in doubles: initial_source must be a str$",
    ),
    (
        "initial_external_player_id",
        lambda: Player(
            "syn-a",
            {D: _imported(initial_external_player_id=SubStr("X1"))},
            external_ids={"ext": "X1"},
        ),
        r"^Seed for syn-a in doubles: initial_external_player_id must be a str or None$",
    ),
    (
        "initial_evidence_reference",
        lambda: Player("syn-a", {D: _provisional(initial_evidence_reference=SubStr("ref-1"))}),
        r"^Seed for syn-a in doubles: initial_evidence_reference must be a str or None$",
    ),
    (
        "initial_evidence_reference_int",
        lambda: Player("syn-a", {D: _provisional(initial_evidence_reference=123)}),
        r"^Seed for syn-a in doubles: initial_evidence_reference must be a str or None$",
    ),
]


def _base_engine() -> RatingEngine:
    engine = RatingEngine()
    engine.seed_provisional_rating("syn-base", D)
    return engine


@pytest.mark.parametrize(("name", "make", "message"), SEED_ROWS, ids=[r[0] for r in SEED_ROWS])
def test_add_player_rejects_a_subclass_and_changes_nothing(
    name: str, make: Callable[[], Player], message: str
) -> None:
    engine = _base_engine()
    before = engine.to_snapshot()
    with pytest.raises(InvalidSeedError, match=message):
        engine.add_player(make())
    assert engine.to_snapshot() == before
    assert sorted(engine.players) == ["syn-base"]


@pytest.mark.parametrize(("name", "make", "message"), SEED_ROWS, ids=[r[0] for r in SEED_ROWS])
def test_replay_seeds_reject_a_subclass(
    name: str, make: Callable[[], Player], message: str
) -> None:
    with pytest.raises(InvalidSeedError, match=message):
        replay([], [make()], as_of=AS_OF)


@pytest.mark.parametrize("mode", ["live", "scheduled"])
@pytest.mark.parametrize(("name", "make", "message"), SEED_ROWS, ids=[r[0] for r in SEED_ROWS])
def test_prequential_seeds_reject_a_subclass(
    name: str, make: Callable[[], Player], message: str, mode: Any
) -> None:
    match = Match("m1", ("syn-p", "syn-q"), ("syn-r", "syn-s"), ((11, 7),), AS_OF, D)
    with pytest.raises(InvalidSeedError, match=message):
        prequential_predictions([match], [make()], mode=mode)


def test_issue_always_equal_reproduction_raises() -> None:
    engine = RatingEngine()
    snapshot = ExternalRatingSnapshot("ext", 4.0, CAPTURED, external_player_id="X")
    seeded = engine.seed_external_rating("syn-a", D, snapshot)
    before = engine.to_snapshot()
    seed = Player(
        "syn-b",
        {D: replace(seeded, initial_external_player_id=AlwaysEqual("X"))},
        external_ids={"ext": "Q"},
    )
    with pytest.raises(
        InvalidSeedError,
        match=r"^Seed for syn-b in doubles: initial_external_player_id must be a str or None$",
    ):
        engine.add_player(seed)
    assert engine.to_snapshot() == before


# -- seed_external_rating ----------------------------------------------------

EXTERNAL_ROWS: list[tuple[str, dict[str, Any], str]] = [
    ("source", {"source": SubStr("ext")}, r"^External snapshot source must be a str$"),
    ("source_strip", {"source": Strip("  ")}, r"^External snapshot source must be a str$"),
    (
        "external_player_id",
        {"external_player_id": SubStr("X1")},
        r"^External snapshot external_player_id must be a str or None$",
    ),
    (
        "external_player_id_always_equal",
        {"external_player_id": AlwaysEqual("X")},
        r"^External snapshot external_player_id must be a str or None$",
    ),
    (
        "evidence_reference",
        {"evidence_reference": SubStr("ref-1")},
        r"^External snapshot evidence_reference must be a str or None$",
    ),
    (
        "evidence_reference_int",
        {"evidence_reference": 123},
        r"^External snapshot evidence_reference must be a str or None$",
    ),
]


@pytest.mark.parametrize(
    ("name", "changes", "message"), EXTERNAL_ROWS, ids=[r[0] for r in EXTERNAL_ROWS]
)
def test_seed_external_rating_rejects_a_subclass_and_changes_nothing(
    name: str, changes: dict[str, Any], message: str
) -> None:
    engine = _base_engine()
    before = engine.to_snapshot()
    snapshot = replace(ExternalRatingSnapshot("ext", 4.0, CAPTURED), **changes)
    with pytest.raises(InvalidSeedError, match=message):
        engine.seed_external_rating("syn-a", D, snapshot)
    assert engine.to_snapshot() == before


def test_seed_external_rating_rejects_a_subclass_player_id() -> None:
    engine = _base_engine()
    before = engine.to_snapshot()
    with pytest.raises(PlayerError, match=r"^player_id is required$"):
        engine.seed_external_rating(
            SubStr("syn-a"), D, ExternalRatingSnapshot("ext", 4.0, CAPTURED)
        )
    assert engine.to_snapshot() == before


# -- attach_external_id and link_account -------------------------------------

LINK_ROWS: list[tuple[str, Callable[[RatingEngine], None], str]] = [
    (
        "attach_player_id",
        lambda e: e.attach_external_id(SubStr("syn-a"), "ext", "Y"),
        r"^player_id is required$",
    ),
    (
        "attach_source",
        lambda e: e.attach_external_id("syn-a", SubStr("ext"), "Y"),
        r"^source and external_player_id are required$",
    ),
    (
        "attach_source_strip",
        lambda e: e.attach_external_id("syn-a", Strip("  "), "Y"),
        r"^source and external_player_id are required$",
    ),
    (
        "attach_external_player_id",
        lambda e: e.attach_external_id("syn-a", "ext", SubStr("Y")),
        r"^source and external_player_id are required$",
    ),
    (
        "link_player_id",
        lambda e: e.link_account(SubStr("syn-a"), "acct-1"),
        r"^player_id is required$",
    ),
    (
        "link_account_user_id",
        lambda e: e.link_account("syn-a", SubStr("acct-1")),
        r"^account_user_id is required$",
    ),
]


@pytest.mark.parametrize(("name", "call", "message"), LINK_ROWS, ids=[r[0] for r in LINK_ROWS])
def test_identity_links_reject_a_subclass_and_create_no_player(
    name: str, call: Callable[[RatingEngine], None], message: str
) -> None:
    engine = _base_engine()
    before = engine.to_snapshot()
    with pytest.raises(PlayerError, match=message):
        call(engine)
    assert engine.to_snapshot() == before
    assert sorted(engine.players) == ["syn-base"]


# -- from_snapshot -------------------------------------------------------------


def _loaded_snapshot() -> Snap:
    seed = Player(
        "syn-a",
        {D: _imported(initial_evidence_reference="ref-1")},
        external_ids={"ext": "X1"},
        account_user_id="acct-1",
    )
    engine = replay([], [seed], as_of=AS_OF)
    loaded: Snap = json.loads(json.dumps(engine.to_snapshot()))
    return loaded


def _sub_state_field(name: str) -> Callable[[Snap], None]:
    def change(player: Snap) -> None:
        state = player["ratings"]["doubles"]
        state[name] = SubStr(state[name])

    return change


def _sub_id(player: Snap) -> None:
    player["id"] = SubStr(player["id"])


def _sub_account(player: Snap) -> None:
    player["account_user_id"] = SubStr(player["account_user_id"])


def _sub_external_key(player: Snap) -> None:
    player["external_ids"] = {SubStr(k): v for k, v in player["external_ids"].items()}


def _sub_external_value(player: Snap) -> None:
    player["external_ids"] = {k: SubStr(v) for k, v in player["external_ids"].items()}


SNAPSHOT_ROWS: list[tuple[str, Callable[[Snap], None], str]] = [
    ("id", _sub_id, r"^snapshot\.players\[0\]\.id must be a non-blank str$"),
    (
        "account_user_id",
        _sub_account,
        r"^snapshot\.players\[0\]\.account_user_id must be a str or null$",
    ),
    (
        "external_ids_key",
        _sub_external_key,
        r"^snapshot\.players\[0\]\.external_ids must map str to str$",
    ),
    (
        "external_ids_value",
        _sub_external_value,
        r"^snapshot\.players\[0\]\.external_ids must map str to str$",
    ),
    (
        "initial_source",
        _sub_state_field("initial_source"),
        r"^snapshot\.players\[0\]\.ratings\.doubles\.initial_source must be a str$",
    ),
    (
        "initial_external_player_id",
        _sub_state_field("initial_external_player_id"),
        r"^snapshot\.players\[0\]\.ratings\.doubles\.initial_external_player_id "
        r"must be a str or null$",
    ),
    (
        "initial_evidence_reference",
        _sub_state_field("initial_evidence_reference"),
        r"^snapshot\.players\[0\]\.ratings\.doubles\.initial_evidence_reference "
        r"must be a str or null$",
    ),
]


@pytest.mark.parametrize(
    ("name", "change", "message"), SNAPSHOT_ROWS, ids=[r[0] for r in SNAPSHOT_ROWS]
)
def test_from_snapshot_rejects_a_subclass(
    name: str, change: Callable[[Snap], None], message: str
) -> None:
    snap = _loaded_snapshot()
    change(snap["players"][0])
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(snap)


@pytest.mark.parametrize(
    ("name", "change", "message"), SNAPSHOT_ROWS, ids=[r[0] for r in SNAPSHOT_ROWS]
)
def test_replay_from_snapshot_rejects_a_subclass(
    name: str, change: Callable[[Snap], None], message: str
) -> None:
    snap = _loaded_snapshot()
    change(snap["players"][0])
    with pytest.raises(SnapshotError, match=message):
        replay([], as_of=AS_OF, from_snapshot=snap)


# -- matches and read lookups --------------------------------------------------


def _match(match_id: str = "m1", first: str = "syn-p") -> Match:
    return Match(match_id, (first, "syn-q"), ("syn-r", "syn-s"), ((11, 7),), AS_OF, D)


def test_validate_rejects_a_subclass_match_id() -> None:
    with pytest.raises(InvalidMatchError, match=r"^match_id is required for duplicate protection$"):
        _match(match_id=SubStr("m1")).validate()


def test_validate_rejects_a_subclass_team_id() -> None:
    with pytest.raises(InvalidMatchError, match=r"^Player ids must be non-empty strings$"):
        _match(first=SubStr("syn-p")).validate()


@pytest.mark.parametrize(
    "match",
    [_match(match_id=SubStr("m1")), _match(first=SubStr("syn-p"))],
    ids=["match_id", "team_id"],
)
def test_record_rejects_a_subclass_id_and_changes_nothing(match: Match) -> None:
    engine = _base_engine()
    before = engine.to_snapshot()
    with pytest.raises(InvalidMatchError):
        engine.record(match)
    assert engine.to_snapshot() == before


LOOKUP_ROWS: list[tuple[str, Callable[[RatingEngine], object]]] = [
    ("player", lambda e: e.player(SubStr("syn-base"))),
    ("state", lambda e: e.state(SubStr("syn-base"), D)),
    ("player_summary", lambda e: e.player_summary(SubStr("syn-base"), D, AS_OF)),
    ("seed_provisional_rating", lambda e: e.seed_provisional_rating(SubStr("syn-a"), S)),
]


@pytest.mark.parametrize(("name", "call"), LOOKUP_ROWS, ids=[r[0] for r in LOOKUP_ROWS])
def test_lookups_reject_a_subclass_player_id(
    name: str, call: Callable[[RatingEngine], object]
) -> None:
    engine = _base_engine()
    before = engine.to_snapshot()
    with pytest.raises(PlayerError, match=r"^player_id is required$"):
        call(engine)
    assert engine.to_snapshot() == before


# -- every snapshot the engine writes loads ------------------------------------


def test_engine_built_through_every_entry_point_round_trips() -> None:
    seed = Player(
        "syn-a",
        {D: _imported(initial_evidence_reference="ref-1")},
        external_ids={"ext": "X1", "other": "O1"},
        account_user_id="acct-a",
    )
    matches = [
        Match("m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 7),), date(2026, 10, 10), D),
    ]
    engine = replay(matches, [seed], as_of=date(2026, 10, 15))
    engine.seed_external_rating(
        "syn-e",
        D,
        ExternalRatingSnapshot(
            "ext", 3.9, CAPTURED, external_player_id="X5", evidence_reference="ref-5"
        ),
    )
    engine.attach_external_id("syn-f", "other", "O6")
    engine.link_account("syn-f", "acct-f")
    engine.add_player(Player("syn-g", external_ids={"third": "T7"}, account_user_id="acct-g"))
    engine.record(
        Match("m2", ("syn-e", "syn-f"), ("syn-g", "syn-b"), ((11, 9),), date(2026, 10, 20), D)
    )
    snap = engine.to_snapshot()
    loaded = RatingEngine.from_snapshot(json.loads(json.dumps(snap)))
    assert loaded.to_snapshot() == snap
