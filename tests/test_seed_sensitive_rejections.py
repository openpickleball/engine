"""The engine records seed-sensitive rejections and keeps them in snapshots (issue #99).

record keeps the players of each update it rejects for BEFORE_IMPORTED_BASELINE,
SINGLES_RATING_GAP, or DOUBLES_PARTNER_GAP, and the seed methods refuse a
seed that could change one. The record lives in the snapshot under
seed_sensitive_rejections (schema version 8), is the same live and in a
replay, and survives a resume. All ids are synthetic.
"""

from __future__ import annotations

import copy
import json
import random
from collections.abc import Callable
from datetime import date, timedelta

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    GameFormat,
    InvalidSeedError,
    Match,
    MatchStatus,
    MatchType,
    OpenEngineError,
    Player,
    PlayerError,
    RatingEngine,
    RatingFormat,
    RatingState,
    RejectionReason,
    ScoringType,
    SnapshotError,
    ledger_order,
    replay,
)

S = RatingFormat.SINGLES
D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
MEN = RatingFormat.DOUBLES_MEN
WOMEN = RatingFormat.DOUBLES_WOMEN
PLAYED = date(2026, 1, 10)
EARLY = date(2026, 1, 1)
LATE = date(2026, 1, 20)
AS_OF = date(2026, 3, 1)
REFUSED = r"rejected .* match that named this player"
SENSITIVE = {
    RejectionReason.BEFORE_IMPORTED_BASELINE,
    RejectionReason.SINGLES_RATING_GAP,
    RejectionReason.DOUBLES_PARTNER_GAP,
}

GAPS = Config(max_singles_rating_gap=1.0, max_doubles_partner_gap=1.0)
SIDE_OUT_GAPS = Config(
    max_singles_rating_gap=1.0,
    max_doubles_partner_gap=1.0,
    allowed_game_formats=frozenset({(ScoringType.SIDE_OUT, 11)}),
)

Snap = dict[str, object]


def _snap(rating: float, captured_on: date = EARLY) -> ExternalRatingSnapshot:
    return ExternalRatingSnapshot("syn-ext", rating, captured_on)


def _match(
    match_id: str,
    team_a: tuple[str, ...],
    team_b: tuple[str, ...],
    fmt: RatingFormat,
    on: date = PLAYED,
    **kwargs: object,
) -> Match:
    return Match(match_id, team_a, team_b, ((11, 6),), on, fmt, **kwargs)


def _seed(player_id: str, fmt: RatingFormat, rating: float, on: date = EARLY) -> Player:
    state = RatingState(
        rating=rating,
        rd=Config().external_seed_rd,
        rd_as_of=on,
        initial_rating=rating,
        initial_source="syn-ext",
        initial_source_date=on,
    )
    return Player(id=player_id, ratings={fmt: state})


def _json(engine: RatingEngine) -> Snap:
    loaded: Snap = json.loads(json.dumps(engine.to_snapshot(), allow_nan=False))
    return loaded


def _refused(engine: RatingEngine, call: Callable[[], object]) -> None:
    before = engine.to_snapshot()
    with pytest.raises(InvalidSeedError, match=REFUSED):
        call()
    assert engine.to_snapshot() == before


# -- recording ---------------------------------------------------------------------


def _gap_engine() -> RatingEngine:
    """One reserved singles gap, one reserved partner gap, one processed doubles parent gap."""
    engine = RatingEngine(GAPS)
    engine.seed_external_rating("syn-a", S, _snap(5.0))
    assert engine.record(_match("syn-sg", ("syn-a",), ("syn-u",), S)).reason is (
        RejectionReason.SINGLES_RATING_GAP
    )
    engine.seed_external_rating("syn-b", D, _snap(5.0))
    assert engine.record(_match("syn-pg", ("syn-b", "syn-v"), ("syn-w", "syn-x"), D)).reason is (
        RejectionReason.DOUBLES_PARTNER_GAP
    )
    for pid in ("syn-p", "syn-q", "syn-r", "syn-s"):
        engine.seed_external_rating(pid, MX, _snap(4.0))
    engine.seed_external_rating("syn-p", D, _snap(5.0))
    engine.seed_external_rating("syn-q", D, _snap(3.0))
    mixed = engine.record(_match("syn-mx", ("syn-p", "syn-q"), ("syn-r", "syn-s"), MX))
    assert mixed.counted
    assert mixed.parent is not None
    assert mixed.parent.reason is RejectionReason.DOUBLES_PARTNER_GAP
    return engine


def test_each_gap_rejection_is_recorded() -> None:
    engine = _gap_engine()
    assert engine._seed_sensitive_rejections == {
        "syn-sg": (S, ("syn-a", "syn-u")),
        "syn-pg": (D, ("syn-b", "syn-v", "syn-w", "syn-x")),
        "syn-mx": (D, ("syn-p", "syn-q", "syn-r", "syn-s")),
    }
    assert "syn-mx" in engine.processed_match_ids
    assert {"syn-sg", "syn-pg"} <= engine._reserved_match_ids


def test_a_baseline_rejection_is_recorded() -> None:
    engine = RatingEngine()
    for pid in ("syn-a", "syn-b", "syn-c", "syn-d"):
        engine.seed_external_rating(pid, D, _snap(4.0, LATE))
    engine.record(_match("syn-m1", ("syn-d", "syn-a"), ("syn-c", "syn-b"), MX))
    assert engine._seed_sensitive_rejections == {
        "syn-m1": (MX, ("syn-a", "syn-b", "syn-c", "syn-d")),
    }


def test_a_baseline_rejected_doubles_update_of_a_counted_category_match_is_recorded() -> None:
    engine = RatingEngine()
    for pid in ("syn-a", "syn-b", "syn-c", "syn-d"):
        engine.seed_external_rating(pid, MX, _snap(4.0, EARLY))
        engine.seed_external_rating(pid, D, _snap(4.0, LATE))
    result = engine.record(_match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), MX))
    assert result.counted
    assert result.parent is not None
    assert result.parent.reason is RejectionReason.BEFORE_IMPORTED_BASELINE
    assert engine._seed_sensitive_rejections == {
        "syn-m1": (D, ("syn-a", "syn-b", "syn-c", "syn-d")),
    }
    snap = _json(engine)
    assert RatingEngine.from_snapshot(snap).to_snapshot() == snap


def test_the_79_rule_refuses_a_doubles_seed_after_a_rejected_parent_update() -> None:
    # The counted category match refuses the doubles seed first (issue #79
    # rule); the rejected doubles update is still recorded.
    engine = _gap_engine()
    assert engine._seed_sensitive_rejections["syn-mx"] == (
        D,
        ("syn-p", "syn-q", "syn-r", "syn-s"),
    )
    before = engine.to_snapshot()
    with pytest.raises(
        InvalidSeedError,
        match="Cannot seed a rating that a counted mixed_doubles match for this player depends on",
    ):
        engine.seed_external_rating("syn-r", D, _snap(4.0))
    assert engine.to_snapshot() == before


def _not_completed() -> Match:
    return Match("syn-x1", ("syn-a",), ("syn-b",), (), PLAYED, S, status=MatchStatus.RETIRED)


def _bad_format() -> Match:
    fmt = GameFormat(ScoringType.RALLY)
    return _match("syn-x1", ("syn-a",), ("syn-b",), S, game_format=fmt)


def _unconfirmed() -> Match:
    return _match("syn-x1", ("syn-a",), ("syn-b",), S, match_type=MatchType.SELF_REPORTED)


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (_not_completed, RejectionReason.MATCH_NOT_COMPLETED),
        (_bad_format, RejectionReason.GAME_FORMAT_NOT_ACCEPTED),
        (_unconfirmed, RejectionReason.SELF_REPORTED_UNCONFIRMED),
    ],
)
def test_an_out_of_scope_rejection_records_nothing_and_seeds_still_work(
    make: Callable[[], Match], reason: RejectionReason
) -> None:
    engine = RatingEngine(SIDE_OUT_GAPS)
    result = engine.record(make())
    assert result.reason is reason
    assert engine._seed_sensitive_rejections == {}
    engine.seed_external_rating("syn-a", S, _snap(7.5))
    engine.add_player(_seed("syn-b", S, 2.5))
    assert engine.players["syn-b"].ratings[S].rating == 2.5


def test_a_repeat_limit_rejection_records_nothing() -> None:
    cfg = Config(self_reported_max_repeats=1)
    engine = RatingEngine(cfg)
    confirmed = frozenset({"syn-a", "syn-b"})
    kind = MatchType.SELF_REPORTED
    first = _match("syn-x1", ("syn-a",), ("syn-b",), S, match_type=kind, confirmed_by=confirmed)
    assert engine.record(first).counted
    again = _match(
        "syn-x2",
        ("syn-a",),
        ("syn-b",),
        S,
        on=PLAYED + timedelta(days=1),
        match_type=kind,
        confirmed_by=confirmed,
    )
    assert engine.record(again).reason is RejectionReason.SELF_REPORTED_REPEAT_LIMIT
    assert engine._seed_sensitive_rejections == {}


def test_a_possible_duplicate_rejection_records_nothing() -> None:
    engine = RatingEngine(Config(reject_content_duplicates=True))
    assert engine.record(_match("syn-x1", ("syn-a",), ("syn-b",), S)).counted
    copy_row = _match("syn-x2", ("syn-a",), ("syn-b",), S)
    assert engine.record(copy_row).reason is RejectionReason.POSSIBLE_DUPLICATE
    assert engine._seed_sensitive_rejections == {}


def test_a_duplicate_match_id_does_not_change_the_record() -> None:
    engine = _gap_engine()
    before = dict(engine._seed_sensitive_rejections)
    again = _match("syn-sg", ("syn-a",), ("syn-z",), S)
    assert engine.record(again).reason is RejectionReason.DUPLICATE_MATCH_ID
    assert engine._seed_sensitive_rejections == before


# -- refusal scope -----------------------------------------------------------------


def test_a_seed_in_another_format_is_accepted() -> None:
    engine = _gap_engine()
    engine.seed_external_rating("syn-a", D, _snap(4.0))
    engine.seed_external_rating("syn-u", MX, _snap(4.0))
    engine.add_player(_seed("syn-v2", S, 4.0))
    assert engine.players["syn-u"].ratings[MX].rating == 4.0


def test_a_doubles_rejection_does_not_block_a_category_seed() -> None:
    engine = _gap_engine()
    engine.seed_external_rating("syn-v", MX, _snap(4.0))
    engine.seed_external_rating("syn-p", MEN, _snap(4.0))
    assert engine.players["syn-v"].ratings[MX].rating == 4.0


def _category_baseline_engine(cfg: Config) -> RatingEngine:
    engine = RatingEngine(cfg)
    for pid in ("syn-a", "syn-b", "syn-c", "syn-d"):
        engine.seed_external_rating(pid, MX, _snap(4.0, LATE))
    result = engine.record(_match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), MX))
    assert result.reason is RejectionReason.BEFORE_IMPORTED_BASELINE
    return engine


def test_a_category_rejection_blocks_the_parent_seed_only_when_categories_feed_it() -> None:
    feeds = _category_baseline_engine(Config())
    _refused(feeds, lambda: feeds.seed_external_rating("syn-a", D, _snap(4.0)))
    apart = _category_baseline_engine(Config(category_feeds_parent=False))
    apart.seed_external_rating("syn-a", D, _snap(4.0))
    assert apart.players["syn-a"].ratings[D].rating == 4.0


def test_add_player_is_refused_for_the_parent_of_a_rejected_category_update() -> None:
    engine = RatingEngine(GAPS)
    engine.seed_external_rating("syn-a", MX, _snap(5.0))
    result = engine.record(_match("syn-m1", ("syn-a", "syn-u"), ("syn-c", "syn-d"), MX))
    assert result.reason is RejectionReason.DOUBLES_PARTNER_GAP
    _refused(engine, lambda: engine.add_player(_seed("syn-u", D, 4.5)))
    assert "syn-u" not in engine.players


def test_a_category_rejection_does_not_block_another_category() -> None:
    engine = _category_baseline_engine(Config())
    engine.seed_external_rating("syn-a", WOMEN, _snap(4.0))
    assert engine.players["syn-a"].ratings[WOMEN].rating == 4.0


def test_add_player_with_an_empty_record_is_accepted() -> None:
    engine = _gap_engine()
    engine.add_player(Player(id="syn-u"))
    assert engine.players["syn-u"].ratings == {}


def test_replay_seeds_for_players_in_rejected_rows_are_accepted() -> None:
    # A fresh replay adds its seeds before any row, so nothing is refused.
    ledger = [_match("syn-sg", ("syn-a",), ("syn-u",), S)]
    seeds = [_seed("syn-a", S, 5.0), _seed("syn-u", S, 5.0)]
    engine = replay(ledger, seeds, cfg=GAPS, as_of=AS_OF)
    assert engine.processed_match_ids == {"syn-sg"}
    assert engine._seed_sensitive_rejections == {}


def test_resume_error_for_a_seed_in_a_rejected_row_is_unchanged() -> None:
    ledger = [_match("syn-sg", ("syn-a",), ("syn-u",), S)]
    checkpoint = replay(ledger, [_seed("syn-a", S, 5.0)], cfg=GAPS, as_of=LATE)
    assert "syn-sg" in checkpoint._seed_sensitive_rejections
    with pytest.raises(SnapshotError, match="names a player in rows the checkpoint considered"):
        replay(
            ledger,
            [_seed("syn-u", S, 5.0)],
            cfg=GAPS,
            as_of=AS_OF,
            from_snapshot=checkpoint.to_snapshot(),
        )


def test_resume_seed_for_a_checkpointed_player_already_in_the_snapshot_is_a_player_error() -> None:
    ledger = [_match("syn-sg", ("syn-a",), ("syn-u",), S)]
    checkpoint = replay(ledger, [_seed("syn-a", S, 5.0)], cfg=GAPS, as_of=LATE)
    assert "syn-a" in checkpoint.players
    assert "syn-a" in checkpoint._ledger_player_ids
    with pytest.raises(PlayerError, match="Player already exists: syn-a"):
        replay(
            ledger,
            [_seed("syn-a", S, 4.5)],
            cfg=GAPS,
            as_of=AS_OF,
            from_snapshot=checkpoint.to_snapshot(),
        )


# -- snapshot ----------------------------------------------------------------------


def test_snapshot_form_and_version() -> None:
    snap = _json(_gap_engine())
    assert snap["snapshot_version"] == 8
    assert snap["seed_sensitive_rejections"] == {
        "syn-mx": {"players": ["syn-p", "syn-q", "syn-r", "syn-s"], "rating_format": "doubles"},
        "syn-pg": {"players": ["syn-b", "syn-v", "syn-w", "syn-x"], "rating_format": "doubles"},
        "syn-sg": {"players": ["syn-a", "syn-u"], "rating_format": "singles"},
    }
    rejections = snap["seed_sensitive_rejections"]
    assert isinstance(rejections, dict)
    assert list(rejections) == sorted(rejections)


def test_round_trip_restores_the_record_and_the_refusal() -> None:
    engine = _gap_engine()
    snap = _json(engine)
    restored = RatingEngine.from_snapshot(snap, GAPS)
    assert restored._seed_sensitive_rejections == engine._seed_sensitive_rejections
    assert restored.to_snapshot() == snap
    _refused(restored, lambda: restored.seed_external_rating("syn-u", S, _snap(4.5)))


def test_a_version_7_snapshot_does_not_load() -> None:
    snap = _json(_gap_engine())
    snap["snapshot_version"] = 7
    with pytest.raises(SnapshotError, match="snapshot_version must be 8, not 7"):
        RatingEngine.from_snapshot(snap, GAPS)


def test_a_snapshot_without_the_key_does_not_load() -> None:
    snap = _json(_gap_engine())
    del snap["seed_sensitive_rejections"]
    with pytest.raises(SnapshotError, match="missing key 'seed_sensitive_rejections'"):
        RatingEngine.from_snapshot(snap, GAPS)


def _rejections(snap: Snap) -> dict[str, object]:
    value = snap["seed_sensitive_rejections"]
    assert isinstance(value, dict)
    return value


def _entry(snap: Snap, key: str) -> dict[str, object]:
    value = _rejections(snap)[key]
    assert isinstance(value, dict)
    return value


def _set_entry(key: str, field: str, value: object) -> Callable[[Snap], None]:
    def edit(snap: Snap) -> None:
        _entry(snap, key)[field] = value

    return edit


def _not_object(snap: Snap) -> None:
    snap["seed_sensitive_rejections"] = []


def _blank_key(snap: Snap) -> None:
    _rejections(snap)[" "] = _entry(snap, "syn-sg")


def _value_not_object(snap: Snap) -> None:
    _rejections(snap)["syn-sg"] = ["syn-a", "syn-u"]


def _extra_key(snap: Snap) -> None:
    _entry(snap, "syn-sg")["reason"] = "singles_rating_gap"


def _missing_key(snap: Snap) -> None:
    del _entry(snap, "syn-sg")["players"]


def _neither(snap: Snap) -> None:
    _rejections(snap)["syn-nope"] = _entry(snap, "syn-sg")


def _processed_not_doubles(snap: Snap) -> None:
    _entry(snap, "syn-mx")["rating_format"] = "mixed_doubles"


def _processed_unknown_player(snap: Snap) -> None:
    _entry(snap, "syn-mx")["players"] = ["syn-p", "syn-q", "syn-r", "syn-zz"]


SHAPE_CASES = [
    ("not an object", _not_object, r"seed_sensitive_rejections must be an object"),
    ("blank key", _blank_key, r"has a key that is not a non-blank str"),
    ("value not object", _value_not_object, r"\['syn-sg'\] must be an object"),
    ("extra key", _extra_key, r"\['syn-sg'\]: unknown key 'reason'"),
    ("missing key", _missing_key, r"\['syn-sg'\]: missing key 'players'"),
    (
        "bad format",
        _set_entry("syn-sg", "rating_format", "pairs"),
        r"\['syn-sg'\]\.rating_format must be a RatingFormat value",
    ),
    (
        "format not str",
        _set_entry("syn-sg", "rating_format", 1),
        r"\['syn-sg'\]\.rating_format must be a RatingFormat value",
    ),
    (
        "players not list",
        _set_entry("syn-sg", "players", "syn-a"),
        r"\['syn-sg'\]\.players must be a list",
    ),
    (
        "blank player",
        _set_entry("syn-sg", "players", ["syn-a", " "]),
        r"\['syn-sg'\]\.players\[1\] must be a non-blank str",
    ),
    (
        "player not str",
        _set_entry("syn-sg", "players", ["syn-a", 7]),
        r"\['syn-sg'\]\.players\[1\] must be a non-blank str",
    ),
    (
        "unsorted",
        _set_entry("syn-sg", "players", ["syn-u", "syn-a"]),
        r"\['syn-sg'\]\.players must be sorted with no repeats",
    ),
    (
        "repeat",
        _set_entry("syn-pg", "players", ["syn-b", "syn-b", "syn-w", "syn-x"]),
        r"\['syn-pg'\]\.players must be sorted with no repeats",
    ),
    (
        "too few",
        _set_entry("syn-pg", "players", ["syn-b", "syn-w"]),
        r"\['syn-pg'\]\.players must name 4 players for doubles",
    ),
    (
        "too many",
        _set_entry("syn-sg", "players", ["syn-a", "syn-u", "syn-v"]),
        r"\['syn-sg'\]\.players must name 2 players for singles",
    ),
    ("neither", _neither, r"'syn-nope', which is in neither processed_match_ids nor reserved"),
    (
        "processed not doubles",
        _processed_not_doubles,
        r"\['syn-mx'\] is a processed match id, so its rating_format must be doubles",
    ),
    (
        "processed unknown player",
        _processed_unknown_player,
        r"\['syn-mx'\]\.players names 'syn-zz', which is not in players",
    ),
]


@pytest.mark.parametrize(
    ("edit", "message"),
    [(case[1], case[2]) for case in SHAPE_CASES],
    ids=[case[0] for case in SHAPE_CASES],
)
def test_snapshot_validation_rule(edit: Callable[[Snap], None], message: str) -> None:
    snap = _json(_gap_engine())
    edit(snap)
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(snap, GAPS)


def test_a_processed_key_needs_category_feeds_parent() -> None:
    cfg = Config(category_feeds_parent=False, max_doubles_partner_gap=1.0)
    engine = RatingEngine(cfg)
    assert engine.record(_match("syn-ok", ("syn-a", "syn-b"), ("syn-c", "syn-d"), D)).counted
    snap = _json(engine)
    _rejections(snap)["syn-ok"] = {
        "players": ["syn-a", "syn-b", "syn-c", "syn-d"],
        "rating_format": "doubles",
    }
    with pytest.raises(SnapshotError, match=r"\['syn-ok'\] .* needs category_feeds_parent"):
        RatingEngine.from_snapshot(snap, cfg)


def test_a_reserved_possible_duplicate_is_never_a_key() -> None:
    cfg = Config(reject_content_duplicates=True, max_singles_rating_gap=1.0)
    engine = RatingEngine(cfg)
    assert engine.record(_match("syn-x1", ("syn-a",), ("syn-b",), S)).counted
    engine.record(_match("syn-x2", ("syn-a",), ("syn-b",), S))
    snap = _json(engine)
    _rejections(snap)["syn-x2"] = {"players": ["syn-a", "syn-b"], "rating_format": "singles"}
    with pytest.raises(SnapshotError, match=r"\['syn-x2'\] is a possible_duplicates key"):
        RatingEngine.from_snapshot(snap, cfg)


def _baseline_snapshot() -> Snap:
    """Default config (no gap rules): a singles baseline rejection of syn-a and syn-b."""
    engine = RatingEngine()
    engine.seed_external_rating("syn-a", S, _snap(4.0, LATE))
    engine.seed_external_rating("syn-b", S, _snap(4.0, LATE))
    engine.seed_provisional_rating("syn-c", S)
    engine.record(_match("syn-m1", ("syn-a",), ("syn-b",), S))
    retired = Match("syn-m2", ("syn-a",), ("syn-c",), (), PLAYED, S, status=MatchStatus.RETIRED)
    engine.record(retired)
    return _json(engine)


def test_a_baseline_snapshot_loads() -> None:
    snap = _baseline_snapshot()
    assert set(_rejections(snap)) == {"syn-m1"}
    assert RatingEngine.from_snapshot(snap).to_snapshot() == snap


@pytest.mark.parametrize(
    ("players", "named"),
    [(["syn-a", "syn-c"], "syn-c"), (["syn-a", "syn-zz"], "syn-zz")],
    ids=["undated state", "no player"],
)
def test_without_a_gap_rule_every_named_player_needs_a_dated_baseline(
    players: list[str], named: str
) -> None:
    snap = _baseline_snapshot()
    _rejections(snap)["syn-m2"] = {"players": players, "rating_format": "singles"}
    with pytest.raises(SnapshotError, match=rf"\['syn-m2'\]\.players names '{named}', which"):
        RatingEngine.from_snapshot(snap)


def test_a_category_baseline_needs_the_dated_state_a_category_match_would_start_from() -> None:
    derived = RatingEngine()
    for pid in ("syn-a", "syn-b", "syn-c", "syn-d"):
        derived.seed_external_rating(pid, D, _snap(4.0, LATE))
    derived.record(_match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), MX))
    snap = _json(derived)
    assert RatingEngine.from_snapshot(snap).to_snapshot() == snap
    # The same players, but syn-a has an undated mixed state of its own.
    other = RatingEngine()
    other.add_player(Player("syn-a", {MX: RatingState(rating=3.5, rd=other.cfg.new_player_rd)}))
    for pid in ("syn-a", "syn-b", "syn-c", "syn-d"):
        other.seed_external_rating(pid, D, _snap(4.0, LATE))
    snap["players"] = _json(other)["players"]
    with pytest.raises(SnapshotError, match=r"\['syn-m1'\]\.players names 'syn-a', which"):
        RatingEngine.from_snapshot(snap)


def test_a_category_baseline_with_categories_apart_needs_a_category_state() -> None:
    cfg = Config(category_feeds_parent=False)
    engine = _category_baseline_engine(cfg)
    snap = _json(engine)
    assert RatingEngine.from_snapshot(snap, cfg).to_snapshot() == snap
    other = RatingEngine(cfg)
    for pid in ("syn-a", "syn-b", "syn-c", "syn-d"):
        other.seed_external_rating(pid, D, _snap(4.0, LATE))
    snap["players"] = _json(other)["players"]
    with pytest.raises(SnapshotError, match=r"\['syn-m1'\]\.players names 'syn-a', which"):
        RatingEngine.from_snapshot(snap, cfg)


def test_a_resumable_snapshot_names_only_ledger_players() -> None:
    ledger = [_match("syn-sg", ("syn-a",), ("syn-u",), S)]
    engine = replay(ledger, [_seed("syn-a", S, 5.0)], cfg=GAPS, as_of=AS_OF)
    snap = _json(engine)
    assert RatingEngine.from_snapshot(snap, GAPS).to_snapshot() == snap
    snap["ledger_player_ids"] = ["syn-a"]
    with pytest.raises(
        SnapshotError, match=r"\['syn-sg'\]\.players names 'syn-u', which is not in ledger_player"
    ):
        RatingEngine.from_snapshot(snap, GAPS)


# -- live, replay, resume, fuzz ------------------------------------------------------

CONFIGS = {
    "default": Config(),
    "singles gap": Config(max_singles_rating_gap=0.6),
    "partner gap": Config(max_doubles_partner_gap=0.6),
    "apart": Config(
        category_feeds_parent=False, max_singles_rating_gap=0.6, max_doubles_partner_gap=0.6
    ),
    "reject copies": Config(reject_content_duplicates=True, max_doubles_partner_gap=0.6),
    "apart no gaps": Config(category_feeds_parent=False),
    "both gaps": Config(max_singles_rating_gap=0.6, max_doubles_partner_gap=0.6),
}
START = date(2025, 1, 1)
END = date(2025, 5, 1)  # the last seed date
MATCH_END = date(2025, 7, 1)  # the last match date
FORMATS = (S, D, MX, MEN)


def _ledger(rng: random.Random, pool: list[str], count: int) -> list[Match]:
    rows: list[Match] = []
    for i in range(count):
        if rows and rng.random() < 0.08:
            # A content copy of an earlier row under a new id.
            twin = rng.choice(rows)
            rows.append(
                Match(
                    f"syn-m{i:03d}",
                    twin.team_a,
                    twin.team_b,
                    twin.games,
                    twin.played_on,
                    twin.rating_format,
                    status=twin.status,
                )
            )
            continue
        fmt = rng.choice(FORMATS)
        ids = rng.sample(pool, 2 * fmt.team_size)
        size = fmt.team_size
        loser = rng.randint(0, 9)
        games = ((11, loser),) if rng.random() < 0.5 else ((loser, 11),)
        status = MatchStatus.RETIRED if rng.random() < 0.05 else MatchStatus.COMPLETED
        rows.append(
            Match(
                f"syn-m{i:03d}",
                tuple(ids[:size]),
                tuple(ids[size:]),
                games if status is MatchStatus.COMPLETED else (),
                START + timedelta(days=rng.randrange(0, (MATCH_END - START).days)),
                fmt,
                status=status,
            )
        )
    return rows


def _seeds(rng: random.Random, pool: list[str]) -> list[Player]:
    seeds: list[Player] = []
    for pid in pool:
        if rng.random() < 0.15:
            continue
        # Half the players have late seeds in every format, so many earlier
        # matches have every player frozen (BEFORE_IMPORTED_BASELINE). Some
        # of their category seeds are early, so a counted category match
        # can still have its doubles update rejected that way.
        late = rng.random() < 0.5
        span = (END - START).days
        ratings: dict[RatingFormat, RatingState] = {}
        for fmt in FORMATS:
            if late and (fmt.parent is None or rng.random() < 0.5):
                on = END - timedelta(days=rng.randrange(0, 10))
            elif late:
                on = START - timedelta(days=10)
            elif rng.random() < 0.5:
                on = START + timedelta(days=rng.randrange(-20, span))
            else:
                continue
            rating = round(rng.uniform(2.5, 6.0), 2)
            ratings[fmt] = _seed(pid, fmt, rating, on).ratings[fmt]
        seeds.append(Player(id=pid, ratings=ratings))
    return seeds


def _dated_by(seeds: list[Player], on: date) -> list[Player]:
    """The seeds whose every state is dated on or before on, as a checkpoint replay needs."""
    return [
        seed
        for seed in seeds
        if all(
            s.initial_source_date is not None and s.initial_source_date <= on
            for s in seed.ratings.values()
        )
    ]


def _normalized(snap: Snap) -> Snap:
    """Drop the fields that differ by design between live recording and replay."""
    out = copy.deepcopy(snap)
    for key in (
        "calculated_as_of",
        "replay_info",
        "ledger_digests",
        "ledger_player_ids",
        "resumable",
    ):
        out[key] = None
    players = out["players"]
    assert isinstance(players, list)
    for player in players:
        for state in player["ratings"].values():
            state["live_updates_since_replay"] = 0
            state["inherited_live_updates"] = 0
    return out


def _round_trips(engine: RatingEngine) -> Snap:
    snap = _json(engine)
    assert RatingEngine.from_snapshot(snap, engine.cfg).to_snapshot() == snap
    return snap


def _live(rows: list[Match], seeds: list[Player], cfg: Config) -> tuple[RatingEngine, list[str]]:
    engine = RatingEngine(cfg)
    for seed in seeds:
        engine.add_player(seed)
    reasons: list[str] = []
    for match in sorted(rows, key=ledger_order):
        result = engine.record(match)
        for part in (result, result.parent):
            if part is not None and part.reason in SENSITIVE:
                reasons.append(f"{part.reason.value}:{'parent' if part is result.parent else ''}")
    return engine, reasons


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_live_equals_replay(name: str) -> None:
    cfg = CONFIGS[name]
    rng = random.Random(99)
    pool = [f"syn-p{i:02d}" for i in range(12)]
    rows = _ledger(rng, pool, 60)
    seeds = _seeds(rng, pool)
    live, reasons = _live(rows, seeds, cfg)
    assert reasons
    full = replay(rows, seeds, cfg=cfg, as_of=AS_OF)
    assert live._seed_sensitive_rejections == full._seed_sensitive_rejections
    assert _normalized(_round_trips(live)) == _normalized(_round_trips(full))


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_resume_equals_full_replay(name: str) -> None:
    cfg = CONFIGS[name]
    rng = random.Random(7)
    pool = [f"syn-p{i:02d}" for i in range(12)]
    rows = _ledger(rng, pool, 60)
    seeds = _seeds(rng, pool)
    middle = END + timedelta(days=10)
    early = [row for row in rows if row.played_on <= middle]
    checkpoint = replay(early, seeds, cfg=cfg, as_of=middle)
    assert checkpoint._seed_sensitive_rejections
    resumed = replay(rows, (), cfg=cfg, as_of=AS_OF, from_snapshot=_round_trips(checkpoint))
    full = replay(rows, seeds, cfg=cfg, as_of=AS_OF)
    assert _round_trips(resumed) == _round_trips(full)


def test_fuzz_every_engine_made_snapshot_loads() -> None:
    seen: set[str] = set()
    for name in sorted(CONFIGS):
        cfg = CONFIGS[name]
        for seed in range(6):
            rng = random.Random(seed)
            pool = [f"syn-p{i:02d}" for i in range(10)]
            rows = sorted(_ledger(rng, pool, 40), key=ledger_order)
            seeds = _seeds(rng, pool)
            # Live, with seeds tried between matches.
            engine = RatingEngine(cfg)
            for player in seeds:
                engine.add_player(player)
            for match in rows:
                result = engine.record(match)
                for part in (result, result.parent):
                    if part is not None and part.reason in SENSITIVE:
                        parent = "parent" if part is result.parent else ""
                        seen.add(f"{part.reason.value}:{parent}")
                pid = rng.choice(pool + ["syn-new"])
                fmt = rng.choice(FORMATS)
                on = START + timedelta(days=rng.randrange(-20, (END - START).days))
                try:
                    if rng.random() < 0.7:
                        engine.seed_external_rating(
                            pid,
                            fmt,
                            _snap(round(rng.uniform(2.5, 6.0), 2), on),
                            replace_unplayed_seed=rng.random() < 0.5,
                        )
                    else:
                        engine.add_player(_seed(pid, fmt, round(rng.uniform(2.5, 6.0), 2), on))
                except OpenEngineError:
                    pass
                _round_trips(engine)
            # Replay and resume of the same ledger.
            full = replay(rows, seeds, cfg=cfg, as_of=AS_OF)
            _round_trips(full)
            middle = START + timedelta(days=rng.randrange(0, (END - START).days))
            early_seeds = _dated_by(seeds, middle)
            checkpoint = replay(
                [r for r in rows if r.played_on <= middle], early_seeds, cfg=cfg, as_of=middle
            )
            resumed = replay(rows, (), cfg=cfg, as_of=AS_OF, from_snapshot=_round_trips(checkpoint))
            expected = replay(rows, early_seeds, cfg=cfg, as_of=AS_OF)
            assert _round_trips(resumed) == _round_trips(expected)
    assert {
        "before_imported_baseline:",
        "before_imported_baseline:parent",
        "singles_rating_gap:",
        "doubles_partner_gap:",
        "doubles_partner_gap:parent",
    } <= seen


def _seed_attempt(
    engine: RatingEngine, rng: random.Random, pool: list[str]
) -> tuple[str, RatingFormat] | str:
    """
    Try one random seed. Returns (player, format) when accepted, else the error text.
    """
    pid = rng.choice(pool + ["syn-new"])
    fmt = rng.choice(FORMATS)
    on = START + timedelta(days=rng.randrange(-20, (END - START).days))
    rating = round(rng.uniform(2.5, 6.0), 2)
    try:
        if rng.random() < 0.7:
            engine.seed_external_rating(
                pid, fmt, _snap(rating, on), replace_unplayed_seed=rng.random() < 0.5
            )
        else:
            engine.add_player(_seed(pid, fmt, rating, on))
    except OpenEngineError as exc:
        return str(exc)
    return pid, fmt


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_fuzz_live_seeds_between_matches_equal_a_replay_of_the_accepted_seeds(
    name: str,
) -> None:
    # Every seed live accepts between matches must give the same state as
    # a replay that adds it before every row.
    cfg = CONFIGS[name]
    refusals = 0
    for seed in range(4):
        rng = random.Random(1000 + seed)
        pool = [f"syn-p{i:02d}" for i in range(10)]
        rows = sorted(_ledger(rng, pool, 40), key=ledger_order)
        engine = RatingEngine(cfg)
        accepted: dict[tuple[str, RatingFormat], RatingState] = {}
        for player in _seeds(rng, pool):
            engine.add_player(player)
            for fmt, state in player.ratings.items():
                accepted[(player.id, fmt)] = copy.deepcopy(state)
        for match in rows:
            engine.record(match)
            for _ in range(2):
                outcome = _seed_attempt(engine, rng, pool)
                if isinstance(outcome, str):
                    refusals += "could count with a new seed" in outcome
                    continue
                pid, fmt = outcome
                accepted[(pid, fmt)] = copy.deepcopy(engine.players[pid].ratings[fmt])
        seeds: dict[str, Player] = {}
        for (pid, fmt), state in sorted(accepted.items(), key=lambda item: item[0][0]):
            seeds.setdefault(pid, Player(id=pid)).ratings[fmt] = state
        full = replay(rows, list(seeds.values()), cfg=cfg, as_of=AS_OF)
        assert _normalized(_round_trips(engine)) == _normalized(_round_trips(full))
    # Some seeds were refused for a seed-sensitive rejection, so the
    # comparison covers the refusal, not only seeds nothing blocks.
    assert refusals
