"""The scoring field of evaluation and fit results is the ScoringType member (issue #167).

EvaluationReport, ScaleFit, and WinCalibrationFit store the ScoringType they were
given, so outputs match inputs. ScoringType is a str enum, so comparing with the
str value, str(), f-strings, and json.dumps keep giving the value.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from open_engine import (
    GameFormat,
    Match,
    RatingFormat,
    ScoringType,
    evaluate,
    fit_scale,
    fit_win_calibration,
)

D = RatingFormat.DOUBLES
PLAYERS = [f"p{i}" for i in range(8)]


def _ledger(scoring: ScoringType) -> list[Match]:
    """60 synthetic doubles matches among 8 invented players, built without randomness."""
    matches = []
    for i in range(60):
        team_a = (PLAYERS[i % 8], PLAYERS[(i * 3 + 1) % 8])
        rest = [p for p in PLAYERS if p not in team_a]
        team_b = (rest[i % len(rest)], rest[(i * 5 + 2) % len(rest)])
        if team_b[0] == team_b[1]:
            team_b = (rest[0], rest[1]) if rest[0] != team_b[0] else (rest[0], rest[2])
        loser = (i * 7) % 10
        games = ((11, loser),) if (i * 11) % 3 else ((loser, 11),)
        played_on = date(2026, 1, 1) + timedelta(days=i // 2)
        matches.append(
            Match(f"m{i:02d}", team_a, team_b, games, played_on, D, game_format=GameFormat(scoring))
        )
    return matches


def _check(value: object, scoring: ScoringType) -> None:
    assert value is scoring
    assert type(value) is ScoringType
    assert value == scoring.value
    assert str(value) == scoring.value
    assert f"{value}" == scoring.value
    assert json.dumps(value) == json.dumps(scoring.value)


@pytest.mark.parametrize("scoring", list(ScoringType))
def test_evaluate_report_scoring_is_the_member(scoring: ScoringType) -> None:
    report = evaluate(_ledger(scoring), burn_in_matches=2, scoring=scoring)
    _check(report.scoring, scoring)


def test_evaluate_report_scoring_is_none_without_scoring() -> None:
    report = evaluate(_ledger(ScoringType.SIDE_OUT), burn_in_matches=2)
    assert report.scoring is None
    assert report.scale is None


@pytest.mark.parametrize("scoring", list(ScoringType))
def test_fit_scale_scoring_is_the_member(scoring: ScoringType) -> None:
    fit = fit_scale(_ledger(scoring), burn_in_matches=2, scoring=scoring, tolerance=0.5)
    _check(fit.scoring, scoring)
    _check(fit.fitted.scoring, scoring)
    _check(fit.at_default.scoring, scoring)


@pytest.mark.parametrize("scoring", list(ScoringType))
def test_fit_win_calibration_scoring_is_the_member(scoring: ScoringType) -> None:
    fit = fit_win_calibration(_ledger(scoring), burn_in_matches=2, scoring=scoring)
    _check(fit.scoring, scoring)
