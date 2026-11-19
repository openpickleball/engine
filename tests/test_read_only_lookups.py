"""Reading a player's rating changes nothing in the engine (issue #23).

player(), state(), and player_summary() raise UnknownPlayerError for an id
with no player record. For a known player with no state in a format, they
report the starting state that format's first match would use, without
storing it. Only the creating methods create. All ids are synthetic.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from datetime import date

import pytest

from open_engine import (
    ExternalRatingSnapshot,
    Match,
    OpenEngineError,
    Player,
    PlayerError,
    RatingEngine,
    RatingFormat,
    UnknownPlayerError,
    replay,
)

D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
S = RatingFormat.SINGLES
AS_OF = date(2025, 3, 1)
LEDGER = [
    Match("syn-1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 4),), date(2025, 2, 1), D),
]


def _engine() -> RatingEngine:
    """A replayed engine, so the snapshot starts resumable."""
    return replay(LEDGER, as_of=AS_OF)


def _snapshot(engine: RatingEngine) -> str:
    return json.dumps(engine.to_snapshot(), sort_keys=True)


# A known player in a format they have, in a category format derived from
# their played doubles rating, and in a format with no parent.
KNOWN_CASES: dict[str, tuple[str, RatingFormat]] = {
    "existing state": ("syn-a", D),
    "missing category state": ("syn-a", MX),
    "missing plain state": ("syn-a", S),
}

READS: dict[str, Callable[[RatingEngine, str, RatingFormat], object]] = {
    "player": lambda e, pid, fmt: e.player(pid),
    "state": lambda e, pid, fmt: e.state(pid, fmt),
    "player_summary": lambda e, pid, fmt: e.player_summary(pid, fmt, as_of=AS_OF),
    "player_summary without as_of": lambda e, pid, fmt: e.player_summary(pid, fmt),
    "predict_point_share": lambda e, pid, fmt: e.predict_point_share(
        (pid,) + ("syn-x",) * (fmt.team_size - 1),
        ("syn-y",) + ("syn-z",) * (fmt.team_size - 1),
        fmt,
    ),
    "to_snapshot": lambda e, pid, fmt: e.to_snapshot(),
}


@pytest.mark.parametrize("case", list(KNOWN_CASES.values()), ids=list(KNOWN_CASES))
@pytest.mark.parametrize("read", list(READS.values()), ids=list(READS))
def test_a_read_of_a_known_player_changes_nothing(
    read: Callable[[RatingEngine, str, RatingFormat], object],
    case: tuple[str, RatingFormat],
) -> None:
    engine = _engine()
    before = _snapshot(engine)
    players_before = list(engine.players)
    read(engine, *case)
    assert _snapshot(engine) == before
    assert list(engine.players) == players_before
    assert engine.to_snapshot()["resumable"] is True


UNKNOWN_READS: dict[str, Callable[[RatingEngine, str], object]] = {
    "player": lambda e, pid: e.player(pid),
    "state": lambda e, pid: e.state(pid, D),
    "player_summary": lambda e, pid: e.player_summary(pid, D, as_of=AS_OF),
    "player_summary without as_of": lambda e, pid: e.player_summary(pid, D),
}


@pytest.mark.parametrize("read", list(UNKNOWN_READS.values()), ids=list(UNKNOWN_READS))
def test_a_read_of_an_unknown_player_raises_and_changes_nothing(
    read: Callable[[RatingEngine, str], object],
) -> None:
    engine = _engine()
    before = _snapshot(engine)
    players_before = list(engine.players)
    with pytest.raises(UnknownPlayerError, match="Unknown player: syn-ghost"):
        read(engine, "syn-ghost")
    assert _snapshot(engine) == before
    assert list(engine.players) == players_before
    assert "syn-ghost" not in engine.players


def test_predictions_for_an_unknown_player_change_nothing() -> None:
    engine = _engine()
    before = _snapshot(engine)
    engine.predict_point_share(("syn-ghost",), ("syn-a",), S)
    assert _snapshot(engine) == before
    assert "syn-ghost" not in engine.players


def test_issue_repro_summary_of_an_unknown_player_on_an_empty_engine() -> None:
    engine = RatingEngine()
    with pytest.raises(UnknownPlayerError, match="Unknown player: ghost"):
        engine.player_summary("ghost", D, as_of=date(2026, 1, 1))
    assert engine.players == {}


def test_unknown_player_error_is_a_player_error() -> None:
    assert issubclass(UnknownPlayerError, PlayerError)
    assert issubclass(UnknownPlayerError, OpenEngineError)
    assert issubclass(UnknownPlayerError, ValueError)


@pytest.mark.parametrize("player_id", ["", "   "], ids=["empty", "whitespace"])
def test_a_blank_id_raises_player_error_not_unknown_player_error(player_id: str) -> None:
    engine = _engine()
    with pytest.raises(PlayerError, match="player_id is required") as info:
        engine.player(player_id)
    assert not isinstance(info.value, UnknownPlayerError)


@pytest.mark.parametrize("fmt", [MX, S], ids=["category with played parent", "no parent"])
def test_a_missing_format_reports_the_state_a_seed_would_create(fmt: RatingFormat) -> None:
    engine = _engine()
    seeded = copy.deepcopy(engine)
    returned = seeded.seed_provisional_rating("syn-a", fmt)

    assert engine.state("syn-a", fmt) == returned
    assert engine.player_summary("syn-a", fmt, as_of=AS_OF) == seeded.player_summary(
        "syn-a", fmt, as_of=AS_OF
    )
    assert fmt not in engine.players["syn-a"].ratings
    # seed_provisional_rating stores no category starting state (issue #112).
    assert (fmt in seeded.players["syn-a"].ratings) == (fmt.parent is None)
    if fmt.parent is None:
        assert seeded.players["syn-a"].ratings[fmt] is returned


def test_a_missing_category_state_derives_from_the_played_parent() -> None:
    engine = _engine()
    summary = engine.player_summary("syn-a", MX, as_of=AS_OF)
    assert summary.initial_source == "open_parent:doubles"
    assert summary.rating == round(engine.players["syn-a"].ratings[D].rating, 3)
    assert MX not in engine.players["syn-a"].ratings


def test_mutating_a_missing_format_state_changes_nothing() -> None:
    engine = _engine()
    before = _snapshot(engine)
    state = engine.state("syn-a", MX)
    state.rating += 1.0
    state.matches_played += 3
    assert _snapshot(engine) == before
    assert engine.state("syn-a", MX).matches_played == 0


def _seed_external(engine: RatingEngine) -> None:
    engine.seed_external_rating(
        "syn-n", D, ExternalRatingSnapshot("external_standard", 4.4, date(2025, 2, 1))
    )


CREATES: dict[str, Callable[[RatingEngine], object]] = {
    "seed_provisional_rating": lambda e: e.seed_provisional_rating("syn-n", D),
    "seed_external_rating": _seed_external,
    "link_account": lambda e: e.link_account("syn-n", "acct-n"),
    "attach_external_id": lambda e: e.attach_external_id("syn-n", "external_standard", "ext-n"),
    "add_player": lambda e: e.add_player(Player("syn-n")),
    "record": lambda e: e.record(Match("syn-9", ("syn-n",), ("syn-m",), ((11, 3),), AS_OF, S)),
}


@pytest.mark.parametrize("create", list(CREATES.values()), ids=list(CREATES))
def test_creating_methods_still_create_a_new_player(
    create: Callable[[RatingEngine], object],
) -> None:
    engine = _engine()
    create(engine)
    assert "syn-n" in engine.players
    assert engine.player("syn-n").id == "syn-n"
    assert engine.to_snapshot()["resumable"] is False
