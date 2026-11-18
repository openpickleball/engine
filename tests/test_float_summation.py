"""Replay, evaluation, and win probability give the same floats on every Python version.

Python 3.12 changed the built-in sum() of floats to compensated summation, so
the same float sum could round differently on 3.11 (issue #40). The engine uses
math.fsum, which is correctly rounded on every version. These values are pinned
as float.hex, and CI runs this file on every supported Python version.
"""

from __future__ import annotations

from datetime import date, timedelta

from open_engine import (
    Config,
    EvaluationReport,
    GameFormat,
    Match,
    RatingFormat,
    evaluate,
    fit_scale,
    fit_win_calibration,
    game_win_probability,
    match_win_probability,
    replay,
)

D = RatingFormat.DOUBLES
PLAYERS = [f"p{i}" for i in range(8)]
AS_OF = date(2026, 3, 1)


def _ledger() -> list[Match]:
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
        matches.append(Match(f"m{i:02d}", team_a, team_b, games, played_on, D))
    return matches


# (rating, rd) as float.hex, after replaying the ledger at AS_OF.
RATINGS = {
    "p0": ("0x1.b4f9adde7c9b1p+1", "0x1.999999999999ap-4"),
    "p1": ("0x1.95b43375fedc3p+1", "0x1.f251f51b235bdp-4"),
    "p2": ("0x1.d30959287549fp+1", "0x1.999999999999ap-4"),
    "p3": ("0x1.a6ae614c57ae7p+1", "0x1.4fc6820642d4ep-3"),
    "p4": ("0x1.bf0f5c1eb2530p+1", "0x1.4ed4e93419b80p-3"),
    "p5": ("0x1.f15e44a3c9e0ap+1", "0x1.18d18f7d6c511p-3"),
    "p6": ("0x1.c220828b71f8bp+1", "0x1.5ae616638b609p-2"),
    "p7": ("0x1.dabc6dc0f7dbdp+1", "0x1.f1405cacab461p-4"),
}

# ScaleFit.profile: (scale, unrounded MSE as float.hex).
PROFILE = [
    (1.0, "0x1.7800e53c7cfd6p-4"),
    (1.5, "0x1.5c6414b79e195p-4"),
    (2.0, "0x1.4e3ae048083e5p-4"),
    (2.5, "0x1.45be2a061c3d3p-4"),
    (2.9, "0x1.412325cd481b0p-4"),
    (3.0, "0x1.4030ea554d241p-4"),
    (3.5, "0x1.3c592b2dea8aap-4"),
    (4.0, "0x1.3993677c5d418p-4"),
    (4.5, "0x1.378410da4bd1bp-4"),
    (5.0, "0x1.35f1f2197d197p-4"),
    (5.5, "0x1.34b98a4570347p-4"),
    (6.0, "0x1.33c2fd1051addp-4"),
    (6.5, "0x1.32fd8b0c394fbp-4"),
    (7.0, "0x1.325cd4f06a909p-4"),
    (7.5, "0x1.31d92a026b505p-4"),
    (7.690983, "0x1.31ad84ad24a7dp-4"),
    (7.809017, "0x1.3193ad6a0c1b3p-4"),
    (7.881966, "0x1.31845767c8ccdp-4"),
    (7.927051, "0x1.317ac36cbec40p-4"),
    (7.954915, "0x1.3175ad954e3ffp-4"),
    (7.972136, "0x1.3171a537ec2efp-4"),
    (7.982779, "0x1.316fdfa7dd3e8p-4"),
    (7.989357, "0x1.316ebe2c00a84p-4"),
    (7.993422, "0x1.316db8f4b0d6cp-4"),
    (7.995935, "0x1.316d752669079p-4"),
    (7.997, "0x1.316c965a26f66p-4"),
    (8.0, "0x1.316c5fa224032p-4"),
]


def test_replay_ratings_rds_and_info_are_pinned() -> None:
    engine = replay(_ledger(), as_of=AS_OF)
    got = {
        pid: (engine.state(pid, D).rating.hex(), engine.state(pid, D).rd.hex()) for pid in PLAYERS
    }
    assert got == RATINGS
    info = engine.replay_info
    assert info is not None
    assert (info.as_of, info.matches_considered, info.matches_counted) == (AS_OF, 60, 60)
    assert info.config_version == Config().version
    assert info.config_fingerprint == Config().fingerprint() == "c5bb49f7c37a013a"


def test_fit_scale_profile_is_pinned() -> None:
    fit = fit_scale(_ledger(), burn_in_matches=2)
    assert [(scale, mse.hex()) for scale, mse in fit.profile] == PROFILE


def test_fit_win_calibration_brier_is_pinned() -> None:
    fit = fit_win_calibration(_ledger(), burn_in_matches=2)
    assert fit.a == 0.2
    assert fit.brier.hex() == "0x1.0298d12261985p-2"
    assert fit.brier_uncalibrated.hex() == "0x1.321fa4bbcb508p-2"


def test_evaluate_report_is_pinned() -> None:
    report = evaluate(_ledger(), burn_in_matches=2)
    assert report == EvaluationReport(
        scoring=None,
        scale=None,
        matches_scored=53,
        mse=0.078403,
        mae=0.238269,
        bias=0.116014,
        calibration=(
            (0.3, 0.3928, 0.6162, 3),
            (0.4, 0.446, 0.6048, 26),
            (0.5, 0.542, 0.5993, 23),
            (0.6, 0.6118, 0.6471, 1),
        ),
    )


def test_win_probability_is_pinned() -> None:
    engine = replay(_ledger(), as_of=AS_OF)
    first_to_11 = GameFormat(win_by=1)
    prob = engine.predict_win_probability(("p0", "p1"), ("p2", "p3"), D, first_to_11)
    assert prob.hex() == "0x1.78744e0d6ee93p-2"
    assert game_win_probability(0.024, first_to_11).hex() == "0x1.e3d87cebfb89fp-42"


def test_win_by_2_and_best_of_5_sums_are_pinned() -> None:
    # The deuce game's direct term and a best of 5 add several floats each.
    assert game_win_probability(0.13436424411240122, GameFormat()).hex() == "0x1.00ae4904fb9a1p-16"
    assert match_win_probability(0.6, GameFormat(), 5).hex() == "0x1.eebb89f7d1eeap-1"
