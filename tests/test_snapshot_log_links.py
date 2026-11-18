"""Review round 1 of issue #102: two more snapshot rules the engine keeps.

In a resumable snapshot, every partner and opponent date of a played
state with an initial_source_date is after that date. And for a
self-reported log entry's latest date, each named player was frozen
there (a baseline on or after the date) or moved, with every other named
player recorded as a partner or opponent on or after it; and some named
player moved. All ids are synthetic.
"""

from __future__ import annotations

import json
import re
from datetime import date

import pytest

from open_engine import (
    ExternalRatingSnapshot,
    Match,
    MatchType,
    Player,
    RatingEngine,
    RatingFormat,
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
    return [
        _sr("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), date(2026, 1, 5), D),
        _sr("syn-m2", ("syn-a", "syn-e"), ("syn-f", "syn-g"), date(2026, 1, 6), MX),
        Match("syn-m3", ("syn-z",), ("syn-y",), ((11, 3),), date(2026, 1, 7), S),
    ]


def _snapshot(rows: list[Match], seeds: list[Player] | None = None) -> Snap:
    loaded: Snap = json.loads(json.dumps(replay(rows, seeds or [], as_of=AS_OF).to_snapshot()))
    return loaded


def _imported(pid: str, fmts: tuple[RatingFormat, ...], on: date) -> Player:
    seeder = RatingEngine()
    for fmt in fmts:
        seeder.seed_external_rating(pid, fmt, ExternalRatingSnapshot("ext", 4.0, on))
    return seeder.players[pid]


def _state(snap: Snap, pid: str, fmt: str) -> dict[str, object]:
    players = snap["players"]
    assert isinstance(players, list)
    for player in players:
        if player["id"] == pid:
            state: dict[str, object] = player["ratings"][fmt]
            return state
    raise AssertionError(pid)


def _bump(snap: Snap, pid: str, fmt: str, by: int) -> None:
    state = _state(snap, pid, fmt)
    state["verified_matches_played"] = int(str(state["verified_matches_played"])) + by


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


def _player_path(snap: Snap, pid: str) -> str:
    players = snap["players"]
    assert isinstance(players, list)
    index = next(i for i, player in enumerate(players) if player["id"] == pid)
    return re.escape(f"snapshot.players[{index}]")


def _link_message(snap: Snap, fmt: str, pid: str, other: str, role: str, on: str) -> str:
    index = _entry_index(snap, fmt)
    return re.escape(
        f"snapshot.self_reported_log[{index}].teams names {pid!r}, whose {fmt} state does "
        f"not record {other!r} as {role} on or after {on}"
    )


# -- R1: partner and opponent dates of a dated state, in a resumable snapshot


@pytest.mark.parametrize("mixed_source", ["ext", "open_parent:doubles"])
def test_dated_on_the_first_match_with_last_played_kept_does_not_load(mixed_source: str) -> None:
    snap = _snapshot(_rows())
    assert snap["resumable"] is True
    _state(snap, "syn-a", "doubles").update(
        initial_source="ext", initial_source_date="2026-01-05", verified_matches_played=1
    )
    _state(snap, "syn-a", "mixed_doubles").update(
        initial_source=mixed_source, initial_source_date="2026-01-05"
    )
    assert _state(snap, "syn-a", "doubles")["last_played"] == "2026-01-06"
    with pytest.raises(
        SnapshotError,
        match=_player_path(snap, "syn-a")
        + re.escape(
            ".ratings.doubles.partners['syn-b'] must be after initial_source_date in a "
            "resumable snapshot"
        ),
    ):
        RatingEngine.from_snapshot(snap)


# -- R2: the log entry's named players must match the states' links


def _relabel_into(snap: Snap, new: str) -> None:
    _bump(snap, "syn-a", "doubles", 1)
    _bump(snap, "syn-a", "mixed_doubles", 1)
    _swap_in_log(snap, "mixed_doubles", "syn-a", new)


@pytest.mark.parametrize("resumable", [True, False])
@pytest.mark.parametrize("fmts", [(MX,), (D,)], ids=["mixed-import", "doubles-import"])
def test_swap_to_a_player_imported_after_the_match_does_not_load(
    resumable: bool, fmts: tuple[RatingFormat, ...]
) -> None:
    snap = _snapshot(_rows(), [_imported("syn-z", fmts, date(2026, 1, 8))])
    snap["resumable"] = resumable
    _relabel_into(snap, "syn-z")
    with pytest.raises(
        SnapshotError,
        match=_link_message(snap, "mixed_doubles", "syn-e", "syn-z", "a partner", "2026-01-06"),
    ):
        RatingEngine.from_snapshot(snap)


def test_swap_to_a_player_who_played_the_format_later_does_not_load() -> None:
    rows = [
        *_rows(),
        Match("syn-m4", ("syn-z", "syn-h"), ("syn-i", "syn-j"), ((11, 4),), date(2026, 1, 8), MX),
    ]
    snap = _snapshot(rows)
    _relabel_into(snap, "syn-z")
    _bump(snap, "syn-z", "doubles", -1)
    _bump(snap, "syn-z", "mixed_doubles", -1)
    with pytest.raises(
        SnapshotError,
        match=_link_message(snap, "mixed_doubles", "syn-e", "syn-z", "a partner", "2026-01-06"),
    ):
        RatingEngine.from_snapshot(snap)


def test_swap_in_a_doubles_entry_to_an_unplayed_later_import_does_not_load() -> None:
    snap = _snapshot(_rows(), [_imported("syn-x", (D,), date(2026, 1, 8))])
    _swap_in_log(snap, "doubles", "syn-a", "syn-x")
    _bump(snap, "syn-a", "doubles", 1)
    with pytest.raises(
        SnapshotError,
        match=_link_message(snap, "doubles", "syn-b", "syn-x", "a partner", "2026-01-06"),
    ):
        RatingEngine.from_snapshot(snap)


def test_swapped_snapshot_cannot_be_loaded_and_then_reseeded() -> None:
    # Before the rule, this snapshot loaded, a reseed of syn-z succeeded
    # (no stored state named syn-z), and to_snapshot then raised.
    snap = _snapshot(_rows(), [_imported("syn-z", (D,), date(2026, 1, 8))])
    snap["resumable"] = False
    _relabel_into(snap, "syn-z")
    with pytest.raises(SnapshotError, match=r"names 'syn-e', whose mixed_doubles state"):
        RatingEngine.from_snapshot(snap)


def test_swap_to_a_named_opponent_link_missing_does_not_load() -> None:
    # Swap an opponent: syn-a's own links still name syn-f, so syn-a is
    # the first named player whose links miss the new name.
    snap = _snapshot(_rows(), [_imported("syn-w", (MX,), date(2026, 1, 8))])
    _swap_in_log(snap, "mixed_doubles", "syn-f", "syn-w")
    _bump(snap, "syn-f", "doubles", 1)
    _bump(snap, "syn-f", "mixed_doubles", 1)
    with pytest.raises(
        SnapshotError,
        match=_link_message(snap, "mixed_doubles", "syn-a", "syn-w", "an opponent", "2026-01-06"),
    ):
        RatingEngine.from_snapshot(snap)


def test_entry_whose_named_players_were_all_frozen_does_not_load() -> None:
    # A match that froze every player is not counted, so it is never logged.
    seeds = [_imported(pid, (MX,), date(2026, 1, 9)) for pid in ("syn-h", "syn-i", "syn-j")]
    seeds.append(_imported("syn-k", (MX,), date(2026, 1, 9)))
    snap = _snapshot(_rows(), seeds)
    snap["resumable"] = False
    entries = snap["self_reported_log"]
    assert isinstance(entries, list)
    entries.append(
        {
            "dates": ["2026-01-08"],
            "rating_format": "mixed_doubles",
            "teams": [["syn-h", "syn-i"], ["syn-j", "syn-k"]],
        }
    )
    with pytest.raises(
        SnapshotError,
        match=re.escape(
            f"snapshot.self_reported_log[{len(entries) - 1}].teams names no player who "
            "moved in the match on 2026-01-08"
        ),
    ):
        RatingEngine.from_snapshot(snap)
