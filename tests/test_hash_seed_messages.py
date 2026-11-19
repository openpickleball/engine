"""Error messages do not depend on the hash seed (issue #145).

A check that walks a set and raises on the first failing member named
whichever member the set gave first, and set order for strs and enum
members changes with PYTHONHASHSEED. Each case runs in fresh interpreters
under several hash seeds and must raise the same error class and message
in every one. All values are synthetic.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

HASH_SEEDS = ("0", "1", "2", "3", "4", "5", "6", "7")

PRELUDE = """
from datetime import date
from open_engine import Config, RatingState, ScoringType, reliability
SO, RA = ScoringType.SIDE_OUT, ScoringType.RALLY


def fake_side_out():
    fake = str.__new__(ScoringType, "side_out")
    fake._value_ = "side_out"
    fake._name_ = "SIDE_OUT"
    return fake


def tampered(formats):
    cfg = Config()
    object.__setattr__(cfg, "allowed_game_formats", formats)
    return reliability(RatingState(rating=3.5, rd=1.0), cfg, date(2025, 3, 1))


class ClsProp:
    @property
    def __class__(self):
        raise RuntimeError("caller code ran")

    def __hash__(self):
        return 1
"""

PAIRS = "ConfigError: allowed_game_formats entries must be (scoring type, whole number) pairs"
AT_LEAST_1 = "ConfigError: allowed_game_formats points must be at least 1"

CASES = [
    pytest.param(
        "Config(game_length_weights={11: 1.0})",
        "ConfigError: No game_length_weight for games to 15",
        id="default formats missing two lengths",
    ),
    pytest.param(
        "Config.from_dict({**Config().to_dict(), 'game_length_weights': {'11': 1.0}})",
        "ConfigError: No game_length_weight for games to 15",
        id="from_dict missing two lengths",
    ),
    pytest.param(
        "Config(allowed_game_formats=frozenset({(SO, 25), (RA, 9), (SO, 7)}))",
        "ConfigError: No game_length_weight for games to 7",
        id="formats missing three lengths",
    ),
    pytest.param(
        "Config(allowed_game_formats={(SO, 11), (RA, 30), (SO, 13)})",
        "ConfigError: No game_length_weight for games to 13",
        id="set formats missing two lengths",
    ),
    pytest.param(
        "Config(allowed_game_formats=frozenset({'ab', (fake_side_out(), 11)}))",
        "ConfigError: allowed_game_formats entries must be (scoring type, whole number) pairs",
        id="not a pair and a non-member",
    ),
    pytest.param(
        "Config(allowed_game_formats=frozenset({(SO, 11.5), (fake_side_out(), 11)}))",
        "ConfigError: allowed_game_formats has a ScoringType that is not a member",
        id="bad points and a non-member",
    ),
    pytest.param(
        "Config(allowed_game_formats=frozenset({('bogus', 11), (fake_side_out(), 13)}))",
        "ConfigError: allowed_game_formats has a ScoringType that is not a member",
        id="unknown scoring and a non-member",
    ),
    pytest.param(
        "tampered(frozenset({'ab', 1.5, (SO, True)}))",
        "ConfigError: cfg.allowed_game_formats entries must be pairs, not float",
        id="rebuild entries of three kinds",
    ),
    pytest.param(
        "tampered({(None, 11), ('bogus', 11), (SO, '11')})",
        "ConfigError: cfg.allowed_game_formats has a key or entry of type NoneType",
        id="rebuild bad scoring and points",
    ),
    pytest.param(
        "tampered(frozenset({b'x', (SO,), (SO, 2.5)}))",
        "ConfigError: cfg.allowed_game_formats entries must be pairs, not bytes",
        id="rebuild bytes and short tuple",
    ),
    pytest.param(
        "tampered(frozenset({(SO, 7), (RA, 9), (SO, 25)}))",
        "ConfigError: No game_length_weight for games to 7",
        id="rebuild formats missing three lengths",
    ),
    pytest.param(
        "Config(allowed_game_formats=frozenset({(SO, ClsProp()), (RA, 11.5), (SO, 'x')}))",
        PAIRS,
        id="caller class property points with other bad entries",
    ),
    pytest.param(
        "Config(allowed_game_formats=frozenset({(SO, ClsProp())}))",
        PAIRS,
        id="caller class property points",
    ),
    pytest.param(
        "Config(allowed_game_formats=frozenset({(SO, 0), (RA, -3), (SO, 11.5)}))",
        PAIRS,
        id="lengths below 1 and bad points",
    ),
    pytest.param(
        "Config(allowed_game_formats=frozenset({(SO, 0), (fake_side_out(), 11)}))",
        "ConfigError: allowed_game_formats has a ScoringType that is not a member",
        id="length below 1 and a non-member",
    ),
    pytest.param(
        "Config(allowed_game_formats=frozenset({(SO, 11), (RA, -3), (SO, 0)}))",
        AT_LEAST_1,
        id="lengths below 1",
    ),
    pytest.param("Config(allowed_game_formats={(SO, -3)})", AT_LEAST_1, id="length -3"),
    pytest.param(
        "Config.from_dict({**Config().to_dict(), 'allowed_game_formats': [['side_out', 0]]})",
        AT_LEAST_1,
        id="from_dict length 0",
    ),
    pytest.param("tampered(frozenset({(SO, -3), (RA, 0)}))", AT_LEAST_1, id="rebuild length -3"),
]


def _outcome(expression: str, hash_seed: str) -> str:
    script = PRELUDE + textwrap.dedent(
        f"""
        try:
            {expression}
        except Exception as exc:
            print(f"{{type(exc).__name__}}: {{exc}}")
        else:
            print("no error")
        """
    )
    done = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "PYTHONHASHSEED": hash_seed},
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.mark.parametrize(("expression", "expected"), CASES)
def test_error_message_is_the_same_under_every_hash_seed(expression: str, expected: str) -> None:
    outcomes = {_outcome(expression, seed) for seed in HASH_SEEDS}
    assert outcomes == {expected}


def test_scoring_type_str_subclass_is_rejected_before_its_code_runs() -> None:
    from open_engine import Config, ConfigError, ScoringType

    calls: list[str] = []

    class CountingStr(str):
        def __hash__(self) -> int:
            calls.append("hash")
            return str.__hash__(self)

        def __eq__(self, other: object) -> bool:
            calls.append("eq")
            return str.__eq__(self, other)

    formats = frozenset({(CountingStr("side_out"), 11), (ScoringType.RALLY, 11)})
    calls.clear()
    with pytest.raises(ConfigError, match=r"entries must be \(scoring type, whole number\) pairs"):
        Config(allowed_game_formats=formats)
    assert calls == []


def test_formats_claiming_frozenset_by_class_property_are_rejected_unread() -> None:
    from open_engine import Config, ConfigError, ScoringType

    calls: list[str] = []

    class ClaimsFrozenset:
        @property
        def __class__(self) -> type:
            calls.append("__class__")
            return frozenset

        def __iter__(self):  # type: ignore[no-untyped-def]
            calls.append("__iter__")
            return iter([(ScoringType.SIDE_OUT, 11)])

    with pytest.raises(ConfigError, match=r"^allowed_game_formats must be a set or frozenset$"):
        Config(allowed_game_formats=ClaimsFrozenset())  # type: ignore[arg-type]
    assert calls == []
