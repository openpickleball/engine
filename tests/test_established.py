"""PlayerSummary.established and Config.established_reliability_threshold (issue #18)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from open_engine import (
    Config,
    ConfigError,
    ExternalRatingSnapshot,
    Match,
    MatchType,
    PlayerSummary,
    RatingEngine,
    RatingFormat,
    evaluate,
    replay,
)

DOUBLES = RatingFormat.DOUBLES
START = date(2026, 1, 5)


def weekly_matches(count: int) -> list[Match]:
    """Weekly league doubles wins 11-9 for p, each with a new partner and new opponents."""
    return [
        Match(
            match_id=f"m{i}",
            team_a=("p", f"q{i}"),
            team_b=(f"r{i}", f"s{i}"),
            games=((11, 9),),
            played_on=START + timedelta(weeks=i),
            rating_format=DOUBLES,
            match_type=MatchType.LEAGUE,
        )
        for i in range(count)
    ]


def last_date(matches: list[Match]) -> date:
    return matches[-1].played_on


def summary_after(count: int, cfg: Config | None = None) -> PlayerSummary:
    matches = weekly_matches(count)
    eng = RatingEngine(cfg)
    for match in matches:
        eng.record(match)
    return eng.player_summary("p", DOUBLES, as_of=last_date(matches))


# Criterion 1: default and fingerprint.


def test_default_threshold_is_60() -> None:
    assert Config().established_reliability_threshold == 60.0


@pytest.mark.parametrize("value", [0.0, 59.9, 100.0])
def test_threshold_is_in_fingerprint(value: float) -> None:
    assert Config(established_reliability_threshold=value).fingerprint() != Config().fingerprint()


# Criterion 2: validation.


@pytest.mark.parametrize("value", [-0.1, 100.1, float("nan")])
def test_threshold_out_of_range_raises(value: float) -> None:
    with pytest.raises(
        ConfigError, match="established_reliability_threshold must be between 0 and 100"
    ):
        Config(established_reliability_threshold=value)


@pytest.mark.parametrize("value", [0.0, 100.0])
def test_threshold_bounds_are_accepted(value: float) -> None:
    assert Config(established_reliability_threshold=value).established_reliability_threshold == (
        value
    )


# Criterion 3: established compares the displayed earned reliability.


def test_new_player_is_not_established() -> None:
    engine = RatingEngine()
    engine.seed_provisional_rating("z", DOUBLES)
    summary = engine.player_summary("z", DOUBLES, as_of=START)
    assert summary.reliability == 0.0
    assert summary.established is False


def test_ten_matches_not_established_eleven_established() -> None:
    ten = summary_after(10)
    assert ten.reliability == 59.3
    assert ten.established is False
    eleven = summary_after(11)
    assert eleven.reliability == 63.1
    assert eleven.established is True
    assert type(eleven.established) is bool


def test_threshold_boundary_uses_displayed_reliability() -> None:
    r = summary_after(11).reliability
    assert summary_after(11, Config(established_reliability_threshold=r)).established is True
    above = Config(established_reliability_threshold=r + 0.1)
    assert summary_after(11, above).established is False


def test_threshold_zero_and_hundred() -> None:
    zero = Config(established_reliability_threshold=0.0)
    engine = RatingEngine(zero)
    engine.seed_provisional_rating("z", DOUBLES)
    summary = engine.player_summary("z", DOUBLES, as_of=START)
    assert summary.reliability == 0.0
    assert summary.established is True
    hundred = Config(established_reliability_threshold=100.0)
    assert summary_after(11, hundred).established is False


# Criterion 4: imported reliability never counts.


def test_imported_reliability_does_not_count() -> None:
    eng = RatingEngine()
    captured = date(2026, 1, 1)
    eng.seed_external_rating("p", DOUBLES, ExternalRatingSnapshot("ext", 4.0, captured, 95.0))
    summary = eng.player_summary("p", DOUBLES, as_of=captured)
    assert summary.imported_reliability == 95.0
    assert summary.reliability < 60
    assert summary.established is False


# Criterion 5: same reference date as reliability.


@pytest.mark.parametrize(("days", "expected"), [(90, True), (120, False)])
def test_established_lapses_with_inactivity(days: int, expected: bool) -> None:
    matches = weekly_matches(11)
    eng = RatingEngine()
    for match in matches:
        eng.record(match)
    summary = eng.player_summary("p", DOUBLES, as_of=last_date(matches) + timedelta(days=days))
    assert summary.established is expected


# Criterion 6: the threshold changes no rating or evaluation output.


def test_threshold_changes_no_ratings_or_evaluation() -> None:
    matches = weekly_matches(11)
    as_of = last_date(matches)
    low = Config(established_reliability_threshold=0.0)
    high = Config(established_reliability_threshold=100.0)
    eng_low = replay(matches, cfg=low, as_of=as_of)
    eng_high = replay(matches, cfg=high, as_of=as_of)
    ids = sorted({pid for m in matches for pid in (*m.team_a, *m.team_b)})
    for pid in ids:
        a = eng_low.player_summary(pid, DOUBLES)
        b = eng_high.player_summary(pid, DOUBLES)
        assert (a.rating, a.rd, a.reliability, a.matches_played) == (
            b.rating,
            b.rd,
            b.reliability,
            b.matches_played,
        )
    report_low = evaluate(matches, cfg=low, burn_in_matches=0)
    report_high = evaluate(matches, cfg=high, burn_in_matches=0)
    assert report_low == report_high
