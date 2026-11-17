"""Engine snapshots: round trip, schema, validation, and determinism (issue #16).

All player and match ids are synthetic.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import math
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

import open_engine
import open_engine.engine as engine_module
from open_engine import (
    Config,
    ExternalRatingSnapshot,
    Match,
    MatchType,
    Player,
    RatingEngine,
    RatingFormat,
    RatingState,
    SnapshotError,
    replay,
)

D = RatingFormat.DOUBLES
S = RatingFormat.SINGLES
MX = RatingFormat.MIXED_DOUBLES
AS_OF = date(2025, 6, 30)


def _seeds() -> list[Player]:
    eng = RatingEngine()
    eng.seed_external_rating(
        "syn-ext",
        D,
        ExternalRatingSnapshot("external_standard", 4.1, date(2025, 1, 1), 70.0, "ext-1", "ref-1"),
    )
    # An int rating and an int reliability that never play, so their types
    # must survive the round trip.
    eng.seed_external_rating(
        "syn-int", S, ExternalRatingSnapshot("external_standard", 4, date(2025, 2, 1), 80)
    )
    seeds = [eng.players[pid] for pid in sorted(eng.players)]
    seeds[0] = dataclasses.replace(seeds[0], account_user_id="acct-1")
    return seeds


def _self_reported(match_id: str, on: date) -> Match:
    return Match(
        match_id,
        ("syn-a",),
        ("syn-b",),
        ((11, 6),),
        on,
        S,
        match_type=MatchType.SELF_REPORTED,
        confirmed_by=frozenset({"syn-a", "syn-b"}),
    )


LEDGER = [
    Match("syn-m01", ("syn-ext", "syn-a"), ("syn-b", "syn-c"), ((11, 7),), date(2025, 2, 3), D),
    Match("syn-m02", ("syn-ext", "syn-c"), ("syn-a", "syn-d"), ((9, 11),), date(2025, 2, 10), MX),
    Match("syn-m03", ("syn-a",), ("syn-c",), ((11, 9), (11, 4)), date(2025, 3, 1), S, best_of=3),
    _self_reported("syn-m04", date(2025, 3, 5)),
    _self_reported("syn-m05", date(2025, 3, 6)),
    Match(
        "syn-m06",
        ("syn-a",),
        ("syn-b",),
        ((11, 2),),
        date(2025, 3, 7),
        S,
        match_type=MatchType.SELF_REPORTED,
    ),
    Match("syn-m07", ("syn-b", "syn-d"), ("syn-a", "syn-c"), ((11, 8),), date(2025, 4, 1), D),
    # After AS_OF, so it is not considered.
    Match("syn-m08", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 5),), date(2025, 8, 1), D),
]


@pytest.fixture
def engine() -> RatingEngine:
    eng = replay(LEDGER, _seeds(), as_of=AS_OF)
    eng.link_account("syn-a", "acct-2")
    eng.attach_external_id("syn-b", "external_standard", "ext-2")
    return eng


@pytest.fixture
def snapshot(engine: RatingEngine) -> dict[str, object]:
    loaded: dict[str, object] = json.loads(json.dumps(engine.to_snapshot(), allow_nan=False))
    return loaded


def _engine_state(eng: RatingEngine) -> dict[str, object]:
    return {
        "players": eng.players,
        "processed_match_ids": eng.processed_match_ids,
        "self_reported_log": eng.self_reported_log,
        "content_keys": eng._content_keys,
        "calculated_as_of": eng.calculated_as_of,
        "replay_info": eng.replay_info,
        "ledger_digests": eng._ledger_digests,
        "ledger_player_ids": eng._ledger_player_ids,
        "resumable": eng._resumable,
    }


# -- export --------------------------------------------------------------------


def test_snapshot_error_is_exported_from_both_modules() -> None:
    assert open_engine.SnapshotError is engine_module.SnapshotError
    assert issubclass(SnapshotError, open_engine.OpenEngineError)
    assert "SnapshotError" in open_engine.__all__


# -- round trip ----------------------------------------------------------------


def test_snapshot_is_json_safe_without_a_custom_encoder(engine: RatingEngine) -> None:
    text = json.dumps(engine.to_snapshot(), allow_nan=False)
    assert json.loads(text) == engine.to_snapshot()


def test_round_trip_through_json_restores_every_field(
    engine: RatingEngine, snapshot: dict[str, object]
) -> None:
    restored = RatingEngine.from_snapshot(snapshot)
    assert _engine_state(restored) == _engine_state(engine)
    assert restored.cfg == engine.cfg
    assert restored.to_snapshot() == snapshot


def test_round_trip_keeps_number_types(engine: RatingEngine, snapshot: dict[str, object]) -> None:
    restored = RatingEngine.from_snapshot(snapshot)
    state = restored.players["syn-int"].ratings[S]
    assert type(state.rating) is int and state.rating == 4
    assert type(state.initial_source_reliability) is int
    for pid, player in restored.players.items():
        for fmt, restored_state in player.ratings.items():
            original = engine.players[pid].ratings[fmt]
            for f in dataclasses.fields(RatingState):
                value = getattr(restored_state, f.name)
                assert type(value) is type(getattr(original, f.name)), (pid, fmt, f.name)


def test_round_trip_covers_what_the_fixture_exercises(engine: RatingEngine) -> None:
    # Guard the fixture itself, so the round trip test checks real content.
    assert engine.self_reported_log
    assert engine.replay_info is not None and engine.replay_info.matches_counted >= 6
    assert engine.players["syn-a"].account_user_id == "acct-2"
    assert engine.players["syn-b"].external_ids == {"external_standard": "ext-2"}
    assert engine.players["syn-ext"].ratings[MX].initial_source == "open_parent:doubles"
    assert "syn-m06" in engine._ledger_digests
    assert "syn-m06" not in engine.processed_match_ids
    assert "syn-m08" not in engine._ledger_digests


def test_round_trip_of_a_live_engine_that_never_replayed() -> None:
    eng = RatingEngine()
    eng.record(LEDGER[0])
    snap = json.loads(json.dumps(eng.to_snapshot()))
    assert snap["calculated_as_of"] is None and snap["replay_info"] is None
    restored = RatingEngine.from_snapshot(snap)
    assert _engine_state(restored) == _engine_state(eng)
    assert restored.players["syn-a"].ratings[D].live_updates_since_replay == 1


def test_from_snapshot_uses_the_config_it_is_given() -> None:
    cfg = Config(k_max=0.5)
    eng = replay(LEDGER, _seeds(), cfg=cfg, as_of=AS_OF)
    restored = RatingEngine.from_snapshot(eng.to_snapshot(), cfg)
    assert restored.cfg is cfg
    assert _engine_state(restored) == _engine_state(eng)


def test_restored_engine_records_like_the_original(
    engine: RatingEngine, snapshot: dict[str, object]
) -> None:
    restored = RatingEngine.from_snapshot(snapshot)
    after = LEDGER[-1]
    assert restored.record(after) == engine.record(after)
    assert restored.to_snapshot() == engine.to_snapshot()


def test_restored_engine_does_not_share_state_with_the_snapshot(
    snapshot: dict[str, object],
) -> None:
    before = copy.deepcopy(snapshot)
    restored = RatingEngine.from_snapshot(snapshot)
    restored.record(LEDGER[-1])
    assert snapshot == before


# -- schema --------------------------------------------------------------------


def test_snapshot_top_level_values(engine: RatingEngine, snapshot: dict[str, object]) -> None:
    assert snapshot["snapshot_version"] == 2
    assert snapshot["engine_version"] == open_engine.__version__
    assert snapshot["config_version"] == engine.cfg.version
    assert snapshot["config_fingerprint"] == engine.cfg.fingerprint()
    assert snapshot["calculated_as_of"] == "2025-06-30"
    assert snapshot["processed_match_ids"] == sorted(engine.processed_match_ids)
    players = snapshot["players"]
    assert isinstance(players, list)
    assert [p["id"] for p in players] == sorted(engine.players)


def test_snapshot_self_reported_log_form(snapshot: dict[str, object]) -> None:
    assert snapshot["self_reported_log"] == [
        {
            "rating_format": "singles",
            "teams": [["syn-a"], ["syn-b"]],
            "dates": ["2025-03-05", "2025-03-06"],
        }
    ]


TOP_LEVEL_KEYS = {
    "snapshot_version",
    "engine_version",
    "config_version",
    "config_fingerprint",
    "calculated_as_of",
    "replay_info",
    "players",
    "processed_match_ids",
    "self_reported_log",
    "content_keys",
    "ledger_digests",
    "ledger_player_ids",
    "resumable",
}


def test_snapshot_covers_every_engine_attribute(snapshot: dict[str, object]) -> None:
    # A new RatingEngine attribute must be added to the snapshot schema (and
    # snapshot_version bumped) before this test is updated.
    assert set(vars(RatingEngine())) == {
        "cfg",
        "players",
        "processed_match_ids",
        "self_reported_log",
        "calculated_as_of",
        "replay_info",
        "_content_keys",
        "_ledger_digests",
        "_ledger_player_ids",
        "_resumable",
    }
    assert set(snapshot) == TOP_LEVEL_KEYS


def test_snapshot_covers_every_player_and_state_field(snapshot: dict[str, object]) -> None:
    players = snapshot["players"]
    assert isinstance(players, list)
    for player in players:
        assert set(player) == {f.name for f in dataclasses.fields(Player)}
        for state in player["ratings"].values():
            assert set(state) == {f.name for f in dataclasses.fields(RatingState)}
    info = snapshot["replay_info"]
    assert isinstance(info, dict)
    assert set(info) == {f.name for f in dataclasses.fields(engine_module.ReplayInfo)}


# -- determinism ---------------------------------------------------------------


def test_snapshot_text_does_not_depend_on_input_order() -> None:
    forward = replay(LEDGER, _seeds(), as_of=AS_OF)
    backward = replay(list(reversed(LEDGER)), list(reversed(_seeds())), as_of=AS_OF)
    assert json.dumps(forward.to_snapshot(), sort_keys=True) == json.dumps(
        backward.to_snapshot(), sort_keys=True
    )


def test_snapshot_emits_sorted_keys(snapshot: dict[str, object]) -> None:
    text = json.dumps(snapshot)
    assert text == json.dumps(json.loads(text), sort_keys=True)


# -- validation ----------------------------------------------------------------

Snap = dict[str, object]


def _player(snap: Snap, pid: str) -> dict[str, object]:
    players = snap["players"]
    assert isinstance(players, list)
    for player in players:
        if player["id"] == pid:
            found: dict[str, object] = player
            return found
    raise AssertionError(pid)


def _state(snap: Snap, pid: str = "syn-a", fmt: str = "doubles") -> dict[str, object]:
    ratings = _player(snap, pid)["ratings"]
    assert isinstance(ratings, dict)
    state: dict[str, object] = ratings[fmt]
    return state


def _set_state(name: str, value: object) -> Callable[[Snap], None]:
    def mutate(snap: Snap) -> None:
        _state(snap)[name] = value

    return mutate


def _set(key: str, value: object) -> Callable[[Snap], None]:
    def mutate(snap: Snap) -> None:
        snap[key] = value

    return mutate


def _set_info(key: str, value: object) -> Callable[[Snap], None]:
    def mutate(snap: Snap) -> None:
        info = snap["replay_info"]
        assert isinstance(info, dict)
        info[key] = value

    return mutate


def _set_player(key: str, value: object) -> Callable[[Snap], None]:
    def mutate(snap: Snap) -> None:
        _player(snap, "syn-a")[key] = value

    return mutate


def _log_entry(snap: Snap) -> dict[str, object]:
    log = snap["self_reported_log"]
    assert isinstance(log, list)
    entry: dict[str, object] = log[0]
    return entry


def _set_log(key: str, value: object) -> Callable[[Snap], None]:
    def mutate(snap: Snap) -> None:
        _log_entry(snap)[key] = value

    return mutate


def _delete(key: str) -> Callable[[Snap], None]:
    def mutate(snap: Snap) -> None:
        del snap[key]

    return mutate


def _duplicate_player(snap: Snap) -> None:
    players = snap["players"]
    assert isinstance(players, list)
    players.append(copy.deepcopy(players[0]))


def _duplicate_log(snap: Snap) -> None:
    log = snap["self_reported_log"]
    assert isinstance(log, list)
    log.append(copy.deepcopy(log[0]))


def _player_extra_key(snap: Snap) -> None:
    _player(snap, "syn-a")["nickname"] = "x"


def _state_missing_key(snap: Snap) -> None:
    del _state(snap)["rd"]


def _bad_rating_format(snap: Snap) -> None:
    ratings = _player(snap, "syn-a")["ratings"]
    assert isinstance(ratings, dict)
    ratings["triples"] = ratings.pop("doubles")


def _bad_partner_date(snap: Snap) -> None:
    partners = _state(snap)["partners"]
    assert isinstance(partners, dict)
    partners[next(iter(partners))] = "2025-2-3"


def _blank_partner_id(snap: Snap) -> None:
    partners = _state(snap)["partners"]
    assert isinstance(partners, dict)
    partners[" "] = "2025-02-03"


def _bad_digest(snap: Snap) -> None:
    digests = snap["ledger_digests"]
    assert isinstance(digests, dict)
    digests["syn-m01"] = "ABC"


def _blank_digest_id(snap: Snap) -> None:
    digests = snap["ledger_digests"]
    assert isinstance(digests, dict)
    digests[""] = "0" * 64


MALFORMED: list[tuple[str, Callable[[Snap], None], str]] = [
    ("missing key", _delete("players"), r"snapshot: missing key 'players'"),
    ("unknown key", _set("extra", 1), r"snapshot: unknown key 'extra'"),
    ("version", _set("snapshot_version", 1), r"snapshot_version must be 2"),
    ("version bool", _set("snapshot_version", True), r"snapshot_version must be 2"),
    ("engine version", _set("engine_version", 3), r"engine_version must be a str"),
    ("config version type", _set("config_version", None), r"config_version must be a str"),
    ("date form", _set("calculated_as_of", "2025-6-30"), r"calculated_as_of must be a date"),
    ("date type", _set("calculated_as_of", 20250630), r"calculated_as_of must be a date"),
    ("info without date", _set("calculated_as_of", None), r"replay_info must be null"),
    ("date without info", _set("replay_info", None), r"replay_info is required"),
    ("info as_of", _set_info("as_of", "2025-06-29"), r"replay_info.as_of must equal"),
    (
        "info fingerprint",
        _set_info("config_fingerprint", "0" * 16),
        r"replay_info.config_fingerprint must equal",
    ),
    ("info version", _set_info("config_version", "0.0.0"), r"replay_info.config_version"),
    ("info count", _set_info("matches_counted", 999), r"matches_counted must not exceed"),
    ("info negative", _set_info("matches_considered", -1), r"matches_considered must be"),
    ("info extra", _set_info("extra", 1), r"replay_info: unknown key 'extra'"),
    ("players type", _set("players", {}), r"players must be a list"),
    ("player type", _set("players", [1]), r"players\[0\] must be an object"),
    ("duplicate player", _duplicate_player, r"duplicate player id 'syn-a'"),
    ("player extra", _player_extra_key, r"unknown key 'nickname'"),
    ("player id", _set_player("id", " "), r"players\[\d+\].id must be a non-blank str"),
    ("account", _set_player("account_user_id", 5), r"account_user_id must be a str or null"),
    ("external ids", _set_player("external_ids", {"a": 1}), r"external_ids must map str to str"),
    ("ratings type", _set_player("ratings", []), r"ratings must be an object"),
    ("format", _bad_rating_format, r"'triples' is not a RatingFormat"),
    ("state missing", _state_missing_key, r"ratings.doubles: missing key 'rd'"),
    ("rating bool", _set_state("rating", True), r"ratings.doubles.rating must be a finite"),
    ("rating str", _set_state("rating", "4.0"), r"ratings.doubles.rating must be a finite"),
    ("rating nan", _set_state("rating", math.nan), r"ratings.doubles.rating must be a finite"),
    ("rating bounds", _set_state("rating", 99.0), r"rating must be between"),
    ("rd bounds", _set_state("rd", 5.0), r"rd must be between"),
    ("initial rating", _set_state("initial_rating", None), r"initial_rating must be a finite"),
    ("count float", _set_state("matches_played", 1.0), r"matches_played must be"),
    ("count negative", _set_state("matches_played", -1), r"matches_played must be"),
    ("live count", _set_state("live_updates_since_replay", False), r"live_updates_since"),
    ("last played", _set_state("last_played", "yesterday"), r"last_played must be a date"),
    ("rd as of", _set_state("rd_as_of", 5), r"rd_as_of must be a date"),
    ("source date", _set_state("initial_source_date", "x"), r"initial_source_date must be"),
    ("source", _set_state("initial_source", 3), r"initial_source must be a str"),
    ("reliability", _set_state("initial_source_reliability", 101), r"between 0 and 100"),
    ("reliability type", _set_state("initial_source_reliability", "9"), r"reliability must be"),
    ("ext player", _set_state("initial_external_player_id", 3), r"external_player_id must"),
    ("evidence", _set_state("initial_evidence_reference", 3), r"evidence_reference must"),
    ("partners type", _set_state("partners", []), r"partners must be an object"),
    ("partner date", _bad_partner_date, r"partners\['syn-.+'\] must be a date"),
    ("partner id", _blank_partner_id, r"partners has an id that is not a non-blank str"),
    ("processed type", _set("processed_match_ids", {}), r"processed_match_ids must be a list"),
    ("processed id", _set("processed_match_ids", [""]), r"processed_match_ids\[0\] must be"),
    (
        "processed duplicate",
        _set("processed_match_ids", ["syn-m01", "syn-m01"]),
        r"duplicate match id 'syn-m01'",
    ),
    ("log type", _set("self_reported_log", {}), r"self_reported_log must be a list"),
    ("log format", _set_log("rating_format", "triples"), r"is not a RatingFormat"),
    ("log team size", _set_log("teams", [["syn-a", "syn-c"], ["syn-b"]]), r"team of 1"),
    ("log team count", _set_log("teams", [["syn-a"]]), r"teams must be two teams"),
    ("log overlap", _set_log("teams", [["syn-a"], ["syn-a"]]), r"a player in both teams"),
    ("log dates", _set_log("dates", []), r"dates must be a non-empty list"),
    ("log date", _set_log("dates", ["2025-03-05", 7]), r"dates\[1\] must be a date"),
    ("log duplicate", _duplicate_log, r"repeats a matchup"),
    ("digest", _bad_digest, r"ledger_digests\['syn-m01'\] must be 64 lowercase hex"),
    ("digest id", _blank_digest_id, r"ledger_digests has an id that is not a non-blank str"),
    ("digests type", _set("ledger_digests", []), r"ledger_digests must be an object"),
]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [pytest.param(m, msg, id=name) for name, m, msg in MALFORMED],
)
def test_from_snapshot_rejects_a_malformed_snapshot(
    snapshot: Snap, mutate: Callable[[Snap], None], message: str
) -> None:
    mutate(snapshot)
    with pytest.raises(SnapshotError, match=message):
        RatingEngine.from_snapshot(snapshot)


@pytest.mark.parametrize("value", [[], "snapshot", None])
def test_from_snapshot_rejects_a_snapshot_that_is_not_an_object(value: object) -> None:
    with pytest.raises(SnapshotError, match="snapshot must be an object"):
        RatingEngine.from_snapshot(value)  # type: ignore[arg-type]


def test_from_snapshot_rejects_another_config(snapshot: Snap) -> None:
    with pytest.raises(SnapshotError, match="config_fingerprint .* does not match"):
        RatingEngine.from_snapshot(snapshot, Config(k_max=0.5))


def test_from_snapshot_rejects_another_config_version(snapshot: Snap) -> None:
    with pytest.raises(SnapshotError, match="config_version '.*' does not match"):
        RatingEngine.from_snapshot(snapshot, Config(version="0.0.0-syn"))


def test_to_snapshot_rejects_state_it_cannot_load(engine: RatingEngine) -> None:
    engine.players["syn-a"].ratings[D].initial_evidence_reference = 5  # type: ignore[assignment]
    with pytest.raises(SnapshotError, match="initial_evidence_reference must be a str or null"):
        engine.to_snapshot()


def test_to_snapshot_rejects_a_datetime_where_a_date_belongs(engine: RatingEngine) -> None:
    engine.players["syn-a"].ratings[D].last_played = datetime(2025, 4, 1, tzinfo=UTC)
    with pytest.raises(SnapshotError, match="last_played must be a date"):
        engine.to_snapshot()


# -- match digests -------------------------------------------------------------

BASE = Match("syn-d1", ("syn-a",), ("syn-b",), ((11, 3),), date(2025, 5, 1), S)
AT = datetime(2025, 5, 1, 18, 0, tzinfo=UTC)
PLUS2 = timezone(timedelta(hours=2))
PLUS2_NAMED = timezone(timedelta(hours=2), "syn-zone")


def _canonical_equal(a: Match, b: Match) -> bool:
    return engine_module._canonical(a) == engine_module._canonical(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        pytest.param(BASE, dataclasses.replace(BASE, best_of=1.0), id="int vs float best_of"),
        pytest.param(BASE, dataclasses.replace(BASE, match_type="rec_verified"), id="str vs enum"),
        pytest.param(BASE, dataclasses.replace(BASE, games=((11, 4),)), id="score"),
        pytest.param(
            dataclasses.replace(BASE, played_at=AT),
            dataclasses.replace(BASE, played_at=AT.astimezone(PLUS2)),
            id="same instant, other offset",
        ),
        pytest.param(BASE, dataclasses.replace(BASE, played_at=AT), id="untimed vs timed"),
    ],
)
def test_digest_separates_rows_canonical_separates(a: Match, b: Match) -> None:
    a.validate()
    b.validate()
    assert not _canonical_equal(a, b)
    assert engine_module._match_digest(a) != engine_module._match_digest(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        pytest.param(BASE, dataclasses.replace(BASE), id="copy"),
        pytest.param(
            dataclasses.replace(BASE, played_at=AT.astimezone(PLUS2)),
            dataclasses.replace(BASE, played_at=AT.astimezone(PLUS2_NAMED)),
            id="same instant and offset, other zone object",
        ),
        pytest.param(
            dataclasses.replace(BASE, confirmed_by=frozenset({"syn-a", "syn-b"})),
            dataclasses.replace(BASE, confirmed_by=frozenset({"syn-b", "syn-a"})),
            id="frozenset order",
        ),
    ],
)
def test_digest_joins_rows_canonical_joins(a: Match, b: Match) -> None:
    a.validate()
    b.validate()
    assert _canonical_equal(a, b)
    assert engine_module._match_digest(a) == engine_module._match_digest(b)


def test_digest_is_64_lowercase_hex() -> None:
    digest = engine_module._match_digest(BASE)
    assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")
