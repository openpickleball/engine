"""A snapshot edited in a second field must not pass the verified-count check (#102).

Two rules close the cases a second edit opened. In a resumable snapshot, a
played state with an initial_source_date has last_played after it. And
every player a self-reported log entry names either moved in that format
on or after the entry's latest date, or was frozen there by a baseline
dated on or after it. All ids are synthetic.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import date

import pytest

import test_snapshot_verified_log as fuzz
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

S = RatingFormat.SINGLES
D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
SR = MatchType.SELF_REPORTED
AS_OF = date(2026, 1, 10)

Snap = dict[str, object]


def _sr(mid: str, a: tuple[str, ...], b: tuple[str, ...], on: date, fmt: RatingFormat) -> Match:
    return Match(
        mid, a, b, ((11, 7),), on, fmt, match_type=SR, confirmed_by=frozenset({a[0], b[0]})
    )


def _rows() -> list[Match]:
    # syn-a has two confirmed self-reported matches: doubles, then mixed.
    # syn-z plays only a league singles match.
    return [
        _sr("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), date(2026, 1, 5), D),
        _sr("syn-m2", ("syn-a", "syn-e"), ("syn-f", "syn-g"), date(2026, 1, 6), MX),
        Match("syn-m3", ("syn-z",), ("syn-y",), ((11, 3),), date(2026, 1, 7), S),
    ]


def _snapshot(rows: list[Match], seeds: list[Player] | None = None) -> Snap:
    engine = replay(rows, seeds or [], as_of=AS_OF)
    loaded: Snap = json.loads(json.dumps(engine.to_snapshot()))
    return loaded


@pytest.fixture
def base() -> Snap:
    snap = _snapshot(_rows())
    assert snap["resumable"] is True
    return snap


def _state(snap: Snap, pid: str, fmt: str) -> dict[str, object]:
    players = snap["players"]
    assert isinstance(players, list)
    for player in players:
        if player["id"] == pid:
            state: dict[str, object] = player["ratings"][fmt]
            return state
    raise AssertionError(pid)


def _player_path(snap: Snap, pid: str) -> str:
    players = snap["players"]
    assert isinstance(players, list)
    index = next(i for i, player in enumerate(players) if player["id"] == pid)
    return re.escape(f"snapshot.players[{index}]")


def _entry_index(snap: Snap, fmt: str) -> int:
    entries = snap["self_reported_log"]
    assert isinstance(entries, list)
    return next(i for i, entry in enumerate(entries) if entry["rating_format"] == fmt)


def _swap_in_log(snap: Snap, fmt: str, old: str, new: str) -> None:
    entries = snap["self_reported_log"]
    assert isinstance(entries, list)
    for entry in entries:
        if entry["rating_format"] == fmt:
            entry["teams"] = [
                sorted(new if pid == old else pid for pid in t) for t in entry["teams"]
            ]


def _relabel_syn_a(snap: Snap) -> None:
    """Move one of syn-a's self-reported updates to syn-z in the log, and call it verified."""
    for fmt in ("doubles", "mixed_doubles"):
        state = _state(snap, "syn-a", fmt)
        state["verified_matches_played"] = int(str(state["verified_matches_played"])) + 1
    _swap_in_log(snap, "mixed_doubles", "syn-a", "syn-z")


def _log_message(snap: Snap, pid: str, fmt: str, on: str) -> str:
    index = _entry_index(snap, fmt)
    return re.escape(
        f"snapshot.self_reported_log[{index}].teams names {pid!r}, who has no {fmt} state "
        f"that a match on {on} could have moved or frozen"
    )


# -- case 2: a dated state played on or before its date, in a resumable snapshot


@pytest.mark.parametrize("mixed_source", ["ext", "open_parent:doubles"])
def test_case_2_dated_after_every_logged_match_does_not_load(base: Snap, mixed_source: str) -> None:
    doubles = _state(base, "syn-a", "doubles")
    doubles.update(
        initial_source="ext", initial_source_date="2026-01-06", verified_matches_played=2
    )
    mixed = _state(base, "syn-a", "mixed_doubles")
    mixed.update(
        initial_source=mixed_source, initial_source_date="2026-01-06", verified_matches_played=1
    )
    with pytest.raises(
        SnapshotError,
        match=_player_path(base, "syn-a")
        + re.escape(
            ".ratings.doubles.last_played must be after initial_source_date in a resumable snapshot"
        ),
    ):
        RatingEngine.from_snapshot(base)


def test_case_2_last_played_on_the_source_date_does_not_load(base: Snap) -> None:
    # Dated on its first match: the second match moved it, so the counts
    # agree with the log, but the first must have frozen it.
    doubles = _state(base, "syn-a", "doubles")
    doubles.update(
        initial_source="ext", initial_source_date="2026-01-05", verified_matches_played=1
    )
    _state(base, "syn-a", "mixed_doubles")["initial_source_date"] = "2026-01-05"
    _state(base, "syn-a", "doubles")["last_played"] = "2026-01-05"
    with pytest.raises(
        SnapshotError,
        match=_player_path(base, "syn-a")
        + re.escape(".ratings.doubles.last_played must be after initial_source_date"),
    ):
        RatingEngine.from_snapshot(base)


# -- case 3: a log entry names a player its format never moved or froze


def test_case_3_log_entry_names_a_singles_only_player(base: Snap) -> None:
    _relabel_syn_a(base)
    with pytest.raises(
        SnapshotError, match=_log_message(base, "syn-z", "mixed_doubles", "2026-01-06")
    ):
        RatingEngine.from_snapshot(base)


def _imported(pid: str, fmts: tuple[RatingFormat, ...], on: date) -> Player:
    seeder = RatingEngine()
    for fmt in fmts:
        seeder.seed_external_rating(pid, fmt, ExternalRatingSnapshot("ext", 4.0, on))
    return seeder.players[pid]


def test_case_3_named_player_with_an_unplayed_undated_state() -> None:
    provisional = Player("syn-z", {MX: RatingState(3.5, Config().new_player_rd)})
    snap = _snapshot(_rows(), [provisional])
    _relabel_syn_a(snap)
    with pytest.raises(
        SnapshotError, match=_log_message(snap, "syn-z", "mixed_doubles", "2026-01-06")
    ):
        RatingEngine.from_snapshot(snap)


def test_case_3_named_player_with_only_a_baseline_dated_before_the_log() -> None:
    snap = _snapshot(_rows(), [_imported("syn-z", (MX,), date(2026, 1, 1))])
    snap["resumable"] = False
    _relabel_syn_a(snap)
    with pytest.raises(
        SnapshotError, match=_log_message(snap, "syn-z", "mixed_doubles", "2026-01-06")
    ):
        RatingEngine.from_snapshot(snap)


def test_case_3_named_player_who_last_played_and_was_dated_before_the_log() -> None:
    rows = [
        *_rows(),
        Match("syn-m4", ("syn-z", "syn-h"), ("syn-i", "syn-j"), ((11, 4),), date(2026, 1, 4), MX),
    ]
    snap = _snapshot(rows, [_imported("syn-z", (D, MX), date(2026, 1, 1))])
    snap["resumable"] = False
    assert _state(snap, "syn-z", "mixed_doubles")["last_played"] == "2026-01-04"
    _relabel_syn_a(snap)
    with pytest.raises(
        SnapshotError, match=_log_message(snap, "syn-z", "mixed_doubles", "2026-01-06")
    ):
        RatingEngine.from_snapshot(snap)


# -- what still loads


@pytest.mark.parametrize("resumable", [True, False])
def test_category_entry_frozen_through_dated_doubles_loads(resumable: bool) -> None:
    # syn-a's doubles import is dated on the mixed match, so the mixed match
    # freezes syn-a and stores no mixed state, but logs syn-a.
    snap = _snapshot(_rows(), [_imported("syn-a", (D,), date(2026, 1, 6))])
    players = snap["players"]
    assert isinstance(players, list)
    syn_a = next(player for player in players if player["id"] == "syn-a")
    assert "mixed_doubles" not in syn_a["ratings"]
    snap["resumable"] = resumable
    RatingEngine.from_snapshot(snap)


def test_case_1_documented_limit_still_loads(base: Snap) -> None:
    # A non-resumable snapshot does not record when a seed was set relative
    # to the logged matches, so an import dated before them, with an
    # external source name, cannot be told apart from a live import.
    _state(base, "syn-a", "doubles").update(
        initial_source="ext", initial_source_date="2025-01-01", verified_matches_played=2
    )
    _state(base, "syn-a", "mixed_doubles").update(
        initial_source="ext", initial_source_date="2025-01-01", verified_matches_played=1
    )
    base["resumable"] = False
    RatingEngine.from_snapshot(base)


# -- the engine-made fuzz reaches both rules


@pytest.fixture
def coverage(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, int]]:
    counts = {"frozen_category_names": 0, "resumable_played_dated": 0}
    original = fuzz._loads

    def spy(engine: RatingEngine, cfg: Config) -> int:
        snap = fuzz._json(engine)
        players = snap["players"]
        entries = snap["self_reported_log"]
        assert isinstance(players, list) and isinstance(entries, list)
        ratings = {player["id"]: player["ratings"] for player in players}
        for entry in entries:
            if RatingFormat(entry["rating_format"]).parent is None:
                continue
            for team in entry["teams"]:
                for pid in team:
                    if entry["rating_format"] not in ratings[pid]:
                        counts["frozen_category_names"] += 1
        if snap["resumable"]:
            for states in ratings.values():
                for state in states.values():
                    if state["matches_played"] and state["initial_source_date"] is not None:
                        counts["resumable_played_dated"] += 1
        return original(engine, cfg)

    monkeypatch.setattr(fuzz, "_loads", spy)
    yield counts


def test_fuzz_exercises_the_new_rules(coverage: dict[str, int]) -> None:
    for seed in range(5):
        fuzz._drive(Config(), seed)
    assert coverage["frozen_category_names"] > 0
    assert coverage["resumable_played_dated"] > 0
