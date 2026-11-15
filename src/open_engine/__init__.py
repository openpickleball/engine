"""OPEN (Open Pickleball Evaluation Network) rating engine.

Demonstration only: not validated on real match data, outputs are not OPEN
ratings, and the API may change without notice. Do not use it to rate real
players.
"""

from .engine import (
    Config,
    EvaluationReport,
    ExternalRatingSnapshot,
    GameFormat,
    Match,
    MatchResult,
    MatchType,
    Player,
    PrequentialPrediction,
    RatingEngine,
    RatingFormat,
    RatingState,
    ReplayInfo,
    ScaleFit,
    ScoringType,
    WinCalibrationFit,
    clone_player,
    evaluate,
    expected_point_share,
    fit_scale,
    fit_win_calibration,
    inflated_rd,
    information_multiplier,
    ledger_order,
    player_k,
    prequential_predictions,
    recency_multiplier,
    reliability,
    replay,
)
from .winprob import calibrate_win_probability, game_win_probability, match_win_probability

__version__ = "0.2.0"

__all__ = [
    "Config",
    "EvaluationReport",
    "ExternalRatingSnapshot",
    "GameFormat",
    "Match",
    "MatchResult",
    "MatchType",
    "Player",
    "PrequentialPrediction",
    "RatingEngine",
    "RatingFormat",
    "RatingState",
    "ReplayInfo",
    "ScaleFit",
    "ScoringType",
    "WinCalibrationFit",
    "calibrate_win_probability",
    "clone_player",
    "evaluate",
    "expected_point_share",
    "fit_scale",
    "fit_win_calibration",
    "game_win_probability",
    "inflated_rd",
    "information_multiplier",
    "ledger_order",
    "match_win_probability",
    "player_k",
    "prequential_predictions",
    "recency_multiplier",
    "reliability",
    "replay",
]
