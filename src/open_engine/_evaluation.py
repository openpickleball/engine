"""
Prequential evaluation and calibration: evaluate, fit_scale, fit_win_calibration.

Demonstration only: not validated on real match data, outputs are not OPEN
ratings, and the API may change without notice.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, fields
from datetime import date
from typing import Literal

from ._config import Config, _config_or_default, _config_shape
from ._engine import RatingEngine
from ._errors import EvaluationError
from ._ledger import _ledger
from ._math import _RESULT_PLACES
from ._model import Match, Player, _validate_match
from ._replay import _engine_under, _replay
from ._seeds import _check_seed_date
from ._types import MatchStatus, ScoringType, _enum_shown, _is_member, _safe_shown
from ._validation import _days_before, _is_exact_int, _is_exact_number, _is_plain_date, _type_name
from .winprob import calibrate_win_probability, match_win_probability

# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationReport:
    """Point-share error and calibration bins for a set of prequential predictions."""

    scoring: str | None
    scale: float | None
    matches_scored: int
    mse: float
    mae: float
    bias: float  # mean(actual - expected); near zero when well calibrated
    calibration: tuple[tuple[float, float, float, int], ...]
    # Each bin: (expected lower bound, mean expected, mean actual, count)


@dataclass(frozen=True)
class PrequentialPrediction:
    """One match predicted from ratings that had not yet seen it."""

    match_id: str
    played_on: date
    players: tuple[str, ...]
    expected: float  # team A's expected point share
    actual: float  # raw points_a / total, not win_weight-adjusted
    team_a_won: bool
    # Win probabilities are modeled only for win_by 1 or 2. For any other
    # win_by both are math.nan; the point-share fields are unaffected.
    win_probability_raw: float  # before calibration
    win_probability: float  # calibrated with cfg


PredictionMode = Literal["live", "scheduled"]


def _is_established(engine: RatingEngine, match: Match, burn_in_matches: int) -> bool:
    return all(
        (s := engine._peek_state(pid, match.rating_format)).initial_source_date is not None
        or s.matches_played >= burn_in_matches
        for pid in match.team_a + match.team_b
    )


def _predict_match(engine: RatingEngine, match: Match) -> PrequentialPrediction:
    cfg = engine.cfg
    scoring = match.game_format.scoring
    expected = engine.predict_point_share(match.team_a, match.team_b, match.rating_format, scoring)
    if match.game_format.win_by in (1, 2):
        raw = match_win_probability(expected, match.game_format, match.best_of)
        calibrated = calibrate_win_probability(raw, cfg.win_calibration_by_scoring[scoring])
    else:
        raw = calibrated = math.nan
    points_a = sum(a for a, _ in match.games)
    total = sum(a + b for a, b in match.games)
    games_a = sum(a > b for a, b in match.games)
    return PrequentialPrediction(
        match_id=match.match_id,
        played_on=match.played_on,
        players=match.team_a + match.team_b,
        expected=expected,
        actual=points_a / total,
        team_a_won=games_a > len(match.games) - games_a,
        win_probability_raw=raw,
        win_probability=calibrated,
    )


def _check_evaluation_arguments(
    cfg: object,
    scoring: object,
    burn_in_matches: object,
    mode: object,
    score_from: object,
    anchor: object,
) -> Config | None:
    """
    The checked Config for the evaluation functions; raise for a bad argument.

    In order: score_from and anchor (None or exactly a date), mode (exactly
    the str "live" or "scheduled"), scoring (None or exactly a
    ScoringType), and burn_in_matches (exactly an int) raise
    EvaluationError, then cfg (None or exactly a Config with only its
    fields) raises ConfigError. Returns None for None, else cfg itself, not
    yet rebuilt: the caller drains its iterables first, then builds the
    copy with _config_or_default (issue #122).
    """
    for name, value in (("score_from", score_from), ("anchor", anchor)):
        if value is not None and not _is_plain_date(value):
            raise EvaluationError(f"{name} must be a plain date, not {_type_name(value)}")
    if type(mode) is not str or mode not in ("live", "scheduled"):
        raise EvaluationError(f"mode must be 'live' or 'scheduled', not {_safe_shown(mode)}")
    if scoring is not None and not _is_member(scoring, ScoringType):
        raise EvaluationError(
            f"scoring must be a ScoringType or None, not {_enum_shown(scoring, ScoringType)}"
        )
    if not _is_exact_int(burn_in_matches):
        raise EvaluationError(f"burn_in_matches must be an int, not {_type_name(burn_in_matches)}")
    return _config_shape(cfg)


def prequential_predictions(
    matches: Iterable[Match],
    seed_players: Iterable[Player] = (),
    *,
    cfg: Config | None = None,
    scoring: ScoringType | None = None,
    burn_in_matches: int = 5,
    mode: PredictionMode = "live",
    score_from: date | None = None,
    anchor: date | None = None,
) -> list[PrequentialPrediction]:
    """
    Predict each eligible match from ratings that have not seen it.

    Only matches that `record` counts are kept, and a match that is not
    completed is never predicted.

    Args:
        matches: The ledger rows, any iterable, read once into a list.
        seed_players: Seeds that follow the seed rules; any iterable.
        cfg: None or exactly a Config with only its fields.
        scoring: None or exactly a ScoringType: the scoring to predict.
        burn_in_matches: Exactly an int: the OPEN matches a participant with
            no imported baseline needs before a match is predicted.
        mode: Exactly "live" or "scheduled".
        score_from: None or a plain date: predict only matches on or after it.
        anchor: None or a plain date: in "scheduled" mode, replay dates are
            anchor + k * cfg.replay_interval_days.

    Returns:
        A list of PrequentialPrediction, one per eligible match that `record`
        counts.

    Raises:
        EvaluationError: score_from, anchor, mode, scoring, or burn_in_matches
            is not valid. These are checked before the ledger is read.
        ConfigError: cfg is not valid.
        InvalidMatchError, LedgerConflictError: as replay raises them for rows.
        InvalidSeedError, PlayerError: a seed breaks the seed or identity
            rules, or is dated after the last completed ledger match.

    See docs/behavior.md, "Prequential predictions".
    """
    shape = _check_evaluation_arguments(cfg, scoring, burn_in_matches, mode, score_from, anchor)
    rows = list(matches)
    seeds = list(seed_players)
    return _prequential(
        rows,
        seeds,
        cfg=_config_or_default(shape),
        scoring=scoring,
        burn_in_matches=burn_in_matches,
        mode=mode,
        score_from=score_from,
        anchor=anchor,
    )


def _prequential(
    matches: Iterable[Match],
    seed_players: Iterable[Player],
    *,
    cfg: Config,
    scoring: ScoringType | None,
    burn_in_matches: int,
    mode: PredictionMode,
    score_from: date | None,
    anchor: date | None,
) -> list[PrequentialPrediction]:
    """
    prequential_predictions after its argument checks.

    cfg is a checked, rebuilt Config (or a fit_scale trial built with
    Config(...)), used as it is: the scratch, live, and window engines do
    not rebuild it again.
    """
    # Read each caller iterable once, before any row or seed is checked, so
    # a generator cannot change a row between its check and its use.
    rows = list(matches)
    seeds = list(seed_players)
    ordered = _ledger(rows)
    # Non-completed rows move no rating: they stay in the replays and
    # windows, but never set the seed date limit or the default anchor.
    completed = [m for m in ordered if m.status is MatchStatus.COMPLETED]
    # add_player on a scratch engine applies every seed check, against the
    # earlier seeds too, in the same order and with the same messages, so
    # both modes reject the same seeds before any rating work.
    scratch = _engine_under(cfg)
    held = scratch._held_identities()
    for player in seeds:
        scratch._add_seed(player, held)
        if completed:
            _check_seed_date(player, completed[-1].played_on, "the last completed ledger match")
    predictions: list[PrequentialPrediction] = []

    def step(engine: RatingEngine, match: Match) -> None:
        _validate_match(match)
        eligible = (
            (scoring is None or match.game_format.scoring is scoring)
            and (score_from is None or match.played_on >= score_from)
            and match.status is MatchStatus.COMPLETED
            and _is_established(engine, match, burn_in_matches)
        )
        prediction = _predict_match(engine, match) if eligible else None
        if engine.record(match).counted and prediction is not None:
            predictions.append(prediction)

    if mode == "live":
        engine = _engine_under(cfg)
        held = engine._held_identities()
        for player in seeds:
            engine._add_seed(player, held)
        for match in ordered:
            step(engine, match)
        return predictions

    if not completed:
        return predictions
    interval = cfg.replay_interval_days
    if anchor is None:
        anchor = completed[0].played_on
    # Each match goes in window k, the largest k with anchor + k * interval
    # strictly before played_on, and is predicted from a replay to that grid
    # date. The matches dated on or before it are exactly those in earlier
    # windows. Windows are keyed by k, not by date, because the first grid
    # date can fall before date.min (issue #92). Only that one can, and no
    # match is before it, so its replay date is clamped at date.min without
    # adding a match dated date.min to the replay that predicts it.
    window_of = {m.match_id: ((m.played_on - anchor).days - 1) // interval for m in ordered}
    windows: dict[int, list[Match]] = {}
    for match in ordered:
        windows.setdefault(window_of[match.match_id], []).append(match)

    for k, window in sorted(windows.items()):
        if score_from is not None and all(m.played_on < score_from for m in window):
            continue
        replay_date = _days_before(anchor, -k * interval)
        engine = _replay(
            [m for m in ordered if window_of[m.match_id] < k],
            seeds,
            cfg=cfg,
            as_of=replay_date,
            check_seed_dates=False,
            track=False,
        )
        for match in window:
            step(engine, match)
    return predictions


def evaluate(
    matches: Iterable[Match],
    seed_players: Iterable[Player] = (),
    *,
    cfg: Config | None = None,
    scoring: ScoringType | None = None,
    burn_in_matches: int = 5,
    mode: PredictionMode = "live",
    score_from: date | None = None,
    anchor: date | None = None,
) -> EvaluationReport:
    """
    Prequential evaluation: each match is predicted before it updates ratings.

    Scores the matches that prequential_predictions() returns, with the same
    eligibility, mode, score_from and anchor rules, and checks every
    argument as prequential_predictions does, in the same order, for an
    empty ledger too.

    Args:
        matches, seed_players, cfg, scoring, burn_in_matches, mode,
            score_from, anchor: As for prequential_predictions.

    Returns:
        An EvaluationReport.

    Raises:
        OpenEngineError: the subclass prequential_predictions raises for the
            same arguments, in the same order.
        EvaluationError: no established match remains to evaluate.
    """
    shape = _check_evaluation_arguments(cfg, scoring, burn_in_matches, mode, score_from, anchor)
    rows = list(matches)
    seeds = list(seed_players)
    checked = _config_or_default(shape)
    predictions = _prequential(
        rows,
        seeds,
        cfg=checked,
        scoring=scoring,
        burn_in_matches=burn_in_matches,
        mode=mode,
        score_from=score_from,
        anchor=anchor,
    )
    return _report_from_predictions(predictions, checked, scoring)[0]


def _scored_pairs(
    predictions: list[PrequentialPrediction], cfg: Config
) -> list[tuple[float, float]]:
    # Same target and precision as MatchResult.expected_a/actual_a.
    w = cfg.win_weight
    return [
        (
            round(p.expected, _RESULT_PLACES),
            round((1.0 - w) * p.actual + w * float(p.team_a_won), _RESULT_PLACES),
        )
        for p in predictions
    ]


def _report_from_predictions(
    predictions: list[PrequentialPrediction], cfg: Config, scoring: ScoringType | None
) -> tuple[EvaluationReport, float]:
    """The rounded report plus the unrounded mean squared error."""
    pairs = _scored_pairs(predictions, cfg)
    if not pairs:
        raise EvaluationError("No established matches to evaluate")
    errors = [actual - expected for expected, actual in pairs]
    bins: list[tuple[float, float, float, int]] = []
    for index in range(10):
        # Both edges are k / 10, so one bin's upper edge is the same float as
        # the next bin's lower edge and every pair lands in exactly one bin.
        lower = index / 10
        upper = (index + 1) / 10
        members = [(e, a) for e, a in pairs if lower <= e < upper or (index == 9 and e == 1.0)]
        if members:
            bins.append(
                (
                    lower,
                    round(math.fsum(e for e, _ in members) / len(members), 4),
                    round(math.fsum(a for _, a in members) / len(members), 4),
                    len(members),
                )
            )
    mse = math.fsum(e * e for e in errors) / len(errors)
    report = EvaluationReport(
        scoring=scoring.value if scoring else None,
        scale=cfg.scale_by_scoring[scoring] if scoring else None,
        matches_scored=len(pairs),
        mse=round(mse, 6),
        mae=round(math.fsum(abs(e) for e in errors) / len(errors), 6),
        bias=round(math.fsum(errors) / len(errors), 6),
        calibration=tuple(bins),
    )
    return report, mse


_SEARCH_STEPS = 14  # coarse grid intervals in fit_scale and fit_win_calibration


def _check_fit_scoring(scoring: object) -> None:
    """Raise EvaluationError unless scoring is exactly a ScoringType (a fit needs one)."""
    if not _is_member(scoring, ScoringType):
        raise EvaluationError(
            f"scoring must be a ScoringType, not {_enum_shown(scoring, ScoringType)}"
        )


def _check_search_bounds(low: float, high: float, tolerance: float) -> None:
    """
    Raise EvaluationError for a search range or tolerance the fits cannot use.

    low, high, and tolerance must each be exactly an int or a float (not a
    bool or a subclass), checked before any range rule.
    """
    for name, value in (("low", low), ("high", high), ("tolerance", tolerance)):
        if not _is_exact_number(value):
            raise EvaluationError(f"{name} must be an int or float, not {_type_name(value)}")
        try:
            float(value)
        except OverflowError:
            raise EvaluationError(f"{name} must be finite as a float") from None
    if not (math.isfinite(low) and math.isfinite(high)):
        raise EvaluationError("low and high must be finite")
    # The search adds two points of the range (midpoints) and multiplies the
    # width by the grid step count before dividing. Both must stay finite.
    if not (math.isfinite(2 * low) and math.isfinite(2 * high)):
        raise EvaluationError("2 * low and 2 * high must be finite")
    if not math.isfinite((high - low) * _SEARCH_STEPS):
        raise EvaluationError(f"(high - low) * {_SEARCH_STEPS} must be finite")
    if not low < high:
        raise EvaluationError("low must be less than high")
    if not (math.isfinite(tolerance) and tolerance > 0):
        raise EvaluationError("tolerance must be finite and greater than 0")


@dataclass(frozen=True)
class ScaleFit:
    """
    Result of fit_scale for one scoring type.

    A fit with at_bound set, or with a flat profile, is not identified.
    """

    scoring: str
    scale: float
    default_scale: float
    fitted: EvaluationReport
    at_default: EvaluationReport
    profile: tuple[tuple[float, float], ...] = ()  # (scale, unrounded mse), sorted by scale
    at_bound: bool = False  # fitted scale within tolerance of low or high


def fit_scale(
    matches: Iterable[Match],
    seed_players: Iterable[Player] = (),
    *,
    cfg: Config | None = None,
    scoring: ScoringType = ScoringType.SIDE_OUT,
    low: float = 1.0,
    high: float = 8.0,
    tolerance: float = 0.01,
    burn_in_matches: int = 5,
    mode: PredictionMode = "live",
    score_from: date | None = None,
    anchor: date | None = None,
) -> ScaleFit:
    """
    Fit one scoring type's scale by minimizing prequential point-share MSE.

    Coarse grid first, then golden-section search around the best grid point.
    The search compares unrounded MSE, so ties at the 6-decimal precision of
    EvaluationReport.mse do not decide the fit.

    Args:
        matches, seed_players, cfg, burn_in_matches, mode, score_from,
            anchor: As for prequential_predictions.
        scoring: Exactly a ScoringType (None is rejected).
        low, high: The search range, each exactly an int or a float.
        tolerance: Exactly an int or a float, finite and greater than 0.

    Returns:
        A ScaleFit. Publish the returned ScaleFit alongside the config that
        adopts it.

    Raises:
        EvaluationError: low, high, or tolerance is not valid (checked first,
            before any prediction work), another argument fails a check
            prequential_predictions makes, scoring is None (checked after cfg's
            type and before cfg is rebuilt), or no established match remains.
        ConfigError, InvalidMatchError, InvalidSeedError, PlayerError,
            LedgerConflictError: as prequential_predictions raises them, in its
            order. The first trial config, at scale low, is built after the cfg
            rebuild and before any row or seed is checked; Config's check that
            scales are positive raises ConfigError there for a low of 0 or less.

    See docs/behavior.md, "Calibration fits".
    """
    _check_search_bounds(low, high, tolerance)
    shape = _check_evaluation_arguments(cfg, scoring, burn_in_matches, mode, score_from, anchor)
    _check_fit_scoring(scoring)
    ledger = list(matches)
    seeds = list(seed_players)
    # Built after matches and seed_players are read (issue #122).
    checked = _config_or_default(shape)
    cache: dict[float, tuple[EvaluationReport, float]] = {}
    # The trial configs are built from the fields of Config itself, read off
    # the checked exact Config, never through dataclasses.replace, which
    # reads the instance's own __dataclass_fields__ and __class__.
    base = {f.name: getattr(checked, f.name) for f in fields(Config)}

    def run(scale: float) -> tuple[EvaluationReport, float]:
        key = round(scale, 6)
        if key not in cache:
            trial = Config(
                **{**base, "scale_by_scoring": {**checked.scale_by_scoring, scoring: scale}}
            )
            predictions = _prequential(
                ledger,
                seeds,
                cfg=trial,
                scoring=scoring,
                burn_in_matches=burn_in_matches,
                mode=mode,
                score_from=score_from,
                anchor=anchor,
            )
            cache[key] = _report_from_predictions(predictions, trial, scoring)
        return cache[key]

    steps = _SEARCH_STEPS
    grid = [low + (high - low) * i / steps for i in range(steps + 1)]
    best = min(grid, key=lambda s: run(s)[1])
    step = (high - low) / steps
    a, b = max(low, best - step), min(high, best + step)

    ratio = (math.sqrt(5) - 1) / 2
    c, d = b - ratio * (b - a), a + ratio * (b - a)
    while b - a > tolerance:
        width = b - a
        if run(c)[1] <= run(d)[1]:
            b, d = d, c
            c = b - ratio * (b - a)
        else:
            a, c = c, d
            d = a + ratio * (b - a)
        if b - a >= width:
            break  # the bracket hit float spacing above a tiny tolerance
    fitted_scale = round((a + b) / 2, 3)

    default_scale = checked.scale_by_scoring[scoring]
    fitted = run(fitted_scale)[0]
    at_default = run(default_scale)[0]
    return ScaleFit(
        scoring=scoring.value,
        scale=fitted_scale,
        default_scale=default_scale,
        fitted=fitted,
        at_default=at_default,
        profile=tuple((float(scale), mse) for scale, (_, mse) in sorted(cache.items())),
        at_bound=fitted_scale - low <= tolerance or high - fitted_scale <= tolerance,
    )


@dataclass(frozen=True)
class WinCalibrationFit:
    """
    Result of fit_win_calibration for one scoring type.

    A fit with at_bound set is not identified.
    """

    scoring: str
    a: float  # exponent on the logit of the raw win probability, rounded to 3 places
    brier: float  # at the fitted a
    brier_uncalibrated: float  # at a = 1
    n: int  # predictions used (those with a modeled win probability)
    at_bound: bool = False  # fitted a within tolerance of low or high


def fit_win_calibration(
    matches: Iterable[Match],
    seed_players: Iterable[Player] = (),
    *,
    cfg: Config | None = None,
    scoring: ScoringType = ScoringType.SIDE_OUT,
    low: float = 0.2,
    high: float = 3.0,
    tolerance: float = 0.001,
    burn_in_matches: int = 5,
    mode: PredictionMode = "live",
    score_from: date | None = None,
    anchor: date | None = None,
) -> WinCalibrationFit:
    """
    Fit one scoring type's win-probability exponent by minimizing the Brier score.

    Uses the raw win probabilities computed with cfg's scale, so fit the scale
    first. Predictions without a modeled win probability (win_by not 1 or 2)
    are skipped. Same search as fit_scale: coarse grid, then golden section.

    Args:
        matches, seed_players, cfg, burn_in_matches, mode, score_from,
            anchor: As for prequential_predictions.
        scoring: Exactly a ScoringType, as for fit_scale.
        low, high, tolerance: The search range and tolerance, as for fit_scale.

    Returns:
        A WinCalibrationFit.

    Raises:
        EvaluationError: low, high, or tolerance is not valid, as for
            fit_scale; another argument fails a check prequential_predictions
            makes; scoring is None (checked after cfg's type and before cfg is
            rebuilt); or no prediction remains.
        ConfigError, InvalidMatchError, InvalidSeedError, PlayerError,
            LedgerConflictError: as prequential_predictions raises them, in its
            order. No trial config is built, so a low of 0 or less raises no
            ConfigError.

    See docs/behavior.md, "Calibration fits".
    """
    _check_search_bounds(low, high, tolerance)
    shape = _check_evaluation_arguments(cfg, scoring, burn_in_matches, mode, score_from, anchor)
    _check_fit_scoring(scoring)
    rows = list(matches)
    seeds = list(seed_players)
    predictions = _prequential(
        rows,
        seeds,
        cfg=_config_or_default(shape),
        scoring=scoring,
        burn_in_matches=burn_in_matches,
        mode=mode,
        score_from=score_from,
        anchor=anchor,
    )
    data = [
        (p.win_probability_raw, 1.0 if p.team_a_won else 0.0)
        for p in predictions
        if not math.isnan(p.win_probability_raw)
    ]
    if not data:
        raise EvaluationError("no predictions with a modeled win probability to fit")
    cache: dict[float, float] = {}

    def brier(a: float) -> float:
        key = round(a, 6)
        if key not in cache:
            cache[key] = math.fsum(
                (calibrate_win_probability(r, key) - w) ** 2 for r, w in data
            ) / len(data)
        return cache[key]

    steps = _SEARCH_STEPS
    grid = [low + (high - low) * i / steps for i in range(steps + 1)]
    best = min(grid, key=brier)
    step = (high - low) / steps
    lo, hi = max(low, best - step), min(high, best + step)

    ratio = (math.sqrt(5) - 1) / 2
    c, d = hi - ratio * (hi - lo), lo + ratio * (hi - lo)
    while hi - lo > tolerance:
        width = hi - lo
        if brier(c) <= brier(d):
            hi, d = d, c
            c = hi - ratio * (hi - lo)
        else:
            lo, c = c, d
            d = lo + ratio * (hi - lo)
        if hi - lo >= width:
            break  # the bracket hit float spacing above a tiny tolerance
    fitted = round((lo + hi) / 2, 3)

    return WinCalibrationFit(
        scoring=scoring.value,
        a=fitted,
        brier=brier(fitted),
        brier_uncalibrated=brier(1.0),
        n=len(data),
        at_bound=fitted - low <= tolerance or high - fitted <= tolerance,
    )


# Public classes keep open_engine.engine, which re-exports them, as their
# __module__. _json_form tags each value in a match digest with its type's
# module and name, and snapshots store those digests, so a new module name
# would change every digest and stop resume from older snapshots (#127).
EvaluationReport.__module__ = "open_engine.engine"
PrequentialPrediction.__module__ = "open_engine.engine"
ScaleFit.__module__ = "open_engine.engine"
WinCalibrationFit.__module__ = "open_engine.engine"
