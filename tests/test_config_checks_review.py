"""Config checks found in review: int arithmetic, float rounding, huge ints, and key types.

Issues #68 and #70. Each test reproduces a config that built and then raised
later, or raised something other than ConfigError.
"""

from __future__ import annotations

import json
import math
import re
import sys
from datetime import date, timedelta

import pytest

from open_engine import (
    Config,
    ConfigError,
    GameFormat,
    Match,
    MatchType,
    RatingEngine,
    RatingFormat,
    ScoringType,
    replay,
)

FMAX = sys.float_info.max
FMAX_INT = int(FMAX)
LIMIT = 2.0**511
HUGE_INT = 10**5000
D = RatingFormat.DOUBLES
S = RatingFormat.SINGLES
START = date(2026, 10, 1)
# Day windows stay at 365 here. A window that reaches back before date.min
# from a match or reference date raises OverflowError (issue #92).
WINDOW = 365
K_PRODUCT_MESSAGE = (
    "k_max times the largest match weight and max_information_multiplier must be finite"
)


# -- 1: the K product is computed in floats ------------------------------------------


def test_an_all_int_k_product_that_overflows_a_float_raises_config_error() -> None:
    with pytest.raises(ConfigError, match=f"^{re.escape(K_PRODUCT_MESSAGE)}$"):
        Config(
            k_max=10**200,
            match_weights=dict.fromkeys(MatchType, 1),
            max_information_multiplier=10**200,
        )


def test_an_all_int_k_product_that_fits_a_float_is_accepted() -> None:
    cfg = Config(
        k_max=10**100,
        match_weights=dict.fromkeys(MatchType, 1),
        max_information_multiplier=10**100,
    )
    assert cfg.k_max == 10**100


# -- 2: int bounds where exact int arithmetic raises ----------------------------------


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        pytest.param(
            {"rd_growth_per_day": 2**500 + 1},
            "rd_growth_per_day must be at most 2**500 when it is an int",
            id="int-growth-above-2**500",
        ),
        pytest.param(
            {"rd_growth_per_day": 2**511},
            "rd_growth_per_day must be at most 2**500 when it is an int",
            id="int-growth-2**511",
        ),
        pytest.param(
            {"game_count_bonus": 2**511 + 1},
            "game_count_bonus must be at most 2**511 when it is an int",
            id="int-bonus-above-2**511",
        ),
        pytest.param(
            {"game_count_bonus": 10**308},
            "game_count_bonus must be at most 2**511 when it is an int",
            id="int-bonus-1e308",
        ),
    ],
)
def test_int_bounds(changes: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=f"^{re.escape(message)}$"):
        Config(**changes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"rd_growth_per_day": 2**500}, id="int-growth-at-bound"),
        pytest.param({"rd_growth_per_day": LIMIT}, id="float-growth-at-bound"),
        pytest.param({"game_count_bonus": 2**511}, id="int-bonus-at-bound"),
        pytest.param({"game_count_bonus": FMAX}, id="float-bonus-near-max"),
    ],
)
def test_int_bounds_leave_floats_and_the_bound_itself(changes: dict[str, object]) -> None:
    cfg = Config(**changes)  # type: ignore[arg-type]
    for name, value in changes.items():
        assert getattr(cfg, name) == value


def _extremes(number: type, k_variant: str) -> Config:
    """Every number field at its upper bound as an int or a float."""

    def n(value: float) -> object:
        return int(value) if number is int else float(value)

    big = n(FMAX)
    growth = 2**500 if number is int else LIMIT
    bonus = 2**511 if number is int else FMAX
    one = n(1)
    k_max, weight, multiplier = {
        "k": (big, one, one),
        "weights": (one, big, one),
        "multiplier": (one, one, big),
    }[k_variant]
    # An int shrink times an int step weight near FMAX does not convert to a
    # float, so a self-reported match would raise OverflowError (issue #93).
    shrink = one if number is int and k_variant != "k" else big
    return Config(
        min_rating=-n(LIMIT),
        max_rating=n(LIMIT),
        default_rating=n(LIMIT),
        scale_by_scoring=dict.fromkeys(ScoringType, big),
        win_calibration_by_scoring=dict.fromkeys(ScoringType, big),
        k_min=k_max,
        k_max=k_max,
        rd_min=one,
        rd_max=n(LIMIT),
        new_player_rd=n(LIMIT),
        external_seed_rd=n(LIMIT),
        category_seed_rd=n(LIMIT),
        rd_shrink_per_match=shrink,
        rd_growth_per_day=growth,
        win_weight=one,
        match_weights=dict.fromkeys(MatchType, weight),
        game_length_weights={11: big, 15: big, 21: big},
        game_count_bonus=bonus,
        max_information_multiplier=multiplier,
        diversity_window_days=WINDOW,
        diversity_target=FMAX_INT,
        self_reported_repeat_window_days=WINDOW,
        self_reported_repeat_factor=one,
        self_reported_max_repeats=FMAX_INT,
        import_reliability_carry_matches=FMAX_INT,
        import_reliability_max_age_days=WINDOW,
        established_reliability_threshold=n(100),
        established_min_verified_matches=FMAX_INT,
        connectivity_reliability_cap=n(100),
        connectivity_min_shared_contacts=FMAX_INT,
        connectivity_min_contact_days=FMAX_INT,
        connectivity_min_outside_players=FMAX_INT,
        max_singles_rating_gap=big,
        max_doubles_partner_gap=big,
    )


def _ledger() -> list[Match]:
    three_games = ((11, 7), (7, 11), (11, 9))
    return [
        Match("x1", ("p1", "p2"), ("p3", "p4"), ((11, 7),), START, D, best_of=1),
        Match(
            "x2",
            ("p1", "p2"),
            ("p3", "p4"),
            three_games,
            START + timedelta(days=7),
            D,
            best_of=3,
            game_format=GameFormat(ScoringType.RALLY, 11),
        ),
        Match(
            "x3",
            ("p1", "p3"),
            ("p2", "p4"),
            ((7, 11),),
            START + timedelta(days=40),
            D,
            best_of=1,
            match_type=MatchType.LEAGUE,
        ),
        Match("x4", ("p1",), ("p4",), ((11, 3),), START + timedelta(days=41), S, best_of=1),
    ]


def _assert_finite(eng: RatingEngine, as_of: date) -> None:
    for fmt in (D, S):
        for pid in ("p1", "p4"):
            summary = eng.player_summary(pid, fmt, as_of=as_of)
            for value in (summary.rating, summary.reliability, summary.rd):
                assert math.isfinite(value)
    share = eng.predict_point_share(("p1", "p2"), ("p3", "p4"), D, ScoringType.RALLY)
    win = eng.predict_win_probability(
        ("p1", "p2"), ("p3", "p4"), D, GameFormat(ScoringType.RALLY, 11), best_of=3
    )
    assert math.isfinite(share)
    assert math.isfinite(win)


@pytest.mark.parametrize("k_variant", ["k", "weights", "multiplier"])
@pytest.mark.parametrize("number", [int, float], ids=["int", "float"])
def test_every_number_at_its_bound_records_and_replays(number: type, k_variant: str) -> None:
    cfg = _extremes(number, k_variant)
    as_of = START + timedelta(days=60)

    live = RatingEngine(cfg)
    for match in _ledger():
        result = live.record(match)
        assert result.counted, match.match_id
        assert math.isfinite(result.expected_a)
        assert math.isfinite(result.imbalance)
        assert all(math.isfinite(delta) for delta in result.deltas.values())
    _assert_finite(live, as_of)

    replayed = replay(_ledger(), cfg=cfg, as_of=as_of)
    for value in replayed.replay_info.net_rating_created.values():  # type: ignore[union-attr]
        assert math.isfinite(value)
    _assert_finite(replayed, as_of)


# -- 3: RD bounds that are equal once converted to floats -----------------------------


def test_rd_bounds_equal_as_floats_raise() -> None:
    with pytest.raises(ConfigError, match="^RD bounds are invalid$"):
        Config(
            rd_min=9007199254740992.0,
            rd_max=9007199254740993,
            new_player_rd=9007199254740992.0,
            external_seed_rd=9007199254740992.0,
            category_seed_rd=9007199254740992.0,
        )


# -- 4: huge ints raise ConfigError, not the int-to-str ValueError ---------------------


def test_huge_format_points_raise_the_entry_message() -> None:
    with pytest.raises(ConfigError, match="^allowed_game_formats entries must be"):
        Config(allowed_game_formats=frozenset({(ScoringType.RALLY, HUGE_INT)}))


def _data(**changes: object) -> dict[str, object]:
    data = Config().to_dict()
    data.update(changes)
    return data


FROM_DICT_CASES = [
    pytest.param(
        _data(game_length_weights={"9" * 5000: 1.0, "11": 1.0, "15": 1.1, "21": 1.2}),
        "game_length_weights has a key that is not a whole number",
        id="length-key-5000-digits",
    ),
    pytest.param(
        _data(game_length_weights={HUGE_INT: 1.0, "11": 1.0, "15": 1.1, "21": 1.2}),
        "game_length_weights keys must be whole numbers",
        id="length-key-huge-int",
    ),
    pytest.param(
        _data(allowed_game_formats=[["rally", HUGE_INT]]),
        "allowed_game_formats entries must be",
        id="format-points-huge-int",
    ),
    pytest.param(
        _data(allowed_game_formats=[["bounce", HUGE_INT]]),
        "allowed_game_formats has an unknown scoring type",
        id="format-unknown-scoring-huge-int",
    ),
    pytest.param(
        _data(allowed_game_formats=[[HUGE_INT]]),
        "allowed_game_formats has an entry that is not a pair",
        id="format-not-a-pair-huge-int",
    ),
    pytest.param(
        _data(match_weights={HUGE_INT: 1.0}),
        "match_weights has an unknown key",
        id="match-weights-huge-int-key",
    ),
    pytest.param(
        {**Config().to_dict(), HUGE_INT: 1},
        "Config data has unknown keys",
        id="top-level-huge-int-key",
    ),
]


@pytest.mark.parametrize(("data", "message"), FROM_DICT_CASES)
def test_from_dict_huge_ints_raise_config_error(data: dict[object, object], message: str) -> None:
    with pytest.raises(ConfigError, match=f"^{re.escape(message)}"):
        Config.from_dict(data)  # type: ignore[arg-type]


# -- 5: keys and entries are stored as their own types --------------------------------


class _LooksLikeRally:
    """Not a str, but hashes and compares equal to "rally"."""

    def __hash__(self) -> int:
        return hash("rally")

    def __eq__(self, other: object) -> bool:
        return other == "rally"


class _OddInt(int):
    """An int whose str() is not its digits."""

    def __str__(self) -> str:
        return "odd"

    def __repr__(self) -> str:
        return "odd"


def test_a_lookalike_scoring_type_in_a_format_is_rejected() -> None:
    # Since issue #145, a format's scoring item must be a member or exactly
    # a str, checked by type before any of its own methods run.
    formats = frozenset({(_LooksLikeRally(), 11), (ScoringType.SIDE_OUT, 11)})
    with pytest.raises(
        ConfigError,
        match=re.escape("allowed_game_formats entries must be (scoring type, whole number) pairs"),
    ):
        Config(allowed_game_formats=formats)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["scale_by_scoring", "win_calibration_by_scoring"])
def test_a_lookalike_scoring_key_is_rejected(name: str) -> None:
    # Since the second review of issues #116, #118, and #119, each key is
    # converted once, from a member or an exact str only: a lookalike's own
    # __hash__ and __eq__ could answer differently on each call.
    values = {ScoringType.SIDE_OUT: 1.5, _LooksLikeRally(): 1.5}
    with pytest.raises(ConfigError, match=f"^{name} needs a value for every scoring type$"):
        Config(**{name: values})  # type: ignore[arg-type]


def test_str_keys_are_stored_as_enum_members() -> None:
    cfg = Config(
        scale_by_scoring={"side_out": 2.9, "rally": 2.9},  # type: ignore[dict-item]
        match_weights={"tournament": 1.0, "league": 0.8, "rec_verified": 0.6, "self_reported": 0.3},  # type: ignore[dict-item]
        allowed_game_formats=frozenset({("rally", 11)}),  # type: ignore[arg-type]
    )
    assert all(type(key) is ScoringType for key in cfg.scale_by_scoring)
    assert all(type(key) is MatchType for key in cfg.match_weights)
    assert all(type(scoring) is ScoringType for scoring, _ in cfg.allowed_game_formats)


def test_int_subclass_keys_and_points_are_stored_as_ints() -> None:
    cfg = Config(
        game_length_weights={_OddInt(11): 1.0, 15: 1.1, 21: 1.2},
        allowed_game_formats=frozenset({(ScoringType.RALLY, _OddInt(11))}),
    )
    plain = Config(
        game_length_weights={11: 1.0, 15: 1.1, 21: 1.2},
        allowed_game_formats=frozenset({(ScoringType.RALLY, 11)}),
    )
    assert cfg.fingerprint() == plain.fingerprint()
    assert all(type(key) is int for key in cfg.game_length_weights)
    assert all(type(points) is int for _, points in cfg.allowed_game_formats)


# Measured on origin/main (3845bec) before the key conversion. Every config here
# builds on main, so its fingerprint must not change. Re-pinned in issue #155
# when params_version joined the fingerprint payload; the old pins were
# confirmed equal to the same configs without that key.
MAIN_FINGERPRINTS = [
    pytest.param({}, "7334117efa23cc98", id="default"),
    pytest.param(
        {"scale_by_scoring": {"side_out": 2.9, "rally": 2.9}},
        "7334117efa23cc98",
        id="str-scale-keys",
    ),
    pytest.param(
        {"win_calibration_by_scoring": {"side_out": 1.2, "rally": 0.8}},
        "2d99846043c85440",
        id="str-calibration-keys",
    ),
    pytest.param(
        {
            "match_weights": {
                "tournament": 1.0,
                "league": 0.8,
                "rec_verified": 0.6,
                "self_reported": 0.3,
            }
        },
        "0dced2b08a369675",
        id="str-match-weight-keys",
    ),
    pytest.param(
        {"allowed_game_formats": frozenset({("rally", 11), ("side_out", 15)})},
        "9795ad22cb234629",
        id="str-format-entries",
    ),
    pytest.param(
        {
            "scale_by_scoring": {ScoringType.SIDE_OUT: 2.5, ScoringType.RALLY: 3.1},
            "game_length_weights": {11: 1.0, 15: 1.05, 21: 1.25, 25: 1.4},
            "max_singles_rating_gap": 0.75,
        },
        "6c883029c4c96142",
        id="enum-custom",
    ),
]


@pytest.mark.parametrize(("changes", "fingerprint"), MAIN_FINGERPRINTS)
def test_fingerprints_match_main(changes: dict[str, object], fingerprint: str) -> None:
    cfg = Config(**changes)  # type: ignore[arg-type]
    assert cfg.fingerprint() == fingerprint
    assert Config.from_dict(json.loads(json.dumps(cfg.to_dict()))) == cfg
