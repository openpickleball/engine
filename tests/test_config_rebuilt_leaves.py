"""The Config rebuild reads every leaf by its real type, before Config(...) (issue #122).

A rewritten Config can hold a hostile object anywhere: a scalar field, a
mapping key or value, a game length key, or an allowed_game_formats entry.
The rebuild must turn each leaf into a plain value by its real type alone,
so no caller code runs while the engine's copy is built, and anything else
raises ConfigError. All ids are synthetic.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from datetime import date

import pytest

import open_engine._config as config_module
from open_engine import (
    Config,
    ConfigError,
    Match,
    MatchType,
    RatingEngine,
    RatingFormat,
    RatingState,
    ScoringType,
    reliability,
    replay,
)

D = RatingFormat.DOUBLES
RALLY = ScoringType.RALLY
AS_OF = date(2026, 1, 2)
MATCH = Match(
    "syn-m1",
    ("syn-a", "syn-b"),
    ("syn-c", "syn-d"),
    ((11, 5),),
    date(2026, 1, 1),
    D,
    match_type=MatchType.TOURNAMENT,
)

# Caller code that runs on a hostile object records its name here.
CALLS: list[str] = []


@pytest.fixture(autouse=True)
def _clear_calls() -> Iterator[None]:
    CALLS.clear()
    yield
    CALLS.clear()


class Hostile:
    """Claims to be a float through __class__, and records every protocol call."""

    @property  # type: ignore[misc]
    def __class__(self) -> type:
        CALLS.append("__class__")
        return float

    def __hash__(self) -> int:
        CALLS.append("__hash__")
        return hash("rally")

    def __eq__(self, other: object) -> bool:
        CALLS.append("__eq__")
        return True

    def __iter__(self) -> Iterator[object]:
        CALLS.append("__iter__")
        return iter(())

    def __repr__(self) -> str:
        CALLS.append("__repr__")
        return "Hostile()"


class SpyInt(int):
    """A real int subclass whose own methods record that they ran."""

    def __hash__(self) -> int:
        CALLS.append("__hash__")
        return int.__hash__(self)

    def __eq__(self, other: object) -> bool:
        CALLS.append("__eq__")
        return int.__eq__(self, other)

    def __repr__(self) -> str:
        CALLS.append("__repr__")
        return int.__repr__(self)

    def __mul__(self, other: object) -> int:
        CALLS.append("__mul__")
        return 10**6

    __rmul__ = __mul__


class LyingPair(tuple[object, ...]):
    """A tuple subclass whose own methods lie and record that they ran."""

    def __iter__(self) -> Iterator[object]:
        CALLS.append("__iter__")
        return iter(("rally", 99))

    def __len__(self) -> int:
        CALLS.append("__len__")
        return 2

    def __getitem__(self, index: object) -> object:
        CALLS.append("__getitem__")
        return 99

    def __hash__(self) -> int:
        CALLS.append("__hash__")
        return tuple.__hash__(self)

    def __eq__(self, other: object) -> bool:
        CALLS.append("__eq__")
        return tuple.__eq__(self, other)


def _tamper(name: str, value: object) -> Config:
    cfg = Config()
    object.__setattr__(cfg, name, value)
    return cfg


def _weights_with(key: object, value: object) -> Config:
    weights: dict[object, object] = dict(Config().match_weights)
    weights[key] = value
    return _tamper("match_weights", weights)


def _hostile_weight_key() -> Config:
    weights: dict[object, object] = dict(Config().match_weights)
    del weights[MatchType.TOURNAMENT]
    weights[Hostile()] = 1.0
    return _tamper("match_weights", weights)


def _hostile_length_key() -> Config:
    lengths: dict[object, object] = dict(Config().game_length_weights)
    del lengths[11]
    lengths[Hostile()] = 1.0
    return _tamper("game_length_weights", lengths)


def _formats_with(entry: object) -> Config:
    formats: set[object] = set(Config().allowed_game_formats)
    formats.discard((RALLY, 11))
    formats.add(entry)
    return _tamper("allowed_game_formats", formats)


def _engine(cfg: Config) -> object:
    return RatingEngine(cfg)


def _replay(cfg: Config) -> object:
    return replay([MATCH], cfg=cfg, as_of=AS_OF)


def _reliability(cfg: Config) -> object:
    return reliability(RatingState(rating=3.5, rd=0.5), cfg, AS_OF)


ENTRY: list[tuple[str, Callable[[Config], object]]] = [
    ("RatingEngine", _engine),
    ("replay", _replay),
    ("reliability", _reliability),
]

HOSTILE: list[tuple[str, Callable[[], Config], str]] = [
    ("scalar", lambda: _tamper("k_max", Hostile()), "k_max"),
    ("optional scalar", lambda: _tamper("max_singles_rating_gap", Hostile()), "max_singles"),
    ("mapping key", _hostile_weight_key, "match_weights"),
    ("mapping value", lambda: _weights_with(MatchType.TOURNAMENT, Hostile()), "match_weights"),
    ("game length key", _hostile_length_key, "game_length_weights"),
    ("format entry", lambda: _formats_with(Hostile()), "allowed_game_formats"),
    ("format scoring", lambda: _formats_with((Hostile(), 11)), "allowed_game_formats"),
    ("format points", lambda: _formats_with((RALLY, Hostile())), "allowed_game_formats"),
]


@pytest.mark.parametrize(("point"), [p for _, p in ENTRY], ids=[n for n, _ in ENTRY])
@pytest.mark.parametrize(
    ("make", "message"), [(m, msg) for _, m, msg in HOSTILE], ids=[i for i, _, _ in HOSTILE]
)
def test_hostile_leaf_raises_without_running_its_code(
    point: Callable[[Config], object], make: Callable[[], Config], message: str
) -> None:
    cfg = make()
    CALLS.clear()
    with pytest.raises(ConfigError, match=message):
        point(cfg)
    assert CALLS == []


@pytest.mark.parametrize(("point"), [p for _, p in ENTRY], ids=[n for n, _ in ENTRY])
def test_int_subclass_leaves_become_plain_without_running_their_code(
    point: Callable[[Config], object],
) -> None:
    weights: dict[object, object] = dict(Config().match_weights)
    weights[MatchType.TOURNAMENT] = SpyInt(1)
    lengths: dict[object, object] = dict(Config().game_length_weights)
    del lengths[11]
    lengths[SpyInt(11)] = SpyInt(1)
    cfg = _tamper("match_weights", weights)
    object.__setattr__(cfg, "game_length_weights", lengths)
    object.__setattr__(cfg, "diversity_target", SpyInt(12))
    CALLS.clear()
    point(cfg)
    assert CALLS == []


def test_int_subclass_leaves_rate_like_their_plain_values() -> None:
    weights: dict[object, object] = dict(Config().match_weights)
    weights[MatchType.TOURNAMENT] = SpyInt(1)
    cfg = _tamper("match_weights", weights)
    plain = Config(match_weights={**Config().match_weights, MatchType.TOURNAMENT: 1})
    CALLS.clear()
    engine = RatingEngine(cfg)
    assert engine.cfg == plain
    assert engine.record(MATCH).deltas == RatingEngine(plain).record(MATCH).deltas
    assert CALLS == []


@pytest.mark.parametrize(("point"), [p for _, p in ENTRY], ids=[n for n, _ in ENTRY])
def test_a_lying_tuple_entry_is_read_through_tuple_itself(
    point: Callable[[Config], object],
) -> None:
    cfg = _formats_with(LyingPair((RALLY, 11)))
    CALLS.clear()
    point(cfg)
    assert CALLS == []


def test_a_lying_tuple_entry_gives_its_real_values() -> None:
    cfg = _formats_with(LyingPair((RALLY, 11)))
    CALLS.clear()
    assert RatingEngine(cfg).cfg == Config()
    assert CALLS == []


class Flaky:
    """A scoring entry equal to "rally" for the first comparison only."""

    n = 0

    def __hash__(self) -> int:
        CALLS.append("__hash__")
        return hash("rally")

    def __eq__(self, other: object) -> bool:
        CALLS.append("__eq__")
        Flaky.n += 1
        return Flaky.n == 1


@pytest.mark.parametrize(("point"), [p for _, p in ENTRY], ids=[n for n, _ in ENTRY])
def test_flaky_scoring_raises_config_error(point: Callable[[Config], object]) -> None:
    cfg = _formats_with((Flaky(), 11))
    CALLS.clear()
    Flaky.n = 0
    with pytest.raises(ConfigError, match="allowed_game_formats"):
        point(cfg)
    assert CALLS == []


HOOKED: list[str] = []


class FrameWalker:
    """A scoring entry whose __hash__ finds the Config being built and rewrites it."""

    def __hash__(self) -> int:
        HOOKED.append("__hash__")
        frame = sys._getframe(1)
        while frame is not None and type(frame.f_locals.get("self")) is not Config:
            frame = frame.f_back
        if frame is not None:
            built = frame.f_locals["self"]
            object.__setattr__(built, "k_max", 5.0)
        return hash("rally")

    def __eq__(self, other: object) -> bool:
        HOOKED.append("__eq__")
        return other == "rally"


@pytest.mark.parametrize(("point"), [p for _, p in ENTRY], ids=[n for n, _ in ENTRY])
def test_frame_walking_entry_raises_and_never_runs(point: Callable[[Config], object]) -> None:
    formats: set[object] = set(Config().allowed_game_formats)
    formats.discard((RALLY, 11))
    formats.add((FrameWalker(), 11))
    cfg = _tamper("allowed_game_formats", formats)
    HOOKED.clear()
    with pytest.raises(ConfigError, match="allowed_game_formats"):
        point(cfg)
    assert HOOKED == []


def test_player_summary_does_not_rebuild_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = RatingEngine(Config(k_max=0.5))
    engine.record(MATCH)
    before = engine.player_summary("syn-a", D, AS_OF)
    seen: list[Config] = []
    original = config_module._rebuilt_config

    def counted(cfg: Config) -> Config:
        seen.append(cfg)
        return original(cfg)

    monkeypatch.setattr(config_module, "_rebuilt_config", counted)
    assert engine.player_summary("syn-a", D, AS_OF) == before
    assert seen == []
