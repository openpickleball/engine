"""
Refactor parity for issue #143: outcomes pinned from main before the split.

_check_resumable, _snap_seed_sensitive_rejections, _prequential,
RatingEngine.player_summary, RatingEngine._consider, RatingEngine._compute,
fit_scale, Match.validate, _network_connectivity, and
Match._validate_field_types were split into helpers with no behavior
change. Each case here runs a public entry point and compares its outcome
(the error class and message, or a digest of the result) with the value
main gave. The two-failure inputs break two adjacent checks at once, so a
helper that runs its checks in another order fails here. A failed engine
call must also leave the engine's snapshot unchanged. All ids are
synthetic.

Nothing here depends on the release version or the hash seed: snapshot
and summary digests leave out the version and fingerprint keys, no
message names a version or fingerprint, and no pinned message lists the
members of a set.
The pinned ratings, predictions, fits, and digests do depend on Config's
defaults and the rating math, so a change to a default or to rating
behavior, which changes published ratings anyway, needs these values
pinned again.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import random
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    GameFormat,
    Match,
    MatchStatus,
    MatchType,
    Player,
    RatingEngine,
    RatingFormat,
    ScoringType,
    evaluate,
    fit_scale,
    fit_win_calibration,
    ledger_order,
    prequential_predictions,
    replay,
)

D0 = date(2025, 1, 1)
SO, RA = ScoringType.SIDE_OUT, ScoringType.RALLY
DBL, SGL, MIX = RatingFormat.DOUBLES, RatingFormat.SINGLES, RatingFormat.MIXED_DOUBLES
PIDS = [f"q{i}" for i in range(10)]
# Keys that change with a release, left out of the digests.
_VERSIONED = ("engine_version", "config_version", "config_fingerprint")

Snap = dict[str, object]


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


def _game(rng: random.Random, to: int, a_wins: bool) -> tuple[int, int]:
    if rng.random() < 0.2:
        high = to + rng.randint(1, 3)
        low = high - 2
    else:
        high, low = to, rng.randint(0, to - 2)
    return (high, low) if a_wins else (low, high)


def _ledger(count: int, seed: int, prefix: str = "m") -> list[Match]:
    """A synthetic ledger: every format, both scorings, all match types, some non-completed."""
    rng = random.Random(seed)
    rows: list[Match] = []
    while len(rows) < count:
        fmt = rng.choice([DBL, DBL, SGL, MIX, RatingFormat.DOUBLES_MEN])
        size = fmt.team_size
        players = rng.sample(PIDS, 2 * size)
        team_a, team_b = tuple(players[:size]), tuple(players[size:])
        scoring = rng.choice([SO, RA])
        to = rng.choice([11, 15, 21])
        a_wins = rng.random() < 0.5
        best_of = rng.choice([1, 1, 3])
        if best_of == 1:
            games: tuple[tuple[int, int], ...] = (_game(rng, to, a_wins),)
        elif rng.random() < 0.5:
            games = (_game(rng, to, a_wins), _game(rng, to, a_wins))
        else:
            games = (_game(rng, to, a_wins), _game(rng, to, not a_wins), _game(rng, to, a_wins))
        match_type = rng.choice(list(MatchType))
        confirmed: frozenset[str] = frozenset()
        if match_type is MatchType.SELF_REPORTED and rng.random() < 0.8:
            confirmed = frozenset({team_a[0], team_b[0]})
        status = MatchStatus.COMPLETED
        if best_of == 3 and rng.random() < 0.08:
            status, games = MatchStatus.RETIRED, games[:1]
        match = Match(
            match_id=f"{prefix}{len(rows):03d}",
            team_a=team_a,
            team_b=team_b,
            games=games,
            played_on=D0 + timedelta(days=rng.randint(0, 120)),
            rating_format=fmt,
            match_type=match_type,
            best_of=best_of,
            game_format=GameFormat(scoring=scoring, points_to=to),
            sequence=rng.randint(0, 2),
            confirmed_by=confirmed,
            status=status,
        )
        if rows and rng.random() < 0.05:
            match = dataclasses.replace(rng.choice(rows), match_id=match.match_id)
        rows.append(match)
    return rows


def _seeds(cfg: Config) -> list[Player]:
    """Four external seeds dated inside the ledger, so some matches are before a baseline."""
    scratch = RatingEngine(cfg)
    for i, (pid, fmt, rating, day, rel) in enumerate(
        [
            ("q0", DBL, 4.4, 30, 80.0),
            ("q1", SGL, 3.2, 10, 40.0),
            ("q2", DBL, 3.9, 60, None),
            ("q3", SGL, 3.6, 50, 60.0),
        ]
    ):
        snap = ExternalRatingSnapshot("ext", rating, D0 + timedelta(days=day), rel, f"x{i}", "doc")
        scratch.seed_external_rating(pid, fmt, snap)
    return [copy.deepcopy(scratch.players[pid]) for pid in ("q0", "q1", "q2", "q3")]


# The last row is before both players' baselines, so every config records a
# seed-sensitive rejection for it.
ROWS = [
    *_ledger(70, 143),
    Match("frozen", ("q1",), ("q3",), ((11, 4),), D0 + timedelta(days=5), SGL),
]
AS_OF = D0 + timedelta(days=130)

CONFIGS: dict[str, Config] = {
    "default": Config(),
    "strict": Config(
        reject_content_duplicates=True,
        max_singles_rating_gap=0.3,
        max_doubles_partner_gap=0.4,
        self_reported_max_repeats=1,
    ),
    "nofeed": Config(category_feeds_parent=False, allowed_game_formats=frozenset({(SO, 11)})),
    "cap": Config(
        connectivity_reliability_cap=45.05,
        connectivity_min_shared_contacts=1,
        connectivity_min_contact_days=2,
        connectivity_min_outside_players=2,
    ),
}


# ---------------------------------------------------------------------------
# Match.validate and Match._validate_field_types: two failures per adjacent pair
# ---------------------------------------------------------------------------

BASE_MATCH = Match(
    "v1",
    ("q0", "q1"),
    ("q2", "q3"),
    ((11, 5), (5, 11), (11, 7)),
    D0,
    DBL,
    best_of=3,
    played_at=datetime(2025, 1, 1, 12, tzinfo=UTC),
    confirmed_by=frozenset({"q0", "q2"}),
)
CHI = timezone(timedelta(hours=-6))


class _OwnZone(tzinfo):
    """A tzinfo the engine must reject without calling it."""

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return timedelta(0)

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(0)


VALIDATE_CASES: dict[str, dict[str, object]] = {
    "valid": {},
    "match_id blank then type": {"match_id": " ", "status": "completed"},
    "match_id type then status": {"match_id": 5, "status": "completed"},
    "status then rating_format": {"status": None, "rating_format": "doubles"},
    "rating_format then match_type": {"rating_format": None, "match_type": "league"},
    "match_type then game_format": {"match_type": None, "game_format": "x"},
    "game_format then team_a": {"game_format": GameFormat(points_to=True), "team_a": ["q0", "q1"]},
    "team_a then team_b": {"team_a": "q0", "team_b": None},
    "team_b then confirmed_by": {"team_b": ["q2", "q3"], "confirmed_by": ["q0"]},
    "confirmed_by then played_on": {"confirmed_by": None, "played_on": "2025-01-01"},
    "played_on then played_at": {"played_on": datetime(2025, 1, 1), "played_at": "x"},
    "played_at type then sequence": {"played_at": date(2025, 1, 1), "sequence": 1.0},
    "played_at zone then sequence": {
        "played_at": datetime(2025, 1, 1, 12, tzinfo=_OwnZone()),
        "sequence": True,
    },
    "sequence then best_of type": {"sequence": None, "best_of": 3.0},
    "best_of type then team size": {"best_of": True, "team_a": ("q0",)},
    "team size then player ids": {"team_a": ("q0",), "team_b": ("", "q3")},
    "player ids then repeats": {"team_b": ("", "q0")},
    "repeats then confirmed ids": {"team_b": ("q0", "q3"), "confirmed_by": frozenset({""})},
    "confirmed ids then subset": {"confirmed_by": frozenset({"", "zz"})},
    "subset then aware": {"confirmed_by": frozenset({"zz"}), "played_at": datetime(2025, 1, 1, 12)},
    "aware then on played_on": {"played_at": datetime(2025, 1, 2, 12)},
    "on played_on then sequence": {
        "played_at": datetime(2025, 1, 2, 12, tzinfo=UTC),
        "sequence": -1,
    },
    "local date is played_on": {"played_at": datetime(2025, 1, 1, 23, tzinfo=CHI)},
    "sequence then games type": {"sequence": -1, "games": None},
    "games type then no games": {"games": None, "best_of": 0},
    "no games then forfeit": {"games": ()},
    "forfeit with games then shape": {"status": MatchStatus.FORFEIT, "games": ((11, 5, 1),)},
    "shape then best_of odd": {"games": ((11, 5, 1),), "best_of": 2},
    "best_of odd then too many": {"best_of": 2, "games": ((11, 5),) * 4},
    "too many then game format": {"best_of": 1, "game_format": GameFormat(win_by=0)},
    "game format then score types": {
        "game_format": GameFormat(cap=10),
        "games": ((11.0, 5), (5, 11), (11, 7)),
    },
    "score types then tied": {"games": ((11, 11), (True, 11), (11, 7))},
    "tied then negative": {"games": ((-1, 11), (11, 11), (11, 7))},
    "negative then game rule": {"games": ((11, 10), (-1, 11), (11, 7))},
    "game rule then after decided": {"games": ((11, 10), (11, 5), (11, 7))},
    "after decided then not decided": {"games": ((11, 5), (11, 5), (11, 7))},
    "not decided": {"games": ((11, 5), (5, 11))},
    "retired decided": {"status": MatchStatus.RETIRED},
    "retired undecided": {"status": MatchStatus.RETIRED, "games": ((11, 5),)},
    "incomplete decided": {"status": MatchStatus.INCOMPLETE, "games": ((11, 5), (11, 3))},
    "forfeit no games": {"status": MatchStatus.FORFEIT, "games": ()},
    "rally 21": {"game_format": GameFormat(RA, 21), "games": ((21, 19), (19, 21), (23, 21))},
    "capped": {"game_format": GameFormat(SO, 11, 2, 13), "games": ((13, 12), (5, 11), (11, 7))},
    "past cap": {"game_format": GameFormat(SO, 11, 2, 13), "games": ((14, 12), (5, 11), (11, 7))},
}


VALIDATE_EXPECTED: dict[str, str] = {
    "after decided then not decided": "InvalidMatchError: No games may be recorded after "
    "the match is decided | ok 7325f1e70b061994",
    "aware then on played_on": "InvalidMatchError: played_at must be timezone-aware | "
    "InvalidMatchError: played_at must be timezone-aware",
    "best_of odd then too many": "InvalidMatchError: best_of must be a positive odd "
    "number | ok 7325f1e70b061994",
    "best_of type then team size": "InvalidMatchError: best_of must be an int, not bool | "
    "InvalidMatchError: best_of must be an int, not bool",
    "capped": "ok f2953d2931e36d0c | ok 7325f1e70b061994",
    "confirmed ids then subset": "InvalidMatchError: confirmed_by player ids must be "
    "non-empty strings | ok 7325f1e70b061994",
    "confirmed_by then played_on": "InvalidMatchError: confirmed_by must be a set or "
    "frozenset, not NoneType | InvalidMatchError: "
    "confirmed_by must be a set or frozenset, not NoneType",
    "forfeit no games": "ok f2953d2931e36d0c | ok 7325f1e70b061994",
    "forfeit with games then shape": "InvalidMatchError: A forfeit cannot have game "
    "scores | ok 7325f1e70b061994",
    "game format then score types": "InvalidMatchError: cap must be greater than "
    "points_to | ok 7325f1e70b061994",
    "game rule then after decided": "InvalidMatchError: Game was not won by the required "
    "margin | ok 7325f1e70b061994",
    "game_format then team_a": "InvalidMatchError: points_to must be an int, not bool | "
    "InvalidMatchError: points_to must be an int, not bool",
    "games type then no games": "InvalidMatchError: games must be a tuple or list, not "
    "NoneType | ok 7325f1e70b061994",
    "incomplete decided": "InvalidMatchError: A incomplete match cannot have a decided "
    "winner | ok 7325f1e70b061994",
    "local date is played_on": "ok f2953d2931e36d0c | ok 1e9d879ec0574558",
    "match_id blank then type": "InvalidMatchError: match_id is required for duplicate "
    "protection | InvalidMatchError: status must be a "
    "MatchStatus, not str",
    "match_id type then status": "InvalidMatchError: match_id is required for duplicate "
    "protection | InvalidMatchError: match_id must be a str, "
    "not int",
    "match_type then game_format": "InvalidMatchError: match_type must be a MatchType, "
    "not NoneType | InvalidMatchError: match_type must be "
    "a MatchType, not NoneType",
    "negative then game rule": "InvalidMatchError: Game was not won by the required "
    "margin | ok 7325f1e70b061994",
    "no games then forfeit": "InvalidMatchError: At least one game score is required | ok "
    "7325f1e70b061994",
    "not decided": "InvalidMatchError: Match score does not contain a completed winner | "
    "ok 7325f1e70b061994",
    "on played_on then sequence": "InvalidMatchError: played_at must fall on played_on | "
    "ok 33dad5b9d65e2750",
    "past cap": "InvalidMatchError: Game score exceeds the cap | ok 7325f1e70b061994",
    "played_at type then sequence": "InvalidMatchError: played_at must be a datetime or "
    "None, not date | InvalidMatchError: played_at must "
    "be a datetime or None, not date",
    "played_at zone then sequence": "InvalidMatchError: played_at tzinfo must be exactly "
    "datetime.timezone or zoneinfo.ZoneInfo, not "
    "_OwnZone; convert with "
    "played_at.astimezone(timezone(timedelta(seconds=played_at.utcoffset().total_seconds()))) "
    "| InvalidMatchError: played_at tzinfo must be "
    "exactly datetime.timezone or zoneinfo.ZoneInfo, not "
    "_OwnZone; convert with "
    "played_at.astimezone(timezone(timedelta(seconds=played_at.utcoffset().total_seconds())))",
    "played_on then played_at": "InvalidMatchError: played_on must be a plain date (not a "
    "datetime), not datetime | InvalidMatchError: played_on "
    "must be a plain date (not a datetime), not datetime",
    "player ids then repeats": "InvalidMatchError: Player ids must be non-empty strings | "
    "ok 7325f1e70b061994",
    "rally 21": "ok f2953d2931e36d0c | ok 7325f1e70b061994",
    "rating_format then match_type": "InvalidMatchError: rating_format must be a "
    "RatingFormat, not NoneType | InvalidMatchError: "
    "rating_format must be a RatingFormat, not NoneType",
    "repeats then confirmed ids": "InvalidMatchError: A player cannot appear more than "
    "once in a match | ok 7325f1e70b061994",
    "retired decided": "InvalidMatchError: A retired match cannot have a decided winner | "
    "ok 7325f1e70b061994",
    "retired undecided": "ok f2953d2931e36d0c | ok 7325f1e70b061994",
    "score types then tied": "InvalidMatchError: Game scores must be nonnegative and "
    "cannot be tied | ok 7325f1e70b061994",
    "sequence then best_of type": "InvalidMatchError: sequence must be an int, not "
    "NoneType | InvalidMatchError: sequence must be an int, "
    "not NoneType",
    "sequence then games type": "InvalidMatchError: sequence must be nonnegative | ok "
    "b5d0d75f6cb16704",
    "shape then best_of odd": "InvalidMatchError: Each game must be a pair of scores | ok "
    "7325f1e70b061994",
    "status then rating_format": "InvalidMatchError: status must be a MatchStatus, not "
    "NoneType | InvalidMatchError: status must be a "
    "MatchStatus, not NoneType",
    "subset then aware": "InvalidMatchError: confirmed_by may only contain match "
    "participants | InvalidMatchError: played_at must be "
    "timezone-aware",
    "team size then player ids": "InvalidMatchError: doubles requires 2 player(s) per "
    "team | ok 7325f1e70b061994",
    "team_a then team_b": "InvalidMatchError: team_a must be a tuple, not str | "
    "InvalidMatchError: team_a must be a tuple, not str",
    "team_b then confirmed_by": "InvalidMatchError: team_b must be a tuple, not list | "
    "InvalidMatchError: team_b must be a tuple, not list",
    "tied then negative": "InvalidMatchError: Game scores must be nonnegative and cannot "
    "be tied | ok 7325f1e70b061994",
    "too many then game format": "InvalidMatchError: Games played cannot exceed best_of | "
    "ok 7325f1e70b061994",
    "valid": "ok f2953d2931e36d0c | ok 7325f1e70b061994",
}


@pytest.mark.parametrize("name", sorted(VALIDATE_CASES))
def test_match_checks_keep_their_order(name: str) -> None:
    match = dataclasses.replace(BASE_MATCH, **VALIDATE_CASES[name])  # type: ignore[arg-type]
    outcome = " | ".join((_outcome(match.validate), _outcome(lambda: ledger_order(match))))
    assert outcome == VALIDATE_EXPECTED[name]


# ---------------------------------------------------------------------------
# record (_consider, _compute): every result and the engine after each call
# ---------------------------------------------------------------------------


def _live_rows() -> list[Match]:
    """ROWS in arrival order, with repeats, an unknown format, and rows that raise."""
    rows = list(ROWS)
    rows.insert(10, ROWS[3])
    rows.insert(20, dataclasses.replace(ROWS[4], match_id="dup-content"))
    rows.insert(30, dataclasses.replace(ROWS[5], best_of=2))
    rows.insert(40, dataclasses.replace(ROWS[6], team_b=("zz",)))
    rows.insert(50, dataclasses.replace(ROWS[7], match_id="late", played_on=D0))
    return rows


RECORD_EXPECTED: dict[str, list[str]] = {
    "cap": [
        "ok a208a4118c6d0e0a",
        "ok aaffcf96034a76bb",
        "ok 605c7cab5f3be17d",
        "ok b0c1a23ba5034e21",
        "ok a19129f6ca9c72e9",
        "ok 922bcbdae8e34254",
        "ok 54e0b185f6cca334",
        "ok 965de7cfc024f8da",
        "ok 60dd49f601fdb5b7",
        "ok 0d206d8f99754ae0",
        "ok ffee21826953f49a",
        "ok c27a72f3c4ecfaac",
        "ok 9efa76105d7e7040",
        "ok 923b570cf9703970",
        "ok e2a788bdbec8cd7f",
        "ok dd6d2e8ddc4c353d",
        "ok dfc8b4986e73d933",
        "ok be4cbcdc806219ae",
        "ok 9caac2112b30ed95",
        "ok b0ad1e2a1ac65cf8",
        "ok 4c9cb13ed83f3741",
        "ok b2b578c213ed8530",
        "ok de99037f62fa8375",
        "ok 0495bacdd5846810",
        "ok 923b570cf9703970",
        "ok 1bb056f07f11e317",
        "ok 923b570cf9703970",
        "ok 5e7b495fa706bab5",
        "ok 57bd920e5d3ff7b9",
        "ok a80d3aa3fcc11b33",
        "InvalidMatchError: best_of must be a positive odd number",
        "ok 34a8ed3560644ac6",
        "ok 01243a57fb85eda6",
        "ok a653b06666f041c2",
        "ok 72d5794e58dffe32",
        "ok 606c4f371defb224",
        "ok 096f6c7ca83a4756",
        "ok c27a72f3c4ecfaac",
        "ok de1f90b2fceb1083",
        "ok 33e39b4005c3547f",
        "InvalidMatchError: mixed_doubles requires 2 player(s) per team",
        "ok 0495bacdd5846810",
        "ok 3c7b685c6e39b591",
        "ok c27a72f3c4ecfaac",
        "ok 92b4e93e44e2dfc6",
        "ok b7517244639bee88",
        "ok d197ace300432ac4",
        "ok b1c70c08476e0126",
        "ok c27a72f3c4ecfaac",
        "ok 770e6a8ec99ad1af",
        "ok da5b0fd8463ed345",
        "ok 1518a1e0aca91a74",
        "ok a9a478c983d4fc6a",
        "ok e19dad713489c504",
        "ok d3eb63f72341d80c",
        "ok 4ec3359ca9d10682",
        "ok b82c0cc2f6e5f489",
        "ok 05b15e2e770252ba",
        "ok 6cfc7f6107fb2e8a",
        "ok 0454dbc7ef2d141a",
        "ok 010fccb00bfc1437",
        "ok c72b1ddcdaa7788f",
        "ok fd419b4bace2e231",
        "ok 9142c2f6666c9cf1",
        "ok b65dd66fc8d658e7",
        "ok 923b570cf9703970",
        "ok 69642c3a45d8e1a6",
        "ok bb1d7bcc43404da1",
        "ok 08dd7fea842b1ea4",
        "ok c40780580dc531c6",
        "ok 9b6e8752303fe7c7",
        "ok 923b570cf9703970",
        "ok 923b570cf9703970",
        "ok 3adc3c2129c3b164",
        "ok d5aefdc6e0ab7575",
        "ok 36a3e0e218a647a2",
        "9525e5f050cd49cb",
    ],
    "default": [
        "ok a208a4118c6d0e0a",
        "ok aaffcf96034a76bb",
        "ok 605c7cab5f3be17d",
        "ok b0c1a23ba5034e21",
        "ok a19129f6ca9c72e9",
        "ok 922bcbdae8e34254",
        "ok 54e0b185f6cca334",
        "ok 965de7cfc024f8da",
        "ok 60dd49f601fdb5b7",
        "ok 0d206d8f99754ae0",
        "ok ffee21826953f49a",
        "ok c27a72f3c4ecfaac",
        "ok 9efa76105d7e7040",
        "ok 923b570cf9703970",
        "ok e2a788bdbec8cd7f",
        "ok dd6d2e8ddc4c353d",
        "ok dfc8b4986e73d933",
        "ok be4cbcdc806219ae",
        "ok 9caac2112b30ed95",
        "ok b0ad1e2a1ac65cf8",
        "ok 4c9cb13ed83f3741",
        "ok b2b578c213ed8530",
        "ok de99037f62fa8375",
        "ok 0495bacdd5846810",
        "ok 923b570cf9703970",
        "ok 1bb056f07f11e317",
        "ok 923b570cf9703970",
        "ok 5e7b495fa706bab5",
        "ok 57bd920e5d3ff7b9",
        "ok a80d3aa3fcc11b33",
        "InvalidMatchError: best_of must be a positive odd number",
        "ok 34a8ed3560644ac6",
        "ok 01243a57fb85eda6",
        "ok a653b06666f041c2",
        "ok 72d5794e58dffe32",
        "ok 606c4f371defb224",
        "ok 096f6c7ca83a4756",
        "ok c27a72f3c4ecfaac",
        "ok de1f90b2fceb1083",
        "ok 33e39b4005c3547f",
        "InvalidMatchError: mixed_doubles requires 2 player(s) per team",
        "ok 0495bacdd5846810",
        "ok 3c7b685c6e39b591",
        "ok c27a72f3c4ecfaac",
        "ok 92b4e93e44e2dfc6",
        "ok b7517244639bee88",
        "ok d197ace300432ac4",
        "ok b1c70c08476e0126",
        "ok c27a72f3c4ecfaac",
        "ok 770e6a8ec99ad1af",
        "ok da5b0fd8463ed345",
        "ok 1518a1e0aca91a74",
        "ok a9a478c983d4fc6a",
        "ok e19dad713489c504",
        "ok d3eb63f72341d80c",
        "ok 4ec3359ca9d10682",
        "ok b82c0cc2f6e5f489",
        "ok 05b15e2e770252ba",
        "ok 6cfc7f6107fb2e8a",
        "ok 0454dbc7ef2d141a",
        "ok 010fccb00bfc1437",
        "ok c72b1ddcdaa7788f",
        "ok fd419b4bace2e231",
        "ok 9142c2f6666c9cf1",
        "ok b65dd66fc8d658e7",
        "ok 923b570cf9703970",
        "ok 69642c3a45d8e1a6",
        "ok bb1d7bcc43404da1",
        "ok 08dd7fea842b1ea4",
        "ok c40780580dc531c6",
        "ok 9b6e8752303fe7c7",
        "ok 923b570cf9703970",
        "ok 923b570cf9703970",
        "ok 3adc3c2129c3b164",
        "ok d5aefdc6e0ab7575",
        "ok 36a3e0e218a647a2",
        "9525e5f050cd49cb",
    ],
    "nofeed": [
        "ok a208a4118c6d0e0a",
        "ok a89ced5b577c0de4",
        "ok 2d2d0959194459a8",
        "ok 1f25af706b1be44d",
        "ok a89ced5b577c0de4",
        "ok ae04a2f4cd905f4e",
        "ok a89ced5b577c0de4",
        "ok ae04a2f4cd905f4e",
        "ok 2d2d0959194459a8",
        "ok b8a90b932fe6994e",
        "ok ffee21826953f49a",
        "ok c27a72f3c4ecfaac",
        "ok a89ced5b577c0de4",
        "ok 2d2d0959194459a8",
        "ok 2d2d0959194459a8",
        "ok ea85f4c1c899e707",
        "ok ae04a2f4cd905f4e",
        "ok a89ced5b577c0de4",
        "ok 2d2d0959194459a8",
        "ok b8a90b932fe6994e",
        "ok a89ced5b577c0de4",
        "ok b8a90b932fe6994e",
        "ok b8a90b932fe6994e",
        "ok 0495bacdd5846810",
        "ok 2d2d0959194459a8",
        "ok ae04a2f4cd905f4e",
        "ok 2d2d0959194459a8",
        "ok 8ea68a9234d1b7b0",
        "ok 2d2d0959194459a8",
        "ok 0b433bdeae7842ec",
        "InvalidMatchError: best_of must be a positive odd number",
        "ok 2d2d0959194459a8",
        "ok 2d2d0959194459a8",
        "ok 2d2d0959194459a8",
        "ok ae04a2f4cd905f4e",
        "ok a89ced5b577c0de4",
        "ok ae04a2f4cd905f4e",
        "ok a89ced5b577c0de4",
        "ok ae04a2f4cd905f4e",
        "ok 6670509f50ebdfe1",
        "InvalidMatchError: mixed_doubles requires 2 player(s) per team",
        "ok 0495bacdd5846810",
        "ok a89ced5b577c0de4",
        "ok a89ced5b577c0de4",
        "ok 47fbab74e562468c",
        "ok ff4598da95602655",
        "ok 2d2d0959194459a8",
        "ok 2d2d0959194459a8",
        "ok a89ced5b577c0de4",
        "ok 31d0b83a50600bd3",
        "ok ae04a2f4cd905f4e",
        "ok a89ced5b577c0de4",
        "ok 2d2d0959194459a8",
        "ok 2d2d0959194459a8",
        "ok ae04a2f4cd905f4e",
        "ok 2d2d0959194459a8",
        "ok 2d2d0959194459a8",
        "ok a89ced5b577c0de4",
        "ok b8a90b932fe6994e",
        "ok d203f51a29facebe",
        "ok 2d2d0959194459a8",
        "ok 2d2d0959194459a8",
        "ok b8a90b932fe6994e",
        "ok a89ced5b577c0de4",
        "ok 7b3d7a54ec07cf6e",
        "ok 2d2d0959194459a8",
        "ok 2d2d0959194459a8",
        "ok e24752788e894369",
        "ok 2d2d0959194459a8",
        "ok 2e7c03d4b5d3ce76",
        "ok 4d528639e77d8221",
        "ok 2d2d0959194459a8",
        "ok 2d2d0959194459a8",
        "ok b8a90b932fe6994e",
        "ok 2d2d0959194459a8",
        "ok 36a3e0e218a647a2",
        "6ba6b605710ce23a",
    ],
    "strict": [
        "ok 5199f201fc6d9676",
        "ok dde4678db978cfb2",
        "ok 1a3296fe344a5e6f",
        "ok 1770a11b2e0bd653",
        "ok 0b78068fecbcb6c1",
        "ok 922bcbdae8e34254",
        "ok 0c4e74c725af8da5",
        "ok dee96bd708827c02",
        "ok 5199f201fc6d9676",
        "ok 0d206d8f99754ae0",
        "ok ffee21826953f49a",
        "ok c27a72f3c4ecfaac",
        "ok 60a5ec004d6a1c03",
        "ok 923b570cf9703970",
        "ok 0b27e93faaa22ebe",
        "ok 18892e7f732e6950",
        "ok 3e797c95576ca939",
        "ok 60a5ec004d6a1c03",
        "ok 5199f201fc6d9676",
        "ok b0ad1e2a1ac65cf8",
        "ok 0b78068fecbcb6c1",
        "ok b2b578c213ed8530",
        "ok de99037f62fa8375",
        "ok 0495bacdd5846810",
        "ok 923b570cf9703970",
        "ok 1770a11b2e0bd653",
        "ok 923b570cf9703970",
        "ok 1ae6a7fecf60d4ee",
        "ok caaee65b895241a7",
        "ok b26f67ceb53b4cfd",
        "InvalidMatchError: best_of must be a positive odd number",
        "ok 7574330f07504831",
        "ok aedc79b8f4302968",
        "ok 5199f201fc6d9676",
        "ok 1770a11b2e0bd653",
        "ok 0b78068fecbcb6c1",
        "ok 1770a11b2e0bd653",
        "ok c27a72f3c4ecfaac",
        "ok 65945f031cdd2ec3",
        "ok 7675c23901f97ba8",
        "InvalidMatchError: mixed_doubles requires 2 player(s) per team",
        "ok 0495bacdd5846810",
        "ok 60a5ec004d6a1c03",
        "ok c27a72f3c4ecfaac",
        "ok 5199f201fc6d9676",
        "ok 5199f201fc6d9676",
        "ok 5199f201fc6d9676",
        "ok 5575fe46ed122a38",
        "ok c27a72f3c4ecfaac",
        "ok 5199f201fc6d9676",
        "ok 39d10a1a3b31896f",
        "ok 60a5ec004d6a1c03",
        "ok 5199f201fc6d9676",
        "ok 5199f201fc6d9676",
        "ok a54b06bef02b2d48",
        "ok d34546aacf5a7b10",
        "ok 5199f201fc6d9676",
        "ok 60a5ec004d6a1c03",
        "ok 45130a1aca2cc603",
        "ok ef0c046f0767d7c0",
        "ok 5199f201fc6d9676",
        "ok a0678e89298bf2d7",
        "ok 274b8c090120cb3f",
        "ok 60a5ec004d6a1c03",
        "ok b65dd66fc8d658e7",
        "ok 923b570cf9703970",
        "ok 5199f201fc6d9676",
        "ok 5199f201fc6d9676",
        "ok 438b9454e5c5b331",
        "ok 46e9cc32e387cf5b",
        "ok 5199f201fc6d9676",
        "ok 923b570cf9703970",
        "ok 923b570cf9703970",
        "ok 3adc3c2129c3b164",
        "ok 5199f201fc6d9676",
        "ok 36a3e0e218a647a2",
        "801cdc59400fd933",
    ],
}


@pytest.mark.parametrize("cfg_name", sorted(CONFIGS))
def test_live_records_keep_their_results(cfg_name: str) -> None:
    engine = RatingEngine(CONFIGS[cfg_name])
    for player in _seeds(CONFIGS[cfg_name]):
        engine.add_player(player)
    outcomes = []
    for match in _live_rows():
        before = _snapshot_digest(engine)
        outcome = _outcome(lambda m=match: engine.record(m))  # type: ignore[misc]
        if not outcome.startswith("ok "):
            assert _snapshot_digest(engine) == before
        outcomes.append(outcome)
    outcomes.append(_snapshot_digest(engine))
    assert outcomes == RECORD_EXPECTED[cfg_name]


# ---------------------------------------------------------------------------
# from_snapshot: _check_resumable and _snap_seed_sensitive_rejections
# ---------------------------------------------------------------------------


def _replayed(cfg_name: str) -> RatingEngine:
    cfg = CONFIGS[cfg_name]
    return replay(ROWS, _seeds(cfg), cfg=cfg, as_of=AS_OF)


def _shift(text: object, days: int) -> str:
    assert isinstance(text, str)
    return (date.fromisoformat(text) + timedelta(days=days)).isoformat()


def _state(snap: Snap, index: int, fmt: str) -> dict[str, object]:
    return snap["players"][index]["ratings"][fmt]  # type: ignore[index,no-any-return]


def _played(snap: Snap) -> list[tuple[int, str]]:
    """(player index, format) of every played state, in snapshot order."""
    found = []
    for index, player in enumerate(snap["players"]):  # type: ignore[arg-type]
        for fmt in sorted(player["ratings"]):
            if player["ratings"][fmt]["matches_played"]:
                found.append((index, fmt))
    return found


def _dated_played(snap: Snap) -> tuple[int, str]:
    """A played state with an initial_source_date."""
    for index, fmt in _played(snap):
        if _state(snap, index, fmt)["initial_source_date"] is not None:
            return index, fmt
    raise AssertionError("no dated played state")


def _resumable_breakers(snap: Snap) -> dict[str, Callable[[Snap], None]]:
    """One break per _check_resumable check, in its order, on three players."""
    as_of = snap["calculated_as_of"]
    played = _played(snap)
    early, late = played[0], played[-1]
    partnered = next(p for p in played if _state(snap, *p)["partners"])
    dated = _dated_played(snap)
    out: dict[str, Callable[[Snap], None]] = {}
    for tag, (index, fmt) in (("early", early), ("partnered", partnered), ("late", late)):
        pid = snap["players"][index]["id"]  # type: ignore[index]

        def drop(s: Snap, pid: object = pid) -> None:
            s["ledger_player_ids"] = [x for x in s["ledger_player_ids"] if x != pid]  # type: ignore[attr-defined]

        def after(s: Snap, key: str, index: int = index, fmt: str = fmt) -> None:
            state = _state(s, index, fmt)
            if key in ("partners", "opponents"):
                seen = state[key]
                seen[sorted(seen)[-1]] = _shift(as_of, 1)  # type: ignore[call-overload,index]
            else:
                state[key] = _shift(as_of, 1)

        out[f"{tag} ledger id"] = drop
        out[f"{tag} last_played"] = lambda s, after=after: after(s, "last_played")
        out[f"{tag} rd_as_of"] = lambda s, after=after: after(s, "rd_as_of")
        out[f"{tag} inherited"] = lambda s, i=index, f=fmt: _state(s, i, f).update(
            inherited_live_updates=1
        )
        out[f"{tag} partner"] = lambda s, after=after: after(s, "partners")
        out[f"{tag} opponent"] = lambda s, after=after: after(s, "opponents")
        out[f"{tag} live"] = lambda s, i=index, f=fmt: _state(s, i, f).update(
            live_updates_since_replay=1
        )
    index, fmt = dated

    def source_at_last(s: Snap) -> None:
        state = _state(s, index, fmt)
        state["initial_source_date"] = state["last_played"]

    def source_at_contact(s: Snap) -> None:
        state = _state(s, index, fmt)
        seen = sorted(set(state["partners"].values()) | set(state["opponents"].values()))  # type: ignore[attr-defined]
        state["initial_source_date"] = seen[0]

    def log_after(s: Snap) -> None:
        s["self_reported_log"][0]["dates"][-1] = _shift(as_of, 1)  # type: ignore[index]

    def undigested(s: Snap) -> None:
        del s["ledger_digests"][s["processed_match_ids"][0]]  # type: ignore[attr-defined,index]
        s["replay_info"]["matches_considered"] -= 1  # type: ignore[index]

    out["source at last_played"] = source_at_last
    out["source at a contact"] = source_at_contact
    out["source after as_of"] = lambda s: _state(s, index, fmt).update(
        initial_source_date=_shift(as_of, 1)
    )
    out["log after"] = log_after
    out["considered"] = lambda s: s["replay_info"].update(  # type: ignore[attr-defined]
        matches_considered=s["replay_info"]["matches_considered"] + 1  # type: ignore[index]
    )
    out["counted"] = lambda s: s["replay_info"].update(  # type: ignore[attr-defined]
        matches_counted=s["replay_info"]["matches_counted"] - 1  # type: ignore[index]
    )
    out["undigested"] = undigested
    derived = (
        next(
            (i, fmt)
            for i, player in enumerate(snap["players"])  # type: ignore[arg-type]
            for fmt in sorted(player["ratings"])
            if player["ratings"][fmt]["initial_source"].startswith("open_parent:")
            and "doubles" in player["ratings"]
        )
        if snap["players"]
        and any(
            st["initial_source"].startswith("open_parent:")
            for player in snap["players"]  # type: ignore[attr-defined]
            for st in player["ratings"].values()
        )
        else None
    )

    def derived_inherited(s: Snap) -> None:
        # Loads but for resumability: the parent's live count covers it.
        assert derived is not None
        _state(s, *derived)["inherited_live_updates"] = 1
        _state(s, derived[0], "doubles")["live_updates_since_replay"] = 1

    if derived is not None:
        out["derived inherited"] = derived_inherited
    return out


def _rejection_breakers(snap: Snap) -> dict[str, Callable[[Snap], None]]:
    """One break per _snap_seed_sensitive_rejections check, in its order."""
    entries: dict[str, dict[str, object]] = snap["seed_sensitive_rejections"]  # type: ignore[assignment]
    keys = sorted(entries)
    first, last = keys[0], keys[-1]
    processed = sorted(set(snap["processed_match_ids"]) - set(entries))  # type: ignore[arg-type]
    reserved = sorted(set(snap["reserved_match_ids"]) - set(entries))  # type: ignore[arg-type]
    duplicates = sorted(snap["possible_duplicates"])  # type: ignore[arg-type]
    four = ["q4", "q5", "q6", "q7"]

    def rej(s: Snap) -> dict[str, dict[str, object]]:
        return s["seed_sensitive_rejections"]  # type: ignore[return-value]

    def add(key: str, players: list[str], fmt: str) -> Callable[[Snap], None]:
        return lambda s: rej(s).update({key: {"players": players, "rating_format": fmt}})

    out: dict[str, Callable[[Snap], None]] = {
        "not an object": lambda s: s.update(seed_sensitive_rejections=[]),
        "blank key": add(" ", four, "doubles"),
        "entry not an object": lambda s: rej(s).update({first: []}),
        "missing key": lambda s: rej(s)[first].pop("players"),
        "unknown key": lambda s: rej(s)[first].update(zz=1),
        "format": lambda s: rej(s)[first].update(rating_format="x"),
        "players not a list": lambda s: rej(s)[first].update(players="q0"),
        "player blank": lambda s: rej(s)[first].update(players=["", "q1", "q2", "q3"]),
        "player count": lambda s: rej(s)[first].update(players=["q1", "q2", "q3"]),
        "player order": lambda s: rej(s)[first].update(
            players=list(reversed(rej(s)[first]["players"]))  # type: ignore[call-overload]
        ),
        "last format": lambda s: rej(s)[last].update(rating_format=5),
        "last player order": lambda s: rej(s)[last].update(
            players=list(reversed(rej(s)[last]["players"]))  # type: ignore[call-overload]
        ),
        "processed singles": add(processed[0], ["q4", "q5"], "singles"),
        "processed unknown": add(processed[0], ["q4", "q5", "q6", "zz"], "doubles"),
        "processed no match": add(processed[0], four, "doubles"),
        "duplicate": add(duplicates[0], four, "doubles") if duplicates else add("n", four, "x"),
        "neither": add("nope", four, "doubles"),
        "reserved no baseline": add(reserved[0], four, "doubles"),
        "reserved unknown": add(reserved[0], ["q4", "q5", "q6", "zz"], "doubles"),
        "reserved singles": add(reserved[-1], ["q8", "q9"], "singles"),
    }
    if snap["ledger_player_ids"] is not None:

        def unlisted(s: Snap) -> None:
            named = rej(s)[last]["players"][0]  # type: ignore[index]
            s["ledger_player_ids"] = [x for x in s["ledger_player_ids"] if x != named]  # type: ignore[attr-defined]

        out["not in ledger_player_ids"] = unlisted
    return out


def _snapshot_cases() -> dict[str, tuple[str, str, tuple[str, ...]]]:
    """name: (config name, breaker group, breakers), single and adjacent pairs."""
    cases: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    for cfg_name in ("default", "strict", "nofeed"):
        snap = _replayed(cfg_name).to_snapshot()
        for group, breakers in (
            ("resumable", _resumable_breakers(snap)),
            ("rejections", _rejection_breakers(snap)),
        ):
            names = list(breakers)
            for name in names:
                cases[f"{cfg_name} {group} {name}"] = (cfg_name, group, (name,))
            for a, b in zip(names, names[1:], strict=False):
                cases[f"{cfg_name} {group} {a} + {b}"] = (cfg_name, group, (a, b))
                cases[f"{cfg_name} {group} {b} + {a}"] = (cfg_name, group, (b, a))
    # One break in each of _check_resumable's passes: the first pass (dates
    # after as_of, inherited_live_updates) must report before the second
    # (dates on or before initial_source_date), on the same and on another player.
    for cfg_name in ("default", "strict", "nofeed"):
        firsts = ["partnered partner", "late last_played", "log after"]
        if cfg_name != "nofeed":
            firsts.append("derived inherited")
        for first in firsts:
            for pair in ((first, "source at a contact"), ("source at a contact", first)):
                cases[f"{cfg_name} resumable passes {' + '.join(pair)}"] = (
                    cfg_name,
                    "resumable",
                    pair,
                )
    return cases


SNAPSHOT_CASES = _snapshot_cases()
SNAPSHOT_EXPECTED: dict[str, str] = {
    "default rejections blank key": "SnapshotError: snapshot.seed_sensitive_rejections "
    "has a key that is not a non-blank str | "
    "SnapshotError: snapshot.seed_sensitive_rejections "
    "has a key that is not a non-blank str",
    "default rejections blank key + entry not an object": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object",
    "default rejections blank key + not an object": "SnapshotError: "
    "snapshot.seed_sensitive_rejections "
    "must be an object | SnapshotError: "
    "snapshot.seed_sensitive_rejections "
    "must be an object",
    "default rejections duplicate": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'].players "
    "names 'q4', which has no dated doubles baseline; "
    "with no gap rule for doubles, only "
    "before_imported_baseline is recorded, and it freezes "
    "every player | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'].players "
    "names 'q4', which has no dated doubles baseline; "
    "with no gap rule for doubles, only "
    "before_imported_baseline is recorded, and it freezes "
    "every player",
    "default rejections duplicate + neither": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'].players "
    "names 'q4', which has no dated doubles "
    "baseline; with no gap rule for doubles, "
    "only before_imported_baseline is recorded, "
    "and it freezes every player | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'].players "
    "names 'q4', which has no dated doubles "
    "baseline; with no gap rule for doubles, "
    "only before_imported_baseline is recorded, "
    "and it freezes every player",
    "default rejections duplicate + processed no match": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'].players "
    "names 'q4', which has no dated "
    "doubles baseline; with no gap "
    "rule for doubles, only "
    "before_imported_baseline is "
    "recorded, and it freezes every "
    "player | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'].players "
    "names 'q4', which has no dated "
    "doubles baseline; with no gap "
    "rule for doubles, only "
    "before_imported_baseline is "
    "recorded, and it freezes every "
    "player",
    "default rejections entry not an object": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object",
    "default rejections entry not an object + blank key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object",
    "default rejections entry not an object + missing key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object",
    "default rejections format": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "default rejections format + players not a list": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "default rejections format + unknown key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "unknown key 'zz' | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "unknown key 'zz'",
    "default rejections last format": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "default rejections last format + last player order": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "default rejections last format + player order": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "default rejections last player order": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no repeats | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no repeats",
    "default rejections last player order + last format": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "default rejections last player order + processed singles": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no "
    "repeats | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no "
    "repeats",
    "default rejections missing key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "missing key 'players' | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "missing key 'players'",
    "default rejections missing key + entry not an object": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object",
    "default rejections missing key + unknown key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "missing key 'players' | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "missing key 'players'",
    "default rejections neither": "SnapshotError: snapshot.seed_sensitive_rejections has "
    "key 'nope', which is in neither processed_match_ids "
    "nor reserved_match_ids | SnapshotError: "
    "snapshot.seed_sensitive_rejections has key 'nope', "
    "which is in neither processed_match_ids nor "
    "reserved_match_ids",
    "default rejections neither + duplicate": "SnapshotError: "
    "snapshot.seed_sensitive_rejections has key "
    "'nope', which is in neither "
    "processed_match_ids nor reserved_match_ids "
    "| SnapshotError: "
    "snapshot.seed_sensitive_rejections has key "
    "'nope', which is in neither "
    "processed_match_ids nor reserved_match_ids",
    "default rejections neither + reserved no baseline": "SnapshotError: "
    "snapshot.seed_sensitive_rejections "
    "has key 'nope', which is in "
    "neither processed_match_ids nor "
    "reserved_match_ids | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections "
    "has key 'nope', which is in "
    "neither processed_match_ids nor "
    "reserved_match_ids",
    "default rejections not an object": "SnapshotError: "
    "snapshot.seed_sensitive_rejections must be an "
    "object | SnapshotError: "
    "snapshot.seed_sensitive_rejections must be an "
    "object",
    "default rejections not an object + blank key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections "
    "must be an object | SnapshotError: "
    "snapshot.seed_sensitive_rejections "
    "must be an object",
    "default rejections not in ledger_player_ids": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which is not in "
    "ledger_player_ids in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which is not in "
    "ledger_player_ids in a resumable "
    "snapshot",
    "default rejections not in ledger_player_ids + reserved singles": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which "
    "is not in "
    "ledger_player_ids "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which "
    "is not in "
    "ledger_player_ids "
    "in a resumable "
    "snapshot",
    "default rejections player blank": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players[0] "
    "must be a non-blank str | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players[0] "
    "must be a non-blank str",
    "default rejections player blank + player count": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles",
    "default rejections player blank + players not a list": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be a list | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be a list",
    "default rejections player count": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles",
    "default rejections player count + player blank": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players[0] "
    "must be a non-blank str | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players[0] "
    "must be a non-blank str",
    "default rejections player count + player order": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles",
    "default rejections player order": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no repeats | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no repeats",
    "default rejections player order + last format": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "default rejections player order + player count": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles",
    "default rejections players not a list": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be a list | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be a list",
    "default rejections players not a list + format": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "default rejections players not a list + player blank": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players[0] "
    "must be a non-blank str | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players[0] "
    "must be a non-blank str",
    "default rejections processed no match": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q4', which has no dated doubles "
    "baseline; with no gap rule for doubles, "
    "only before_imported_baseline is recorded, "
    "and it freezes every player | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q4', which has no dated doubles "
    "baseline; with no gap rule for doubles, "
    "only before_imported_baseline is recorded, "
    "and it freezes every player",
    "default rejections processed no match + duplicate": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q4', which has no dated "
    "doubles baseline; with no gap "
    "rule for doubles, only "
    "before_imported_baseline is "
    "recorded, and it freezes every "
    "player | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q4', which has no dated "
    "doubles baseline; with no gap "
    "rule for doubles, only "
    "before_imported_baseline is "
    "recorded, and it freezes every "
    "player",
    "default rejections processed no match + processed unknown": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'zz', which is "
    "not in players | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'zz', which is "
    "not in players",
    "default rejections processed singles": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] "
    "is a processed match id, so its "
    "rating_format must be doubles: a counted "
    "match records only a rejected overall "
    "doubles update | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] "
    "is a processed match id, so its "
    "rating_format must be doubles: a counted "
    "match records only a rejected overall "
    "doubles update",
    "default rejections processed singles + last player order": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no "
    "repeats | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no "
    "repeats",
    "default rejections processed singles + processed unknown": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'zz', which is not "
    "in players | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'zz', which is not "
    "in players",
    "default rejections processed unknown": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'zz', which is not in players | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'zz', which is not in players",
    "default rejections processed unknown + processed no match": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q4', which has "
    "no dated doubles "
    "baseline; with no gap "
    "rule for doubles, only "
    "before_imported_baseline "
    "is recorded, and it "
    "freezes every player | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q4', which has "
    "no dated doubles "
    "baseline; with no gap "
    "rule for doubles, only "
    "before_imported_baseline "
    "is recorded, and it "
    "freezes every player",
    "default rejections processed unknown + processed singles": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] "
    "is a processed match id, "
    "so its rating_format "
    "must be doubles: a "
    "counted match records "
    "only a rejected overall "
    "doubles update | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] "
    "is a processed match id, "
    "so its rating_format "
    "must be doubles: a "
    "counted match records "
    "only a rejected overall "
    "doubles update",
    "default rejections reserved no baseline": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m005'].players "
    "names 'q4', which has no dated doubles "
    "baseline; with no gap rule for doubles, "
    "only before_imported_baseline is "
    "recorded, and it freezes every player | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m005'].players "
    "names 'q4', which has no dated doubles "
    "baseline; with no gap rule for doubles, "
    "only before_imported_baseline is "
    "recorded, and it freezes every player",
    "default rejections reserved no baseline + neither": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m005'].players "
    "names 'q4', which has no dated "
    "doubles baseline; with no gap "
    "rule for doubles, only "
    "before_imported_baseline is "
    "recorded, and it freezes every "
    "player | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m005'].players "
    "names 'q4', which has no dated "
    "doubles baseline; with no gap "
    "rule for doubles, only "
    "before_imported_baseline is "
    "recorded, and it freezes every "
    "player",
    "default rejections reserved no baseline + reserved unknown": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m005'].players "
    "names 'q4', which has "
    "no dated doubles "
    "baseline; with no gap "
    "rule for doubles, only "
    "before_imported_baseline "
    "is recorded, and it "
    "freezes every player | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m005'].players "
    "names 'q4', which has "
    "no dated doubles "
    "baseline; with no gap "
    "rule for doubles, only "
    "before_imported_baseline "
    "is recorded, and it "
    "freezes every player",
    "default rejections reserved singles": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m067'].players "
    "names 'q8', which has no dated singles "
    "baseline; with no gap rule for singles, only "
    "before_imported_baseline is recorded, and it "
    "freezes every player | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m067'].players "
    "names 'q8', which has no dated singles "
    "baseline; with no gap rule for singles, only "
    "before_imported_baseline is recorded, and it "
    "freezes every player",
    "default rejections reserved singles + not in ledger_player_ids": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which "
    "is not in "
    "ledger_player_ids "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which "
    "is not in "
    "ledger_player_ids "
    "in a resumable "
    "snapshot",
    "default rejections reserved singles + reserved unknown": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m067'].players "
    "names 'q8', which has no "
    "dated singles baseline; "
    "with no gap rule for "
    "singles, only "
    "before_imported_baseline "
    "is recorded, and it "
    "freezes every player | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m067'].players "
    "names 'q8', which has no "
    "dated singles baseline; "
    "with no gap rule for "
    "singles, only "
    "before_imported_baseline "
    "is recorded, and it "
    "freezes every player",
    "default rejections reserved unknown": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m005'].players "
    "names 'q4', which has no dated doubles "
    "baseline; with no gap rule for doubles, only "
    "before_imported_baseline is recorded, and it "
    "freezes every player | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m005'].players "
    "names 'q4', which has no dated doubles "
    "baseline; with no gap rule for doubles, only "
    "before_imported_baseline is recorded, and it "
    "freezes every player",
    "default rejections reserved unknown + reserved no baseline": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m005'].players "
    "names 'q4', which has "
    "no dated doubles "
    "baseline; with no gap "
    "rule for doubles, only "
    "before_imported_baseline "
    "is recorded, and it "
    "freezes every player | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m005'].players "
    "names 'q4', which has "
    "no dated doubles "
    "baseline; with no gap "
    "rule for doubles, only "
    "before_imported_baseline "
    "is recorded, and it "
    "freezes every player",
    "default rejections reserved unknown + reserved singles": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m005'].players "
    "names 'q4', which has no "
    "dated doubles baseline; "
    "with no gap rule for "
    "doubles, only "
    "before_imported_baseline "
    "is recorded, and it "
    "freezes every player | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m005'].players "
    "names 'q4', which has no "
    "dated doubles baseline; "
    "with no gap rule for "
    "doubles, only "
    "before_imported_baseline "
    "is recorded, and it "
    "freezes every player",
    "default rejections unknown key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "unknown key 'zz' | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "unknown key 'zz'",
    "default rejections unknown key + format": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "unknown key 'zz' | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "unknown key 'zz'",
    "default rejections unknown key + missing key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "missing key 'players' | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "missing key 'players'",
    "default resumable considered": "SnapshotError: "
    "snapshot.replay_info.matches_considered must equal "
    "the number of ledger_digests in a resumable snapshot "
    "| SnapshotError: "
    "snapshot.replay_info.matches_considered must equal "
    "the number of ledger_digests in a resumable snapshot",
    "default resumable considered + counted": "SnapshotError: "
    "snapshot.replay_info.matches_considered "
    "must equal the number of ledger_digests in "
    "a resumable snapshot | SnapshotError: "
    "snapshot.replay_info.matches_considered "
    "must equal the number of ledger_digests in "
    "a resumable snapshot",
    "default resumable considered + log after": "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q0', who has no doubles state "
    "that a match on 2025-05-12 could have "
    "moved or frozen | SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q0', who has no doubles state "
    "that a match on 2025-05-12 could have "
    "moved or frozen",
    "default resumable counted": "SnapshotError: snapshot.replay_info.matches_counted "
    "must equal the number of processed_match_ids in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.replay_info.matches_counted must equal the "
    "number of processed_match_ids in a resumable snapshot",
    "default resumable counted + considered": "SnapshotError: "
    "snapshot.replay_info.matches_considered "
    "must equal the number of ledger_digests in "
    "a resumable snapshot | SnapshotError: "
    "snapshot.replay_info.matches_considered "
    "must equal the number of ledger_digests in "
    "a resumable snapshot",
    "default resumable counted + undigested": "SnapshotError: "
    "snapshot.replay_info.matches_counted must "
    "equal the number of processed_match_ids in "
    "a resumable snapshot | SnapshotError: "
    "snapshot.replay_info.matches_counted must "
    "equal the number of processed_match_ids in "
    "a resumable snapshot",
    "default resumable derived inherited": "SnapshotError: "
    "snapshot.players[0].ratings.mixed_doubles.inherited_live_updates "
    "must be 0 in a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.mixed_doubles.inherited_live_updates "
    "must be 0 in a resumable snapshot",
    "default resumable derived inherited + undigested": "SnapshotError: "
    "snapshot.players[0].ratings.mixed_doubles.inherited_live_updates "
    "must be 0 in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[0].ratings.mixed_doubles.inherited_live_updates "
    "must be 0 in a resumable "
    "snapshot",
    "default resumable early inherited": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is derived from its "
    "parent | SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is derived from its "
    "parent",
    "default resumable early inherited + early partner": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "default resumable early inherited + early rd_as_of": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "default resumable early last_played": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "default resumable early last_played + early ledger id": "SnapshotError: "
    "snapshot.players[0].id 'q0' "
    "has played matches but is "
    "not in ledger_player_ids in "
    "a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].id 'q0' "
    "has played matches but is "
    "not in ledger_player_ids in "
    "a resumable snapshot",
    "default resumable early last_played + early rd_as_of": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after calculated_as_of in "
    "a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after calculated_as_of in "
    "a resumable snapshot",
    "default resumable early ledger id": "SnapshotError: snapshot.players[0].id 'q0' has "
    "played matches but is not in ledger_player_ids "
    "in a resumable snapshot | SnapshotError: "
    "snapshot.players[0].id 'q0' has played matches "
    "but is not in ledger_player_ids in a resumable "
    "snapshot",
    "default resumable early ledger id + early last_played": "SnapshotError: "
    "snapshot.players[0].id 'q0' "
    "has played matches but is "
    "not in ledger_player_ids in "
    "a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].id 'q0' "
    "has played matches but is "
    "not in ledger_player_ids in "
    "a resumable snapshot",
    "default resumable early live": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.live_updates_since_replay "
    "must be 0 in a resumable snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.live_updates_since_replay "
    "must be 0 in a resumable snapshot",
    "default resumable early live + early opponent": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "default resumable early live + partnered ledger id": "SnapshotError: "
    "snapshot.players[0].id 'q0' "
    "has played matches but is not "
    "in ledger_player_ids in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].id 'q0' "
    "has played matches but is not "
    "in ledger_player_ids in a "
    "resumable snapshot",
    "default resumable early opponent": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "default resumable early opponent + early live": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "default resumable early opponent + early partner": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "default resumable early partner": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of in a resumable snapshot "
    "| SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of in a resumable snapshot",
    "default resumable early partner + early inherited": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "default resumable early partner + early opponent": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "default resumable early rd_as_of": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.rd_as_of is "
    "after calculated_as_of in a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.rd_as_of is "
    "after calculated_as_of in a resumable snapshot",
    "default resumable early rd_as_of + early inherited": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "default resumable early rd_as_of + early last_played": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after calculated_as_of in "
    "a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after calculated_as_of in "
    "a resumable snapshot",
    "default resumable late inherited": "SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is derived from its "
    "parent | SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is derived from its "
    "parent",
    "default resumable late inherited + late partner": "SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "default resumable late inherited + late rd_as_of": "SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "default resumable late last_played": "SnapshotError: "
    "snapshot.players[9].ratings.singles.last_played "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[9].ratings.singles.last_played "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "default resumable late last_played + late ledger id": "SnapshotError: "
    "snapshot.players[9].id 'q9' "
    "has played matches but is not "
    "in ledger_player_ids in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[9].id 'q9' "
    "has played matches but is not "
    "in ledger_player_ids in a "
    "resumable snapshot",
    "default resumable late last_played + late rd_as_of": "SnapshotError: "
    "snapshot.players[9].ratings.singles.last_played "
    "is after calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[9].ratings.singles.last_played "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "default resumable late ledger id": "SnapshotError: snapshot.players[9].id 'q9' has "
    "played matches but is not in ledger_player_ids "
    "in a resumable snapshot | SnapshotError: "
    "snapshot.players[9].id 'q9' has played matches "
    "but is not in ledger_player_ids in a resumable "
    "snapshot",
    "default resumable late ledger id + late last_played": "SnapshotError: "
    "snapshot.players[9].id 'q9' "
    "has played matches but is not "
    "in ledger_player_ids in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[9].id 'q9' "
    "has played matches but is not "
    "in ledger_player_ids in a "
    "resumable snapshot",
    "default resumable late ledger id + partnered live": "SnapshotError: "
    "snapshot.players[9].id 'q9' has "
    "played matches but is not in "
    "ledger_player_ids in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[9].id 'q9' has "
    "played matches but is not in "
    "ledger_player_ids in a "
    "resumable snapshot",
    "default resumable late live": "SnapshotError: "
    "snapshot.players[9].ratings.singles.live_updates_since_replay "
    "must be 0 in a resumable snapshot | SnapshotError: "
    "snapshot.players[9].ratings.singles.live_updates_since_replay "
    "must be 0 in a resumable snapshot",
    "default resumable late live + late opponent": "SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "default resumable late live + source at last_played": "SnapshotError: "
    "snapshot.players[0].ratings.doubles: "
    "matches_played - "
    "verified_matches_played (1) "
    "must not exceed the "
    "self-reported doubles and "
    "category matches logged for "
    "this player (0) | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles: "
    "matches_played - "
    "verified_matches_played (1) "
    "must not exceed the "
    "self-reported doubles and "
    "category matches logged for "
    "this player (0)",
    "default resumable late opponent": "SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a resumable snapshot "
    "| SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a resumable snapshot",
    "default resumable late opponent + late live": "SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "default resumable late opponent + late partner": "SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "default resumable late partner": "engine 8acfa891cb92d093 | engine 7b057f7eda9d66a9",
    "default resumable late partner + late inherited": "SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "default resumable late partner + late opponent": "SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "default resumable late rd_as_of": "SnapshotError: "
    "snapshot.players[9].ratings.singles.rd_as_of is "
    "after calculated_as_of in a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[9].ratings.singles.rd_as_of is "
    "after calculated_as_of in a resumable snapshot",
    "default resumable late rd_as_of + late inherited": "SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "default resumable late rd_as_of + late last_played": "SnapshotError: "
    "snapshot.players[9].ratings.singles.last_played "
    "is after calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[9].ratings.singles.last_played "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "default resumable log after": "SnapshotError: snapshot.self_reported_log[0].teams "
    "names 'q0', who has no doubles state that a match on "
    "2025-05-12 could have moved or frozen | "
    "SnapshotError: snapshot.self_reported_log[0].teams "
    "names 'q0', who has no doubles state that a match on "
    "2025-05-12 could have moved or frozen",
    "default resumable log after + considered": "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q0', who has no doubles state "
    "that a match on 2025-05-12 could have "
    "moved or frozen | SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q0', who has no doubles state "
    "that a match on 2025-05-12 could have "
    "moved or frozen",
    "default resumable log after + source after as_of": "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q1', who has no doubles "
    "state that a match on 2025-05-12 "
    "could have moved or frozen | "
    "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q1', who has no doubles "
    "state that a match on 2025-05-12 "
    "could have moved or frozen",
    "default resumable partnered inherited": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is derived from "
    "its parent | SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is derived from "
    "its parent",
    "default resumable partnered inherited + partnered partner": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent",
    "default resumable partnered inherited + partnered rd_as_of": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent",
    "default resumable partnered last_played": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "default resumable partnered last_played + partnered ledger id": "SnapshotError: "
    "snapshot.players[0].id "
    "'q0' has played "
    "matches but is not "
    "in "
    "ledger_player_ids "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.players[0].id "
    "'q0' has played "
    "matches but is not "
    "in "
    "ledger_player_ids "
    "in a resumable "
    "snapshot",
    "default resumable partnered last_played + partnered rd_as_of": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after "
    "calculated_as_of in "
    "a resumable snapshot "
    "| SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after "
    "calculated_as_of in "
    "a resumable snapshot",
    "default resumable partnered ledger id": "SnapshotError: snapshot.players[0].id 'q0' "
    "has played matches but is not in "
    "ledger_player_ids in a resumable snapshot | "
    "SnapshotError: snapshot.players[0].id 'q0' "
    "has played matches but is not in "
    "ledger_player_ids in a resumable snapshot",
    "default resumable partnered ledger id + early live": "SnapshotError: "
    "snapshot.players[0].id 'q0' "
    "has played matches but is not "
    "in ledger_player_ids in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].id 'q0' "
    "has played matches but is not "
    "in ledger_player_ids in a "
    "resumable snapshot",
    "default resumable partnered ledger id + partnered last_played": "SnapshotError: "
    "snapshot.players[0].id "
    "'q0' has played "
    "matches but is not "
    "in "
    "ledger_player_ids "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.players[0].id "
    "'q0' has played "
    "matches but is not "
    "in "
    "ledger_player_ids "
    "in a resumable "
    "snapshot",
    "default resumable partnered live": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.live_updates_since_replay "
    "must be 0 in a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.live_updates_since_replay "
    "must be 0 in a resumable snapshot",
    "default resumable partnered live + late ledger id": "SnapshotError: "
    "snapshot.players[9].id 'q9' has "
    "played matches but is not in "
    "ledger_player_ids in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[9].id 'q9' has "
    "played matches but is not in "
    "ledger_player_ids in a "
    "resumable snapshot",
    "default resumable partnered live + partnered opponent": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of "
    "in a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of "
    "in a resumable snapshot",
    "default resumable partnered opponent": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "default resumable partnered opponent + partnered live": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of "
    "in a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of "
    "in a resumable snapshot",
    "default resumable partnered opponent + partnered partner": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after "
    "calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after "
    "calculated_as_of in a "
    "resumable snapshot",
    "default resumable partnered partner": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "default resumable partnered partner + partnered inherited": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent",
    "default resumable partnered partner + partnered opponent": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after "
    "calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after "
    "calculated_as_of in a "
    "resumable snapshot",
    "default resumable partnered rd_as_of": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.rd_as_of "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.rd_as_of "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "default resumable partnered rd_as_of + partnered inherited": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent",
    "default resumable partnered rd_as_of + partnered last_played": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after "
    "calculated_as_of in "
    "a resumable snapshot "
    "| SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after "
    "calculated_as_of in "
    "a resumable snapshot",
    "default resumable passes derived inherited + source at a contact": "SnapshotError: "
    "snapshot.players[0].ratings.mixed_doubles.initial_source_date "
    "must equal the "
    "parent state's "
    "initial_source_date "
    "when "
    "inherited_live_updates "
    "is nonzero | "
    "SnapshotError: "
    "snapshot.players[0].ratings.mixed_doubles.initial_source_date "
    "must equal the "
    "parent state's "
    "initial_source_date "
    "when "
    "inherited_live_updates "
    "is nonzero",
    "default resumable passes late last_played + source at a contact": "SnapshotError: "
    "snapshot.players[9].ratings.singles.last_played "
    "is after "
    "calculated_as_of "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.players[9].ratings.singles.last_played "
    "is after "
    "calculated_as_of "
    "in a resumable "
    "snapshot",
    "default resumable passes log after + source at a contact": "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q0', who has no "
    "doubles state that a "
    "match on 2025-05-12 "
    "could have moved or "
    "frozen | SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q0', who has no "
    "doubles state that a "
    "match on 2025-05-12 "
    "could have moved or "
    "frozen",
    "default resumable passes partnered partner + source at a contact": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after "
    "calculated_as_of "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after "
    "calculated_as_of "
    "in a resumable "
    "snapshot",
    "default resumable passes source at a contact + derived inherited": "SnapshotError: "
    "snapshot.players[0].ratings.mixed_doubles.initial_source_date "
    "must equal the "
    "parent state's "
    "initial_source_date "
    "when "
    "inherited_live_updates "
    "is nonzero | "
    "SnapshotError: "
    "snapshot.players[0].ratings.mixed_doubles.initial_source_date "
    "must equal the "
    "parent state's "
    "initial_source_date "
    "when "
    "inherited_live_updates "
    "is nonzero",
    "default resumable passes source at a contact + late last_played": "SnapshotError: "
    "snapshot.players[9].ratings.singles.last_played "
    "is after "
    "calculated_as_of "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.players[9].ratings.singles.last_played "
    "is after "
    "calculated_as_of "
    "in a resumable "
    "snapshot",
    "default resumable passes source at a contact + log after": "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q0', who has no "
    "doubles state that a "
    "match on 2025-05-12 "
    "could have moved or "
    "frozen | SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q0', who has no "
    "doubles state that a "
    "match on 2025-05-12 "
    "could have moved or "
    "frozen",
    "default resumable passes source at a contact + partnered partner": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after "
    "calculated_as_of "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after "
    "calculated_as_of "
    "in a resumable "
    "snapshot",
    "default resumable source after as_of": "SnapshotError: "
    "snapshot.players[0].ratings.doubles: "
    "matches_played - verified_matches_played (1) "
    "must not exceed the self-reported doubles "
    "and category matches logged for this player "
    "(0) | SnapshotError: "
    "snapshot.players[0].ratings.doubles: "
    "matches_played - verified_matches_played (1) "
    "must not exceed the self-reported doubles "
    "and category matches logged for this player "
    "(0)",
    "default resumable source after as_of + log after": "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q1', who has no doubles "
    "state that a match on 2025-05-12 "
    "could have moved or frozen | "
    "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q1', who has no doubles "
    "state that a match on 2025-05-12 "
    "could have moved or frozen",
    "default resumable source after as_of + source at a contact": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q2'] "
    "must be after "
    "initial_source_date in "
    "a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q2'] "
    "must be after "
    "initial_source_date in "
    "a resumable snapshot",
    "default resumable source at a contact": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q2'] "
    "must be after initial_source_date in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q2'] "
    "must be after initial_source_date in a "
    "resumable snapshot",
    "default resumable source at a contact + source after as_of": "SnapshotError: "
    "snapshot.players[0].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not exceed "
    "the self-reported "
    "doubles and category "
    "matches logged for "
    "this player (0) | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not exceed "
    "the self-reported "
    "doubles and category "
    "matches logged for "
    "this player (0)",
    "default resumable source at a contact + source at last_played": "SnapshotError: "
    "snapshot.players[0].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not exceed "
    "the self-reported "
    "doubles and "
    "category matches "
    "logged for this "
    "player (0) | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not exceed "
    "the self-reported "
    "doubles and "
    "category matches "
    "logged for this "
    "player (0)",
    "default resumable source at last_played": "SnapshotError: "
    "snapshot.players[0].ratings.doubles: "
    "matches_played - verified_matches_played "
    "(1) must not exceed the self-reported "
    "doubles and category matches logged for "
    "this player (0) | SnapshotError: "
    "snapshot.players[0].ratings.doubles: "
    "matches_played - verified_matches_played "
    "(1) must not exceed the self-reported "
    "doubles and category matches logged for "
    "this player (0)",
    "default resumable source at last_played + late live": "SnapshotError: "
    "snapshot.players[0].ratings.doubles: "
    "matches_played - "
    "verified_matches_played (1) "
    "must not exceed the "
    "self-reported doubles and "
    "category matches logged for "
    "this player (0) | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles: "
    "matches_played - "
    "verified_matches_played (1) "
    "must not exceed the "
    "self-reported doubles and "
    "category matches logged for "
    "this player (0)",
    "default resumable source at last_played + source at a contact": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q2'] "
    "must be after "
    "initial_source_date "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q2'] "
    "must be after "
    "initial_source_date "
    "in a resumable "
    "snapshot",
    "default resumable undigested": "SnapshotError: snapshot.processed_match_ids names "
    "'m000', which is not in ledger_digests in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.processed_match_ids names 'm000', which is "
    "not in ledger_digests in a resumable snapshot",
    "default resumable undigested + counted": "SnapshotError: "
    "snapshot.replay_info.matches_counted must "
    "equal the number of processed_match_ids in "
    "a resumable snapshot | SnapshotError: "
    "snapshot.replay_info.matches_counted must "
    "equal the number of processed_match_ids in "
    "a resumable snapshot",
    "default resumable undigested + derived inherited": "SnapshotError: "
    "snapshot.players[0].ratings.mixed_doubles.inherited_live_updates "
    "must be 0 in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[0].ratings.mixed_doubles.inherited_live_updates "
    "must be 0 in a resumable "
    "snapshot",
    "nofeed rejections blank key": "SnapshotError: snapshot.seed_sensitive_rejections has "
    "a key that is not a non-blank str | SnapshotError: "
    "snapshot.seed_sensitive_rejections has a key that is "
    "not a non-blank str",
    "nofeed rejections blank key + entry not an object": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object",
    "nofeed rejections blank key + not an object": "SnapshotError: "
    "snapshot.seed_sensitive_rejections "
    "must be an object | SnapshotError: "
    "snapshot.seed_sensitive_rejections "
    "must be an object",
    "nofeed rejections duplicate": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['n'].rating_format "
    "must be a RatingFormat value | SnapshotError: "
    "snapshot.seed_sensitive_rejections['n'].rating_format "
    "must be a RatingFormat value",
    "nofeed rejections duplicate + neither": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['n'].rating_format "
    "must be a RatingFormat value | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['n'].rating_format "
    "must be a RatingFormat value",
    "nofeed rejections duplicate + processed no match": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['n'].rating_format "
    "must be a RatingFormat value | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['n'].rating_format "
    "must be a RatingFormat value",
    "nofeed rejections entry not an object": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object",
    "nofeed rejections entry not an object + blank key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object",
    "nofeed rejections entry not an object + missing key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object",
    "nofeed rejections format": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "nofeed rejections format + players not a list": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "nofeed rejections format + unknown key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "unknown key 'zz' | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "unknown key 'zz'",
    "nofeed rejections last format": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "nofeed rejections last format + last player order": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "nofeed rejections last format + player order": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "nofeed rejections last player order": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no repeats | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no repeats",
    "nofeed rejections last player order + last format": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "nofeed rejections last player order + processed singles": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no "
    "repeats | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no "
    "repeats",
    "nofeed rejections missing key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "missing key 'players' | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "missing key 'players'",
    "nofeed rejections missing key + entry not an object": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object",
    "nofeed rejections missing key + unknown key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "missing key 'players' | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "missing key 'players'",
    "nofeed rejections neither": "SnapshotError: snapshot.seed_sensitive_rejections has "
    "key 'nope', which is in neither processed_match_ids nor "
    "reserved_match_ids | SnapshotError: "
    "snapshot.seed_sensitive_rejections has key 'nope', "
    "which is in neither processed_match_ids nor "
    "reserved_match_ids",
    "nofeed rejections neither + duplicate": "SnapshotError: "
    "snapshot.seed_sensitive_rejections has key "
    "'nope', which is in neither "
    "processed_match_ids nor reserved_match_ids "
    "| SnapshotError: "
    "snapshot.seed_sensitive_rejections has key "
    "'nope', which is in neither "
    "processed_match_ids nor reserved_match_ids",
    "nofeed rejections neither + reserved no baseline": "SnapshotError: "
    "snapshot.seed_sensitive_rejections "
    "has key 'nope', which is in "
    "neither processed_match_ids nor "
    "reserved_match_ids | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections "
    "has key 'nope', which is in "
    "neither processed_match_ids nor "
    "reserved_match_ids",
    "nofeed rejections not an object": "SnapshotError: snapshot.seed_sensitive_rejections "
    "must be an object | SnapshotError: "
    "snapshot.seed_sensitive_rejections must be an "
    "object",
    "nofeed rejections not an object + blank key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections "
    "must be an object | SnapshotError: "
    "snapshot.seed_sensitive_rejections "
    "must be an object",
    "nofeed rejections not in ledger_player_ids": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which is not in "
    "ledger_player_ids in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which is not in "
    "ledger_player_ids in a resumable "
    "snapshot",
    "nofeed rejections not in ledger_player_ids + reserved singles": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which "
    "is not in "
    "ledger_player_ids "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which "
    "is not in "
    "ledger_player_ids "
    "in a resumable "
    "snapshot",
    "nofeed rejections player blank": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players[0] "
    "must be a non-blank str | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players[0] "
    "must be a non-blank str",
    "nofeed rejections player blank + player count": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles",
    "nofeed rejections player blank + players not a list": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be a list | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be a list",
    "nofeed rejections player count": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles",
    "nofeed rejections player count + player blank": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players[0] "
    "must be a non-blank str | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players[0] "
    "must be a non-blank str",
    "nofeed rejections player count + player order": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles",
    "nofeed rejections player order": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no repeats | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no repeats",
    "nofeed rejections player order + last format": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "nofeed rejections player order + player count": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles",
    "nofeed rejections players not a list": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be a list | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be a list",
    "nofeed rejections players not a list + format": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "nofeed rejections players not a list + player blank": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players[0] "
    "must be a non-blank str | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players[0] "
    "must be a non-blank str",
    "nofeed rejections processed no match": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] "
    "is a processed match id, which needs "
    "category_feeds_parent: only then does a "
    "counted match send a doubles update | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] "
    "is a processed match id, which needs "
    "category_feeds_parent: only then does a "
    "counted match send a doubles update",
    "nofeed rejections processed no match + duplicate": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] "
    "is a processed match id, which "
    "needs category_feeds_parent: "
    "only then does a counted match "
    "send a doubles update | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] "
    "is a processed match id, which "
    "needs category_feeds_parent: "
    "only then does a counted match "
    "send a doubles update",
    "nofeed rejections processed no match + processed unknown": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] "
    "is a processed match id, "
    "which needs "
    "category_feeds_parent: "
    "only then does a counted "
    "match send a doubles "
    "update | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] "
    "is a processed match id, "
    "which needs "
    "category_feeds_parent: "
    "only then does a counted "
    "match send a doubles "
    "update",
    "nofeed rejections processed singles": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] is "
    "a processed match id, so its rating_format "
    "must be doubles: a counted match records only "
    "a rejected overall doubles update | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] is "
    "a processed match id, so its rating_format "
    "must be doubles: a counted match records only "
    "a rejected overall doubles update",
    "nofeed rejections processed singles + last player order": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no "
    "repeats | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no "
    "repeats",
    "nofeed rejections processed singles + processed unknown": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] "
    "is a processed match id, "
    "which needs "
    "category_feeds_parent: "
    "only then does a counted "
    "match send a doubles "
    "update | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] "
    "is a processed match id, "
    "which needs "
    "category_feeds_parent: "
    "only then does a counted "
    "match send a doubles "
    "update",
    "nofeed rejections processed unknown": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] is "
    "a processed match id, which needs "
    "category_feeds_parent: only then does a "
    "counted match send a doubles update | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] is "
    "a processed match id, which needs "
    "category_feeds_parent: only then does a "
    "counted match send a doubles update",
    "nofeed rejections processed unknown + processed no match": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] "
    "is a processed match id, "
    "which needs "
    "category_feeds_parent: "
    "only then does a counted "
    "match send a doubles "
    "update | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] "
    "is a processed match id, "
    "which needs "
    "category_feeds_parent: "
    "only then does a counted "
    "match send a doubles "
    "update",
    "nofeed rejections processed unknown + processed singles": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] "
    "is a processed match id, "
    "so its rating_format must "
    "be doubles: a counted "
    "match records only a "
    "rejected overall doubles "
    "update | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'] "
    "is a processed match id, "
    "so its rating_format must "
    "be doubles: a counted "
    "match records only a "
    "rejected overall doubles "
    "update",
    "nofeed rejections reserved no baseline": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'].players "
    "names 'q4', which has no dated doubles "
    "baseline; with no gap rule for doubles, "
    "only before_imported_baseline is recorded, "
    "and it freezes every player | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'].players "
    "names 'q4', which has no dated doubles "
    "baseline; with no gap rule for doubles, "
    "only before_imported_baseline is recorded, "
    "and it freezes every player",
    "nofeed rejections reserved no baseline + neither": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'].players "
    "names 'q4', which has no dated "
    "doubles baseline; with no gap "
    "rule for doubles, only "
    "before_imported_baseline is "
    "recorded, and it freezes every "
    "player | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'].players "
    "names 'q4', which has no dated "
    "doubles baseline; with no gap "
    "rule for doubles, only "
    "before_imported_baseline is "
    "recorded, and it freezes every "
    "player",
    "nofeed rejections reserved no baseline + reserved unknown": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'].players "
    "names 'q4', which has "
    "no dated doubles "
    "baseline; with no gap "
    "rule for doubles, only "
    "before_imported_baseline "
    "is recorded, and it "
    "freezes every player | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'].players "
    "names 'q4', which has "
    "no dated doubles "
    "baseline; with no gap "
    "rule for doubles, only "
    "before_imported_baseline "
    "is recorded, and it "
    "freezes every player",
    "nofeed rejections reserved singles": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m069'].players "
    "names 'q8', which has no dated singles "
    "baseline; with no gap rule for singles, only "
    "before_imported_baseline is recorded, and it "
    "freezes every player | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m069'].players "
    "names 'q8', which has no dated singles "
    "baseline; with no gap rule for singles, only "
    "before_imported_baseline is recorded, and it "
    "freezes every player",
    "nofeed rejections reserved singles + not in ledger_player_ids": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which "
    "is not in "
    "ledger_player_ids "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which "
    "is not in "
    "ledger_player_ids "
    "in a resumable "
    "snapshot",
    "nofeed rejections reserved singles + reserved unknown": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m069'].players "
    "names 'q8', which has no "
    "dated singles baseline; "
    "with no gap rule for "
    "singles, only "
    "before_imported_baseline is "
    "recorded, and it freezes "
    "every player | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m069'].players "
    "names 'q8', which has no "
    "dated singles baseline; "
    "with no gap rule for "
    "singles, only "
    "before_imported_baseline is "
    "recorded, and it freezes "
    "every player",
    "nofeed rejections reserved unknown": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'].players "
    "names 'q4', which has no dated doubles "
    "baseline; with no gap rule for doubles, only "
    "before_imported_baseline is recorded, and it "
    "freezes every player | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'].players "
    "names 'q4', which has no dated doubles "
    "baseline; with no gap rule for doubles, only "
    "before_imported_baseline is recorded, and it "
    "freezes every player",
    "nofeed rejections reserved unknown + reserved no baseline": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'].players "
    "names 'q4', which has "
    "no dated doubles "
    "baseline; with no gap "
    "rule for doubles, only "
    "before_imported_baseline "
    "is recorded, and it "
    "freezes every player | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'].players "
    "names 'q4', which has "
    "no dated doubles "
    "baseline; with no gap "
    "rule for doubles, only "
    "before_imported_baseline "
    "is recorded, and it "
    "freezes every player",
    "nofeed rejections reserved unknown + reserved singles": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'].players "
    "names 'q4', which has no "
    "dated doubles baseline; "
    "with no gap rule for "
    "doubles, only "
    "before_imported_baseline is "
    "recorded, and it freezes "
    "every player | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'].players "
    "names 'q4', which has no "
    "dated doubles baseline; "
    "with no gap rule for "
    "doubles, only "
    "before_imported_baseline is "
    "recorded, and it freezes "
    "every player",
    "nofeed rejections unknown key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "unknown key 'zz' | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "unknown key 'zz'",
    "nofeed rejections unknown key + format": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "unknown key 'zz' | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "unknown key 'zz'",
    "nofeed rejections unknown key + missing key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "missing key 'players' | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "missing key 'players'",
    "nofeed resumable considered": "SnapshotError: "
    "snapshot.replay_info.matches_considered must equal "
    "the number of ledger_digests in a resumable snapshot "
    "| SnapshotError: "
    "snapshot.replay_info.matches_considered must equal "
    "the number of ledger_digests in a resumable snapshot",
    "nofeed resumable considered + counted": "SnapshotError: "
    "snapshot.replay_info.matches_considered "
    "must equal the number of ledger_digests in "
    "a resumable snapshot | SnapshotError: "
    "snapshot.replay_info.matches_considered "
    "must equal the number of ledger_digests in "
    "a resumable snapshot",
    "nofeed resumable considered + log after": "SnapshotError: "
    "snapshot.self_reported_log[0].teams names "
    "'q0', who has no doubles state that a "
    "match on 2025-05-12 could have moved or "
    "frozen | SnapshotError: "
    "snapshot.self_reported_log[0].teams names "
    "'q0', who has no doubles state that a "
    "match on 2025-05-12 could have moved or "
    "frozen",
    "nofeed resumable counted": "SnapshotError: snapshot.replay_info.matches_counted must "
    "equal the number of processed_match_ids in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.replay_info.matches_counted must equal the "
    "number of processed_match_ids in a resumable snapshot",
    "nofeed resumable counted + considered": "SnapshotError: "
    "snapshot.replay_info.matches_considered "
    "must equal the number of ledger_digests in "
    "a resumable snapshot | SnapshotError: "
    "snapshot.replay_info.matches_considered "
    "must equal the number of ledger_digests in "
    "a resumable snapshot",
    "nofeed resumable counted + undigested": "SnapshotError: "
    "snapshot.replay_info.matches_counted must "
    "equal the number of processed_match_ids in "
    "a resumable snapshot | SnapshotError: "
    "snapshot.replay_info.matches_counted must "
    "equal the number of processed_match_ids in "
    "a resumable snapshot",
    "nofeed resumable early inherited": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is derived from its "
    "parent | SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is derived from its "
    "parent",
    "nofeed resumable early inherited + early partner": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "nofeed resumable early inherited + early rd_as_of": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "nofeed resumable early last_played": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "nofeed resumable early last_played + early ledger id": "SnapshotError: "
    "snapshot.players[0].id 'q0' "
    "has played matches but is "
    "not in ledger_player_ids in "
    "a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].id 'q0' "
    "has played matches but is "
    "not in ledger_player_ids in "
    "a resumable snapshot",
    "nofeed resumable early last_played + early rd_as_of": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after calculated_as_of in "
    "a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after calculated_as_of in "
    "a resumable snapshot",
    "nofeed resumable early ledger id": "SnapshotError: snapshot.players[0].id 'q0' has "
    "played matches but is not in ledger_player_ids "
    "in a resumable snapshot | SnapshotError: "
    "snapshot.players[0].id 'q0' has played matches "
    "but is not in ledger_player_ids in a resumable "
    "snapshot",
    "nofeed resumable early ledger id + early last_played": "SnapshotError: "
    "snapshot.players[0].id 'q0' "
    "has played matches but is "
    "not in ledger_player_ids in "
    "a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].id 'q0' "
    "has played matches but is "
    "not in ledger_player_ids in "
    "a resumable snapshot",
    "nofeed resumable early live": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.live_updates_since_replay "
    "must be 0 in a resumable snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.live_updates_since_replay "
    "must be 0 in a resumable snapshot",
    "nofeed resumable early live + early opponent": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "nofeed resumable early live + partnered ledger id": "SnapshotError: "
    "snapshot.players[0].id 'q0' has "
    "played matches but is not in "
    "ledger_player_ids in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].id 'q0' has "
    "played matches but is not in "
    "ledger_player_ids in a "
    "resumable snapshot",
    "nofeed resumable early opponent": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in a resumable snapshot "
    "| SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in a resumable snapshot",
    "nofeed resumable early opponent + early live": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "nofeed resumable early opponent + early partner": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "nofeed resumable early partner": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of in a resumable snapshot "
    "| SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of in a resumable snapshot",
    "nofeed resumable early partner + early inherited": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "nofeed resumable early partner + early opponent": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "nofeed resumable early rd_as_of": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.rd_as_of is "
    "after calculated_as_of in a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.rd_as_of is "
    "after calculated_as_of in a resumable snapshot",
    "nofeed resumable early rd_as_of + early inherited": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "nofeed resumable early rd_as_of + early last_played": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after calculated_as_of in "
    "a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after calculated_as_of in "
    "a resumable snapshot",
    "nofeed resumable late inherited": "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.inherited_live_updates "
    "must be 0 unless the state is derived from its "
    "parent | SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.inherited_live_updates "
    "must be 0 unless the state is derived from its "
    "parent",
    "nofeed resumable late inherited + late partner": "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "nofeed resumable late inherited + late rd_as_of": "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "nofeed resumable late last_played": "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.last_played "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.last_played "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "nofeed resumable late last_played + late ledger id": "SnapshotError: "
    "snapshot.players[9].id 'q9' "
    "has played matches but is not "
    "in ledger_player_ids in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[9].id 'q9' "
    "has played matches but is not "
    "in ledger_player_ids in a "
    "resumable snapshot",
    "nofeed resumable late last_played + late rd_as_of": "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.last_played "
    "is after calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.last_played "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "nofeed resumable late ledger id": "SnapshotError: snapshot.players[9].id 'q9' has "
    "played matches but is not in ledger_player_ids in "
    "a resumable snapshot | SnapshotError: "
    "snapshot.players[9].id 'q9' has played matches "
    "but is not in ledger_player_ids in a resumable "
    "snapshot",
    "nofeed resumable late ledger id + late last_played": "SnapshotError: "
    "snapshot.players[9].id 'q9' "
    "has played matches but is not "
    "in ledger_player_ids in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[9].id 'q9' "
    "has played matches but is not "
    "in ledger_player_ids in a "
    "resumable snapshot",
    "nofeed resumable late ledger id + partnered live": "SnapshotError: "
    "snapshot.players[9].id 'q9' has "
    "played matches but is not in "
    "ledger_player_ids in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[9].id 'q9' has "
    "played matches but is not in "
    "ledger_player_ids in a resumable "
    "snapshot",
    "nofeed resumable late live": "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.live_updates_since_replay "
    "must be 0 in a resumable snapshot | SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.live_updates_since_replay "
    "must be 0 in a resumable snapshot",
    "nofeed resumable late live + late opponent": "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.opponents['q3'] "
    "is after calculated_as_of in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.opponents['q3'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "nofeed resumable late live + source at last_played": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "must be after "
    "initial_source_date in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "must be after "
    "initial_source_date in a "
    "resumable snapshot",
    "nofeed resumable late opponent": "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.opponents['q3'] "
    "is after calculated_as_of in a resumable snapshot "
    "| SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.opponents['q3'] "
    "is after calculated_as_of in a resumable snapshot",
    "nofeed resumable late opponent + late live": "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.opponents['q3'] "
    "is after calculated_as_of in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.opponents['q3'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "nofeed resumable late opponent + late partner": "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.partners['q6'] "
    "is after calculated_as_of in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.partners['q6'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "nofeed resumable late partner": "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.partners['q6'] "
    "is after calculated_as_of in a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.partners['q6'] "
    "is after calculated_as_of in a resumable snapshot",
    "nofeed resumable late partner + late inherited": "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "nofeed resumable late partner + late opponent": "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.partners['q6'] "
    "is after calculated_as_of in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.partners['q6'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "nofeed resumable late rd_as_of": "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.rd_as_of "
    "is after calculated_as_of in a resumable snapshot "
    "| SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.rd_as_of "
    "is after calculated_as_of in a resumable snapshot",
    "nofeed resumable late rd_as_of + late inherited": "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "nofeed resumable late rd_as_of + late last_played": "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.last_played "
    "is after calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.last_played "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "nofeed resumable log after": "SnapshotError: snapshot.self_reported_log[0].teams "
    "names 'q0', who has no doubles state that a match on "
    "2025-05-12 could have moved or frozen | SnapshotError: "
    "snapshot.self_reported_log[0].teams names 'q0', who "
    "has no doubles state that a match on 2025-05-12 could "
    "have moved or frozen",
    "nofeed resumable log after + considered": "SnapshotError: "
    "snapshot.self_reported_log[0].teams names "
    "'q0', who has no doubles state that a "
    "match on 2025-05-12 could have moved or "
    "frozen | SnapshotError: "
    "snapshot.self_reported_log[0].teams names "
    "'q0', who has no doubles state that a "
    "match on 2025-05-12 could have moved or "
    "frozen",
    "nofeed resumable log after + source after as_of": "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q1', who has no doubles "
    "state that a match on 2025-05-12 "
    "could have moved or frozen | "
    "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q1', who has no doubles "
    "state that a match on 2025-05-12 "
    "could have moved or frozen",
    "nofeed resumable partnered inherited": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is derived from "
    "its parent | SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is derived from "
    "its parent",
    "nofeed resumable partnered inherited + partnered partner": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent",
    "nofeed resumable partnered inherited + partnered rd_as_of": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent",
    "nofeed resumable partnered last_played": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "nofeed resumable partnered last_played + partnered ledger id": "SnapshotError: "
    "snapshot.players[0].id "
    "'q0' has played "
    "matches but is not "
    "in ledger_player_ids "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.players[0].id "
    "'q0' has played "
    "matches but is not "
    "in ledger_player_ids "
    "in a resumable "
    "snapshot",
    "nofeed resumable partnered last_played + partnered rd_as_of": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after "
    "calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after "
    "calculated_as_of in a "
    "resumable snapshot",
    "nofeed resumable partnered ledger id": "SnapshotError: snapshot.players[0].id 'q0' "
    "has played matches but is not in "
    "ledger_player_ids in a resumable snapshot | "
    "SnapshotError: snapshot.players[0].id 'q0' "
    "has played matches but is not in "
    "ledger_player_ids in a resumable snapshot",
    "nofeed resumable partnered ledger id + early live": "SnapshotError: "
    "snapshot.players[0].id 'q0' has "
    "played matches but is not in "
    "ledger_player_ids in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].id 'q0' has "
    "played matches but is not in "
    "ledger_player_ids in a "
    "resumable snapshot",
    "nofeed resumable partnered ledger id + partnered last_played": "SnapshotError: "
    "snapshot.players[0].id "
    "'q0' has played "
    "matches but is not "
    "in ledger_player_ids "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.players[0].id "
    "'q0' has played "
    "matches but is not "
    "in ledger_player_ids "
    "in a resumable "
    "snapshot",
    "nofeed resumable partnered live": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.live_updates_since_replay "
    "must be 0 in a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.live_updates_since_replay "
    "must be 0 in a resumable snapshot",
    "nofeed resumable partnered live + late ledger id": "SnapshotError: "
    "snapshot.players[9].id 'q9' has "
    "played matches but is not in "
    "ledger_player_ids in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[9].id 'q9' has "
    "played matches but is not in "
    "ledger_player_ids in a resumable "
    "snapshot",
    "nofeed resumable partnered live + partnered opponent": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in "
    "a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in "
    "a resumable snapshot",
    "nofeed resumable partnered opponent": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "nofeed resumable partnered opponent + partnered live": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in "
    "a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in "
    "a resumable snapshot",
    "nofeed resumable partnered opponent + partnered partner": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of "
    "in a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of "
    "in a resumable snapshot",
    "nofeed resumable partnered partner": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "nofeed resumable partnered partner + partnered inherited": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent",
    "nofeed resumable partnered partner + partnered opponent": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of "
    "in a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after calculated_as_of "
    "in a resumable snapshot",
    "nofeed resumable partnered rd_as_of": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.rd_as_of "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.rd_as_of "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "nofeed resumable partnered rd_as_of + partnered inherited": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent",
    "nofeed resumable partnered rd_as_of + partnered last_played": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after "
    "calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "is after "
    "calculated_as_of in a "
    "resumable snapshot",
    "nofeed resumable passes late last_played + source at a contact": "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.last_played "
    "is after "
    "calculated_as_of "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.last_played "
    "is after "
    "calculated_as_of "
    "in a resumable "
    "snapshot",
    "nofeed resumable passes log after + source at a contact": "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q0', who has no "
    "doubles state that a "
    "match on 2025-05-12 could "
    "have moved or frozen | "
    "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q0', who has no "
    "doubles state that a "
    "match on 2025-05-12 could "
    "have moved or frozen",
    "nofeed resumable passes partnered partner + source at a contact": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after "
    "calculated_as_of "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after "
    "calculated_as_of "
    "in a resumable "
    "snapshot",
    "nofeed resumable passes source at a contact + late last_played": "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.last_played "
    "is after "
    "calculated_as_of "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.players[9].ratings.mixed_doubles.last_played "
    "is after "
    "calculated_as_of "
    "in a resumable "
    "snapshot",
    "nofeed resumable passes source at a contact + log after": "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q0', who has no "
    "doubles state that a "
    "match on 2025-05-12 could "
    "have moved or frozen | "
    "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q0', who has no "
    "doubles state that a "
    "match on 2025-05-12 could "
    "have moved or frozen",
    "nofeed resumable passes source at a contact + partnered partner": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after "
    "calculated_as_of "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "is after "
    "calculated_as_of "
    "in a resumable "
    "snapshot",
    "nofeed resumable source after as_of": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.initial_source_date "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.initial_source_date "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "nofeed resumable source after as_of + log after": "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q1', who has no doubles "
    "state that a match on 2025-05-12 "
    "could have moved or frozen | "
    "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q1', who has no doubles "
    "state that a match on 2025-05-12 "
    "could have moved or frozen",
    "nofeed resumable source after as_of + source at a contact": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "must be after "
    "initial_source_date in "
    "a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "must be after "
    "initial_source_date in "
    "a resumable snapshot",
    "nofeed resumable source at a contact": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "must be after initial_source_date in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "must be after initial_source_date in a "
    "resumable snapshot",
    "nofeed resumable source at a contact + source after as_of": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.initial_source_date "
    "is after "
    "calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.initial_source_date "
    "is after "
    "calculated_as_of in a "
    "resumable snapshot",
    "nofeed resumable source at a contact + source at last_played": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "must be after "
    "initial_source_date "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "must be after "
    "initial_source_date "
    "in a resumable "
    "snapshot",
    "nofeed resumable source at last_played": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "must be after initial_source_date in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "must be after initial_source_date in a "
    "resumable snapshot",
    "nofeed resumable source at last_played + late live": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "must be after "
    "initial_source_date in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.last_played "
    "must be after "
    "initial_source_date in a "
    "resumable snapshot",
    "nofeed resumable source at last_played + source at a contact": "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "must be after "
    "initial_source_date "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.doubles.partners['q9'] "
    "must be after "
    "initial_source_date "
    "in a resumable "
    "snapshot",
    "nofeed resumable undigested": "SnapshotError: snapshot.processed_match_ids names "
    "'m000', which is not in ledger_digests in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.processed_match_ids names 'm000', which is "
    "not in ledger_digests in a resumable snapshot",
    "nofeed resumable undigested + counted": "SnapshotError: "
    "snapshot.replay_info.matches_counted must "
    "equal the number of processed_match_ids in "
    "a resumable snapshot | SnapshotError: "
    "snapshot.replay_info.matches_counted must "
    "equal the number of processed_match_ids in "
    "a resumable snapshot",
    "strict rejections blank key": "SnapshotError: snapshot.seed_sensitive_rejections has "
    "a key that is not a non-blank str | SnapshotError: "
    "snapshot.seed_sensitive_rejections has a key that is "
    "not a non-blank str",
    "strict rejections blank key + entry not an object": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object",
    "strict rejections blank key + not an object": "SnapshotError: "
    "snapshot.seed_sensitive_rejections "
    "must be an object | SnapshotError: "
    "snapshot.seed_sensitive_rejections "
    "must be an object",
    "strict rejections duplicate": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] is a "
    "possible_duplicates key; a possible duplicate is "
    "rejected before any rating is read | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] is a "
    "possible_duplicates key; a possible duplicate is "
    "rejected before any rating is read",
    "strict rejections duplicate + neither": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] "
    "is a possible_duplicates key; a possible "
    "duplicate is rejected before any rating is "
    "read | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] "
    "is a possible_duplicates key; a possible "
    "duplicate is rejected before any rating is "
    "read",
    "strict rejections duplicate + processed no match": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] "
    "is a possible_duplicates key; a "
    "possible duplicate is rejected "
    "before any rating is read | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] "
    "is a possible_duplicates key; a "
    "possible duplicate is rejected "
    "before any rating is read",
    "strict rejections entry not an object": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object",
    "strict rejections entry not an object + blank key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object",
    "strict rejections entry not an object + missing key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object",
    "strict rejections format": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "strict rejections format + players not a list": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "strict rejections format + unknown key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "unknown key 'zz' | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "unknown key 'zz'",
    "strict rejections last format": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m069'].rating_format "
    "must be a RatingFormat value | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m069'].rating_format "
    "must be a RatingFormat value",
    "strict rejections last format + last player order": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m069'].rating_format "
    "must be a RatingFormat value | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m069'].rating_format "
    "must be a RatingFormat value",
    "strict rejections last format + player order": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no repeats | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no repeats",
    "strict rejections last player order": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m069'].players "
    "must be sorted with no repeats | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m069'].players "
    "must be sorted with no repeats",
    "strict rejections last player order + last format": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m069'].rating_format "
    "must be a RatingFormat value | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m069'].rating_format "
    "must be a RatingFormat value",
    "strict rejections last player order + processed singles": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m069'].players "
    "must be sorted with no "
    "repeats | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m069'].players "
    "must be sorted with no "
    "repeats",
    "strict rejections missing key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "missing key 'players' | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "missing key 'players'",
    "strict rejections missing key + entry not an object": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'] "
    "must be an object",
    "strict rejections missing key + unknown key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "missing key 'players' | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "missing key 'players'",
    "strict rejections neither": "SnapshotError: snapshot.seed_sensitive_rejections has "
    "key 'nope', which is in neither processed_match_ids nor "
    "reserved_match_ids | SnapshotError: "
    "snapshot.seed_sensitive_rejections has key 'nope', "
    "which is in neither processed_match_ids nor "
    "reserved_match_ids",
    "strict rejections neither + duplicate": "SnapshotError: "
    "snapshot.seed_sensitive_rejections has key "
    "'nope', which is in neither "
    "processed_match_ids nor reserved_match_ids "
    "| SnapshotError: "
    "snapshot.seed_sensitive_rejections has key "
    "'nope', which is in neither "
    "processed_match_ids nor reserved_match_ids",
    "strict rejections neither + reserved no baseline": "SnapshotError: "
    "snapshot.seed_sensitive_rejections "
    "has key 'nope', which is in "
    "neither processed_match_ids nor "
    "reserved_match_ids | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections "
    "has key 'nope', which is in "
    "neither processed_match_ids nor "
    "reserved_match_ids",
    "strict rejections not an object": "SnapshotError: snapshot.seed_sensitive_rejections "
    "must be an object | SnapshotError: "
    "snapshot.seed_sensitive_rejections must be an "
    "object",
    "strict rejections not an object + blank key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections "
    "must be an object | SnapshotError: "
    "snapshot.seed_sensitive_rejections "
    "must be an object",
    "strict rejections not in ledger_player_ids": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q0', which is not in "
    "ledger_player_ids in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q0', which is not in "
    "ledger_player_ids in a resumable "
    "snapshot",
    "strict rejections not in ledger_player_ids + reserved singles": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q0', which "
    "is not in "
    "ledger_player_ids "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q0', which "
    "is not in "
    "ledger_player_ids "
    "in a resumable "
    "snapshot",
    "strict rejections player blank": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players[0] "
    "must be a non-blank str | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players[0] "
    "must be a non-blank str",
    "strict rejections player blank + player count": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles",
    "strict rejections player blank + players not a list": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be a list | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be a list",
    "strict rejections player count": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles",
    "strict rejections player count + player blank": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players[0] "
    "must be a non-blank str | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players[0] "
    "must be a non-blank str",
    "strict rejections player count + player order": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles",
    "strict rejections player order": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no repeats | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no repeats",
    "strict rejections player order + last format": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no repeats | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be sorted with no repeats",
    "strict rejections player order + player count": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must name 2 players for singles",
    "strict rejections players not a list": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be a list | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "must be a list",
    "strict rejections players not a list + format": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].rating_format "
    "must be a RatingFormat value",
    "strict rejections players not a list + player blank": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players[0] "
    "must be a non-blank str | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players[0] "
    "must be a non-blank str",
    "strict rejections processed no match": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'] "
    "names no player with a played category state "
    "that has one other named player as a partner "
    "and the other two as opponents, as a counted "
    "category match leaves | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'] "
    "names no player with a played category state "
    "that has one other named player as a partner "
    "and the other two as opponents, as a counted "
    "category match leaves",
    "strict rejections processed no match + duplicate": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'] "
    "names no player with a played "
    "category state that has one "
    "other named player as a partner "
    "and the other two as opponents, "
    "as a counted category match "
    "leaves | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'] "
    "names no player with a played "
    "category state that has one "
    "other named player as a partner "
    "and the other two as opponents, "
    "as a counted category match "
    "leaves",
    "strict rejections processed no match + processed unknown": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'].players "
    "names 'zz', which is not "
    "in players | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'].players "
    "names 'zz', which is not "
    "in players",
    "strict rejections processed singles": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'] is "
    "a processed match id, so its rating_format "
    "must be doubles: a counted match records only "
    "a rejected overall doubles update | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'] is "
    "a processed match id, so its rating_format "
    "must be doubles: a counted match records only "
    "a rejected overall doubles update",
    "strict rejections processed singles + last player order": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m069'].players "
    "must be sorted with no "
    "repeats | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m069'].players "
    "must be sorted with no "
    "repeats",
    "strict rejections processed singles + processed unknown": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'].players "
    "names 'zz', which is not "
    "in players | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'].players "
    "names 'zz', which is not "
    "in players",
    "strict rejections processed unknown": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'].players "
    "names 'zz', which is not in players | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'].players "
    "names 'zz', which is not in players",
    "strict rejections processed unknown + processed no match": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'] "
    "names no player with a "
    "played category state "
    "that has one other named "
    "player as a partner and "
    "the other two as "
    "opponents, as a counted "
    "category match leaves | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'] "
    "names no player with a "
    "played category state "
    "that has one other named "
    "player as a partner and "
    "the other two as "
    "opponents, as a counted "
    "category match leaves",
    "strict rejections processed unknown + processed singles": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'] "
    "is a processed match id, "
    "so its rating_format must "
    "be doubles: a counted "
    "match records only a "
    "rejected overall doubles "
    "update | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m001'] "
    "is a processed match id, "
    "so its rating_format must "
    "be doubles: a counted "
    "match records only a "
    "rejected overall doubles "
    "update",
    "strict rejections reserved no baseline": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] "
    "is a possible_duplicates key; a possible "
    "duplicate is rejected before any rating is "
    "read | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] "
    "is a possible_duplicates key; a possible "
    "duplicate is rejected before any rating is "
    "read",
    "strict rejections reserved no baseline + neither": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] "
    "is a possible_duplicates key; a "
    "possible duplicate is rejected "
    "before any rating is read | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] "
    "is a possible_duplicates key; a "
    "possible duplicate is rejected "
    "before any rating is read",
    "strict rejections reserved no baseline + reserved unknown": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] "
    "is a "
    "possible_duplicates "
    "key; a possible "
    "duplicate is rejected "
    "before any rating is "
    "read | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] "
    "is a "
    "possible_duplicates "
    "key; a possible "
    "duplicate is rejected "
    "before any rating is "
    "read",
    "strict rejections reserved singles": "engine f10e8842c3dee303 | engine 27c090dd02dadfe2",
    "strict rejections reserved singles + not in ledger_player_ids": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q0', which "
    "is not in "
    "ledger_player_ids "
    "in a resumable "
    "snapshot | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q0', which "
    "is not in "
    "ledger_player_ids "
    "in a resumable "
    "snapshot",
    "strict rejections reserved singles + reserved unknown": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] "
    "is a possible_duplicates "
    "key; a possible duplicate "
    "is rejected before any "
    "rating is read | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] "
    "is a possible_duplicates "
    "key; a possible duplicate "
    "is rejected before any "
    "rating is read",
    "strict rejections reserved unknown": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] is "
    "a possible_duplicates key; a possible "
    "duplicate is rejected before any rating is "
    "read | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] is "
    "a possible_duplicates key; a possible "
    "duplicate is rejected before any rating is "
    "read",
    "strict rejections reserved unknown + reserved no baseline": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] "
    "is a "
    "possible_duplicates "
    "key; a possible "
    "duplicate is rejected "
    "before any rating is "
    "read | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] "
    "is a "
    "possible_duplicates "
    "key; a possible "
    "duplicate is rejected "
    "before any rating is "
    "read",
    "strict rejections reserved unknown + reserved singles": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] "
    "is a possible_duplicates "
    "key; a possible duplicate "
    "is rejected before any "
    "rating is read | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m004'] "
    "is a possible_duplicates "
    "key; a possible duplicate "
    "is rejected before any "
    "rating is read",
    "strict rejections unknown key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "unknown key 'zz' | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "unknown key 'zz'",
    "strict rejections unknown key + format": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "unknown key 'zz' | SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "unknown key 'zz'",
    "strict rejections unknown key + missing key": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "missing key 'players' | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen']: "
    "missing key 'players'",
    "strict resumable considered": "SnapshotError: "
    "snapshot.replay_info.matches_considered must equal "
    "the number of ledger_digests in a resumable snapshot "
    "| SnapshotError: "
    "snapshot.replay_info.matches_considered must equal "
    "the number of ledger_digests in a resumable snapshot",
    "strict resumable considered + counted": "SnapshotError: "
    "snapshot.replay_info.matches_considered "
    "must equal the number of ledger_digests in "
    "a resumable snapshot | SnapshotError: "
    "snapshot.replay_info.matches_considered "
    "must equal the number of ledger_digests in "
    "a resumable snapshot",
    "strict resumable considered + log after": "SnapshotError: "
    "snapshot.self_reported_log[0].teams names "
    "'q1', who has no doubles state that a "
    "match on 2025-05-12 could have moved or "
    "frozen | SnapshotError: "
    "snapshot.self_reported_log[0].teams names "
    "'q1', who has no doubles state that a "
    "match on 2025-05-12 could have moved or "
    "frozen",
    "strict resumable counted": "SnapshotError: snapshot.replay_info.matches_counted must "
    "equal the number of processed_match_ids in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.replay_info.matches_counted must equal the "
    "number of processed_match_ids in a resumable snapshot",
    "strict resumable counted + considered": "SnapshotError: "
    "snapshot.replay_info.matches_considered "
    "must equal the number of ledger_digests in "
    "a resumable snapshot | SnapshotError: "
    "snapshot.replay_info.matches_considered "
    "must equal the number of ledger_digests in "
    "a resumable snapshot",
    "strict resumable counted + undigested": "SnapshotError: "
    "snapshot.replay_info.matches_counted must "
    "equal the number of processed_match_ids in "
    "a resumable snapshot | SnapshotError: "
    "snapshot.replay_info.matches_counted must "
    "equal the number of processed_match_ids in "
    "a resumable snapshot",
    "strict resumable derived inherited": "SnapshotError: "
    "snapshot.players[1].ratings.doubles_men.inherited_live_updates "
    "must be 0 in a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[1].ratings.doubles_men.inherited_live_updates "
    "must be 0 in a resumable snapshot",
    "strict resumable derived inherited + undigested": "SnapshotError: "
    "snapshot.players[1].ratings.doubles_men.inherited_live_updates "
    "must be 0 in a resumable snapshot "
    "| SnapshotError: "
    "snapshot.players[1].ratings.doubles_men.inherited_live_updates "
    "must be 0 in a resumable snapshot",
    "strict resumable early inherited": "SnapshotError: "
    "snapshot.players[0].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is derived from its "
    "parent | SnapshotError: "
    "snapshot.players[0].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is derived from its "
    "parent",
    "strict resumable early inherited + early partner": "SnapshotError: "
    "snapshot.players[0].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "strict resumable early inherited + early rd_as_of": "SnapshotError: "
    "snapshot.players[0].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "strict resumable early last_played": "SnapshotError: "
    "snapshot.players[0].ratings.singles.last_played "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[0].ratings.singles.last_played "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "strict resumable early last_played + early ledger id": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q0', which is not in "
    "ledger_player_ids in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q0', which is not in "
    "ledger_player_ids in a "
    "resumable snapshot",
    "strict resumable early last_played + early rd_as_of": "SnapshotError: "
    "snapshot.players[0].ratings.singles.last_played "
    "is after calculated_as_of in "
    "a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.singles.last_played "
    "is after calculated_as_of in "
    "a resumable snapshot",
    "strict resumable early ledger id": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q0', which is not in ledger_player_ids in "
    "a resumable snapshot | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q0', which is not in ledger_player_ids in "
    "a resumable snapshot",
    "strict resumable early ledger id + early last_played": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q0', which is not in "
    "ledger_player_ids in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q0', which is not in "
    "ledger_player_ids in a "
    "resumable snapshot",
    "strict resumable early live": "SnapshotError: "
    "snapshot.players[0].ratings.singles.live_updates_since_replay "
    "must be 0 in a resumable snapshot | SnapshotError: "
    "snapshot.players[0].ratings.singles.live_updates_since_replay "
    "must be 0 in a resumable snapshot",
    "strict resumable early live + early opponent": "SnapshotError: "
    "snapshot.players[0].ratings.singles.opponents['q2'] "
    "is after calculated_as_of in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[0].ratings.singles.opponents['q2'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "strict resumable early live + partnered ledger id": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which is not in "
    "ledger_player_ids in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which is not in "
    "ledger_player_ids in a "
    "resumable snapshot",
    "strict resumable early opponent": "SnapshotError: "
    "snapshot.players[0].ratings.singles.opponents['q2'] "
    "is after calculated_as_of in a resumable snapshot "
    "| SnapshotError: "
    "snapshot.players[0].ratings.singles.opponents['q2'] "
    "is after calculated_as_of in a resumable snapshot",
    "strict resumable early opponent + early live": "SnapshotError: "
    "snapshot.players[0].ratings.singles.opponents['q2'] "
    "is after calculated_as_of in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[0].ratings.singles.opponents['q2'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "strict resumable early opponent + early partner": "SnapshotError: "
    "snapshot.players[0].ratings.singles.opponents['q2'] "
    "is after calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.singles.opponents['q2'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "strict resumable early partner": "engine 9112a66656868710 | engine fa09ef92c4e2dac2",
    "strict resumable early partner + early inherited": "SnapshotError: "
    "snapshot.players[0].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "strict resumable early partner + early opponent": "SnapshotError: "
    "snapshot.players[0].ratings.singles.opponents['q2'] "
    "is after calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.singles.opponents['q2'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "strict resumable early rd_as_of": "SnapshotError: "
    "snapshot.players[0].ratings.singles.rd_as_of is "
    "after calculated_as_of in a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.singles.rd_as_of is "
    "after calculated_as_of in a resumable snapshot",
    "strict resumable early rd_as_of + early inherited": "SnapshotError: "
    "snapshot.players[0].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[0].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "strict resumable early rd_as_of + early last_played": "SnapshotError: "
    "snapshot.players[0].ratings.singles.last_played "
    "is after calculated_as_of in "
    "a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[0].ratings.singles.last_played "
    "is after calculated_as_of in "
    "a resumable snapshot",
    "strict resumable late inherited": "SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is derived from its "
    "parent | SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is derived from its "
    "parent",
    "strict resumable late inherited + late partner": "SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "strict resumable late inherited + late rd_as_of": "SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "strict resumable late last_played": "SnapshotError: "
    "snapshot.players[9].ratings.singles.last_played "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[9].ratings.singles.last_played "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "strict resumable late last_played + late ledger id": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q9', which is not in "
    "ledger_player_ids in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q9', which is not in "
    "ledger_player_ids in a "
    "resumable snapshot",
    "strict resumable late last_played + late rd_as_of": "SnapshotError: "
    "snapshot.players[9].ratings.singles.last_played "
    "is after calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[9].ratings.singles.last_played "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "strict resumable late ledger id": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q9', which is not in ledger_player_ids in "
    "a resumable snapshot | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q9', which is not in ledger_player_ids in "
    "a resumable snapshot",
    "strict resumable late ledger id + late last_played": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q9', which is not in "
    "ledger_player_ids in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q9', which is not in "
    "ledger_player_ids in a "
    "resumable snapshot",
    "strict resumable late ledger id + partnered live": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q9', which is not in "
    "ledger_player_ids in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q9', which is not in "
    "ledger_player_ids in a resumable "
    "snapshot",
    "strict resumable late live": "SnapshotError: "
    "snapshot.players[9].ratings.singles.live_updates_since_replay "
    "must be 0 in a resumable snapshot | SnapshotError: "
    "snapshot.players[9].ratings.singles.live_updates_since_replay "
    "must be 0 in a resumable snapshot",
    "strict resumable late live + late opponent": "SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "strict resumable late live + source at last_played": "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played (1) "
    "must not exceed the "
    "self-reported doubles and "
    "category matches logged for "
    "this player (0) | "
    "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played (1) "
    "must not exceed the "
    "self-reported doubles and "
    "category matches logged for "
    "this player (0)",
    "strict resumable late opponent": "SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a resumable snapshot "
    "| SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a resumable snapshot",
    "strict resumable late opponent + late live": "SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "strict resumable late opponent + late partner": "SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "strict resumable late partner": "engine 9112a66656868710 | engine fa09ef92c4e2dac2",
    "strict resumable late partner + late inherited": "SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "strict resumable late partner + late opponent": "SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a "
    "resumable snapshot | SnapshotError: "
    "snapshot.players[9].ratings.singles.opponents['q8'] "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "strict resumable late rd_as_of": "SnapshotError: "
    "snapshot.players[9].ratings.singles.rd_as_of is "
    "after calculated_as_of in a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[9].ratings.singles.rd_as_of is "
    "after calculated_as_of in a resumable snapshot",
    "strict resumable late rd_as_of + late inherited": "SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent | "
    "SnapshotError: "
    "snapshot.players[9].ratings.singles.inherited_live_updates "
    "must be 0 unless the state is "
    "derived from its parent",
    "strict resumable late rd_as_of + late last_played": "SnapshotError: "
    "snapshot.players[9].ratings.singles.last_played "
    "is after calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[9].ratings.singles.last_played "
    "is after calculated_as_of in a "
    "resumable snapshot",
    "strict resumable log after": "SnapshotError: snapshot.self_reported_log[0].teams "
    "names 'q1', who has no doubles state that a match on "
    "2025-05-12 could have moved or frozen | SnapshotError: "
    "snapshot.self_reported_log[0].teams names 'q1', who "
    "has no doubles state that a match on 2025-05-12 could "
    "have moved or frozen",
    "strict resumable log after + considered": "SnapshotError: "
    "snapshot.self_reported_log[0].teams names "
    "'q1', who has no doubles state that a "
    "match on 2025-05-12 could have moved or "
    "frozen | SnapshotError: "
    "snapshot.self_reported_log[0].teams names "
    "'q1', who has no doubles state that a "
    "match on 2025-05-12 could have moved or "
    "frozen",
    "strict resumable log after + source after as_of": "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q1', who has no doubles "
    "state that a match on 2025-05-12 "
    "could have moved or frozen | "
    "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q1', who has no doubles "
    "state that a match on 2025-05-12 "
    "could have moved or frozen",
    "strict resumable partnered inherited": "SnapshotError: "
    "snapshot.players[1].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is derived from "
    "its parent | SnapshotError: "
    "snapshot.players[1].ratings.doubles.inherited_live_updates "
    "must be 0 unless the state is derived from "
    "its parent",
    "strict resumable partnered inherited + partnered partner": "SnapshotError: "
    "snapshot.players[1].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent | "
    "SnapshotError: "
    "snapshot.players[1].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent",
    "strict resumable partnered inherited + partnered rd_as_of": "SnapshotError: "
    "snapshot.players[1].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent | "
    "SnapshotError: "
    "snapshot.players[1].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent",
    "strict resumable partnered last_played": "SnapshotError: "
    "snapshot.players[1].ratings.doubles.last_played "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[1].ratings.doubles.last_played "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "strict resumable partnered last_played + partnered ledger id": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which is "
    "not in "
    "ledger_player_ids in "
    "a resumable snapshot "
    "| SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which is "
    "not in "
    "ledger_player_ids in "
    "a resumable snapshot",
    "strict resumable partnered last_played + partnered rd_as_of": "SnapshotError: "
    "snapshot.players[1].ratings.doubles.last_played "
    "is after "
    "calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[1].ratings.doubles.last_played "
    "is after "
    "calculated_as_of in a "
    "resumable snapshot",
    "strict resumable partnered ledger id": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which is not in "
    "ledger_player_ids in a resumable snapshot | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which is not in "
    "ledger_player_ids in a resumable snapshot",
    "strict resumable partnered ledger id + early live": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which is not in "
    "ledger_player_ids in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which is not in "
    "ledger_player_ids in a "
    "resumable snapshot",
    "strict resumable partnered ledger id + partnered last_played": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which is "
    "not in "
    "ledger_player_ids in "
    "a resumable snapshot "
    "| SnapshotError: "
    "snapshot.seed_sensitive_rejections['frozen'].players "
    "names 'q1', which is "
    "not in "
    "ledger_player_ids in "
    "a resumable snapshot",
    "strict resumable partnered live": "SnapshotError: "
    "snapshot.players[1].ratings.doubles.live_updates_since_replay "
    "must be 0 in a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[1].ratings.doubles.live_updates_since_replay "
    "must be 0 in a resumable snapshot",
    "strict resumable partnered live + late ledger id": "SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q9', which is not in "
    "ledger_player_ids in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.seed_sensitive_rejections['m000'].players "
    "names 'q9', which is not in "
    "ledger_player_ids in a resumable "
    "snapshot",
    "strict resumable partnered live + partnered opponent": "SnapshotError: "
    "snapshot.players[1].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in "
    "a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[1].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in "
    "a resumable snapshot",
    "strict resumable partnered opponent": "SnapshotError: "
    "snapshot.players[1].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[1].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "strict resumable partnered opponent + partnered live": "SnapshotError: "
    "snapshot.players[1].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in "
    "a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[1].ratings.doubles.opponents['q9'] "
    "is after calculated_as_of in "
    "a resumable snapshot",
    "strict resumable partnered opponent + partnered partner": "SnapshotError: "
    "snapshot.players[1].ratings.doubles.partners['q7'] "
    "is after calculated_as_of "
    "in a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[1].ratings.doubles.partners['q7'] "
    "is after calculated_as_of "
    "in a resumable snapshot",
    "strict resumable partnered partner": "SnapshotError: "
    "snapshot.players[1].ratings.doubles.partners['q7'] "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[1].ratings.doubles.partners['q7'] "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "strict resumable partnered partner + partnered inherited": "SnapshotError: "
    "snapshot.players[1].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent | "
    "SnapshotError: "
    "snapshot.players[1].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent",
    "strict resumable partnered partner + partnered opponent": "SnapshotError: "
    "snapshot.players[1].ratings.doubles.partners['q7'] "
    "is after calculated_as_of "
    "in a resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[1].ratings.doubles.partners['q7'] "
    "is after calculated_as_of "
    "in a resumable snapshot",
    "strict resumable partnered rd_as_of": "SnapshotError: "
    "snapshot.players[1].ratings.doubles.rd_as_of "
    "is after calculated_as_of in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.players[1].ratings.doubles.rd_as_of "
    "is after calculated_as_of in a resumable "
    "snapshot",
    "strict resumable partnered rd_as_of + partnered inherited": "SnapshotError: "
    "snapshot.players[1].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent | "
    "SnapshotError: "
    "snapshot.players[1].ratings.doubles.inherited_live_updates "
    "must be 0 unless the "
    "state is derived from "
    "its parent",
    "strict resumable partnered rd_as_of + partnered last_played": "SnapshotError: "
    "snapshot.players[1].ratings.doubles.last_played "
    "is after "
    "calculated_as_of in a "
    "resumable snapshot | "
    "SnapshotError: "
    "snapshot.players[1].ratings.doubles.last_played "
    "is after "
    "calculated_as_of in a "
    "resumable snapshot",
    "strict resumable passes derived inherited + source at a contact": "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not "
    "exceed the "
    "self-reported "
    "doubles and "
    "category matches "
    "logged for this "
    "player (0) | "
    "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not "
    "exceed the "
    "self-reported "
    "doubles and "
    "category matches "
    "logged for this "
    "player (0)",
    "strict resumable passes late last_played + source at a contact": "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not "
    "exceed the "
    "self-reported "
    "doubles and "
    "category matches "
    "logged for this "
    "player (0) | "
    "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not "
    "exceed the "
    "self-reported "
    "doubles and "
    "category matches "
    "logged for this "
    "player (0)",
    "strict resumable passes log after + source at a contact": "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q1', who has no "
    "doubles state that a "
    "match on 2025-05-12 could "
    "have moved or frozen | "
    "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q1', who has no "
    "doubles state that a "
    "match on 2025-05-12 could "
    "have moved or frozen",
    "strict resumable passes partnered partner + source at a contact": "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not "
    "exceed the "
    "self-reported "
    "doubles and "
    "category matches "
    "logged for this "
    "player (0) | "
    "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not "
    "exceed the "
    "self-reported "
    "doubles and "
    "category matches "
    "logged for this "
    "player (0)",
    "strict resumable passes source at a contact + derived inherited": "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not "
    "exceed the "
    "self-reported "
    "doubles and "
    "category matches "
    "logged for this "
    "player (0) | "
    "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not "
    "exceed the "
    "self-reported "
    "doubles and "
    "category matches "
    "logged for this "
    "player (0)",
    "strict resumable passes source at a contact + late last_played": "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not "
    "exceed the "
    "self-reported "
    "doubles and "
    "category matches "
    "logged for this "
    "player (0) | "
    "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not "
    "exceed the "
    "self-reported "
    "doubles and "
    "category matches "
    "logged for this "
    "player (0)",
    "strict resumable passes source at a contact + log after": "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q1', who has no "
    "doubles state that a "
    "match on 2025-05-12 could "
    "have moved or frozen | "
    "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q1', who has no "
    "doubles state that a "
    "match on 2025-05-12 could "
    "have moved or frozen",
    "strict resumable passes source at a contact + partnered partner": "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not "
    "exceed the "
    "self-reported "
    "doubles and "
    "category matches "
    "logged for this "
    "player (0) | "
    "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not "
    "exceed the "
    "self-reported "
    "doubles and "
    "category matches "
    "logged for this "
    "player (0)",
    "strict resumable source after as_of": "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - verified_matches_played (1) "
    "must not exceed the self-reported doubles and "
    "category matches logged for this player (0) | "
    "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - verified_matches_played (1) "
    "must not exceed the self-reported doubles and "
    "category matches logged for this player (0)",
    "strict resumable source after as_of + log after": "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q1', who has no doubles "
    "state that a match on 2025-05-12 "
    "could have moved or frozen | "
    "SnapshotError: "
    "snapshot.self_reported_log[0].teams "
    "names 'q1', who has no doubles "
    "state that a match on 2025-05-12 "
    "could have moved or frozen",
    "strict resumable source after as_of + source at a contact": "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not exceed the "
    "self-reported doubles "
    "and category matches "
    "logged for this player "
    "(0) | SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not exceed the "
    "self-reported doubles "
    "and category matches "
    "logged for this player "
    "(0)",
    "strict resumable source at a contact": "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - verified_matches_played (1) "
    "must not exceed the self-reported doubles "
    "and category matches logged for this player "
    "(0) | SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - verified_matches_played (1) "
    "must not exceed the self-reported doubles "
    "and category matches logged for this player "
    "(0)",
    "strict resumable source at a contact + source after as_of": "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not exceed the "
    "self-reported doubles "
    "and category matches "
    "logged for this player "
    "(0) | SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not exceed the "
    "self-reported doubles "
    "and category matches "
    "logged for this player "
    "(0)",
    "strict resumable source at a contact + source at last_played": "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not exceed "
    "the self-reported "
    "doubles and category "
    "matches logged for "
    "this player (0) | "
    "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not exceed "
    "the self-reported "
    "doubles and category "
    "matches logged for "
    "this player (0)",
    "strict resumable source at last_played": "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - verified_matches_played "
    "(1) must not exceed the self-reported "
    "doubles and category matches logged for "
    "this player (0) | SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - verified_matches_played "
    "(1) must not exceed the self-reported "
    "doubles and category matches logged for "
    "this player (0)",
    "strict resumable source at last_played + late live": "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played (1) "
    "must not exceed the "
    "self-reported doubles and "
    "category matches logged for "
    "this player (0) | "
    "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played (1) "
    "must not exceed the "
    "self-reported doubles and "
    "category matches logged for "
    "this player (0)",
    "strict resumable source at last_played + source at a contact": "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not exceed "
    "the self-reported "
    "doubles and category "
    "matches logged for "
    "this player (0) | "
    "SnapshotError: "
    "snapshot.players[2].ratings.doubles: "
    "matches_played - "
    "verified_matches_played "
    "(1) must not exceed "
    "the self-reported "
    "doubles and category "
    "matches logged for "
    "this player (0)",
    "strict resumable undigested": "SnapshotError: snapshot.processed_match_ids names "
    "'m001', which is not in ledger_digests in a resumable "
    "snapshot | SnapshotError: "
    "snapshot.processed_match_ids names 'm001', which is "
    "not in ledger_digests in a resumable snapshot",
    "strict resumable undigested + counted": "SnapshotError: "
    "snapshot.replay_info.matches_counted must "
    "equal the number of processed_match_ids in "
    "a resumable snapshot | SnapshotError: "
    "snapshot.replay_info.matches_counted must "
    "equal the number of processed_match_ids in "
    "a resumable snapshot",
    "strict resumable undigested + derived inherited": "SnapshotError: "
    "snapshot.players[1].ratings.doubles_men.inherited_live_updates "
    "must be 0 in a resumable snapshot "
    "| SnapshotError: "
    "snapshot.players[1].ratings.doubles_men.inherited_live_updates "
    "must be 0 in a resumable snapshot",
}


def _broken_snapshot(name: str) -> Snap:
    """The case's snapshot with its breakers applied; one that an earlier break undid is skipped."""
    cfg_name, group, chosen = SNAPSHOT_CASES[name]
    snap = _replayed(cfg_name).to_snapshot()
    pick = _resumable_breakers if group == "resumable" else _rejection_breakers
    breakers = pick(snap)
    for breaker in chosen:
        try:
            breakers[breaker](snap)
        except (AttributeError, IndexError, KeyError, TypeError):
            continue
    return snap


@pytest.mark.parametrize("name", sorted(SNAPSHOT_CASES))
def test_snapshot_checks_keep_their_order(name: str) -> None:
    snap = _broken_snapshot(name)
    cfg = CONFIGS[SNAPSHOT_CASES[name][0]]
    outcome = _outcome(lambda: RatingEngine.from_snapshot(snap, cfg))
    later = AS_OF + timedelta(days=5)
    resumed = _outcome(lambda: replay(ROWS, cfg=cfg, as_of=later, from_snapshot=snap))
    assert f"{outcome} | {resumed}" == SNAPSHOT_EXPECTED[name]


# ---------------------------------------------------------------------------
# prequential_predictions, evaluate, fit_scale, fit_win_calibration
# ---------------------------------------------------------------------------


def _evaluation_cases() -> dict[str, Callable[[], object]]:
    cases: dict[str, Callable[[], object]] = {}
    for cfg_name in ("default", "strict", "nofeed"):
        cfg = CONFIGS[cfg_name]
        seeds = _seeds(cfg)
        for mode in ("live", "scheduled"):
            for scoring, score_from, anchor in (
                (None, None, None),
                (SO, D0 + timedelta(days=40), None),
                (RA, None, D0 - timedelta(days=3)),
                (None, D0 + timedelta(days=200), D0 + timedelta(days=11)),
            ):
                tag = f"{cfg_name} {mode} {scoring} {score_from} {anchor}"
                kw = {
                    "cfg": cfg,
                    "scoring": scoring,
                    "burn_in_matches": 1,
                    "mode": mode,
                    "score_from": score_from,
                    "anchor": anchor,
                }
                cases[f"prequential {tag}"] = lambda kw=kw, seeds=seeds: prequential_predictions(
                    ROWS, seeds, **kw
                )
                cases[f"evaluate {tag}"] = lambda kw=kw, seeds=seeds: evaluate(ROWS, seeds, **kw)
            for scoring in (SO, RA):
                tag = f"{cfg_name} {mode} {scoring}"
                cases[f"fit_scale {tag}"] = lambda m=mode, sc=scoring, cfg=cfg, seeds=seeds: (
                    fit_scale(
                        ROWS[:30],
                        seeds,
                        cfg=cfg,
                        scoring=sc,
                        burn_in_matches=1,
                        mode=m,
                        tolerance=0.1,
                    )
                )
                cases[f"fit_win {tag}"] = lambda m=mode, sc=scoring, cfg=cfg, seeds=seeds: (
                    fit_win_calibration(
                        ROWS[:30],
                        seeds,
                        cfg=cfg,
                        scoring=sc,
                        burn_in_matches=1,
                        mode=m,
                        tolerance=0.01,
                    )
                )
    cfg = CONFIGS["default"]
    seeds = _seeds(cfg)
    scratch = RatingEngine(cfg)
    later = ExternalRatingSnapshot("ext", 4.0, D0 + timedelta(days=400), None, "x-late", None)
    scratch.seed_external_rating("late", DBL, later)
    late = copy.deepcopy(scratch.players["late"])
    bad_row = dataclasses.replace(ROWS[3], best_of=2)
    conflict = dataclasses.replace(ROWS[5], sequence=ROWS[5].sequence + 9)
    twin = copy.deepcopy(seeds[1])
    rows = ROWS[:30]
    errors: dict[str, tuple[list[object], list[object], dict[str, object]]] = {
        "score_from then mode": (rows, seeds, {"score_from": "x", "mode": "Live"}),
        "anchor then mode": (rows, seeds, {"anchor": datetime(2025, 1, 1), "mode": 1}),
        "mode then scoring": (rows, seeds, {"mode": "x", "scoring": "rally"}),
        "scoring then burn": (rows, seeds, {"scoring": 1, "burn_in_matches": 1.0}),
        "burn then cfg": (rows, seeds, {"burn_in_matches": True, "cfg": "x"}),
        "cfg then row": (rows + [bad_row], seeds, {"cfg": 1}),
        "row then conflict": (rows + [bad_row, conflict], seeds, {}),
        "conflict then seed": (rows + [conflict], seeds + [twin], {}),
        "not a match then seed": (rows + ["x"], seeds + [late], {}),
        "seed then late seed": (rows, seeds + [twin, late], {}),
        "late seed": (rows, seeds + [late], {}),
        "not a seed": (rows, seeds + ["x"], {}),
        "only non-completed rows": (
            [dataclasses.replace(m, status=MatchStatus.INCOMPLETE, games=()) for m in rows],
            seeds + [late],
            {},
        ),
    }
    for name, (ms, ss, kw) in errors.items():
        for mode in ("live", "scheduled"):
            full = {"cfg": cfg, "burn_in_matches": 1, "mode": mode, **kw}
            cases[f"prequential error {mode} {name}"] = lambda ms=ms, ss=ss, kw=full: (
                prequential_predictions(ms, ss, **kw)  # type: ignore[arg-type]
            )
            fit_kw = {"scoring": SO, "tolerance": 0.5, **full}
            cases[f"fit_scale error {mode} {name}"] = lambda ms=ms, ss=ss, kw=fit_kw: fit_scale(
                ms,
                ss,
                **kw,  # type: ignore[arg-type]
            )
    cases["fit_scale low 0"] = lambda: fit_scale(rows, seeds, low=0.0)
    cases["fit_scale scoring None"] = lambda: fit_scale(rows, seeds, scoring=None)  # type: ignore[arg-type]
    cases["fit_scale bounds"] = lambda: fit_scale(rows, seeds, low=5.0, high=2.0)
    cases["fit_scale empty"] = lambda: fit_scale([], [])
    cases["fit_scale tiny tolerance"] = lambda: fit_scale(
        rows, seeds, low=2.0, high=2.0000001, tolerance=1e-12, burn_in_matches=0
    )
    cases["fit_win empty"] = lambda: fit_win_calibration([], [])
    return cases


EVALUATION_CASES = _evaluation_cases()
EVALUATION_EXPECTED: dict[str, str] = {
    "evaluate default live None 2025-07-20 2025-01-12": "EvaluationError: No established "
    "matches to evaluate",
    "evaluate default live None None None": "ok 63d4ecc2691d47df",
    "evaluate default live rally None 2024-12-29": "ok 7ade33da245b1acd",
    "evaluate default live side_out 2025-02-10 None": "ok c537ef2be8566f15",
    "evaluate default scheduled None 2025-07-20 2025-01-12": "EvaluationError: No "
    "established matches to "
    "evaluate",
    "evaluate default scheduled None None None": "ok 63d4ecc2691d47df",
    "evaluate default scheduled rally None 2024-12-29": "ok 7ade33da245b1acd",
    "evaluate default scheduled side_out 2025-02-10 None": "ok c537ef2be8566f15",
    "evaluate nofeed live None 2025-07-20 2025-01-12": "EvaluationError: No established "
    "matches to evaluate",
    "evaluate nofeed live None None None": "ok c878c5ead3f71d50",
    "evaluate nofeed live rally None 2024-12-29": "EvaluationError: No established "
    "matches to evaluate",
    "evaluate nofeed live side_out 2025-02-10 None": "ok 46044d756577c2b0",
    "evaluate nofeed scheduled None 2025-07-20 2025-01-12": "EvaluationError: No "
    "established matches to "
    "evaluate",
    "evaluate nofeed scheduled None None None": "ok c878c5ead3f71d50",
    "evaluate nofeed scheduled rally None 2024-12-29": "EvaluationError: No established "
    "matches to evaluate",
    "evaluate nofeed scheduled side_out 2025-02-10 None": "ok 46044d756577c2b0",
    "evaluate strict live None 2025-07-20 2025-01-12": "EvaluationError: No established "
    "matches to evaluate",
    "evaluate strict live None None None": "ok f9dde7f60e06968b",
    "evaluate strict live rally None 2024-12-29": "ok db6da43856582cb7",
    "evaluate strict live side_out 2025-02-10 None": "ok 0e5d912f6d33a136",
    "evaluate strict scheduled None 2025-07-20 2025-01-12": "EvaluationError: No "
    "established matches to "
    "evaluate",
    "evaluate strict scheduled None None None": "ok f9dde7f60e06968b",
    "evaluate strict scheduled rally None 2024-12-29": "ok db6da43856582cb7",
    "evaluate strict scheduled side_out 2025-02-10 None": "ok 0e5d912f6d33a136",
    "fit_scale bounds": "EvaluationError: low must be less than high",
    "fit_scale default live rally": "ok 5ae84fbd41481b6e",
    "fit_scale default live side_out": "ok ef9e90846ed8837f",
    "fit_scale default scheduled rally": "ok 5ae84fbd41481b6e",
    "fit_scale default scheduled side_out": "ok ef9e90846ed8837f",
    "fit_scale empty": "EvaluationError: No established matches to evaluate",
    "fit_scale error live anchor then mode": "EvaluationError: anchor must be a plain "
    "date, not datetime",
    "fit_scale error live burn then cfg": "EvaluationError: burn_in_matches must be an "
    "int, not bool",
    "fit_scale error live cfg then row": "ConfigError: cfg must be exactly a Config, not int",
    "fit_scale error live conflict then seed": "LedgerConflictError: Conflicting ledger "
    "rows for match_id: m005",
    "fit_scale error live late seed": "InvalidSeedError: Seed for late in doubles: "
    "initial_source_date must not be after the last "
    "completed ledger match",
    "fit_scale error live mode then scoring": "EvaluationError: mode must be 'live' or "
    "'scheduled', not 'x'",
    "fit_scale error live not a match then seed": "InvalidMatchError: match must be a "
    "Match, not str",
    "fit_scale error live not a seed": "InvalidSeedError: Seed must be a Player, not str",
    "fit_scale error live only non-completed rows": "EvaluationError: No established "
    "matches to evaluate",
    "fit_scale error live row then conflict": "InvalidMatchError: best_of must be a "
    "positive odd number",
    "fit_scale error live score_from then mode": "EvaluationError: score_from must be a "
    "plain date, not str",
    "fit_scale error live scoring then burn": "EvaluationError: scoring must be a "
    "ScoringType or None, not int",
    "fit_scale error live seed then late seed": "PlayerError: Player already exists: q1",
    "fit_scale error scheduled anchor then mode": "EvaluationError: anchor must be a "
    "plain date, not datetime",
    "fit_scale error scheduled burn then cfg": "EvaluationError: burn_in_matches must be "
    "an int, not bool",
    "fit_scale error scheduled cfg then row": "ConfigError: cfg must be exactly a Config, not int",
    "fit_scale error scheduled conflict then seed": "LedgerConflictError: Conflicting "
    "ledger rows for match_id: m005",
    "fit_scale error scheduled late seed": "InvalidSeedError: Seed for late in doubles: "
    "initial_source_date must not be after the "
    "last completed ledger match",
    "fit_scale error scheduled mode then scoring": "EvaluationError: mode must be 'live' "
    "or 'scheduled', not 'x'",
    "fit_scale error scheduled not a match then seed": "InvalidMatchError: match must be "
    "a Match, not str",
    "fit_scale error scheduled not a seed": "InvalidSeedError: Seed must be a Player, not str",
    "fit_scale error scheduled only non-completed rows": "EvaluationError: No established "
    "matches to evaluate",
    "fit_scale error scheduled row then conflict": "InvalidMatchError: best_of must be a "
    "positive odd number",
    "fit_scale error scheduled score_from then mode": "EvaluationError: score_from must "
    "be a plain date, not str",
    "fit_scale error scheduled scoring then burn": "EvaluationError: scoring must be a "
    "ScoringType or None, not int",
    "fit_scale error scheduled seed then late seed": "PlayerError: Player already exists: q1",
    "fit_scale low 0": "ConfigError: scales must be positive",
    "fit_scale nofeed live rally": "EvaluationError: No established matches to evaluate",
    "fit_scale nofeed live side_out": "EvaluationError: No established matches to evaluate",
    "fit_scale nofeed scheduled rally": "EvaluationError: No established matches to evaluate",
    "fit_scale nofeed scheduled side_out": "EvaluationError: No established matches to evaluate",
    "fit_scale scoring None": "EvaluationError: scoring must be a ScoringType, not NoneType",
    "fit_scale strict live rally": "ok cc92b8147b29ef82",
    "fit_scale strict live side_out": "ok c3664f848c8996d8",
    "fit_scale strict scheduled rally": "ok cc92b8147b29ef82",
    "fit_scale strict scheduled side_out": "ok c3664f848c8996d8",
    "fit_scale tiny tolerance": "ok 08239fa0327f1ac9",
    "fit_win default live rally": "ok c0b0608dda3381e5",
    "fit_win default live side_out": "ok a7f64233b374a9fe",
    "fit_win default scheduled rally": "ok c0b0608dda3381e5",
    "fit_win default scheduled side_out": "ok a7f64233b374a9fe",
    "fit_win empty": "EvaluationError: no predictions with a modeled win probability to fit",
    "fit_win nofeed live rally": "EvaluationError: no predictions with a modeled win "
    "probability to fit",
    "fit_win nofeed live side_out": "EvaluationError: no predictions with a modeled win "
    "probability to fit",
    "fit_win nofeed scheduled rally": "EvaluationError: no predictions with a modeled win "
    "probability to fit",
    "fit_win nofeed scheduled side_out": "EvaluationError: no predictions with a modeled "
    "win probability to fit",
    "fit_win strict live rally": "ok 2ec84c2dbd790d3d",
    "fit_win strict live side_out": "ok 1b23cb194ee1bb9f",
    "fit_win strict scheduled rally": "ok 2ec84c2dbd790d3d",
    "fit_win strict scheduled side_out": "ok 1b23cb194ee1bb9f",
    "prequential default live None 2025-07-20 2025-01-12": "ok b3283bf184bb082f",
    "prequential default live None None None": "ok 859b35d7e330367f",
    "prequential default live rally None 2024-12-29": "ok 4a7893454eee7792",
    "prequential default live side_out 2025-02-10 None": "ok 14dc6effec3c63c4",
    "prequential default scheduled None 2025-07-20 2025-01-12": "ok b3283bf184bb082f",
    "prequential default scheduled None None None": "ok 859b35d7e330367f",
    "prequential default scheduled rally None 2024-12-29": "ok 4a7893454eee7792",
    "prequential default scheduled side_out 2025-02-10 None": "ok 14dc6effec3c63c4",
    "prequential error live anchor then mode": "EvaluationError: anchor must be a plain "
    "date, not datetime",
    "prequential error live burn then cfg": "EvaluationError: burn_in_matches must be an "
    "int, not bool",
    "prequential error live cfg then row": "ConfigError: cfg must be exactly a Config, not int",
    "prequential error live conflict then seed": "LedgerConflictError: Conflicting ledger "
    "rows for match_id: m005",
    "prequential error live late seed": "InvalidSeedError: Seed for late in doubles: "
    "initial_source_date must not be after the last "
    "completed ledger match",
    "prequential error live mode then scoring": "EvaluationError: mode must be 'live' or "
    "'scheduled', not 'x'",
    "prequential error live not a match then seed": "InvalidMatchError: match must be a "
    "Match, not str",
    "prequential error live not a seed": "InvalidSeedError: Seed must be a Player, not str",
    "prequential error live only non-completed rows": "ok b3283bf184bb082f",
    "prequential error live row then conflict": "InvalidMatchError: best_of must be a "
    "positive odd number",
    "prequential error live score_from then mode": "EvaluationError: score_from must be a "
    "plain date, not str",
    "prequential error live scoring then burn": "EvaluationError: scoring must be a "
    "ScoringType or None, not int",
    "prequential error live seed then late seed": "PlayerError: Player already exists: q1",
    "prequential error scheduled anchor then mode": "EvaluationError: anchor must be a "
    "plain date, not datetime",
    "prequential error scheduled burn then cfg": "EvaluationError: burn_in_matches must "
    "be an int, not bool",
    "prequential error scheduled cfg then row": "ConfigError: cfg must be exactly a "
    "Config, not int",
    "prequential error scheduled conflict then seed": "LedgerConflictError: Conflicting "
    "ledger rows for match_id: m005",
    "prequential error scheduled late seed": "InvalidSeedError: Seed for late in doubles: "
    "initial_source_date must not be after the "
    "last completed ledger match",
    "prequential error scheduled mode then scoring": "EvaluationError: mode must be "
    "'live' or 'scheduled', not 'x'",
    "prequential error scheduled not a match then seed": "InvalidMatchError: match must "
    "be a Match, not str",
    "prequential error scheduled not a seed": "InvalidSeedError: Seed must be a Player, not str",
    "prequential error scheduled only non-completed rows": "ok b3283bf184bb082f",
    "prequential error scheduled row then conflict": "InvalidMatchError: best_of must be "
    "a positive odd number",
    "prequential error scheduled score_from then mode": "EvaluationError: score_from must "
    "be a plain date, not str",
    "prequential error scheduled scoring then burn": "EvaluationError: scoring must be a "
    "ScoringType or None, not int",
    "prequential error scheduled seed then late seed": "PlayerError: Player already exists: q1",
    "prequential nofeed live None 2025-07-20 2025-01-12": "ok b3283bf184bb082f",
    "prequential nofeed live None None None": "ok 74f4fe54d2a7b36f",
    "prequential nofeed live rally None 2024-12-29": "ok b3283bf184bb082f",
    "prequential nofeed live side_out 2025-02-10 None": "ok 74f4fe54d2a7b36f",
    "prequential nofeed scheduled None 2025-07-20 2025-01-12": "ok b3283bf184bb082f",
    "prequential nofeed scheduled None None None": "ok 74f4fe54d2a7b36f",
    "prequential nofeed scheduled rally None 2024-12-29": "ok b3283bf184bb082f",
    "prequential nofeed scheduled side_out 2025-02-10 None": "ok 74f4fe54d2a7b36f",
    "prequential strict live None 2025-07-20 2025-01-12": "ok b3283bf184bb082f",
    "prequential strict live None None None": "ok 575b1a800339dd71",
    "prequential strict live rally None 2024-12-29": "ok 00ed90a70cd8204a",
    "prequential strict live side_out 2025-02-10 None": "ok 0230f71920aa5009",
    "prequential strict scheduled None 2025-07-20 2025-01-12": "ok b3283bf184bb082f",
    "prequential strict scheduled None None None": "ok 575b1a800339dd71",
    "prequential strict scheduled rally None 2024-12-29": "ok 00ed90a70cd8204a",
    "prequential strict scheduled side_out 2025-02-10 None": "ok 0230f71920aa5009",
}


@pytest.mark.parametrize("name", sorted(EVALUATION_CASES))
def test_evaluation_keeps_its_results(name: str) -> None:
    assert _outcome(EVALUATION_CASES[name]) == EVALUATION_EXPECTED[name]


# ---------------------------------------------------------------------------
# player_summary and _network_connectivity
# ---------------------------------------------------------------------------

SUMMARY_DATES: tuple[date | None, ...] = (None, D0 + timedelta(days=50), D0 + timedelta(days=900))
SUMMARY_EXPECTED: dict[str, str] = {
    "cap q0": "15 03afafee42d6d482",
    "cap q1": "15 ddce747daa6462dd",
    "cap q2": "15 ed8b2439b7870ea0",
    "cap q3": "15 53314871b9674fdb",
    "cap q4": "15 417e9906e47b0842",
    "cap q5": "15 0d3613bb987a0c18",
    "cap q6": "15 f11c0865b9a610a5",
    "cap q7": "15 0b0bfd67de1910a7",
    "cap q8": "15 0edef55c84f17ee6",
    "cap q9": "15 f3282729eb77b252",
    "cap zz": "15 31a63d8510f7421c",
    "default q0": "15 db5bc3c20b184057",
    "default q1": "15 855ba3c0e22aca11",
    "default q2": "15 bec65fc6fe8f8359",
    "default q3": "15 75ef8a2fb87119f6",
    "default q4": "15 76c5e409d9a1a115",
    "default q5": "15 cb84d2fa80f33126",
    "default q6": "15 590d35eef0fd4edf",
    "default q7": "15 f5c25ba2ff59304a",
    "default q8": "15 e4febc40f7044fd1",
    "default q9": "15 4dd18b637416949c",
    "default zz": "15 31a63d8510f7421c",
    "nofeed q0": "15 8686fd4906d63aad",
    "nofeed q1": "15 105210d9a40535f6",
    "nofeed q2": "15 b5f1748f7c4594de",
    "nofeed q3": "15 99a0bcd418962923",
    "nofeed q4": "15 c7147a65e4fc2af9",
    "nofeed q5": "15 0a546e3f37363dc4",
    "nofeed q6": "15 d3dd449f660fac25",
    "nofeed q7": "15 e2750ab8129c0360",
    "nofeed q8": "15 efff371c359b9bdc",
    "nofeed q9": "15 6ee0c2e63c98a6ee",
    "nofeed zz": "15 31a63d8510f7421c",
    "strict q0": "15 853427ec5d4c0cb9",
    "strict q1": "15 e106daa1e5b9917d",
    "strict q2": "15 94ffd957b09342b3",
    "strict q3": "15 fd6a8a8595d06615",
    "strict q4": "15 1552d4bdda0baa92",
    "strict q5": "15 62789a11892a8314",
    "strict q6": "15 ce6bf44748b540d8",
    "strict q7": "15 922a42dccbf02004",
    "strict q8": "15 37c1c736109102d3",
    "strict q9": "15 b6915486028ce8d9",
    "strict zz": "15 31a63d8510f7421c",
}


def _summary(engine: RatingEngine, pid: object, fmt: object, as_of: object) -> object:
    """player_summary's fields but config_version, which changes with a release."""
    fields = dataclasses.asdict(engine.player_summary(pid, fmt, as_of))  # type: ignore[arg-type]
    fields.pop("config_version")
    return fields


def _summaries(cfg_name: str) -> dict[str, str]:
    """Per player: a digest of every format's summary at every date, the engine unchanged."""
    engine = _replayed(cfg_name)
    before = _snapshot_digest(engine)
    out = {}
    for pid in [*PIDS, "zz"]:
        outcomes = [
            _outcome(lambda p=pid, f=fmt, a=as_of: _summary(engine, p, f, a))  # type: ignore[misc]
            for fmt in RatingFormat
            for as_of in SUMMARY_DATES
        ]
        out[f"{cfg_name} {pid}"] = f"{len(outcomes)} {_digest(outcomes)}"
    assert _snapshot_digest(engine) == before
    return out


@pytest.mark.parametrize("cfg_name", sorted(CONFIGS))
def test_player_summaries_keep_their_values(cfg_name: str) -> None:
    expected = {k: v for k, v in SUMMARY_EXPECTED.items() if k.split(" ")[0] == cfg_name}
    assert _summaries(cfg_name) == expected


SUMMARY_ARGUMENTS: dict[str, tuple[object, object, object]] = {
    "as_of then player": (datetime(2025, 1, 1), "", DBL),
    "as_of subclass": (type("DateSub", (date,), {})(2025, 1, 1), "q0", DBL),
    "player then unknown": (None, 5, DBL),
    "unknown then format": (None, "zz", "doubles"),
    "format": (None, "q0", "doubles"),
    "valid": (None, "q0", DBL),
}
SUMMARY_ARGUMENTS_EXPECTED: dict[str, str] = {
    "as_of subclass": "ReferenceDateError: player_summary as_of must be exactly a date, "
    "not DateSub | ReferenceDateError: player_summary as_of must be "
    "exactly a date, not DateSub",
    "as_of then player": "ReferenceDateError: player_summary as_of must be exactly a "
    "date, not datetime | ReferenceDateError: player_summary as_of "
    "must be exactly a date, not datetime",
    "format": "PlayerError: rating_format must be a RatingFormat, not str | "
    "ReferenceDateError: player_summary needs as_of when the engine has not "
    "been replayed: pass as_of or replay first",
    "player then unknown": "PlayerError: player_id is required | ReferenceDateError: "
    "player_summary needs as_of when the engine has not been "
    "replayed: pass as_of or replay first",
    "unknown then format": "UnknownPlayerError: Unknown player: zz | ReferenceDateError: "
    "player_summary needs as_of when the engine has not been "
    "replayed: pass as_of or replay first",
    "valid": "ok 9e0e7f61675bc239 | ReferenceDateError: player_summary needs as_of when "
    "the engine has not been replayed: pass as_of or replay first",
}


@pytest.mark.parametrize("name", sorted(SUMMARY_ARGUMENTS))
def test_player_summary_checks_keep_their_order(name: str) -> None:
    as_of, pid, fmt = SUMMARY_ARGUMENTS[name]
    replayed = _replayed("cap")
    never = RatingEngine(CONFIGS["cap"])
    never.add_player(Player(id="q0"))
    outcomes = [
        _outcome(lambda e=engine: _summary(e, pid, fmt, as_of))  # type: ignore[misc]
        for engine in (replayed, never)
    ]
    assert " | ".join(outcomes) == SUMMARY_ARGUMENTS_EXPECTED[name]


# ---------------------------------------------------------------------------
# _prequential_windows: the window each match goes in
# ---------------------------------------------------------------------------


def _window_rows(anchor: date, interval: int) -> list[Match]:
    """Matches on grid dates, the day before, and the day after, in ledger order."""
    rows = []
    for k in (-1, 0, 1, 2):
        grid = anchor + timedelta(days=k * interval)
        for offset in (-1, 0, 1):
            day = grid + timedelta(days=offset)
            match_id = f"k{k}{offset:+d}"
            rows.append(Match(match_id, ("q0",), ("q1",), ((11, 4),), day, SGL))
    return sorted({m.played_on: m for m in rows}.values(), key=ledger_order)


WINDOWS_EXPECTED: dict[int, dict[str, int]] = {
    1: {"k-1-1": -3, "k0-1": -2, "k1-1": -1, "k2+0": 1, "k2+1": 2, "k2-1": 0},
    7: {
        "k-1+0": -2,
        "k-1+1": -1,
        "k-1-1": -2,
        "k0+0": -1,
        "k0+1": 0,
        "k0-1": -1,
        "k1+0": 0,
        "k1+1": 1,
        "k1-1": 0,
        "k2+0": 1,
        "k2+1": 2,
        "k2-1": 1,
    },
}


@pytest.mark.parametrize("interval", [1, 7])
def test_scheduled_windows_keep_their_split(interval: int) -> None:
    # The helper is new with issue 143; main computed the same split inline.
    from open_engine._evaluation import _prequential_windows

    anchor = D0 + timedelta(days=10)
    rows = _window_rows(anchor, interval)
    window_of, windows = _prequential_windows(rows, anchor, interval)
    assert window_of == WINDOWS_EXPECTED[interval]
    assert [m.match_id for k in sorted(windows) for m in windows[k]] == [m.match_id for m in rows]
    assert all(window_of[m.match_id] == k for k in windows for m in windows[k])
