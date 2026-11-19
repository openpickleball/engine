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
"""

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
