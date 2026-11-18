"""Exact types for seed containers, Config.version, and snapshot strs (issue #96 review).

A Player, RatingState, or ExternalRatingSnapshot subclass can return a
different value on a later read, so it can pass the seed checks and then
store something else. A dict subclass can report itself empty while
holding items. A str subclass in a snapshot can lie in comparisons. Each
must be rejected before anything changes. All ids are synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from open_engine import (
    Config,
    ConfigError,
    ExternalRatingSnapshot,
    InvalidSeedError,
    Match,
    Player,
    RatingEngine,
    RatingFormat,
    RatingState,
    SnapshotError,
    __version__,
    prequential_predictions,
    replay,
)

D = RatingFormat.DOUBLES
S = RatingFormat.SINGLES
CAPTURED = date(2026, 10, 1)
AS_OF = date(2026, 11, 1)

Snap = dict[str, Any]


class SubPlayer(Player):
    """A Player subclass that changes nothing."""


class SubState(RatingState):
    """A RatingState subclass that changes nothing."""


class SubSnapshot(ExternalRatingSnapshot):
    """An ExternalRatingSnapshot subclass that changes nothing."""


class SubStr(str):
    """A str subclass that changes nothing."""


class ShiftingId(Player):
    """A Player whose id reads "syn-new" three times, then "syn-victim"."""

    def __getattribute__(self, name: str) -> Any:
        if name == "id":
            reads = object.__getattribute__(self, "__dict__").get("_reads", 0)
            object.__getattribute__(self, "__dict__")["_reads"] = reads + 1
            return "syn-new" if reads < 3 else "syn-victim"
        return object.__getattribute__(self, name)


class EmptyLooking(dict):  # type: ignore[type-arg]
    """A dict subclass that reports no items while holding some."""

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return False


class ItemsLie(dict):  # type: ignore[type-arg]
    """A dict subclass whose items differ from its lookups."""

    def __init__(self, shown: dict[Any, Any], hidden: dict[Any, Any]) -> None:
        super().__init__(shown)
        self._hidden = hidden

    def items(self) -> Any:
        return self._hidden.items()


def _provisional(**changes: Any) -> RatingState:
    state = RatingEngine().seed_provisional_rating("syn-x", D)
    return replace(state, **changes)


def _sub_state() -> RatingState:
    state = _provisional()
    return SubState(**{name: getattr(state, name) for name in state.__dataclass_fields__})


def _with_victim() -> RatingEngine:
    engine = RatingEngine()
    snapshot = ExternalRatingSnapshot("ext", 4.0, CAPTURED, external_player_id="X")
    engine.seed_external_rating("syn-victim", D, snapshot)
    return engine


# -- F1 and F2: seed containers must be exact types ----------------------------

SEED_ROWS: list[tuple[str, Callable[[], Player], str]] = [
    (
        "player_subclass",
        lambda: SubPlayer("syn-a", {D: _provisional()}),
        r"^Seed must be a Player, not SubPlayer$",
    ),
    (
        "player_shifting_id",
        lambda: ShiftingId("syn-new"),
        r"^Seed must be a Player, not ShiftingId$",
    ),
    (
        "state_subclass",
        lambda: Player("syn-a", {D: _sub_state()}),
        r"^Seed for syn-a in doubles: rating state must be a RatingState$",
    ),
    (
        "partners_dict_subclass",
        lambda: Player("syn-a", {D: _provisional(partners=EmptyLooking({"syn-p1": CAPTURED}))}),
        r"^Seed for syn-a in doubles: partners must be a dict$",
    ),
    (
        "opponents_dict_subclass",
        lambda: Player("syn-a", {D: _provisional(opponents=EmptyLooking({"syn-p1": CAPTURED}))}),
        r"^Seed for syn-a in doubles: opponents must be a dict$",
    ),
]

ROW_IDS = [row[0] for row in SEED_ROWS]


@pytest.mark.parametrize(("name", "make", "message"), SEED_ROWS, ids=ROW_IDS)
def test_add_player_rejects_a_container_subclass(
    name: str, make: Callable[[], Player], message: str
) -> None:
    engine = _with_victim()
    before = engine.to_snapshot()
    with pytest.raises(InvalidSeedError, match=message):
        engine.add_player(make())
    assert engine.to_snapshot() == before


@pytest.mark.parametrize(("name", "make", "message"), SEED_ROWS, ids=ROW_IDS)
def test_replay_rejects_a_container_subclass(
    name: str, make: Callable[[], Player], message: str
) -> None:
    with pytest.raises(InvalidSeedError, match=message):
        replay([], [make()], as_of=AS_OF)


@pytest.mark.parametrize("mode", ["live", "scheduled"])
@pytest.mark.parametrize(("name", "make", "message"), SEED_ROWS, ids=ROW_IDS)
def test_prequential_rejects_a_container_subclass(
    name: str, make: Callable[[], Player], message: str, mode: Any
) -> None:
    match = Match("m1", ("syn-p", "syn-q"), ("syn-r", "syn-s"), ((11, 7),), AS_OF, D)
    with pytest.raises(InvalidSeedError, match=message):
        prequential_predictions([match], [make()], mode=mode)


def test_add_player_with_a_shifting_id_leaves_the_victim() -> None:
    engine = _with_victim()
    victim = engine.to_snapshot()["players"]
    with pytest.raises(InvalidSeedError):
        engine.add_player(ShiftingId("syn-new"))
    assert engine.to_snapshot()["players"] == victim


def test_seed_external_rating_rejects_a_snapshot_subclass() -> None:
    engine = _with_victim()
    before = engine.to_snapshot()
    with pytest.raises(InvalidSeedError, match=r"^snapshot must be an ExternalRatingSnapshot$"):
        engine.seed_external_rating("syn-a", D, SubSnapshot("ext2", 4.0, CAPTURED))
    assert engine.to_snapshot() == before


def test_ratings_dict_subclass_with_lying_items_is_rejected() -> None:
    shown = {D: _provisional()}
    hidden = {D: _provisional(rating=99.0)}
    engine = RatingEngine()
    with pytest.raises(InvalidSeedError, match=r"^Seed for syn-a: ratings must be a dict$"):
        engine.add_player(Player("syn-a", ItemsLie(shown, hidden)))
    assert engine.players == {}


# -- F3: Config.version must be exactly a str ---------------------------------


def test_config_rejects_a_str_subclass_version() -> None:
    with pytest.raises(ConfigError, match=r"^version must be a str$"):
        Config(version=SubStr(__version__))


def test_a_plain_config_gives_a_snapshot_that_loads() -> None:
    cfg = Config(version=__version__)
    snap = replay([], [Player("syn-a")], cfg=cfg, as_of=AS_OF).to_snapshot()
    loaded = RatingEngine.from_snapshot(json.loads(json.dumps(snap)), cfg)
    assert loaded.to_snapshot() == snap


# -- F4: snapshot strs must be exact ------------------------------------------


class NeverDifferent(str):
    """A str subclass that never compares as different."""

    def __ne__(self, other: object) -> bool:
        return False

    def __eq__(self, other: object) -> bool:
        return True

    __hash__ = str.__hash__


class FakeHex(str):
    """A str subclass that reports 64 hex characters whatever it holds."""

    def __len__(self) -> int:
        return 64

    def __iter__(self) -> Iterator[str]:
        return iter("a" * 64)


def _m(match_id: str, played_on: date, score: tuple[int, int] = (11, 7)) -> Match:
    return Match(match_id, ("syn-a", "syn-b"), ("syn-c", "syn-d"), (score,), played_on, D)


def _checkpoint() -> Snap:
    engine = replay([_m("m1", date(2026, 10, 5))], as_of=date(2026, 10, 6))
    snap: Snap = json.loads(json.dumps(engine.to_snapshot()))
    return snap


def test_resume_rejects_a_ledger_digest_subclass() -> None:
    snap = _checkpoint()
    snap["ledger_digests"] = {k: NeverDifferent(v) for k, v in snap["ledger_digests"].items()}
    changed = [_m("m1", date(2026, 10, 5), (11, 3)), _m("m2", date(2026, 10, 8))]
    with pytest.raises(SnapshotError, match=r"^snapshot\.ledger_digests\['m1'\] must be 64"):
        replay(changed, as_of=date(2026, 10, 9), from_snapshot=snap)


def test_from_snapshot_rejects_a_content_key_subclass() -> None:
    snap = _checkpoint()
    snap["content_keys"] = {FakeHex("zz"): m for m in snap["content_keys"].values()}
    with pytest.raises(
        SnapshotError, match=r"^snapshot\.content_keys has a key that is not 64 lowercase hex"
    ):
        RatingEngine.from_snapshot(snap)


def test_from_snapshot_rejects_a_format_key_subclass() -> None:
    snap = _checkpoint()
    player = snap["players"][0]
    player["ratings"] = {SubStr(k): v for k, v in player["ratings"].items()}
    with pytest.raises(SnapshotError, match=r"\.ratings key 'doubles' is not a RatingFormat$"):
        RatingEngine.from_snapshot(snap)


def test_from_snapshot_rejects_a_date_subclass() -> None:
    snap = _checkpoint()
    snap["calculated_as_of"] = SubStr(snap["calculated_as_of"])
    with pytest.raises(
        SnapshotError, match=r"^snapshot\.calculated_as_of must be a date in YYYY-MM-DD form$"
    ):
        RatingEngine.from_snapshot(snap)


# -- F6: seed_provisional_rating checks rating_format -------------------------


class FakeFormat:
    """A hashable object shaped like a RatingFormat, without being one."""

    parent = None
    value = "fake"


@pytest.mark.parametrize("rating_format", ["singles", FakeFormat()], ids=["str", "object"])
def test_seed_provisional_rating_rejects_a_non_format(rating_format: object) -> None:
    engine = RatingEngine()
    with pytest.raises(InvalidSeedError, match=r"^rating_format must be a RatingFormat$"):
        engine.seed_provisional_rating("syn-a", rating_format)  # type: ignore[arg-type]
    assert engine.players == {}
