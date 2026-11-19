"""An external seed never replaces a rating that carries OPEN history (issue #79).

seed_external_rating refuses to replace a state derived from overall
doubles (initial_source "open_parent:..."), and refuses to seed a format in
which the player was in a counted OPEN match, even one in which the player
was frozen. A refusal changes nothing, and live ratings still agree with a
replay of the same ledger and seeds. All ids are synthetic.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from helpers import derived_seed, store_state
from open_engine import (
    Config,
    ExternalRatingSnapshot,
    InvalidSeedError,
    Match,
    MatchType,
    OpenEngineError,
    Player,
    RatingEngine,
    RatingFormat,
    replay,
)
from open_engine.engine import clone_player

S = RatingFormat.SINGLES
D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
CAPTURED = date(2026, 3, 1)
EARLIER = date(2026, 1, 1)
AS_OF = date(2026, 4, 1)

OPEN_PARENT = r"^Cannot replace a rating derived from OPEN overall doubles$"
COUNTED = r"^Cannot seed a rating format in which OPEN matches have counted for this player$"


def _match(match_id: str, fmt: RatingFormat, played_on: date) -> Match:
    if fmt is S:
        return Match(match_id, ("syn-a",), ("syn-b",), ((11, 7),), played_on, fmt)
    return Match(
        match_id,
        ("syn-a", "syn-b"),
        ("syn-c", "syn-d"),
        ((11, 7),),
        played_on,
        fmt,
        match_type=MatchType.TOURNAMENT,
    )


def _seeds(fmt: RatingFormat) -> list[Player]:
    """syn-a seeded from an import in fmt, as a replay seed."""
    eng = RatingEngine()
    eng.seed_external_rating("syn-a", fmt, ExternalRatingSnapshot("ext", 4.0, CAPTURED))
    return [clone_player(eng.player("syn-a"))]


def _live(seeds: list[Player], ledger: list[Match]) -> RatingEngine:
    eng = RatingEngine()
    for player in seeds:
        eng.add_player(player)
    for match in ledger:
        eng.record(match)
    return eng


def _ratings(eng: RatingEngine) -> dict[tuple[str, str], tuple[object, ...]]:
    return {
        (pid, fmt.value): (
            state.rating,
            state.rd,
            state.matches_played,
            state.initial_source,
            state.initial_source_date,
        )
        for pid, player in eng.players.items()
        for fmt, state in player.ratings.items()
    }


def _assert_refused(
    eng: RatingEngine, fmt: RatingFormat, match: str, *, replace: bool = True
) -> None:
    before = eng.to_snapshot()
    with pytest.raises(InvalidSeedError, match=match) as info:
        eng.seed_external_rating(
            "syn-a",
            fmt,
            ExternalRatingSnapshot("ext", 2.5, EARLIER),
            replace_unplayed_seed=replace,
        )
    assert isinstance(info.value, OpenEngineError)
    assert eng.to_snapshot() == before


def test_a_state_derived_from_open_doubles_is_not_replaced() -> None:
    # The issue's repro: three counted doubles matches, then a derived
    # mixed doubles state, then an import for mixed doubles. Since issue
    # #112 the engine stores no such state, but one stored by an earlier
    # release (in a loaded snapshot) must still be refused.
    ledger = [_match(f"syn-m{i}", D, date(2026, 1, 5 + i)) for i in range(3)]
    eng = _live([], ledger)
    derived = store_state(eng, "syn-a", MX)
    assert derived.initial_source == "open_parent:doubles"
    assert derived.matches_played == 0
    _assert_refused(eng, MX, OPEN_PARENT)
    # Live still agrees with a replay of the same ledger.
    replayed = replay(ledger, as_of=AS_OF)
    live = _ratings(eng)
    del live[("syn-a", MX.value)]  # stored by store_state only
    assert live == _ratings(replayed)


def test_a_derived_state_from_an_imported_parent_is_not_replaced() -> None:
    # No OPEN matches at all: the parent is an unplayed import, and the
    # derived state, stored by a seed, still names its OPEN parent.
    eng = RatingEngine()
    eng.add_player(derived_seed("syn-a", ExternalRatingSnapshot("ext", 4.0, CAPTURED), MX))
    assert eng.players["syn-a"].ratings[MX].initial_source == "open_parent:doubles"
    _assert_refused(eng, MX, OPEN_PARENT)


@pytest.mark.parametrize("fmt", [S, D, MX])
def test_a_counted_match_in_which_the_player_was_frozen_blocks_a_reseed(
    fmt: RatingFormat,
) -> None:
    # The issue comment's repro: the match predates the import, so it
    # counts for the other players but syn-a is frozen and keeps
    # matches_played 0. For mixed doubles, syn-a has no stored state: the
    # derived starting state carries the doubles import date.
    seed_fmt = D if fmt is MX else fmt
    seeds = _seeds(seed_fmt)
    ledger = [_match("syn-m1", fmt, date(2026, 2, 1))]
    eng = _live(seeds, ledger)
    if fmt is MX:
        assert MX not in eng.player("syn-a").ratings
    else:
        assert eng.player("syn-a").ratings[fmt].matches_played == 0
    _assert_refused(eng, fmt, COUNTED)
    # Live agrees with a replay of the same ledger and the original seed.
    assert _ratings(eng) == _ratings(replay(ledger, seeds, as_of=AS_OF))


@pytest.mark.parametrize("fmt", [S, D])
def test_a_counted_frozen_match_blocks_a_seed_without_replace(fmt: RatingFormat) -> None:
    eng = _live(_seeds(fmt), [_match("syn-m1", fmt, date(2026, 2, 1))])
    _assert_refused(eng, fmt, COUNTED, replace=False)


def test_the_setup_freezes_syn_a_in_a_counted_match() -> None:
    # Guards the setup above: syn-a is frozen and the match counts.
    eng = RatingEngine()
    for player in _seeds(S):
        eng.add_player(player)
    result = eng.record(_match("syn-m1", S, date(2026, 2, 1)))
    assert result.counted
    assert result.frozen == ("syn-a",)


def test_a_category_with_no_stored_state_and_no_match_can_still_be_seeded() -> None:
    # The parent has OPEN matches, but the category has neither a stored
    # state nor a counted match: an import for it is direct evidence.
    eng = _live([], [_match(f"syn-m{i}", D, date(2026, 1, 5 + i)) for i in range(3)])
    state = eng.seed_external_rating("syn-a", MX, ExternalRatingSnapshot("ext", 2.5, EARLIER))
    assert state.initial_source == "ext"
    assert eng.state("syn-a", MX).rating == 2.5


def test_an_unplayed_seed_with_no_counted_match_can_still_be_replaced() -> None:
    # A counted match for other players in the format, without syn-a.
    eng = _live(_seeds(S), [])
    other = Match("syn-m1", ("syn-x",), ("syn-y",), ((11, 7),), date(2026, 2, 1), S)
    assert eng.record(other).counted
    state = eng.seed_external_rating(
        "syn-a", S, ExternalRatingSnapshot("ext", 2.5, EARLIER), replace_unplayed_seed=True
    )
    assert state.rating == 2.5
    assert state.initial_source_date == EARLIER


def test_a_snapshot_of_the_wrong_type_raises_an_engine_error() -> None:
    eng = RatingEngine()
    with pytest.raises(
        InvalidSeedError, match=r"^snapshot must be an ExternalRatingSnapshot$"
    ) as info:
        eng.seed_external_rating("syn-a", S, {"source": "ext", "rating": 4.0})  # type: ignore[arg-type]
    assert isinstance(info.value, OpenEngineError)
    assert eng.players == {}


def test_the_refusal_survives_a_snapshot_round_trip() -> None:
    # The frozen match leaves no trace on syn-a's own state; the check
    # reads the stored partners and opponents, which the snapshot keeps.
    eng = _live(_seeds(S), [_match("syn-m1", S, date(2026, 2, 1))])
    loaded = RatingEngine.from_snapshot(json.loads(json.dumps(eng.to_snapshot())))
    _assert_refused(loaded, S, COUNTED)


def test_a_frozen_doubles_update_of_a_category_match_blocks_a_doubles_reseed() -> None:
    # The mixed match counts, and so does its doubles update, in which
    # syn-a is frozen.
    seeds = _seeds(D)
    ledger = [_match("syn-m1", MX, date(2026, 2, 1))]
    eng = _live(seeds, ledger)
    assert eng.record(ledger[0]).reason is not None  # a repeat: nothing changes
    _assert_refused(eng, D, COUNTED)
    assert _ratings(eng) == _ratings(replay(ledger, seeds, as_of=AS_OF))


def _all_seeded_in_doubles_but_syn_b_in_mixed() -> list[Player]:
    eng = RatingEngine()
    eng.seed_external_rating("syn-b", MX, ExternalRatingSnapshot("ext", 4.0, EARLIER))
    for pid in ("syn-a", "syn-b", "syn-c", "syn-d"):
        eng.seed_external_rating(pid, D, ExternalRatingSnapshot("ext", 4.0, CAPTURED))
    return [clone_player(eng.player(pid)) for pid in sorted(eng.players)]


def test_a_doubles_seed_a_counted_category_match_was_rated_from_is_not_replaced() -> None:
    # Every doubles player is frozen, so the doubles update is rejected and
    # no doubles state names syn-a. The mixed match counts (syn-b moves),
    # and syn-a, with no mixed state, was frozen through the state derived
    # from the doubles seed. An earlier doubles date would unfreeze syn-a
    # in that match on replay.
    seeds = _all_seeded_in_doubles_but_syn_b_in_mixed()
    ledger = [_match("syn-m1", MX, date(2026, 2, 1))]
    eng = _live(seeds, [])
    result = eng.record(ledger[0])
    assert result.counted
    assert result.frozen == ("syn-a", "syn-c", "syn-d")
    assert result.parent is not None and not result.parent.counted
    _assert_refused(
        eng,
        D,
        r"^Cannot seed a rating that a counted mixed_doubles match for this "
        r"player depends on$",
    )
    assert _ratings(eng) == _ratings(replay(ledger, seeds, as_of=AS_OF))


def test_a_doubles_seed_is_refused_even_with_the_players_own_category_import() -> None:
    # syn-a has its own mixed import, but the match's doubles update was
    # rejected as before every imported doubles baseline. An earlier
    # doubles date for syn-a would make that update count on replay.
    seed_eng = RatingEngine()
    for player in _all_seeded_in_doubles_but_syn_b_in_mixed():
        seed_eng.add_player(player)
    seed_eng.seed_external_rating("syn-a", MX, ExternalRatingSnapshot("ext", 4.0, CAPTURED))
    seeds = [clone_player(seed_eng.player(pid)) for pid in sorted(seed_eng.players)]
    ledger = [_match("syn-m1", MX, date(2026, 2, 1))]
    eng = _live(seeds, [])
    result = eng.record(ledger[0])
    assert result.counted
    assert result.parent is not None and not result.parent.counted
    _assert_refused(
        eng,
        D,
        r"^Cannot seed a rating that a counted mixed_doubles match for this "
        r"player depends on$",
    )
    assert _ratings(eng) == _ratings(replay(ledger, seeds, as_of=AS_OF))


def test_a_fresh_player_can_be_seeded_in_every_format() -> None:
    for fmt in RatingFormat:
        eng = _live([], [_match(f"syn-m{i}", D, date(2026, 1, 5 + i)) for i in range(3)])
        state = eng.seed_external_rating(
            "syn-new", fmt, ExternalRatingSnapshot("ext", 3.0, EARLIER)
        )
        assert state.initial_source == "ext"


def test_a_doubles_seed_a_derived_category_state_was_rated_from_is_not_replaced() -> None:
    # Nobody is frozen. syn-a's mixed state is derived from its doubles
    # seed and moves in the mixed match; the doubles update is rejected by
    # the partner gap, so no doubles state names syn-a. A new doubles
    # rating would change syn-a's mixed starting rating on replay.
    cfg = Config(max_doubles_partner_gap=1.0)
    seed_eng = RatingEngine(cfg)
    snap = ExternalRatingSnapshot("ext", 4.0, EARLIER)
    seed_eng.seed_external_rating("syn-a", D, snap)
    seed_eng.seed_external_rating("syn-b", D, ExternalRatingSnapshot("ext", 2.0, EARLIER))
    for pid in ("syn-b", "syn-c", "syn-d"):
        seed_eng.seed_external_rating(pid, MX, snap)
    seeds = [clone_player(seed_eng.player(pid)) for pid in sorted(seed_eng.players)]
    ledger = [_match("syn-m1", MX, date(2026, 2, 1))]
    eng = RatingEngine(cfg)
    for player in seeds:
        eng.add_player(player)
    result = eng.record(ledger[0])
    assert result.counted and result.frozen == ()
    assert result.parent is not None and not result.parent.counted
    assert eng.state("syn-a", MX).initial_source == "open_parent:doubles"
    _assert_refused(
        eng,
        D,
        r"^Cannot seed a rating that a counted mixed_doubles match for this "
        r"player depends on$",
    )
    assert _ratings(eng) == _ratings(replay(ledger, seeds, cfg=cfg, as_of=AS_OF))


def test_a_stored_derived_category_state_does_not_free_the_doubles_seed() -> None:
    # syn-a is frozen in the mixed match through its derived state, which
    # an earlier release's seed_provisional_rating then stored.
    seeds = _all_seeded_in_doubles_but_syn_b_in_mixed()
    eng = _live(seeds, [_match("syn-m1", MX, date(2026, 2, 1))])
    store_state(eng, "syn-a", MX)
    assert MX in eng.players["syn-a"].ratings
    _assert_refused(
        eng,
        D,
        r"^Cannot seed a rating that a counted mixed_doubles match for this "
        r"player depends on$",
    )


@pytest.mark.parametrize("replace", [False, True])
def test_a_first_doubles_seed_after_a_provisional_category_match_is_refused(
    replace: bool,
) -> None:
    # syn-a has no doubles rating, so its mixed state starts provisional
    # and moves in the mixed match. The doubles update is rejected by the
    # partner gap, so no doubles state names syn-a. A doubles seed would
    # make a replay derive syn-a's mixed state from it instead.
    cfg = Config(max_doubles_partner_gap=1.0)
    seed_eng = RatingEngine(cfg)
    snap = ExternalRatingSnapshot("ext", 3.5, EARLIER)
    seed_eng.seed_external_rating("syn-b", D, ExternalRatingSnapshot("ext", 5.5, EARLIER))
    for pid in ("syn-b", "syn-c", "syn-d"):
        seed_eng.seed_external_rating(pid, MX, snap)
    if replace:
        # An unplayed provisional doubles state is not used to derive mixed.
        seed_eng.seed_provisional_rating("syn-a", D)
    seeds = [clone_player(seed_eng.player(pid)) for pid in sorted(seed_eng.players)]
    ledger = [_match("syn-m1", MX, date(2026, 2, 1))]
    eng = RatingEngine(cfg)
    for player in seeds:
        eng.add_player(player)
    result = eng.record(ledger[0])
    assert result.counted
    assert result.parent is not None and not result.parent.counted
    assert eng.state("syn-a", MX).initial_source == "open_provisional"
    _assert_refused(
        eng,
        D,
        r"^Cannot seed a rating that a counted mixed_doubles match for this "
        r"player depends on$",
        replace=replace,
    )
    assert _ratings(eng) == _ratings(replay(ledger, seeds, cfg=cfg, as_of=AS_OF))
