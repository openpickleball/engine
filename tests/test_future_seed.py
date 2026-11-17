"""A seed dated after the replay date, or after the ledger, is rejected."""

from __future__ import annotations

from datetime import date

import pytest

from open_engine import (
    ExternalRatingSnapshot,
    InvalidSeedError,
    Match,
    Player,
    RatingEngine,
    RatingFormat,
    prequential_predictions,
    replay,
)
from open_engine.engine import PredictionMode

D = RatingFormat.DOUBLES
MIXED = RatingFormat.MIXED_DOUBLES
AS_OF = date(2026, 11, 1)
FAR = date(9999, 1, 1)


def _imported(captured_on: date, fmt: RatingFormat = D, reliability: float = 100) -> Player:
    eng = RatingEngine()
    eng.seed_external_rating(
        "f", D, ExternalRatingSnapshot("external_standard", 4.0, captured_on, reliability)
    )
    if fmt is not D:
        eng.seed_provisional_rating("f", fmt)
        del eng.players["f"].ratings[D]
    return eng.players["f"]


@pytest.mark.parametrize("captured_on", [FAR, date(2026, 11, 2)])
@pytest.mark.parametrize("fmt", [D, MIXED])
def test_replay_rejects_seed_dated_after_as_of(captured_on: date, fmt: RatingFormat) -> None:
    with pytest.raises(
        InvalidSeedError, match=rf"Seed for f in {fmt}: initial_source_date must not be after as_of"
    ):
        replay([], [_imported(captured_on, fmt)], as_of=AS_OF)


def test_replay_accepts_seed_dated_on_as_of() -> None:
    engine = replay([], [_imported(AS_OF)], as_of=AS_OF)
    assert engine.players["f"].ratings[D].initial_source_date == AS_OF


LEDGER = [
    Match("m1", ("f", "b"), ("c", "d"), ((11, 7),), date(2026, 10, 1), D),
    Match("m2", ("f", "b"), ("c", "d"), ((9, 11),), date(2026, 10, 20), D),
]


@pytest.mark.parametrize("mode", ["live", "scheduled"])
@pytest.mark.parametrize("captured_on", [date(2026, 10, 21), FAR])
def test_prequential_rejects_seed_dated_after_the_ledger(
    mode: PredictionMode, captured_on: date
) -> None:
    with pytest.raises(
        InvalidSeedError,
        match="Seed for f in doubles: initial_source_date must not be after the last completed "
        "ledger match",
    ):
        prequential_predictions(LEDGER, [_imported(captured_on)], mode=mode)


@pytest.mark.parametrize("mode", ["live", "scheduled"])
def test_prequential_accepts_seed_dated_on_the_last_match(mode: PredictionMode) -> None:
    prequential_predictions(LEDGER, [_imported(date(2026, 10, 20))], mode=mode)


def test_scheduled_prequential_with_mid_ledger_seed_is_unchanged() -> None:
    # Values computed on origin/main (214674b), before seed date checks.
    eng = RatingEngine()
    snap = ExternalRatingSnapshot("external_standard", 4.4, date(2026, 10, 10), 80)
    eng.seed_external_rating("a", D, snap)
    for pid in "bcd":
        eng.seed_provisional_rating(pid, D)
    seeds = [eng.players[pid] for pid in "abcd"]
    ledger = [
        Match(f"s{i}", ("a", "b"), ("c", "d"), ((11, 5 + i),), date(2026, 10, 1 + 7 * i), D)
        for i in range(5)
    ]
    preds = prequential_predictions(ledger, seeds, mode="scheduled", burn_in_matches=0)
    assert [(p.match_id, p.expected) for p in preds] == [
        ("s0", 0.5883861232069059),
        ("s1", 0.601187965358943),
        ("s2", 0.6067501541461146),
        ("s3", 0.607354515377584),
        ("s4", 0.6036189453941041),
    ]


def test_future_import_is_not_carried_in_summary() -> None:
    eng = RatingEngine()
    eng.seed_external_rating("f", D, ExternalRatingSnapshot("external_standard", 4.0, FAR, 100))
    summary = eng.player_summary("f", D, as_of=AS_OF)
    assert summary.imported_reliability is None


def test_import_on_the_reference_date_is_carried() -> None:
    eng = RatingEngine()
    eng.seed_external_rating("f", D, ExternalRatingSnapshot("external_standard", 4.0, AS_OF, 100))
    assert eng.player_summary("f", D, as_of=AS_OF).imported_reliability == 100.0
