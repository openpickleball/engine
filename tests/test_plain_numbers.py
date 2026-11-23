"""
Config and seed numbers are stored as plain int or float (issue #107).

An int or float subclass can override its operators, so checks, the
fingerprint, and rating code could each see a different value. The engine
stores the plain value of the subclass's own value, so all three agree.
All ids are synthetic.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
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
    MatchType,
    Player,
    RatingEngine,
    RatingFormat,
    RatingState,
    ScoringType,
    SnapshotError,
    prequential_predictions,
    replay,
)
from open_engine._config import (
    _CONFIG_MAPPING_FIELDS,
    _CONFIG_NUMBER_FIELDS,
    _CONFIG_OPTIONAL_NUMBER_FIELDS,
    _CONFIG_WHOLE_FIELDS,
)

D = RatingFormat.DOUBLES
AS_OF = date(2026, 11, 1)
CAPTURED = date(2026, 10, 1)
PLAIN_FINGERPRINT = "30dc01fa90e94c97"
LEDGER = [Match("m1", ("syn-p", "syn-q"), ("syn-r", "syn-s"), ((11, 7),), date(2026, 10, 5), D)]


class FloatSub(float):
    """An honest float subclass."""


class IntSub(int):
    """An honest int subclass."""


class Big(int):
    """An int whose multiplication lies."""

    def __mul__(self, other: object) -> int:
        return 10**6

    __rmul__ = __mul__


class AlwaysTrue(float):
    """A float whose order comparisons all return True."""

    def __lt__(self, other: object) -> bool:
        return True

    __le__ = __gt__ = __ge__ = __lt__


class NeverTrue(float):
    """A float whose order comparisons all return False."""

    def __lt__(self, other: object) -> bool:
        return False

    __le__ = __gt__ = __ge__ = __lt__


class InRange(float):
    """A float that claims to be within every bound."""

    def __ge__(self, other: object) -> bool:
        return True

    def __le__(self, other: object) -> bool:
        return True


class Zero(int):
    """An int that claims to equal 0."""

    def __ne__(self, other: object) -> bool:
        return False

    def __eq__(self, other: object) -> bool:
        return True

    __hash__ = int.__hash__


class Arith(float):
    """A float whose arithmetic lies."""

    def __sub__(self, other: object) -> float:
        return 1e6

    __rsub__ = __mul__ = __rmul__ = __add__ = __radd__ = __sub__


class TwistedPair(tuple):  # type: ignore[type-arg]
    """A tuple whose iteration yields a different pair than its items."""

    def __iter__(self) -> Iterator[Any]:
        return iter((ScoringType.SIDE_OUT, 15))


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_issue_repro_gives_equal_deltas_and_fingerprints() -> None:
    weights = dict.fromkeys(MatchType, 1.0)
    lying = Config(match_weights={**weights, MatchType.TOURNAMENT: Big(1)})
    plain = Config(match_weights={**weights, MatchType.TOURNAMENT: 1})
    match = Match(
        "m1",
        ("syn-a", "syn-b"),
        ("syn-c", "syn-d"),
        ((11, 5),),
        date(2026, 1, 1),
        D,
        match_type=MatchType.TOURNAMENT,
    )
    assert lying.fingerprint() == plain.fingerprint()
    assert RatingEngine(lying).record(match).deltas == RatingEngine(plain).record(match).deltas


OPTIONAL_VALUES = {
    "connectivity_reliability_cap": 50.0,
    "max_singles_rating_gap": 1.0,
    "max_doubles_partner_gap": 1.0,
}


@pytest.mark.parametrize(
    "name", _CONFIG_NUMBER_FIELDS + _CONFIG_WHOLE_FIELDS + _CONFIG_OPTIONAL_NUMBER_FIELDS
)
def test_number_field_subclass_is_stored_plain(name: str) -> None:
    value = getattr(Config(), name)
    if value is None:
        value = OPTIONAL_VALUES[name]
    plain_type = type(value)
    sub = IntSub(value) if plain_type is int else FloatSub(value)
    cfg = Config(**{name: sub})
    assert type(getattr(cfg, name)) is plain_type
    assert getattr(cfg, name) == value
    assert cfg.fingerprint() == Config(**{name: value}).fingerprint()


@pytest.mark.parametrize("name", _CONFIG_MAPPING_FIELDS)
def test_mapping_value_subclass_is_stored_plain(name: str) -> None:
    original = dict(getattr(Config(), name))
    changed = {
        key: IntSub(item) if type(item) is int else FloatSub(item) for key, item in original.items()
    }
    cfg = Config(**{name: changed})
    stored = getattr(cfg, name)
    assert all(type(stored[key]) is type(item) for key, item in original.items())
    assert cfg.fingerprint() == Config().fingerprint()


def test_game_length_keys_and_format_points_are_stored_plain() -> None:
    weights = {IntSub(11): 1.0, IntSub(15): 1.1, IntSub(21): 1.2}
    formats = frozenset((ScoringType.RALLY, IntSub(points)) for points in (11, 15))
    cfg = Config(game_length_weights=weights, allowed_game_formats=formats)
    assert all(type(key) is int for key in cfg.game_length_weights)
    assert all(type(points) is int for _, points in cfg.allowed_game_formats)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"win_weight": AlwaysTrue(5.0)}, r"^win_weight must be between 0 and 1$"),
        ({"k_max": AlwaysTrue(-1.0)}, r"^K-factor bounds are invalid$"),
        (
            {"match_weights": {**dict.fromkeys(MatchType, 1.0), MatchType.LEAGUE: NeverTrue(-1.0)}},
            r"^match_weights values must be positive$",
        ),
        (
            {"scale_by_scoring": dict.fromkeys(ScoringType, NeverTrue(-1.0))},
            r"^scales must be positive$",
        ),
    ],
    ids=["win_weight", "k_max", "match_weights", "scale_by_scoring"],
)
def test_lying_comparisons_cannot_pass_a_range_check(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        Config(**changes)  # type: ignore[arg-type]


def test_allowed_game_format_stores_the_pair_its_check_read() -> None:
    entry = TwistedPair((ScoringType.RALLY, 11))
    cfg = Config(allowed_game_formats=frozenset({entry}))
    assert cfg.allowed_game_formats == frozenset({(ScoringType.RALLY, 11)})


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"win_weight": True}, r"^win_weight must be a finite number$"),
        (
            {"match_weights": {**dict.fromkeys(MatchType, 1.0), MatchType.LEAGUE: True}},
            r"^match_weights values must be finite numbers$",
        ),
    ],
    ids=["number_field", "mapping_value"],
)
def test_bool_is_still_rejected(changes: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Config(**changes)  # type: ignore[arg-type]


def test_plain_fingerprint_is_unchanged() -> None:
    assert Config().fingerprint() == PLAIN_FINGERPRINT


def test_instance_dataclass_fields_does_not_change_the_fingerprint() -> None:
    cfg = Config()
    object.__setattr__(cfg, "__dataclass_fields__", {})
    assert cfg.fingerprint() == PLAIN_FINGERPRINT


@dataclass(frozen=True, eq=False)
class _ExtendedConfig(Config):
    extra: int = 7


def test_subclass_fields_do_not_change_the_fingerprint() -> None:
    assert _ExtendedConfig().fingerprint() == PLAIN_FINGERPRINT


# ---------------------------------------------------------------------------
# Seeds
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


@pytest.mark.parametrize(
    "entry", [row[1] for row in ENTRY_POINTS], ids=[row[0] for row in ENTRY_POINTS]
)
def test_lying_in_range_seed_rating_is_rejected(entry: Callable[[Player], None]) -> None:
    state = replace(_provisional(), rating=InRange(99.0), initial_rating=InRange(99.0))
    with pytest.raises(
        InvalidSeedError,
        match=r"^Seed for syn-a in doubles: rating must be between min_rating and max_rating$",
    ):
        entry(Player("syn-a", {D: state}))


@pytest.mark.parametrize(
    "entry", [row[1] for row in ENTRY_POINTS], ids=[row[0] for row in ENTRY_POINTS]
)
def test_lying_zero_count_is_rejected(entry: Callable[[Player], None]) -> None:
    state = replace(_provisional(), matches_played=Zero(3))
    with pytest.raises(
        InvalidSeedError, match=r"^Seed for syn-a in doubles: matches_played must be 0$"
    ):
        entry(Player("syn-a", {D: state}))


SEED_IDS = ("syn-a", "syn-b", "syn-c", "syn-d")
MATCH = Match("m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 6),), date(2026, 10, 5), D)


def _subclass_state() -> RatingState:
    state = _provisional()
    return replace(
        state,
        rating=Arith(4.0),
        rd=Arith(state.rd),
        initial_rating=Arith(4.0),
        matches_played=IntSub(0),
        verified_matches_played=IntSub(0),
        live_updates_since_replay=IntSub(0),
        inherited_live_updates=IntSub(0),
    )


def _plain_state() -> RatingState:
    return replace(_provisional(), rating=4.0, initial_rating=4.0)


NUMBER_FIELDS = (
    "rating",
    "rd",
    "initial_rating",
    "matches_played",
    "verified_matches_played",
    "live_updates_since_replay",
    "inherited_live_updates",
)


def _assert_plain(state: RatingState) -> None:
    for name in NUMBER_FIELDS:
        assert type(getattr(state, name)) in (int, float), name


def test_add_player_stores_plain_numbers_and_keeps_the_callers_state() -> None:
    lying_states = {pid: _subclass_state() for pid in SEED_IDS}
    lying = RatingEngine()
    plain = RatingEngine()
    for pid in SEED_IDS:
        lying.add_player(Player(pid, {D: lying_states[pid]}))
        plain.add_player(Player(pid, {D: _plain_state()}))
    for pid in SEED_IDS:
        _assert_plain(lying.state(pid, D))
        assert type(lying_states[pid].rating) is Arith
        assert type(lying_states[pid].matches_played) is IntSub
    lying.record(MATCH)
    plain.record(MATCH)
    assert {pid: lying.state(pid, D).rating for pid in SEED_IDS} == {
        pid: plain.state(pid, D).rating for pid in SEED_IDS
    }


def test_replay_stores_plain_numbers() -> None:
    lying = replay([MATCH], [Player(pid, {D: _subclass_state()}) for pid in SEED_IDS], as_of=AS_OF)
    plain = replay([MATCH], [Player(pid, {D: _plain_state()}) for pid in SEED_IDS], as_of=AS_OF)
    for pid in SEED_IDS:
        _assert_plain(lying.state(pid, D))
    assert {pid: lying.state(pid, D).rating for pid in SEED_IDS} == {
        pid: plain.state(pid, D).rating for pid in SEED_IDS
    }


def test_seed_external_rating_rejects_a_lying_rating() -> None:
    snap = ExternalRatingSnapshot("external_standard", InRange(99.0), CAPTURED)
    with pytest.raises(
        InvalidSeedError, match=r"^External rating is outside the configured scale$"
    ):
        RatingEngine().seed_external_rating("syn-a", D, snap)


def test_seed_external_rating_rejects_a_lying_reliability() -> None:
    snap = ExternalRatingSnapshot("external_standard", 4.0, CAPTURED, InRange(150.0))
    with pytest.raises(InvalidSeedError, match=r"^External reliability must be between 0 and 100$"):
        RatingEngine().seed_external_rating("syn-a", D, snap)


def test_seed_external_rating_stores_plain_numbers() -> None:
    snap = ExternalRatingSnapshot("external_standard", FloatSub(4.0), CAPTURED, FloatSub(60.0))
    state = RatingEngine().seed_external_rating("syn-a", D, snap)
    assert type(state.rating) is float
    assert type(state.initial_rating) is float
    assert type(state.initial_source_reliability) is float
    assert type(snap.rating) is FloatSub
    assert type(snap.reliability) is FloatSub


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def _snapshot() -> dict[str, Any]:
    eng = RatingEngine()
    snap = ExternalRatingSnapshot("external_standard", 4.0, CAPTURED, 60.0)
    eng.seed_external_rating("syn-p", D, snap)
    eng.record(LEDGER[0])
    return eng.to_snapshot()


def _doubles_state(snap: dict[str, Any], pid: str) -> dict[str, Any]:
    player = next(p for p in snap["players"] if p["id"] == pid)
    state: dict[str, Any] = player["ratings"][D.value]
    return state


def test_from_snapshot_stores_plain_numbers() -> None:
    snap = _snapshot()
    state = _doubles_state(snap, "syn-p")
    for name in ("rating", "rd", "initial_rating", "initial_source_reliability"):
        state[name] = FloatSub(state[name])
    for name in (
        "matches_played",
        "verified_matches_played",
        "live_updates_since_replay",
        "inherited_live_updates",
    ):
        state[name] = IntSub(state[name])
    loaded = RatingEngine.from_snapshot(snap).state("syn-p", D)
    _assert_plain(loaded)
    assert type(loaded.initial_source_reliability) is float


def test_from_snapshot_rejects_a_lying_rating() -> None:
    snap = _snapshot()
    _doubles_state(snap, "syn-p")["rating"] = InRange(99.0)
    with pytest.raises(SnapshotError, match=r"\.rating must be between min_rating and max_rating$"):
        RatingEngine.from_snapshot(snap)
