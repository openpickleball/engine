"""
Review follow-ups for plain Config and seed numbers (issue #107).

Covers mapping keys read once, Config subclasses in from_dict, values that
spoof their class, instance __dataclass_fields__ on seeds, and plain
storage gaps. All ids are synthetic.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
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
    ScoringType,
    SnapshotError,
    prequential_predictions,
    replay,
)

D = RatingFormat.DOUBLES
AS_OF = date(2026, 11, 1)
CAPTURED = date(2026, 10, 1)
LEDGER = [Match("m1", ("syn-p", "syn-q"), ("syn-r", "syn-s"), ((11, 7),), date(2026, 10, 5), D)]


class PairsMapping(Mapping[Any, Any]):
    """A Mapping whose items() yields the given pairs, repeats included."""

    def __init__(self, pairs: list[tuple[Any, Any]]) -> None:
        self._pairs = pairs

    def __getitem__(self, key: Any) -> Any:
        for k, v in self._pairs:
            if k is key:
                return v
        raise KeyError(key)

    def __iter__(self) -> Iterator[Any]:
        return iter([k for k, _ in self._pairs])

    def __len__(self) -> int:
        return len(self._pairs)

    def items(self) -> Any:
        return list(self._pairs)


class Colliding(int):
    """An int that equals everything and always hashes to 0."""

    def __eq__(self, other: object) -> bool:
        return True

    def __hash__(self) -> int:
        return 0


class Unhashable(int):
    """An int subclass with no hash."""

    __hash__ = None  # type: ignore[assignment]


class FloatSub(float):
    """An honest float subclass."""


class IntSub(int):
    """An honest int subclass."""


class FakeInt(float):
    """A real float subclass whose __class__ claims int."""

    @property  # type: ignore[misc]
    def __class__(self) -> type:
        return int


class ClaimFloat:
    """Not a number, but its __class__ claims float."""

    @property  # type: ignore[misc]
    def __class__(self) -> type:
        return float


class ClaimInt:
    """Not a number, but its __class__ claims int."""

    @property  # type: ignore[misc]
    def __class__(self) -> type:
        return int


class ClaimTuple:
    """Not a tuple, but its __class__ claims tuple."""

    @property  # type: ignore[misc]
    def __class__(self) -> type:
        return tuple


# ---------------------------------------------------------------------------
# A: game_length_weights keys
# ---------------------------------------------------------------------------


def test_colliding_keys_are_kept_by_their_own_values() -> None:
    weights = PairsMapping([(Colliding(11), 1.0), (Colliding(15), 1.1), (21, 1.2)])
    cfg = Config(game_length_weights=weights)
    assert dict(cfg.game_length_weights) == {11: 1.0, 15: 1.1, 21: 1.2}


def test_repeated_items_raise() -> None:
    weights = PairsMapping([(11, 1.0), (11, 1.0), (15, 1.1), (21, 1.2)])
    with pytest.raises(ConfigError, match=r"^game_length_weights repeats the game length 11$"):
        Config(game_length_weights=weights)


def test_unhashable_int_subclass_key_is_stored_plain() -> None:
    weights = PairsMapping([(Unhashable(11), 1.0), (15, 1.1), (21, 1.2)])
    cfg = Config(game_length_weights=weights)
    assert dict(cfg.game_length_weights) == {11: 1.0, 15: 1.1, 21: 1.2}
    assert all(type(key) is int for key in cfg.game_length_weights)


def test_unhashable_key_raises_config_error() -> None:
    weights = PairsMapping([([1], 1.0), (11, 1.0), (15, 1.1), (21, 1.2)])
    with pytest.raises(ConfigError, match=r"^game_length_weights keys must be whole numbers$"):
        Config(game_length_weights=weights)


# ---------------------------------------------------------------------------
# B: from_dict on a Config subclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class _ExtendedConfig(Config):
    extra: int = 7


def test_subclass_from_dict_round_trip() -> None:
    built = _ExtendedConfig.from_dict(_ExtendedConfig().to_dict())
    assert built.fingerprint() == Config().fingerprint()


# ---------------------------------------------------------------------------
# C: values that spoof their class
# ---------------------------------------------------------------------------


def _provisional() -> RatingState:
    return RatingEngine().seed_provisional_rating("syn-x", D)


def _add_player(seed: Player) -> None:
    RatingEngine().add_player(seed)


def _replay(seed: Player) -> None:
    replay([], [seed], as_of=AS_OF)


def _live(seed: Player) -> None:
    prequential_predictions([], [seed], mode="live")


def _scheduled(seed: Player) -> None:
    prequential_predictions([], [seed], mode="scheduled")


def _resume(seed: Player) -> None:
    snap = replay(LEDGER, as_of=AS_OF).to_snapshot()
    replay(LEDGER, [seed], as_of=AS_OF, from_snapshot=snap)


ENTRY_POINTS: list[tuple[str, Callable[[Player], None]]] = [
    ("add_player", _add_player),
    ("replay", _replay),
    ("prequential_live", _live),
    ("prequential_scheduled", _scheduled),
    ("resume", _resume),
]
ENTRY_IDS = [row[0] for row in ENTRY_POINTS]
ENTRIES = [row[1] for row in ENTRY_POINTS]


@pytest.mark.parametrize("entry", ENTRIES, ids=ENTRY_IDS)
def test_seed_rating_claiming_float_is_rejected(entry: Callable[[Player], None]) -> None:
    state = replace(_provisional(), rating=ClaimFloat())
    with pytest.raises(
        InvalidSeedError, match=r"^Seed for syn-a in doubles: rating must be a finite number$"
    ):
        entry(Player("syn-a", {D: state}))


@pytest.mark.parametrize("entry", ENTRIES, ids=ENTRY_IDS)
def test_seed_count_claiming_int_is_rejected(entry: Callable[[Player], None]) -> None:
    state = replace(_provisional(), matches_played=ClaimInt())
    with pytest.raises(
        InvalidSeedError, match=r"^Seed for syn-a in doubles: matches_played must be an int$"
    ):
        entry(Player("syn-a", {D: state}))


def test_seed_float_subclass_claiming_int_is_stored_as_its_float() -> None:
    state = replace(_provisional(), rating=FakeInt(4.0), initial_rating=FakeInt(4.0))
    eng = RatingEngine()
    eng.add_player(Player("syn-a", {D: state}))
    stored = eng.state("syn-a", D)
    assert type(stored.rating) is float
    assert stored.rating == 4.0


def test_external_rating_claiming_float_is_rejected() -> None:
    snap = ExternalRatingSnapshot("external_standard", ClaimFloat(), CAPTURED)  # type: ignore[arg-type]
    with pytest.raises(InvalidSeedError, match=r"^External rating must be a finite number$"):
        RatingEngine().seed_external_rating("syn-a", D, snap)


def test_external_float_subclass_claiming_int_is_stored_as_its_float() -> None:
    snap = ExternalRatingSnapshot("external_standard", FakeInt(4.0), CAPTURED)
    state = RatingEngine().seed_external_rating("syn-a", D, snap)
    assert type(state.rating) is float
    assert state.rating == 4.0


def _live_snapshot() -> dict[str, Any]:
    eng = RatingEngine()
    eng.record(LEDGER[0])
    return eng.to_snapshot()


def _state_of(snap: dict[str, Any], pid: str) -> dict[str, Any]:
    player = next(p for p in snap["players"] if p["id"] == pid)
    state: dict[str, Any] = player["ratings"][D.value]
    return state


def test_snapshot_rating_claiming_float_raises_snapshot_error() -> None:
    snap = _live_snapshot()
    _state_of(snap, "syn-p")["rating"] = ClaimFloat()
    with pytest.raises(SnapshotError, match=r"\.rating must be a finite number"):
        RatingEngine.from_snapshot(snap)


def test_snapshot_count_claiming_int_raises_snapshot_error() -> None:
    snap = _live_snapshot()
    _state_of(snap, "syn-p")["matches_played"] = ClaimInt()
    with pytest.raises(SnapshotError, match=r"\.matches_played must be a nonnegative int"):
        RatingEngine.from_snapshot(snap)


def test_config_format_entry_claiming_tuple_raises_config_error() -> None:
    formats = frozenset({ClaimTuple(), (ScoringType.RALLY, 11)})
    with pytest.raises(
        ConfigError,
        match=r"^allowed_game_formats entries must be \(scoring type, whole number\) pairs$",
    ):
        Config(allowed_game_formats=formats)  # type: ignore[arg-type]


def test_config_number_claiming_float_raises_config_error() -> None:
    with pytest.raises(ConfigError, match=r"^win_weight must be a finite number$"):
        Config(win_weight=ClaimFloat())  # type: ignore[arg-type]


def test_config_float_subclass_claiming_int_is_stored_as_its_float() -> None:
    cfg = Config(win_weight=FakeInt(0.5))
    assert type(cfg.win_weight) is float
    assert cfg.fingerprint() == Config(win_weight=0.5).fingerprint()


# ---------------------------------------------------------------------------
# D: instance __dataclass_fields__ on seeds
# ---------------------------------------------------------------------------


def test_player_with_instance_dataclass_fields_seeds_every_field() -> None:
    player = Player("syn-a", {D: _provisional()}, external_ids={"src": "syn-ext"})
    player.__dataclass_fields__ = {}  # type: ignore[misc]
    eng = RatingEngine()
    eng.add_player(player)
    stored = eng.player("syn-a")
    assert stored.external_ids == {"src": "syn-ext"}
    assert D in stored.ratings


def test_external_snapshot_with_instance_dataclass_fields_seeds_every_field() -> None:
    snap = ExternalRatingSnapshot("external_standard", 4.0, CAPTURED, 60.0, "syn-ext", "syn-ref")
    object.__setattr__(snap, "__dataclass_fields__", {})
    state = RatingEngine().seed_external_rating("syn-a", D, snap)
    assert state.rating == 4.0
    assert state.initial_source == "external_standard"
    assert state.initial_source_reliability == 60.0
    assert state.initial_external_player_id == "syn-ext"
    assert state.initial_evidence_reference == "syn-ref"


def test_state_with_instance_dataclass_fields_keeps_every_field() -> None:
    state = replace(_provisional(), initial_source="syn-src")
    state.__dataclass_fields__ = {}  # type: ignore[misc]
    eng = RatingEngine()
    eng.add_player(Player("syn-a", {D: state}))
    stored = eng.state("syn-a", D)
    assert stored.initial_source == "syn-src"
    assert stored.rating == state.rating


# ---------------------------------------------------------------------------
# F: storage gaps
# ---------------------------------------------------------------------------


def _imported_subclass_state() -> RatingState:
    snap = ExternalRatingSnapshot("external_standard", 4.0, CAPTURED, 60.0)
    state = RatingEngine().seed_external_rating("syn-x", D, snap)
    return replace(state, initial_source_reliability=FloatSub(60.0))


def test_add_player_stores_plain_imported_reliability() -> None:
    eng = RatingEngine()
    eng.add_player(Player("syn-a", {D: _imported_subclass_state()}))
    assert type(eng.state("syn-a", D).initial_source_reliability) is float


def test_replay_stores_plain_imported_reliability() -> None:
    eng = replay([], [Player("syn-a", {D: _imported_subclass_state()})], as_of=AS_OF)
    assert type(eng.state("syn-a", D).initial_source_reliability) is float


def test_replay_leaves_the_callers_seed_unchanged() -> None:
    state = replace(_provisional(), rating=FloatSub(4.0), matches_played=IntSub(0))
    ratings = {D: state}
    player = Player("syn-a", ratings)
    replay([], [player], as_of=AS_OF)
    assert player.ratings is ratings
    assert list(ratings) == [D]
    assert ratings[D] is state
    assert type(state.rating) is FloatSub
    assert type(state.matches_played) is IntSub


def test_snapshot_replay_counts_load_plain() -> None:
    snap = replay(LEDGER, as_of=AS_OF).to_snapshot()
    info = snap["replay_info"]
    info["matches_considered"] = IntSub(info["matches_considered"])
    info["matches_counted"] = IntSub(info["matches_counted"])
    loaded = RatingEngine.from_snapshot(snap)
    assert loaded.replay_info is not None
    assert type(loaded.replay_info.matches_considered) is int
    assert type(loaded.replay_info.matches_counted) is int
