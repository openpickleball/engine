"""The same match under different match ids is flagged or rejected (issue #21).

All player and match ids are synthetic.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest

from open_engine import (
    Config,
    ConfigError,
    GameFormat,
    Match,
    MatchType,
    RatingEngine,
    RatingFormat,
    RejectionReason,
    ScoringType,
    SnapshotError,
    replay,
)

D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
ON = date(2026, 3, 1)
LATER = date(2026, 4, 1)
TEAM_A = ("syn-a", "syn-b")
TEAM_B = ("syn-c", "syn-d")
BOTH = frozenset(TEAM_A + TEAM_B)


def _m(
    match_id: str,
    team_a: tuple[str, ...] = TEAM_A,
    team_b: tuple[str, ...] = TEAM_B,
    games: tuple[tuple[int, int], ...] = ((11, 7),),
    on: date = ON,
    fmt: RatingFormat = D,
    **kwargs: object,
) -> Match:
    return Match(match_id, team_a, team_b, games, on, fmt, **kwargs)  # type: ignore[arg-type]


def _reject() -> Config:
    return Config(reject_content_duplicates=True)


ORIGINAL = _m("syn-m1")
BEST_OF_3 = _m("syn-m1", games=((11, 7), (5, 11), (11, 9)), best_of=3)

# (first, second): the second is a likely duplicate of the first.
DUPLICATES: list[tuple[str, Match, Match]] = [
    ("same sides", ORIGINAL, _m("syn-m2")),
    ("sides swapped", ORIGINAL, _m("syn-m2", TEAM_B, TEAM_A, ((7, 11),))),
    ("in-team order", ORIGINAL, _m("syn-m2", ("syn-b", "syn-a"), ("syn-d", "syn-c"))),
    (
        "swapped and reordered",
        ORIGINAL,
        _m("syn-m2", ("syn-d", "syn-c"), ("syn-b", "syn-a"), ((7, 11),)),
    ),
    (
        "other labels",
        ORIGINAL,
        _m(
            "syn-m2",
            match_type=MatchType.TOURNAMENT,
            played_at=datetime(2026, 3, 1, 15, 30, tzinfo=UTC),
            sequence=4,
        ),
    ),
    (
        "best of 3 swapped",
        BEST_OF_3,
        _m("syn-m2", TEAM_B, TEAM_A, ((7, 11), (11, 5), (9, 11)), best_of=3),
    ),
]
DUP_IDS = [case[0] for case in DUPLICATES]
DUP_PAIRS = [case[1:] for case in DUPLICATES]

NOT_DUPLICATES: list[tuple[str, Match]] = [
    ("other date", _m("syn-m2", on=date(2026, 3, 2))),
    ("other player on team a", _m("syn-m2", ("syn-a", "syn-e"))),
    ("other player on team b", _m("syn-m2", TEAM_A, ("syn-c", "syn-e"))),
    ("other scores", _m("syn-m2", games=((11, 8),))),
    ("other side won", _m("syn-m2", TEAM_B, TEAM_A, ((11, 7),))),
]


def _content_key(on: date, team_1: list[str], team_2: list[str], games: list[list[int]]) -> str:
    text = json.dumps([on.isoformat(), team_1, team_2, games], separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


# -- flag (default) ------------------------------------------------------------


@pytest.mark.parametrize(("first", "second"), DUP_PAIRS, ids=DUP_IDS)
def test_default_flags_a_live_duplicate_and_counts_it(first: Match, second: Match) -> None:
    eng = RatingEngine()
    one = eng.record(first)
    two = eng.record(second)
    assert one.counted and one.possible_duplicate_of is None
    assert two.counted is True
    assert two.reason is None
    assert two.possible_duplicate_of == first.match_id
    assert second.match_id in eng.processed_match_ids


@pytest.mark.parametrize(("first", "second"), DUP_PAIRS, ids=DUP_IDS)
def test_default_counts_a_duplicate_in_replay(first: Match, second: Match) -> None:
    eng = replay([second, first], as_of=LATER)
    assert eng.replay_info is not None
    assert eng.replay_info.matches_counted == 2
    content_keys = eng.to_snapshot()["content_keys"]
    assert isinstance(content_keys, dict)
    assert list(content_keys.values()) == [first.match_id]


def test_default_deltas_equal_the_values_before_the_change() -> None:
    # Measured on main before issue #21: the default config only flags.
    eng = RatingEngine()
    one = eng.record(_m("dup", ("a", "b"), ("c", "d"), ((11, 0),)))
    two = eng.record(_m("dup2", ("c", "d"), ("b", "a"), ((0, 11),)))
    assert one.deltas == {"a": 0.225, "b": 0.225, "c": -0.225, "d": -0.225}
    assert two.deltas == {"a": 0.17507, "b": 0.17507, "c": -0.17507, "d": -0.17507}
    assert two.possible_duplicate_of == "dup"


# -- reject --------------------------------------------------------------------


@pytest.mark.parametrize(("first", "second"), DUP_PAIRS, ids=DUP_IDS)
def test_reject_does_not_count_a_live_duplicate(first: Match, second: Match) -> None:
    eng = RatingEngine(_reject())
    assert eng.record(first).counted
    before = copy.deepcopy(eng.players)
    two = eng.record(second)
    assert two.counted is False
    assert two.reason is RejectionReason.POSSIBLE_DUPLICATE
    assert two.possible_duplicate_of == first.match_id
    assert two.deltas == {}
    assert eng.players == before
    assert second.match_id not in eng.processed_match_ids


@pytest.mark.parametrize(("first", "second"), DUP_PAIRS, ids=DUP_IDS)
def test_reject_does_not_count_a_duplicate_in_replay(first: Match, second: Match) -> None:
    cfg = _reject()
    both = replay([second, first], cfg=cfg, as_of=LATER)
    alone = replay([first], cfg=cfg, as_of=LATER)
    assert both.replay_info is not None
    assert both.replay_info.matches_considered == 2
    assert both.replay_info.matches_counted == 1
    assert both.processed_match_ids == {first.match_id}
    assert both.players == alone.players


def test_rejection_reason_value() -> None:
    assert RejectionReason.POSSIBLE_DUPLICATE == "possible_duplicate"


# -- not duplicates ------------------------------------------------------------


@pytest.mark.parametrize("cfg_factory", [Config, _reject], ids=["default", "reject"])
@pytest.mark.parametrize(
    "second", [case[1] for case in NOT_DUPLICATES], ids=[case[0] for case in NOT_DUPLICATES]
)
def test_other_matches_are_not_flagged(cfg_factory: Callable[[], Config], second: Match) -> None:
    eng = RatingEngine(cfg_factory())
    eng.record(ORIGINAL)
    result = eng.record(second)
    assert result.counted is True
    assert result.possible_duplicate_of is None


@pytest.mark.parametrize("cfg_factory", [Config, _reject], ids=["default", "reject"])
def test_a_twin_rejected_as_unconfirmed_is_not_a_target(
    cfg_factory: Callable[[], Config],
) -> None:
    eng = RatingEngine(cfg_factory())
    first = eng.record(_m("syn-m1", match_type=MatchType.SELF_REPORTED))
    assert first.reason is RejectionReason.SELF_REPORTED_UNCONFIRMED
    second = eng.record(_m("syn-m2"))
    assert second.counted is True
    assert second.possible_duplicate_of is None


def _side_out_only(reject: bool) -> Config:
    return Config(
        allowed_game_formats=frozenset({(ScoringType.SIDE_OUT, 11)}),
        reject_content_duplicates=reject,
    )


@pytest.mark.parametrize("reject", [False, True], ids=["default", "reject"])
def test_a_twin_rejected_for_its_game_format_is_not_a_target(reject: bool) -> None:
    eng = RatingEngine(_side_out_only(reject))
    first = eng.record(_m("syn-m1", game_format=GameFormat(ScoringType.RALLY)))
    assert first.reason is RejectionReason.GAME_FORMAT_NOT_ACCEPTED
    second = eng.record(_m("syn-m2"))
    assert second.counted is True
    assert second.possible_duplicate_of is None


# -- third copy, categories, and other rejections -------------------------------


@pytest.mark.parametrize("reject", [False, True], ids=["default", "reject"])
def test_a_third_copy_points_to_the_first(reject: bool) -> None:
    eng = RatingEngine(Config(reject_content_duplicates=reject))
    eng.record(ORIGINAL)
    two = eng.record(_m("syn-m2", TEAM_B, TEAM_A, ((7, 11),)))
    three = eng.record(_m("syn-m3"))
    assert two.possible_duplicate_of == "syn-m1"
    assert three.possible_duplicate_of == "syn-m1"
    assert three.counted is not reject


def test_category_copy_is_flagged_and_its_parent_is_not() -> None:
    eng = RatingEngine()
    eng.record(ORIGINAL)
    result = eng.record(_m("syn-m2", fmt=MX))
    assert result.counted is True
    assert result.possible_duplicate_of == "syn-m1"
    assert result.parent is not None
    assert result.parent.possible_duplicate_of is None


def test_category_original_parent_is_not_flagged() -> None:
    eng = RatingEngine()
    first = eng.record(_m("syn-m1", fmt=MX))
    second = eng.record(_m("syn-m2", fmt=MX))
    assert first.parent is not None and first.parent.possible_duplicate_of is None
    assert second.possible_duplicate_of == "syn-m1"
    assert second.parent is not None and second.parent.possible_duplicate_of is None


def test_reject_category_copy_has_no_parent() -> None:
    eng = RatingEngine(_reject())
    eng.record(ORIGINAL)
    result = eng.record(_m("syn-m2", fmt=MX))
    assert result.reason is RejectionReason.POSSIBLE_DUPLICATE
    assert result.parent is None
    assert eng.players["syn-a"].ratings.keys() == {D}


@pytest.mark.parametrize("cfg_factory", [Config, _reject], ids=["default", "reject"])
def test_a_repeated_match_id_is_not_flagged(cfg_factory: Callable[[], Config]) -> None:
    eng = RatingEngine(cfg_factory())
    eng.record(ORIGINAL)
    again = eng.record(ORIGINAL)
    assert again.reason is RejectionReason.DUPLICATE_MATCH_ID
    assert again.possible_duplicate_of is None


@pytest.mark.parametrize("reject", [False, True], ids=["default", "reject"])
def test_a_duplicate_rejected_for_its_game_format_is_not_flagged(reject: bool) -> None:
    eng = RatingEngine(_side_out_only(reject))
    eng.record(ORIGINAL)
    result = eng.record(_m("syn-m2", game_format=GameFormat(ScoringType.RALLY)))
    assert result.reason is RejectionReason.GAME_FORMAT_NOT_ACCEPTED
    assert result.possible_duplicate_of is None


def test_default_duplicate_rejected_as_unconfirmed_is_not_flagged() -> None:
    eng = RatingEngine()
    eng.record(ORIGINAL)
    result = eng.record(_m("syn-m2", match_type=MatchType.SELF_REPORTED))
    assert result.reason is RejectionReason.SELF_REPORTED_UNCONFIRMED
    assert result.possible_duplicate_of is None


# -- self-reported repeat log --------------------------------------------------


def _self_reported(match_id: str) -> Match:
    return _m(match_id, match_type=MatchType.SELF_REPORTED, confirmed_by=BOTH)


def test_default_self_reported_duplicate_enters_the_repeat_log() -> None:
    eng = RatingEngine()
    eng.record(_self_reported("syn-m1"))
    result = eng.record(_self_reported("syn-m2"))
    assert result.counted is True
    assert result.possible_duplicate_of == "syn-m1"
    assert list(eng.self_reported_log.values()) == [[ON, ON]]


def test_reject_self_reported_duplicate_stays_out_of_the_repeat_log() -> None:
    eng = RatingEngine(_reject())
    eng.record(_self_reported("syn-m1"))
    result = eng.record(_self_reported("syn-m2"))
    assert result.reason is RejectionReason.POSSIBLE_DUPLICATE
    assert list(eng.self_reported_log.values()) == [[ON]]


# -- determinism ---------------------------------------------------------------

LEDGER = [
    ORIGINAL,
    _m("syn-m2", TEAM_B, TEAM_A, ((7, 11),)),
    _m("syn-m3", ("syn-b", "syn-a")),
    _m("syn-m4", games=((11, 8),)),
    _m("syn-m5", on=date(2026, 3, 2)),
    _m("syn-m6", fmt=MX),
    _m("syn-m7", TEAM_B, TEAM_A, ((11, 7),)),
]


@pytest.mark.parametrize("cfg_factory", [Config, _reject], ids=["default", "reject"])
def test_replay_gives_the_same_result_in_any_input_order(
    cfg_factory: Callable[[], Config],
) -> None:
    cfg = cfg_factory()
    expected = replay(LEDGER, cfg=cfg, as_of=LATER).to_snapshot()
    for order in (LEDGER[::-1], LEDGER[3:] + LEDGER[:3], LEDGER[1::2] + LEDGER[::2]):
        assert replay(order, cfg=cfg, as_of=LATER).to_snapshot() == expected


def test_reject_replay_counts_only_the_first_of_each_content() -> None:
    eng = replay(LEDGER[::-1], cfg=_reject(), as_of=LATER)
    assert eng.processed_match_ids == {"syn-m1", "syn-m4", "syn-m5", "syn-m7"}


# -- config --------------------------------------------------------------------


def test_reject_content_duplicates_defaults_to_false() -> None:
    assert Config().reject_content_duplicates is False


@pytest.mark.parametrize("bad", [1, 0, "yes", None], ids=repr)
def test_reject_content_duplicates_must_be_a_bool(bad: object) -> None:
    with pytest.raises(ConfigError, match="reject_content_duplicates must be a bool"):
        Config(reject_content_duplicates=bad)


def test_reject_content_duplicates_changes_the_fingerprint() -> None:
    assert _reject().fingerprint() != Config().fingerprint()


# -- snapshot ------------------------------------------------------------------

Snap = dict[str, object]


@pytest.fixture
def snapshot() -> Snap:
    eng = replay([ORIGINAL, _m("syn-m2", TEAM_B, TEAM_A, ((7, 11),))], as_of=LATER)
    loaded: Snap = json.loads(json.dumps(eng.to_snapshot(), allow_nan=False))
    return loaded


def test_snapshot_carries_content_keys(snapshot: Snap) -> None:
    assert snapshot["snapshot_version"] == 7
    key = _content_key(ON, list(TEAM_A), list(TEAM_B), [[11, 7]])
    assert snapshot["content_keys"] == {key: "syn-m1"}


def test_content_key_sorts_teams_and_swaps_scores() -> None:
    eng = RatingEngine()
    eng.record(_m("syn-m1", ("syn-d", "syn-c"), ("syn-b", "syn-a"), ((7, 11),)))
    key = _content_key(ON, list(TEAM_A), list(TEAM_B), [[11, 7]])
    assert eng.to_snapshot()["content_keys"] == {key: "syn-m1"}


def test_snapshot_round_trip_keeps_content_keys(snapshot: Snap) -> None:
    assert RatingEngine.from_snapshot(snapshot).to_snapshot() == snapshot


def test_live_duplicate_after_loading_is_flagged(snapshot: Snap) -> None:
    eng = RatingEngine.from_snapshot(snapshot)
    result = eng.record(_m("syn-m3", ("syn-b", "syn-a")))
    assert result.counted is True
    assert result.possible_duplicate_of == "syn-m1"


def test_live_duplicate_after_loading_is_rejected_under_reject() -> None:
    cfg = _reject()
    eng = replay([ORIGINAL], cfg=cfg, as_of=LATER)
    loaded = RatingEngine.from_snapshot(json.loads(json.dumps(eng.to_snapshot())), cfg)
    result = loaded.record(_m("syn-m3"))
    assert result.reason is RejectionReason.POSSIBLE_DUPLICATE
    assert result.possible_duplicate_of == "syn-m1"


def test_version_1_snapshot_is_refused(snapshot: Snap) -> None:
    snapshot["snapshot_version"] = 1
    with pytest.raises(SnapshotError, match="snapshot_version must be 7"):
        RatingEngine.from_snapshot(snapshot)


def _keys(snap: Snap) -> dict[str, object]:
    keys = snap["content_keys"]
    assert isinstance(keys, dict)
    return keys


def _first_key(snap: Snap) -> str:
    return next(iter(_keys(snap)))


def _not_object(snap: Snap) -> None:
    snap["content_keys"] = [_first_key(snap)]


def _missing(snap: Snap) -> None:
    del snap["content_keys"]


def _uppercase_key(snap: Snap) -> None:
    keys = _keys(snap)
    key = _first_key(snap)
    keys["A" * 64] = keys.pop(key)


def _short_key(snap: Snap) -> None:
    keys = _keys(snap)
    keys["abc"] = keys.pop(_first_key(snap))


def _blank_id(snap: Snap) -> None:
    _keys(snap)[_first_key(snap)] = " "


def _int_id(snap: Snap) -> None:
    _keys(snap)[_first_key(snap)] = 7


def _unprocessed_id(snap: Snap) -> None:
    _keys(snap)[_first_key(snap)] = "syn-m9"


def _two_keys_one_id(snap: Snap) -> None:
    _keys(snap)["0" * 64] = "syn-m1"


MALFORMED: list[tuple[str, Callable[[Snap], None], str]] = [
    ("not an object", _not_object, r"snapshot\.content_keys must be an object"),
    ("missing", _missing, r"snapshot: missing key 'content_keys'"),
    (
        "uppercase key",
        _uppercase_key,
        r"snapshot\.content_keys has a key that is not 64 lowercase hex characters",
    ),
    (
        "short key",
        _short_key,
        r"snapshot\.content_keys has a key that is not 64 lowercase hex characters",
    ),
    ("blank id", _blank_id, r"snapshot\.content_keys\[.*\] must be a non-blank str"),
    ("int id", _int_id, r"snapshot\.content_keys\[.*\] must be a non-blank str"),
    (
        "unprocessed id",
        _unprocessed_id,
        r"snapshot\.content_keys\[.*\].*not in processed_match_ids",
    ),
    ("two keys one id", _two_keys_one_id, r"snapshot\.content_keys maps more than one key to"),
]


@pytest.mark.parametrize(
    ("mutate", "message"), [case[1:] for case in MALFORMED], ids=[case[0] for case in MALFORMED]
)
def test_malformed_content_keys_raise(
    snapshot: Snap, mutate: Callable[[Snap], None], message: str
) -> None:
    mutate(snapshot)
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(snapshot)


# -- resume --------------------------------------------------------------------

CHECKPOINT = date(2026, 3, 15)
END = date(2026, 5, 1)
RESUME_LEDGER = [
    ORIGINAL,
    _m("syn-m2", TEAM_B, TEAM_A, ((7, 11),)),
    _m("syn-m3", on=LATER),
    _m("syn-m4", ("syn-b", "syn-a"), on=LATER),
    _m("syn-m5", games=((11, 9),), on=LATER),
]


@pytest.mark.parametrize("cfg_factory", [Config, _reject], ids=["default", "reject"])
@pytest.mark.parametrize("newer_only", [False, True], ids=["whole", "newer only"])
def test_resume_equals_a_full_replay(cfg_factory: Callable[[], Config], newer_only: bool) -> None:
    cfg = cfg_factory()
    full = replay(RESUME_LEDGER, cfg=cfg, as_of=END).to_snapshot()
    checkpoint = replay(RESUME_LEDGER, cfg=cfg, as_of=CHECKPOINT).to_snapshot()
    rows = [m for m in RESUME_LEDGER if m.played_on > CHECKPOINT] if newer_only else RESUME_LEDGER
    resumed = replay(rows, cfg=cfg, as_of=END, from_snapshot=checkpoint)
    assert resumed.to_snapshot() == full
    assert len(_keys(full)) == 3
