"""
OPEN rating engine: provenance-aware, performance-based Elo for pickleball.

Status: demonstration only. These methods have not been validated on real
match data, the outputs are not OPEN ratings, and the API may change without
notice. Do not use this engine to rate real players.

This is a transparent, configurable rating engine. It is not a reproduction of
any proprietary rating algorithm. It supports ratings seeded from a verified
snapshot of a standard external rating system, independently maintained OPEN
ratings, unregistered player records, doubles categories that also feed the
overall doubles rating, match-source weighting, game format weighting,
inactivity-based uncertainty, rolling partner/opponent diversity, scale
calibration, and deterministic historical replay.

The code lives in private modules, in import order: _validation (type and
number predicates), _types (the enums and game formats), _config (Config),
_model (Match, Player, the rating state, and the result records), _ledger
(ledger order and the match ledger), _math (the rating math and reliability),
_connectivity (network connectivity), _seeds (seed validation), _snapshot (the
snapshot schema), _engine (RatingEngine, which records matches and takes and
loads snapshots), _replay (replay and resume from a snapshot), and _evaluation
(prequential_predictions, evaluate, fit_scale, and fit_win_calibration). This
module re-exports their public names, so `from open_engine.engine import X`
keeps working. Private names are not re-exported: import them from their
module.

docs/model.md states the rating model: each equation the engine uses, with the
function it comes from. docs/behavior.md holds the published behavior notes
(input types, errors, ledger order, duplicates, late matches, imbalance,
reliability display, snapshots, and the rest) and the requirements the backend
must meet. Many public docstrings name the section of docs/behavior.md that
holds their edge cases. Neither page ships with the package. They are
at https://github.com/openpickleball/engine/blob/main/docs/model.md and
https://github.com/openpickleball/engine/blob/main/docs/behavior.md, and each
docstring's "docs/behavior.md" means the second page.
"""

from __future__ import annotations

# Explicit re-exports, so `from open_engine.engine import X` passes mypy --strict.
from ._config import Config as Config
from ._engine import RatingEngine as RatingEngine
from ._errors import ConfigError as ConfigError
from ._errors import EvaluationError as EvaluationError
from ._errors import InvalidMatchError as InvalidMatchError
from ._errors import InvalidSeedError as InvalidSeedError
from ._errors import LedgerConflictError as LedgerConflictError
from ._errors import OpenEngineError as OpenEngineError
from ._errors import PlayerError as PlayerError
from ._errors import ReferenceDateError as ReferenceDateError
from ._errors import SnapshotError as SnapshotError
from ._errors import UnknownPlayerError as UnknownPlayerError
from ._evaluation import EvaluationReport as EvaluationReport
from ._evaluation import PredictionMode as PredictionMode
from ._evaluation import PrequentialPrediction as PrequentialPrediction
from ._evaluation import ScaleFit as ScaleFit
from ._evaluation import WinCalibrationFit as WinCalibrationFit
from ._evaluation import evaluate as evaluate
from ._evaluation import fit_scale as fit_scale
from ._evaluation import fit_win_calibration as fit_win_calibration
from ._evaluation import prequential_predictions as prequential_predictions
from ._ledger import MatchupKey as MatchupKey
from ._ledger import ledger_order as ledger_order
from ._math import clone_player as clone_player
from ._math import expected_point_share as expected_point_share
from ._math import inflated_rd as inflated_rd
from ._math import information_multiplier as information_multiplier
from ._math import player_k as player_k
from ._math import reliability as reliability
from ._model import ExternalRatingSnapshot as ExternalRatingSnapshot
from ._model import Match as Match
from ._model import MatchResult as MatchResult
from ._model import NetworkConnectivity as NetworkConnectivity
from ._model import Player as Player
from ._model import PlayerSummary as PlayerSummary
from ._model import RatingState as RatingState
from ._model import RejectionReason as RejectionReason
from ._model import ReplayInfo as ReplayInfo
from ._replay import replay as replay
from ._snapshot import SNAPSHOT_VERSION as SNAPSHOT_VERSION
from ._types import ALL_GAME_FORMATS as ALL_GAME_FORMATS
from ._types import GameFormat as GameFormat
from ._types import MatchStatus as MatchStatus
from ._types import MatchType as MatchType
from ._types import RatingFormat as RatingFormat
from ._types import ScoringType as ScoringType
from ._version import __version__ as __version__
from .winprob import calibrate_win_probability as calibrate_win_probability
from .winprob import match_win_probability as match_win_probability
