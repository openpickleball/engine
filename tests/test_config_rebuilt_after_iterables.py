"""The Config copy is built after the caller's iterables are drained (issue #122).

Draining matches and seed_players runs caller code. A generator can walk
the frame stack and rewrite any exact Config it finds there, so the copy
must not exist until the last caller code has run. A rewrite of the
caller's own Config while it is drained is rebuilt, and so checked, like
any other rewrite. All ids are synthetic.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict
from datetime import date

import pytest

from open_engine import (
    Config,
    ConfigError,
    Match,
    MatchType,
    Player,
    ScoringType,
    evaluate,
    fit_scale,
    fit_win_calibration,
    prequential_predictions,
    replay,
)
from test_exact_input_plain_unchanged import _ledger, _seeds

AS_OF = date(2026, 6, 1)
MID = date(2026, 3, 15)


def _rewrite_configs(name: str, value: object, *, caller: Config, own: bool) -> None:
    """Rewrite each exact Config in a calling frame: the caller's (own) or every other."""
    frame = sys._getframe(2)
    while frame is not None:
        for local in list(frame.f_locals.values()):
            if type(local) is Config and (local is caller) == own:
                object.__setattr__(local, name, value)
        frame = frame.f_back


def _walking(
    items: Iterable[object], name: str, value: object, *, caller: Config, own: bool
) -> Iterator[object]:
    _rewrite_configs(name, value, caller=caller, own=own)
    yield from items


Call = Callable[[Iterable[Match], Iterable[Player], Config], object]


def _replay(rows: Iterable[Match], seeds: Iterable[Player], cfg: Config) -> object:
    result = replay(rows, seeds, cfg=cfg, as_of=AS_OF)
    assert result.replay_info is not None
    return result.to_snapshot(), result.replay_info.config_fingerprint


def _resume(rows: Iterable[Match], seeds: Iterable[Player], cfg: Config) -> object:
    mid = replay(_ledger(), _seeds(), cfg=Config(), as_of=MID).to_snapshot()
    # The checkpoint holds the seeds, so none is passed again, but replay
    # still drains the seed_players iterable it is given.
    no_seeds = (seed for seed in seeds if seed is None)
    result = replay(rows, no_seeds, cfg=cfg, as_of=AS_OF, from_snapshot=mid)
    assert result.replay_info is not None
    return result.to_snapshot(), result.replay_info.config_fingerprint


def _prequential(rows: Iterable[Match], seeds: Iterable[Player], cfg: Config) -> object:
    return prequential_predictions(rows, seeds, cfg=cfg, burn_in_matches=2)


def _evaluate(rows: Iterable[Match], seeds: Iterable[Player], cfg: Config) -> object:
    return asdict(evaluate(rows, seeds, cfg=cfg, burn_in_matches=2))


def _fit_scale(rows: Iterable[Match], seeds: Iterable[Player], cfg: Config) -> object:
    fit = fit_scale(rows, seeds, cfg=cfg, burn_in_matches=2, low=2.0, high=4.0, tolerance=0.5)
    return asdict(fit)


def _fit_win(rows: Iterable[Match], seeds: Iterable[Player], cfg: Config) -> object:
    return asdict(fit_win_calibration(rows, seeds, cfg=cfg, burn_in_matches=2, tolerance=0.05))


CALLS: list[tuple[str, Call]] = [
    ("replay", _replay),
    ("replay_resume", _resume),
    ("prequential_predictions", _prequential),
    ("evaluate", _evaluate),
    ("fit_scale", _fit_scale),
    ("fit_win_calibration", _fit_win),
]
WHICH = ["matches", "seed_players"]


def _run(call: Call, which: str, cfg: Config, name: str, value: object, own: bool) -> object:
    rows: Iterable[Match] = _ledger()
    seeds: Iterable[Player] = _seeds()
    if which == "matches":
        rows = _walking(rows, name, value, caller=cfg, own=own)  # type: ignore[assignment]
    else:
        seeds = _walking(seeds, name, value, caller=cfg, own=own)  # type: ignore[assignment]
    return call(rows, seeds, cfg)


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("call", [c for _, c in CALLS], ids=[n for n, _ in CALLS])
def test_no_copy_exists_while_the_iterables_are_drained(call: Call, which: str) -> None:
    untouched = call(_ledger(), _seeds(), Config())
    cfg = Config()
    assert _run(call, which, cfg, "k_max", 5.0, own=False) == untouched
    assert cfg.fingerprint() == Config().fingerprint()


@pytest.mark.parametrize("which", WHICH)
@pytest.mark.parametrize("call", [c for _, c in CALLS], ids=[n for n, _ in CALLS])
def test_a_caller_config_rewritten_while_drained_is_checked(call: Call, which: str) -> None:
    with pytest.raises(ConfigError, match="K-factor bounds are invalid"):
        _run(call, which, Config(), "k_max", -1.0, own=True)


class Big(int):
    def __mul__(self, other: object) -> int:
        return 10**6

    __rmul__ = __mul__


@pytest.mark.parametrize("which", WHICH)
def test_a_caller_config_rewritten_while_drained_is_converted(which: str) -> None:
    weights = {**Config().match_weights, MatchType.TOURNAMENT: Big(1)}
    reference = Config(match_weights=weights)
    expected = _replay(_ledger(), _seeds(), reference)
    assert _run(_replay, which, Config(), "match_weights", dict(weights), own=True) == expected


def _flagged(seen: list[str]) -> Iterator[Match]:
    seen.append("drained")
    yield from _ledger()


@pytest.mark.parametrize(
    "call",
    [
        lambda rows, cfg: replay(rows, cfg=cfg, as_of=AS_OF),
        lambda rows, cfg: prequential_predictions(rows, cfg=cfg),
        lambda rows, cfg: evaluate(rows, cfg=cfg),
        lambda rows, cfg: fit_scale(rows, cfg=cfg),
        lambda rows, cfg: fit_win_calibration(rows, cfg=cfg, scoring=ScoringType.SIDE_OUT),
    ],
    ids=["replay", "prequential_predictions", "evaluate", "fit_scale", "fit_win_calibration"],
)
def test_a_wrong_typed_cfg_raises_before_the_iterables_are_drained(
    call: Callable[[Iterable[Match], object], object],
) -> None:
    seen: list[str] = []
    with pytest.raises(ConfigError, match="cfg must be exactly a Config"):
        call(_flagged(seen), 5)
    assert seen == []
