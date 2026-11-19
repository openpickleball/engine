"""
Refactor parity for issue #128: outcomes pinned from main before the split.

Config.__post_init__, _read_snapshot, seed_external_rating, _compute, and
_consider were split into helpers with no behavior change. Each case here
runs a public entry point and compares its outcome (the error class and
message, or a digest of the result) with the value main gave. The
two-failure inputs break two adjacent checks at once, so a helper that runs
its checks in another order fails here. A failed engine call must also
leave the engine's snapshot unchanged. All ids are synthetic.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from collections.abc import Callable, Iterator, Mapping
from datetime import date, timedelta

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    GameFormat,
    Match,
    MatchType,
    RatingEngine,
    RatingFormat,
    RatingState,
    ScoringType,
    reliability,
    replay,
)

D0 = date(2025, 1, 1)
SO, RA = ScoringType.SIDE_OUT, ScoringType.RALLY
WEIGHTS = {
    MatchType.TOURNAMENT: 1.0,
    MatchType.LEAGUE: 0.9,
    MatchType.REC_VERIFIED: 0.75,
    MatchType.SELF_REPORTED: 0.4,
}
# Keys that change with a release, left out of the digests.
_VERSIONED = ("engine_version", "config_version", "config_fingerprint")


def _digest(value: object) -> str:
    text = json.dumps(value, sort_keys=True, default=repr)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _snapshot_digest(engine: RatingEngine) -> str:
    snap = engine.to_snapshot()
    for key in _VERSIONED:
        snap.pop(key)
    info = snap.get("replay_info")
    if isinstance(info, dict):
        info.pop("config_version")
        info.pop("config_fingerprint")
    return _digest(snap)


def _outcome(call: Callable[[], object]) -> str:
    try:
        result = call()
    except Exception as exc:  # noqa: BLE001  any error class is an outcome here
        return f"{type(exc).__name__}: {exc}"
    if isinstance(result, RatingEngine):
        return "engine " + _snapshot_digest(result)
    if dataclasses.is_dataclass(result) and not isinstance(result, Config):
        return "ok " + _digest(dataclasses.asdict(result))  # type: ignore[arg-type]
    return "ok " + _digest(repr(result))


# ---------------------------------------------------------------------------
# Config.__post_init__: two failures for each adjacent pair of check groups
# ---------------------------------------------------------------------------


class _Pairs(Mapping[object, object]):
    """A mapping whose items repeat a key, as no dict can."""

    def __init__(self, *pairs: tuple[object, object]) -> None:
        self._pairs = pairs

    def __getitem__(self, key: object) -> object:
        return next(value for k, value in self._pairs if k == key)

    def __iter__(self) -> Iterator[object]:
        return iter([k for k, _ in self._pairs])

    def __len__(self) -> int:
        return len(self._pairs)


CONFIG_PAIRS: dict[str, dict[str, object]] = {
    "version then number": {"version": 1, "k_min": "x"},
    "number then whole": {"k_min": "x", "diversity_target": 1.5},
    "whole then optional": {"diversity_target": 1.5, "max_singles_rating_gap": "x"},
    "optional then bool": {"max_singles_rating_gap": "x", "category_feeds_parent": 1},
    "bool then mapping": {"category_feeds_parent": 1, "match_weights": []},
    "mapping then mapping value": {"scale_by_scoring": [], "match_weights": {"x": "y"}},
    "mapping value then length key": {
        "scale_by_scoring": {SO: "x", RA: 2.9},
        "game_length_weights": {"11": 1.0},
    },
    "length key then repeat": {"game_length_weights": _Pairs((11, 1.0), (11, 1.0), ("x", 1.0))},
    "repeat then key below 1": {"game_length_weights": _Pairs((0, 1.0), (15, 1.0), (15, 1.0))},
    "key below 1 then value": {"game_length_weights": {0: 0.0, 15: 1.0, 21: 1.0}},
    "length value then formats type": {
        "game_length_weights": {11: 0.0, 15: 1.0, 21: 1.0},
        "allowed_game_formats": [(SO, 11)],
    },
    "formats type then entry": {
        "allowed_game_formats": [(SO, 11, 2)],
        "scale_by_scoring": {SO: 2.9},
    },
    "format entry then enum keys": {
        "allowed_game_formats": frozenset({("bogus", 11)}),
        "scale_by_scoring": {SO: 2.9},
    },
    "scale keys then calibration keys": {
        "scale_by_scoring": {SO: 2.9},
        "win_calibration_by_scoring": {SO: 1.0},
    },
    "calibration keys then weight keys": {
        "win_calibration_by_scoring": {SO: 1.0},
        "match_weights": {MatchType.TOURNAMENT: 1.0},
    },
    "weight keys then scale value": {
        "match_weights": {MatchType.TOURNAMENT: 1.0},
        "scale_by_scoring": {SO: 0.0, RA: 2.9},
    },
    "scale value then calibration value": {
        "scale_by_scoring": {SO: 0.0, RA: 2.9},
        "win_calibration_by_scoring": {SO: 0.0, RA: 1.0},
    },
    "calibration value then weight value": {
        "win_calibration_by_scoring": {SO: 0.0, RA: 1.0},
        "match_weights": {**WEIGHTS, MatchType.LEAGUE: 0.0},
    },
    "weight value then length coverage": {
        "match_weights": {**WEIGHTS, MatchType.LEAGUE: 0.0},
        "allowed_game_formats": frozenset({(SO, 30)}),
    },
    "length coverage then rating limit": {
        "allowed_game_formats": frozenset({(SO, 30)}),
        "max_rating": 2.0**512,
    },
    "rating limit then order": {"max_rating": 2.0**512, "min_rating": 2.0**512},
    "rating order then default": {"min_rating": 9.0, "default_rating": 1.0},
    "default then K": {"default_rating": 9.0, "k_min": 1.0},
    "K then RD": {"k_min": 1.0, "rd_min": 0.0},
    "RD then rd_max limit": {"rd_min": 0.0, "rd_max": 2.0**512},
    "rd_max limit then seed RD": {"rd_max": 2.0**512, "new_player_rd": 0.05},
    "seed RD then shrink": {"category_seed_rd": 0.05, "rd_shrink_per_match": -1.0},
    "shrink then growth": {"rd_shrink_per_match": -1.0, "rd_growth_per_day": -1.0},
    "growth then int growth": {"rd_growth_per_day": 2.0**512, "win_weight": 2.0},
    "int growth then win weight": {"rd_growth_per_day": 2**501, "win_weight": 2.0},
    "win weight then bonus": {"win_weight": 2.0, "game_count_bonus": -1.0},
    "bonus then int bonus": {"game_count_bonus": -1, "max_information_multiplier": 0.5},
    "int bonus then multiplier": {"game_count_bonus": 2**600, "max_information_multiplier": 0.5},
    "multiplier then K finite": {"max_information_multiplier": 0.5, "k_max": 1e308},
    "K finite then int reach": {
        "k_max": 1e308,
        "match_weights": {**WEIGHTS, MatchType.SELF_REPORTED: 10**308},
        "max_information_multiplier": 2,
        "self_reported_repeat_factor": 1,
        "game_count_bonus": 1,
    },
    "int reach then shrink product": {
        "match_weights": {**WEIGHTS, MatchType.SELF_REPORTED: 10**308},
        "max_information_multiplier": 2,
        "self_reported_repeat_factor": 1,
        "game_count_bonus": 1,
        "rd_shrink_per_match": 10**200,
    },
    "shrink product then replay interval": {
        "match_weights": {**WEIGHTS, MatchType.SELF_REPORTED: 10**200},
        "max_information_multiplier": 2,
        "self_reported_repeat_factor": 1,
        "game_count_bonus": 1,
        "rd_shrink_per_match": 10**200,
        "replay_interval_days": 0,
    },
    "replay interval then diversity": {"replay_interval_days": 0, "diversity_target": 0},
    "diversity then repeat factor": {
        "diversity_window_days": 0,
        "self_reported_repeat_factor": 0.0,
    },
    "repeat factor then repeat limits": {
        "self_reported_repeat_factor": 2.0,
        "self_reported_max_repeats": 0,
    },
    "repeat limits then import limits": {
        "self_reported_repeat_window_days": 0,
        "import_reliability_carry_matches": -1,
    },
    "import limits then day limits": {
        "import_reliability_max_age_days": -1,
        "diversity_window_days": 10**9,
    },
    "day limits then threshold": {
        "replay_interval_days": 10**9,
        "established_reliability_threshold": 101.0,
    },
    "threshold then min verified": {
        "established_reliability_threshold": -1.0,
        "established_min_verified_matches": -1,
    },
    "min verified then cap": {
        "established_min_verified_matches": -1,
        "connectivity_reliability_cap": 101.0,
    },
    "cap then connectivity": {
        "connectivity_reliability_cap": -1.0,
        "connectivity_min_contact_days": 0,
    },
    "connectivity then gaps": {
        "connectivity_min_outside_players": 0,
        "max_doubles_partner_gap": -1.0,
    },
    "gaps": {"max_singles_rating_gap": -1.0, "max_doubles_partner_gap": -1.0},
    "valid ints": {
        "match_weights": {m: 1 for m in MatchType},
        "max_information_multiplier": 2,
        "self_reported_repeat_factor": 1,
        "game_count_bonus": 1,
        "rd_shrink_per_match": 0,
        "min_rating": 0,
        "max_rating": 10,
    },
    "valid default": {},
}

CONFIG_EXPECTED: dict[str, str] = {
    "K finite then int reach": "ConfigError: k_max times the largest match weight and "
    "max_information_multiplier must be finite",
    "K then RD": "ConfigError: K-factor bounds are invalid",
    "RD then rd_max limit": "ConfigError: RD bounds are invalid",
    "bonus then int bonus": "ConfigError: game_count_bonus must be at least 0",
    "bool then mapping": "ConfigError: category_feeds_parent must be a bool",
    "calibration keys then weight keys": "ConfigError: win_calibration_by_scoring needs a "
    "value for every scoring type",
    "calibration value then weight value": "ConfigError: win calibration values must be positive",
    "cap then connectivity": "ConfigError: connectivity_reliability_cap must be None or a "
    "number from 0 to 100",
    "connectivity then gaps": "ConfigError: connectivity_min_outside_players must be a "
    "positive int",
    "day limits then threshold": "ConfigError: replay_interval_days must be at most 3652058",
    "default then K": "ConfigError: default_rating must be within rating bounds",
    "diversity then repeat factor": "ConfigError: diversity settings must be positive",
    "format entry then enum keys": "ConfigError: allowed_game_formats entries must be "
    "(scoring type, whole number) pairs",
    "formats type then entry": "ConfigError: allowed_game_formats must be a set or frozenset",
    "gaps": "ConfigError: max_singles_rating_gap must be None or at least 0",
    "growth then int growth": "ConfigError: rd_growth_per_day must be between 0 and 2**511",
    "import limits then day limits": "ConfigError: import reliability limits must be nonnegative",
    "int bonus then multiplier": "ConfigError: game_count_bonus must be at most 2**511 "
    "when it is an int",
    "int growth then win weight": "ConfigError: rd_growth_per_day must be at most 2**500 "
    "when it is an int",
    "int reach then shrink product": "ConfigError: k_max times the largest match weight "
    "and max_information_multiplier must be finite",
    "key below 1 then value": "ConfigError: game_length_weights keys must be at least 1",
    "length coverage then rating limit": "ConfigError: No game_length_weight for games to 30",
    "length key then repeat": "ConfigError: game_length_weights keys must be whole numbers",
    "length value then formats type": "ConfigError: game_length_weights values must be positive",
    "mapping then mapping value": "ConfigError: scale_by_scoring must be a mapping",
    "mapping value then length key": "ConfigError: scale_by_scoring values must be finite numbers",
    "min verified then cap": "ConfigError: established_min_verified_matches must be a "
    "nonnegative int",
    "multiplier then K finite": "ConfigError: max_information_multiplier must be at least 1",
    "number then whole": "ConfigError: k_min must be a finite number",
    "optional then bool": "ConfigError: max_singles_rating_gap must be None or a finite number",
    "rating limit then order": "ConfigError: min_rating must be between -2**511 and 2**511",
    "rating order then default": "ConfigError: min_rating must be less than max_rating",
    "rd_max limit then seed RD": "ConfigError: rd_max must be at most 2**511",
    "repeat factor then repeat limits": "ConfigError: self_reported_repeat_factor must be "
    "in (0, 1]",
    "repeat limits then import limits": "ConfigError: self-reported repeat limits must be positive",
    "repeat then key below 1": "ConfigError: game_length_weights repeats the game length 15",
    "replay interval then diversity": "ConfigError: replay_interval_days must be positive",
    "scale keys then calibration keys": "ConfigError: scale_by_scoring needs a value for "
    "every scoring type",
    "scale value then calibration value": "ConfigError: scales must be positive",
    "seed RD then shrink": "ConfigError: category_seed_rd must be within RD bounds",
    "shrink product then replay interval": "ConfigError: rd_shrink_per_match times the "
    "largest match weight and "
    "max_information_multiplier must be finite",
    "shrink then growth": "ConfigError: rd_shrink_per_match must be at least 0",
    "threshold then min verified": "ConfigError: established_reliability_threshold must "
    "be between 0 and 100",
    "valid default": "ok a13775baa545a879",
    "valid ints": "ok 01905fcb23fcd058",
    "version then number": "ConfigError: version must be a str",
    "weight keys then scale value": "ConfigError: match_weights needs a value for every match type",
    "weight value then length coverage": "ConfigError: match_weights values must be positive",
    "whole then optional": "ConfigError: diversity_target must be a whole number",
    "win weight then bonus": "ConfigError: win_weight must be between 0 and 1",
}


@pytest.mark.parametrize("name", sorted(CONFIG_PAIRS))
def test_config_checks_keep_their_order(name: str) -> None:
    def build() -> object:
        cfg = Config(**CONFIG_PAIRS[name])  # type: ignore[arg-type]
        return cfg.fingerprint()

    assert _outcome(build) == CONFIG_EXPECTED[name]


# ---------------------------------------------------------------------------
# Config.from_dict
# ---------------------------------------------------------------------------


def _from_dict_cases() -> dict[str, dict[object, object]]:
    base: dict[object, object] = dict(Config().to_dict().items())
    cases: dict[str, dict[object, object]] = {"plain": dict(base)}
    missing = dict(base)
    del missing["k_min"]
    missing["zz"] = 1
    cases["missing and unknown"] = missing
    cases["bad mapping and bad number"] = {**base, "match_weights": [], "k_min": "x"}
    cases["bad formats and bad lengths"] = {
        **base,
        "allowed_game_formats": [["side_out", "11"]],
        "game_length_weights": {"x": 1.0},
    }
    return cases


FROM_DICT_CASES = _from_dict_cases()
FROM_DICT_EXPECTED: dict[str, str] = {
    "bad formats and bad lengths": "ConfigError: game_length_weights has a key that is "
    "not a whole number: 'x'",
    "bad mapping and bad number": "ConfigError: match_weights must be a mapping",
    "missing and unknown": "ConfigError: Config data has missing keys: k_min",
    "plain": "ok a13775baa545a879",
}


@pytest.mark.parametrize("name", sorted(FROM_DICT_CASES))
def test_config_from_dict_keeps_its_outcomes(name: str) -> None:
    data: dict[str, object] = FROM_DICT_CASES[name]  # type: ignore[assignment]
    assert _outcome(lambda: Config.from_dict(data).fingerprint()) == FROM_DICT_EXPECTED[name]


# ---------------------------------------------------------------------------
# A fixed ledger: record (_consider and _compute) and replay
# ---------------------------------------------------------------------------


def _match(
    match_id: str,
    team_a: tuple[str, ...],
    team_b: tuple[str, ...],
    games: tuple[tuple[int, int], ...],
    day: int,
    rating_format: RatingFormat,
    match_type: MatchType = MatchType.REC_VERIFIED,
    **kw: object,
) -> Match:
    return Match(
        match_id=match_id,
        team_a=team_a,
        team_b=team_b,
        games=games,
        played_on=D0 + timedelta(days=day),
        rating_format=rating_format,
        match_type=match_type,
        **kw,  # type: ignore[arg-type]
    )


DBL, SGL, MIX = RatingFormat.DOUBLES, RatingFormat.SINGLES, RatingFormat.MIXED_DOUBLES
SELF = MatchType.SELF_REPORTED


def _ledger() -> list[Match]:
    both = frozenset({"a1", "b1"})
    return [
        _match("m01", ("a1", "a2"), ("b1", "b2"), ((11, 7),), 1, DBL),
        _match("m02", ("a1",), ("b1",), ((11, 9), (8, 11), (11, 4)), 2, SGL, best_of=3),
        _match("m03", ("a1", "b2"), ("b1", "a2"), ((13, 11),), 3, MIX, MatchType.TOURNAMENT),
        _match("m04", ("a1", "a2"), ("b1", "b2"), ((11, 3),), 4, DBL, SELF, confirmed_by=both),
        _match("m05", ("a1", "a2"), ("b2", "b1"), ((11, 3),), 4, DBL, SELF, confirmed_by=both),
        _match("m06", ("a1", "a2"), ("b1", "b2"), ((11, 5),), 5, DBL, SELF),
        _match("m07", ("c1", "c2"), ("d1", "d2"), ((11, 9),), 6, DBL, game_format=GameFormat(RA)),
        _match(
            "m08", ("c1", "c2"), ("d1", "d2"), ((15, 2),), 7, DBL, game_format=GameFormat(SO, 15)
        ),
        _match("m09", ("e1", "c2"), ("d1", "a1"), ((11, 7),), 0, MIX, MatchType.LEAGUE),
        _match("m10", ("c1",), ("e1",), ((21, 19),), 8, SGL, game_format=GameFormat(RA, 21)),
        _match("m11", ("a1", "a2"), ("b1", "b2"), ((11, 7),), 1, DBL),
    ]


LEDGER_CONFIGS: dict[str, Config] = {
    "default": Config(),
    "strict": Config(
        reject_content_duplicates=True,
        max_singles_rating_gap=0.01,
        max_doubles_partner_gap=0.3,
        self_reported_max_repeats=1,
    ),
    "nofeed": Config(category_feeds_parent=False, self_reported_requires_both_teams=False),
}


def _seeded(cfg: Config) -> RatingEngine:
    engine = RatingEngine(cfg)
    snap = ExternalRatingSnapshot("ext", 4.4, D0 + timedelta(days=3), 70.0, "x-c1", "doc")
    engine.seed_external_rating("c1", RatingFormat.DOUBLES, snap)
    engine.seed_external_rating(
        "d1", RatingFormat.DOUBLES, dataclasses.replace(snap, rating=3.1, external_player_id=None)
    )
    return engine


LEDGER_EXPECTED: dict[str, list[str]] = {
    "default": [
        "ok 4443ad1e39f5b45f",
        "ok 7c627d5f985e5e20",
        "ok 818e837fac2bf0c5",
        "ok ef115ba577950ae5",
        "ok 6a2de81204c2812b",
        "ok 923b570cf9703970",
        "ok da540ff01436c482",
        "ok c06b545ad09f52ed",
        "ok 44d27dbfd532fdc4",
        "ok a0ce0ebf305b48c2",
        "ok aa428d91eb84e1a6",
        "6cc4665ee08f8846",
    ],
    "nofeed": [
        "ok 4443ad1e39f5b45f",
        "ok 7c627d5f985e5e20",
        "ok c046edab0922134c",
        "ok a046ea8bee49978c",
        "ok 43e5802073b5b237",
        "ok 391a4883e5c02b25",
        "ok da540ff01436c482",
        "ok c06b545ad09f52ed",
        "ok cae2a6fa5e2f0a91",
        "ok a0ce0ebf305b48c2",
        "ok e6fb962f2ef35827",
        "32fd2be78acec855",
    ],
    "strict": [
        "ok 4443ad1e39f5b45f",
        "ok 7c627d5f985e5e20",
        "ok 818e837fac2bf0c5",
        "ok ef115ba577950ae5",
        "ok 83f96bc7dc2c65ae",
        "ok 923b570cf9703970",
        "ok 5199f201fc6d9676",
        "ok 5199f201fc6d9676",
        "ok 60a5ec004d6a1c03",
        "ok a0ce0ebf305b48c2",
        "ok 562cdf7d93bb0811",
        "770bc448043ad72e",
    ],
}


@pytest.mark.parametrize("cfg_name", sorted(LEDGER_CONFIGS))
def test_live_records_keep_their_results(cfg_name: str) -> None:
    engine = _seeded(LEDGER_CONFIGS[cfg_name])
    outcomes = [_outcome(lambda m=m: engine.record(m)) for m in _ledger()]  # type: ignore[misc]
    outcomes.append(_snapshot_digest(engine))
    assert outcomes == LEDGER_EXPECTED[cfg_name]


REPLAY_EXPECTED: dict[str, str] = {
    "default": "07a10ebd239d8f76 07a10ebd239d8f76",
    "nofeed": "0d3fdab8f845a6da 0d3fdab8f845a6da",
    "strict": "3c7105c3c64b64f7 3c7105c3c64b64f7",
}


@pytest.mark.parametrize("cfg_name", sorted(LEDGER_CONFIGS))
def test_replay_and_resume_keep_their_results(cfg_name: str) -> None:
    cfg = LEDGER_CONFIGS[cfg_name]
    seeds = list(_seeded(cfg).players.values())
    rows = _ledger()
    full = replay(rows, seeds, cfg=cfg, as_of=D0 + timedelta(days=10))
    cut = D0 + timedelta(days=4)
    half = replay([m for m in rows if m.played_on <= cut], seeds, cfg=cfg, as_of=cut)
    resumed = replay(rows, cfg=cfg, as_of=D0 + timedelta(days=10), from_snapshot=half.to_snapshot())
    outcome = " ".join((_snapshot_digest(full), _snapshot_digest(resumed)))
    assert outcome == REPLAY_EXPECTED[cfg_name]


# ---------------------------------------------------------------------------
# seed_external_rating: two failures for each adjacent pair of checks
# ---------------------------------------------------------------------------

GOOD_SNAPSHOT = ExternalRatingSnapshot("ext", 4.0, D0 + timedelta(days=20), 50.0, "x-new", "doc")


def _snap(**kw: object) -> ExternalRatingSnapshot:
    return dataclasses.replace(GOOD_SNAPSHOT, **kw)  # type: ignore[arg-type]


# (player id, format, snapshot, replace_unplayed_seed)
SEED_CASES: dict[str, tuple[object, object, object, object]] = {
    "format then flag": ("new", "doubles", GOOD_SNAPSHOT, 1),
    "flag then snapshot type": ("new", DBL, "x", 1),
    "snapshot type then player id": ("", DBL, "x", False),
    "date then source type": ("new", DBL, _snap(captured_on="2025-01-01", source=5), False),
    "source type then external id type": ("new", DBL, _snap(source=5, external_player_id=5), False),
    "external id type then evidence type": (
        "new",
        DBL,
        _snap(external_player_id=5, evidence_reference=5),
        False,
    ),
    "evidence type then blank source": ("new", DBL, _snap(evidence_reference=5, source=" "), False),
    "blank source then reserved source": ("new", DBL, _snap(source=""), False),
    "reserved source then blank external id": (
        "new",
        DBL,
        _snap(source="open_x", external_player_id=" "),
        False,
    ),
    "blank external id then blank evidence": (
        "new",
        DBL,
        _snap(external_player_id="", evidence_reference=""),
        False,
    ),
    "blank evidence then rating type": (
        "new",
        DBL,
        _snap(evidence_reference=" ", rating="3"),
        False,
    ),
    "rating type then reliability type": (
        "new",
        DBL,
        _snap(rating=float("nan"), reliability="x"),
        False,
    ),
    "reliability type then rating range": ("new", DBL, _snap(reliability=True, rating=9.0), False),
    "rating range then reliability range": ("new", DBL, _snap(rating=1.0, reliability=101), False),
    "reliability range then player id": (5, DBL, _snap(reliability=-1), False),
    "player id then played rating": ("", DBL, GOOD_SNAPSHOT, False),
    "played rating then counted match": ("a1", DBL, GOOD_SNAPSHOT, True),
    "derived state then counted match": ("y9", MIX, GOOD_SNAPSHOT, True),
    "frozen counted match then category": ("q3", DBL, _snap(external_player_id=None), True),
    "category then seed sensitive": ("r1", DBL, _snap(external_player_id=None), True),
    "seed sensitive then replace flag": ("q3", DBL, _snap(external_player_id=None), False),
    "replace flag then identity": ("q5", DBL, _snap(external_player_id="x-c1"), False),
    "identity linked elsewhere": ("z9", DBL, _snap(external_player_id="x-c1"), False),
    "identity differs": ("q5", DBL, _snap(external_player_id="x-other"), True),
    "replace unplayed seed": ("q5", DBL, _snap(external_player_id=None), True),
    "new player": ("z9", MIX, GOOD_SNAPSHOT, False),
    "doubles seed refreshes category": ("y9", DBL, GOOD_SNAPSHOT, True),
}

SEED_EXPECTED: dict[str, dict[str, str]] = {
    "default": {
        "blank evidence then rating type": "InvalidSeedError: External snapshot "
        "evidence_reference must not be blank "
        "22dc2b7dd7e2de51",
        "blank external id then blank evidence": "InvalidSeedError: External "
        "snapshot external_player_id "
        "must not be blank "
        "22dc2b7dd7e2de51",
        "blank source then reserved source": "InvalidSeedError: External snapshot "
        "source must not be blank "
        "22dc2b7dd7e2de51",
        "category then seed sensitive": "InvalidSeedError: Cannot seed a rating "
        "that a counted mixed_doubles match for "
        "this player depends on 22dc2b7dd7e2de51",
        "date then source type": "InvalidSeedError: External snapshot captured_on "
        "must be a date 22dc2b7dd7e2de51",
        "derived state then counted match": "InvalidSeedError: Cannot replace a "
        "rating derived from OPEN overall "
        "doubles 22dc2b7dd7e2de51",
        "doubles seed refreshes category": "ok dd787c82c18debe7 b55e674fb3ec72c3",
        "evidence type then blank source": "InvalidSeedError: External snapshot "
        "evidence_reference must be a str or "
        "None 22dc2b7dd7e2de51",
        "external id type then evidence type": "InvalidSeedError: External "
        "snapshot external_player_id must "
        "be a str or None 22dc2b7dd7e2de51",
        "flag then snapshot type": "InvalidSeedError: replace_unplayed_seed must "
        "be a bool, not int 22dc2b7dd7e2de51",
        "format then flag": "InvalidSeedError: rating_format must be a "
        "RatingFormat 22dc2b7dd7e2de51",
        "frozen counted match then category": "InvalidSeedError: Cannot seed a "
        "rating format in which OPEN "
        "matches have counted for this "
        "player 22dc2b7dd7e2de51",
        "identity differs": "PlayerError: A different external identity is "
        "already linked 22dc2b7dd7e2de51",
        "identity linked elsewhere": "PlayerError: This external identity is "
        "already linked to another player "
        "22dc2b7dd7e2de51",
        "new player": "ok dd787c82c18debe7 582893def6711912",
        "played rating then counted match": "InvalidSeedError: Cannot replace an "
        "OPEN rating after OPEN matches have "
        "been processed 22dc2b7dd7e2de51",
        "player id then played rating": "PlayerError: player_id is required 22dc2b7dd7e2de51",
        "rating range then reliability range": "InvalidSeedError: External rating "
        "is outside the configured scale "
        "22dc2b7dd7e2de51",
        "rating type then reliability type": "InvalidSeedError: External rating "
        "must be a finite number "
        "22dc2b7dd7e2de51",
        "reliability range then player id": "InvalidSeedError: External "
        "reliability must be between 0 and "
        "100 22dc2b7dd7e2de51",
        "reliability type then rating range": "InvalidSeedError: External "
        "reliability must be a finite "
        "number 22dc2b7dd7e2de51",
        "replace flag then identity": "InvalidSeedError: A rating already exists; "
        "explicitly allow replacing an unplayed "
        "seed 22dc2b7dd7e2de51",
        "replace unplayed seed": "ok 4d91663e649d1b30 5a7c57398c06c89b",
        "reserved source then blank external id": "InvalidSeedError: External "
        "snapshot source must not start "
        "with open_ 22dc2b7dd7e2de51",
        "seed sensitive then replace flag": "InvalidSeedError: Cannot seed a "
        "rating format in which OPEN matches "
        "have counted for this player "
        "22dc2b7dd7e2de51",
        "snapshot type then player id": "InvalidSeedError: snapshot must be an "
        "ExternalRatingSnapshot 22dc2b7dd7e2de51",
        "source type then external id type": "InvalidSeedError: External snapshot "
        "source must be a str "
        "22dc2b7dd7e2de51",
    },
    "strict": {
        "blank evidence then rating type": "InvalidSeedError: External snapshot "
        "evidence_reference must not be blank "
        "a3281978f97e2062",
        "blank external id then blank evidence": "InvalidSeedError: External "
        "snapshot external_player_id must "
        "not be blank a3281978f97e2062",
        "blank source then reserved source": "InvalidSeedError: External snapshot "
        "source must not be blank "
        "a3281978f97e2062",
        "category then seed sensitive": "InvalidSeedError: Cannot seed a rating "
        "that a counted mixed_doubles match for "
        "this player depends on a3281978f97e2062",
        "date then source type": "InvalidSeedError: External snapshot captured_on "
        "must be a date a3281978f97e2062",
        "derived state then counted match": "InvalidSeedError: Cannot replace a "
        "rating derived from OPEN overall "
        "doubles a3281978f97e2062",
        "doubles seed refreshes category": "ok dd787c82c18debe7 e54e313eecdf42da",
        "evidence type then blank source": "InvalidSeedError: External snapshot "
        "evidence_reference must be a str or "
        "None a3281978f97e2062",
        "external id type then evidence type": "InvalidSeedError: External "
        "snapshot external_player_id must "
        "be a str or None a3281978f97e2062",
        "flag then snapshot type": "InvalidSeedError: replace_unplayed_seed must "
        "be a bool, not int a3281978f97e2062",
        "format then flag": "InvalidSeedError: rating_format must be a "
        "RatingFormat a3281978f97e2062",
        "frozen counted match then category": "InvalidSeedError: Cannot seed: a "
        "rejected doubles match that named "
        "this player (m20) could count with "
        "a new seed; a new seed for this "
        "player needs a full replay from "
        "corrected seeds a3281978f97e2062",
        "identity differs": "PlayerError: A different external identity is already "
        "linked a3281978f97e2062",
        "identity linked elsewhere": "PlayerError: This external identity is "
        "already linked to another player "
        "a3281978f97e2062",
        "new player": "ok dd787c82c18debe7 3ea718984a4caf07",
        "played rating then counted match": "InvalidSeedError: Cannot replace an "
        "OPEN rating after OPEN matches have "
        "been processed a3281978f97e2062",
        "player id then played rating": "PlayerError: player_id is required a3281978f97e2062",
        "rating range then reliability range": "InvalidSeedError: External rating "
        "is outside the configured scale "
        "a3281978f97e2062",
        "rating type then reliability type": "InvalidSeedError: External rating "
        "must be a finite number "
        "a3281978f97e2062",
        "reliability range then player id": "InvalidSeedError: External "
        "reliability must be between 0 and 100 "
        "a3281978f97e2062",
        "reliability type then rating range": "InvalidSeedError: External "
        "reliability must be a finite number "
        "a3281978f97e2062",
        "replace flag then identity": "InvalidSeedError: A rating already exists; "
        "explicitly allow replacing an unplayed seed "
        "a3281978f97e2062",
        "replace unplayed seed": "ok 4d91663e649d1b30 a66daa713df2e6d2",
        "reserved source then blank external id": "InvalidSeedError: External "
        "snapshot source must not start "
        "with open_ a3281978f97e2062",
        "seed sensitive then replace flag": "InvalidSeedError: Cannot seed: a "
        "rejected doubles match that named "
        "this player (m20) could count with a "
        "new seed; a new seed for this player "
        "needs a full replay from corrected "
        "seeds a3281978f97e2062",
        "snapshot type then player id": "InvalidSeedError: snapshot must be an "
        "ExternalRatingSnapshot a3281978f97e2062",
        "source type then external id type": "InvalidSeedError: External snapshot "
        "source must be a str "
        "a3281978f97e2062",
    },
}


def _seed_engine(cfg_name: str) -> RatingEngine:
    engine = _seeded(LEDGER_CONFIGS[cfg_name])
    for match in _ledger():
        engine.record(match)
    engine.seed_external_rating("y9", DBL, _snap(external_player_id=None))
    held = engine.players["y9"]
    engine._put_state(held, MIX, copy.deepcopy(engine.state("y9", MIX)))
    # q5: an unplayed seed, linked to x-q5.
    engine.seed_external_rating("q5", DBL, _snap(external_player_id="x-q5"))
    # q3: frozen in a counted doubles match, so still unplayed.
    later = _snap(captured_on=D0 + timedelta(days=30), external_player_id=None)
    engine.seed_external_rating("q3", DBL, later)
    engine.record(_match("m20", ("q3", "a1"), ("b1", "b2"), ((11, 6),), 25, DBL))
    # r1 to r4: a counted mixed match whose doubles update every player is frozen in.
    provisional = RatingState(rating=3.5, rd=0.8, initial_rating=3.5)
    for pid in ("r1", "r2", "r3", "r4"):
        seed = _snap(captured_on=D0 + timedelta(days=40), external_player_id=None)
        engine.seed_external_rating(pid, DBL, seed)
        engine._put_state(engine.players[pid], MIX, copy.deepcopy(provisional))
    engine.record(_match("m21", ("r1", "r2"), ("r3", "r4"), ((11, 6),), 35, MIX))
    return engine


@pytest.mark.parametrize("cfg_name", ["default", "strict"])
@pytest.mark.parametrize("name", sorted(SEED_CASES))
def test_seed_external_rating_keeps_its_check_order(cfg_name: str, name: str) -> None:
    engine = _seed_engine(cfg_name)
    pid, fmt, snap, flag = SEED_CASES[name]
    before = _snapshot_digest(engine)
    outcome = _outcome(
        lambda: engine.seed_external_rating(pid, fmt, snap, replace_unplayed_seed=flag)  # type: ignore[arg-type]
    )
    after = _snapshot_digest(engine)
    if not outcome.startswith("ok "):
        assert after == before
    assert f"{outcome} {after}" == SEED_EXPECTED[cfg_name][name]


# ---------------------------------------------------------------------------
# from_snapshot: two failures for each adjacent pair of read steps
# ---------------------------------------------------------------------------


def _base_snapshot() -> dict[str, object]:
    cfg = LEDGER_CONFIGS["strict"]
    seeds = list(_seeded(cfg).players.values())
    return replay(_ledger(), seeds, cfg=cfg, as_of=D0 + timedelta(days=10)).to_snapshot()


def _mutate(snap: dict[str, object], name: str) -> dict[str, object]:
    snap = copy.deepcopy(snap)
    players = snap["players"]
    assert isinstance(players, list)
    edits: dict[str, Callable[[], None]] = {
        "version": lambda: snap.update(snapshot_version=7),
        "engine version": lambda: snap.update(engine_version=5),
        "config version": lambda: snap.update(config_version="0.0.0"),
        "fingerprint": lambda: snap.update(config_fingerprint="0" * 16),
        "calculated_as_of": lambda: snap.update(calculated_as_of="x"),
        "replay_info": lambda: snap.update(replay_info=[]),
        "players": lambda: snap.update(players={}),
        "duplicate player": lambda: players.append(copy.deepcopy(players[0])),
        "processed": lambda: snap.update(processed_match_ids="x"),
        "reserved": lambda: snap.update(reserved_match_ids="x"),
        "log": lambda: snap.update(self_reported_log={}),
        "content_keys": lambda: snap.update(content_keys=[]),
        "resumable": lambda: snap.update(resumable=1),
        "digests": lambda: snap.update(ledger_digests=[]),
        "digest value": lambda: snap.update(ledger_digests={"m01": "x"}),
        "ledger players": lambda: snap.update(ledger_player_ids={}),
        "possible duplicates": lambda: snap.update(possible_duplicates=[]),
        "seed sensitive": lambda: snap.update(seed_sensitive_rejections=[]),
        "drop processed": lambda: snap.update(processed_match_ids=[]),
        "drop digests": lambda: snap.update(ledger_digests={}),
    }
    for part in name.split(" + "):
        edits[part]()
    return snap


SNAPSHOT_STEPS = [
    "version",
    "engine version",
    "config version",
    "fingerprint",
    "calculated_as_of",
    "replay_info",
    "players",
    "duplicate player",
    "processed",
    "reserved",
    "log",
    "content_keys",
    "resumable",
    "digests",
    "digest value",
    "ledger players",
    "possible duplicates",
    "seed sensitive",
    "drop processed",
    "drop digests",
]
SNAPSHOT_CASES = (
    ["base"]
    + [f"{a} + {b}" for a, b in zip(SNAPSHOT_STEPS, SNAPSHOT_STEPS[1:], strict=False)]
    + SNAPSHOT_STEPS[-3:]
)
SNAPSHOT_EXPECTED: dict[str, str] = {
    "base": "engine 3c7105c3c64b64f7",
    "calculated_as_of + replay_info": "SnapshotError: snapshot.calculated_as_of must be a "
    "date in YYYY-MM-DD form",
    "config version + fingerprint": "SnapshotError: snapshot.config_version '0.0.0' does "
    "not match the Config ('0.3.0.dev0')",
    "content_keys + resumable": "SnapshotError: snapshot.content_keys must be an object",
    "digest value + ledger players": "SnapshotError: snapshot.ledger_digests['m01'] must "
    "be 64 lowercase hex characters",
    "digests + digest value": "SnapshotError: snapshot.ledger_digests['m01'] must be 64 "
    "lowercase hex characters",
    "drop digests": "SnapshotError: snapshot.possible_duplicates has key 'm05', which is "
    "not in ledger_digests",
    "drop processed": "SnapshotError: "
    "snapshot.content_keys['2647295ebe6f68269ef0a2604c0968badc12c4365e143a566959866f5980c0b3'] "
    "names 'm10', which is not in processed_match_ids",
    "drop processed + drop digests": "SnapshotError: "
    "snapshot.content_keys['2647295ebe6f68269ef0a2604c0968badc12c4365e143a566959866f5980c0b3'] "
    "names 'm10', which is not in processed_match_ids",
    "duplicate player + processed": "SnapshotError: snapshot.players[7]: duplicate player id 'a1'",
    "engine version + config version": "SnapshotError: snapshot.engine_version must be a str",
    "fingerprint + calculated_as_of": "SnapshotError: snapshot.config_fingerprint "
    "'0000000000000000' does not match the Config "
    "('0b658532e39b53e2')",
    "ledger players + possible duplicates": "SnapshotError: snapshot.ledger_player_ids "
    "must be a list",
    "log + content_keys": "SnapshotError: snapshot.self_reported_log must be a list",
    "players + duplicate player": "SnapshotError: snapshot.players must be a list",
    "possible duplicates + seed sensitive": "SnapshotError: snapshot.possible_duplicates "
    "must be an object",
    "processed + reserved": "SnapshotError: snapshot.processed_match_ids must be a list",
    "replay_info + players": "SnapshotError: snapshot.replay_info must be an object",
    "reserved + log": "SnapshotError: snapshot.reserved_match_ids must be a list",
    "resumable + digests": "SnapshotError: snapshot.resumable must be a bool",
    "seed sensitive": "SnapshotError: snapshot.seed_sensitive_rejections must be an object",
    "seed sensitive + drop processed": "SnapshotError: "
    "snapshot.content_keys['2647295ebe6f68269ef0a2604c0968badc12c4365e143a566959866f5980c0b3'] "
    "names 'm10', which is not in processed_match_ids",
    "version + engine version": "SnapshotError: snapshot.snapshot_version must be 8, not 7",
}


@pytest.mark.parametrize("name", SNAPSHOT_CASES)
def test_from_snapshot_keeps_its_check_order(name: str) -> None:
    base = _base_snapshot()
    snap = base if name == "base" else _mutate(base, name)
    cfg = LEDGER_CONFIGS["strict"]
    assert _outcome(lambda: RatingEngine.from_snapshot(snap, cfg)) == SNAPSHOT_EXPECTED[name]


# ---------------------------------------------------------------------------
# reliability: state, then cfg, then as_of
# ---------------------------------------------------------------------------

STATE = RatingState(rating=4.0, rd=0.5, rd_as_of=D0, partners={"a": D0}, opponents={"b": D0})
RELIABILITY_CASES: dict[str, tuple[object, object, object]] = {
    "state then cfg": (dataclasses.replace(STATE, rd="x"), "x", "x"),  # type: ignore[arg-type]
    "cfg then as_of": (STATE, "x", "x"),
    "as_of": (STATE, Config(), "x"),
    "partners then cfg": (dataclasses.replace(STATE, partners={5: D0}), None, "x"),  # type: ignore[dict-item]
    "too large": (dataclasses.replace(STATE, rd=1e308), Config(), D0 + timedelta(days=9)),
    "value": (STATE, Config(), D0 + timedelta(days=40)),
}
RELIABILITY_EXPECTED: dict[str, str] = {
    "as_of": "ReferenceDateError: reliability as_of must be a date, not str",
    "cfg then as_of": "ConfigError: cfg must be exactly a Config, not str",
    "partners then cfg": "EvaluationError: partners must map str ids to dates",
    "state then cfg": "EvaluationError: rd must be an int or float, not str",
    "too large": "EvaluationError: rd is too large to compute reliability",
    "value": "ok 9eee089ea6a56ddd",
}


@pytest.mark.parametrize("name", sorted(RELIABILITY_CASES))
def test_reliability_keeps_its_check_order(name: str) -> None:
    state, cfg, as_of = RELIABILITY_CASES[name]
    outcome = _outcome(lambda: reliability(state, cfg, as_of))  # type: ignore[arg-type]
    assert outcome == RELIABILITY_EXPECTED[name]
