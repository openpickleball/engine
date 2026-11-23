"""Plain inputs give the same fingerprint, ratings, report, and fit as before #116.

The pinned values were measured on c23ab5a, before the exact input type
checks (issues #116, #118, #119). Issue #155 re-pinned the fingerprint and
SNAPSHOT_SHA when params_version joined the fingerprint payload and
snapshot_version became 9; the old pins were confirmed equal to the same
state without those changes. All ids are synthetic.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    GameFormat,
    Match,
    MatchType,
    Player,
    RatingEngine,
    RatingFormat,
    ScoringType,
    evaluate,
    fit_scale,
    replay,
)

D = RatingFormat.DOUBLES
MIXED = RatingFormat.MIXED_DOUBLES
S = RatingFormat.SINGLES
EAST = timezone(timedelta(hours=-5))
WEST = timezone(timedelta(hours=-8))
RALLY = GameFormat(ScoringType.RALLY, 15, 2)


def _seeds() -> list[Player]:
    eng = RatingEngine()
    eng.seed_external_rating(
        "syn-imp", D, ExternalRatingSnapshot("external_standard", 4.1, date(2026, 1, 2), 80)
    )
    return [copy.deepcopy(eng.players["syn-imp"])]


def _ledger() -> list[Match]:
    rows: list[Match] = []
    doubles = ["syn-imp", "syn-b", "syn-c", "syn-d"]
    for i in range(24):
        day = date(2026, 2, 1) + timedelta(days=i * 3)
        rot = doubles[i % 4 :] + doubles[: i % 4]
        team_a, team_b = (rot[0], rot[1]), (rot[2], rot[3])
        if i % 3 == 0:
            rows.append(
                Match(
                    f"syn-d{i}",
                    team_a,
                    team_b,
                    ((11, 7 + i % 3),) if i % 2 else ((8, 11),),
                    day,
                    D,
                    played_at=datetime(day.year, day.month, day.day, 10 + i % 5, tzinfo=EAST),
                )
            )
        elif i % 3 == 1:
            rows.append(
                Match(
                    f"syn-d{i}",
                    team_a,
                    team_b,
                    ((11, 9), (6, 11), (11, 4 + i % 4)),
                    day,
                    D,
                    best_of=3,
                    sequence=i % 4,
                    match_type=MatchType.TOURNAMENT,
                )
            )
        else:
            rows.append(
                Match(
                    f"syn-x{i}",
                    team_a,
                    team_b,
                    ((15, 10 + i % 3),) if i % 4 else ((12, 15),),
                    day,
                    MIXED,
                    game_format=RALLY,
                    played_at=datetime(day.year, day.month, day.day, 9, tzinfo=WEST),
                    sequence=1,
                )
            )
        rows.append(
            Match(
                f"syn-s{i}",
                ("syn-s1",),
                ("syn-s2",),
                ((11, 5 + i % 5),) if i % 3 else ((9, 11),),
                day,
                S,
                sequence=2,
            )
        )
    return rows


def _snapshot_sha() -> str:
    engine = replay(_ledger(), _seeds(), as_of=date(2026, 6, 1))
    text = json.dumps(engine.to_snapshot(), sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


def test_default_fingerprint_unchanged() -> None:
    assert Config().fingerprint() == "30dc01fa90e94c97"


def test_replay_snapshot_unchanged() -> None:
    assert _snapshot_sha() == SNAPSHOT_SHA


def test_evaluate_report_unchanged() -> None:
    report = evaluate(_ledger(), _seeds(), burn_in_matches=2)
    assert asdict(report) == EVALUATE_REPORT


def test_fit_scale_unchanged() -> None:
    fit = fit_scale(_ledger(), _seeds(), burn_in_matches=2, tolerance=0.05)
    assert asdict(fit) == FIT_SCALE


SNAPSHOT_SHA = "23b83d59f5db1ca169a307282a624dc4f330d7b43489ab4021d06526fd35d2cb"
EVALUATE_REPORT = {
    "scoring": None,
    "scale": None,
    "matches_scored": 42,
    "mse": 0.006789,
    "mae": 0.071175,
    "bias": 0.018455,
    "calibration": ((0.4, 0.4777, 0.5268, 9), (0.5, 0.5377, 0.5478, 33)),
}
FIT_SCALE = {
    "scoring": "side_out",
    "scale": 3.58,
    "default_scale": 2.9,
    "fitted": {
        "scoring": "side_out",
        "scale": 3.58,
        "matches_scored": 36,
        "mse": 0.007213,
        "mae": 0.073152,
        "bias": 0.022105,
        "calibration": ((0.4, 0.4797, 0.5185, 7), (0.5, 0.5363, 0.5544, 29)),
    },
    "at_default": {
        "scoring": "side_out",
        "scale": 2.9,
        "matches_scored": 36,
        "mse": 0.007232,
        "mae": 0.072976,
        "bias": 0.019407,
        "calibration": ((0.4, 0.4777, 0.5185, 7), (0.5, 0.5401, 0.5544, 29)),
    },
    "profile": (
        (1.0, 0.008104945175000001),
        (1.5, 0.007573864669444446),
        (2.0, 0.007370180594444446),
        (2.5, 0.007271545244444445),
        (2.9, 0.0072321648666666665),
        (3.0, 0.007226341780555554),
        (3.381966, 0.007214144680555556),
        (3.472136, 0.007213154005555557),
        (3.5, 0.007213032036111114),
        (3.527864, 0.007213070750000001),
        (3.562306, 0.0072129575861111135),
        (3.58, 0.0072129493166666675),
        (3.583592, 0.007212910033333335),
        (3.596748, 0.007212986144444445),
        (3.618034, 0.007213181233333334),
        (3.763932, 0.007214810519444444),
        (4.0, 0.007219831322222223),
        (4.5, 0.007239397794444445),
        (5.0, 0.007266785405555556),
        (5.5, 0.007299036611111113),
        (6.0, 0.007333898608333337),
        (6.5, 0.0073699461416666685),
        (7.0, 0.007406434225),
        (7.5, 0.007442722088888887),
        (8.0, 0.007478374805555555),
    ),
    "at_bound": False,
}
