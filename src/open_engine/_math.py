"""
The rating math and reliability: expected share, RD, K, and their helpers.

Demonstration only: not validated on real match data, outputs are not OPEN
ratings, and the API may change without notice.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import replace
from datetime import date

from ._config import Config, _require_config
from ._errors import EvaluationError, InvalidMatchError, ReferenceDateError
from ._model import Match, Player, RatingState
from ._validation import _days_before, _is_plain_date, _is_plain_str, _number_broken, _type_name

# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

# The rating model, equation by equation, is in docs/model.md.

# expected_point_share clamps (team B rating - team A rating) / scale to
# -12..12, so 10.0**exponent stays finite whatever the rating range and
# scale. At the clamp the expected share is about 1e-12 from 0 or 1. Under
# the default rating range and scales the exponent stays below 2.1 in
# size (6 / 2.9), so the clamp changes nothing there.
_SHARE_EXPONENT_LIMIT = 12.0

# Reliability blends certainty (from RD) with partner and opponent variety:
# 100 * certainty * (base + variety_weight * variety). A player with no
# variety keeps 60 percent of their certainty, and full variety gives all
# of it. Declared choices, not calibrated ones; they change no rating.
_RELIABILITY_BASE_WEIGHT = 0.60
_RELIABILITY_VARIETY_WEIGHT = 0.40

# _compute marks a player clamped when the rating range cut their change:
# the applied rating differs from the unclamped one (old rating + raw
# delta) by more than this. An unclamped rating equals that sum exactly,
# so only a clamp can differ. A clamp that cuts off 1e-12 or less is not
# listed in clamped: that is far below the 5 places a delta is reported to.
_CLAMP_TOLERANCE = 1e-12

# Decimal places of reported numbers. Ratings and states keep full
# precision; only the reported values are rounded.
# MatchResult deltas, expected_a, actual_a, surprise, effective_weight,
# and imbalance, and the evaluation pairs that mirror them (_scored_pairs).
_RESULT_PLACES = 5
# _compute also marks a player clamped when float rounding of the new
# rating dropped at least this much of their change: the applied change
# (new rating minus old rating) differs from the raw delta by half a unit
# in the last reported place, so the loss can show in deltas and imbalance
# (issue #129). A smaller loss is ordinary rounding and is not listed: an
# absolute tolerance near 1e-12 would list players at every rating of
# 2**14 or more in magnitude.
_VISIBLE_LOSS = 0.5 * 10**-_RESULT_PLACES
# PlayerSummary.rating.
_SUMMARY_RATING_PLACES = 3
# PlayerSummary.rd.
_SUMMARY_RD_PLACES = 4
# Reliability on the 0 to 100 scale: reliability(), the connectivity cap
# as displayed, and PlayerSummary.imported_reliability.
_RELIABILITY_PLACES = 1
# One unit in the last displayed reliability place, used to show the
# connectivity cap rounded down.
_RELIABILITY_STEP = 0.1


def expected_point_share(team_a_rating: float, team_b_rating: float, scale: float) -> float:
    """
    Expected fraction of total points won by team A.

    1 / (1 + 10**((team_b_rating - team_a_rating) / scale)), with the
    exponent clamped to -12..12. See docs/model.md.

    Args:
        team_a_rating: Team A's rating.
        team_b_rating: Team B's rating.
        scale: The rating scale in the formula above.

    Returns:
        Team A's expected share of points.

    Raises:
        EvaluationError: unless each argument is exactly an int or a float
            (not a bool or a subclass) that is finite as a float; then when
            scale is 0 or less; then when the rating difference divided by
            scale is too large to compute.
    """
    for name, value in (
        ("team_a_rating", team_a_rating),
        ("team_b_rating", team_b_rating),
        ("scale", scale),
    ):
        problem = _number_broken(name, value)
        if problem is not None:
            raise EvaluationError(problem)
    if scale <= 0:
        raise EvaluationError("scale must be greater than 0")
    try:
        exponent = (team_b_rating - team_a_rating) / scale
    except OverflowError:
        raise EvaluationError("the rating difference is too large to compute a share") from None
    exponent = max(-_SHARE_EXPONENT_LIMIT, min(_SHARE_EXPONENT_LIMIT, exponent))
    return 1.0 / (1.0 + float(10.0**exponent))


def inflated_rd(state: RatingState, on: date, cfg: Config) -> float:
    """
    RD after inactivity since rd_as_of. The rating itself does not drift.

    sqrt(rd**2 + rd_growth_per_day**2 * days), at most rd_max. With no
    rd_as_of, or on or before it, the stored rd. See docs/model.md.
    """
    if state.rd_as_of is None or on <= state.rd_as_of:
        return state.rd
    days = (on - state.rd_as_of).days
    return min(cfg.rd_max, math.sqrt(state.rd**2 + (cfg.rd_growth_per_day**2) * days))


def player_k(state: RatingState, cfg: Config) -> float:
    """
    The player's K, linear in RD from k_min at rd_min to k_max at rd_max.

    The RD position is clamped to 0..1. _compute passes a state whose RD
    is already inflated for inactivity. See docs/model.md.
    """
    fraction = (state.rd - cfg.rd_min) / (cfg.rd_max - cfg.rd_min)
    fraction = max(0.0, min(1.0, fraction))
    return cfg.k_min + (cfg.k_max - cfg.k_min) * fraction


def information_multiplier(match: Match, cfg: Config) -> float:
    """
    How much evidence a match carries for its game length and game count.

    game_length_weights[points_to] * (1 + game_count_bonus * (games - 1)),
    at most max_information_multiplier. See docs/model.md.
    """
    length = cfg.game_length_weights[match.game_format.points_to]
    count = 1.0 + cfg.game_count_bonus * (len(match.games) - 1)
    return min(cfg.max_information_multiplier, length * count)


def _actual_point_share(match: Match, cfg: Config) -> tuple[float, bool]:
    """
    Team A's match performance, and whether team A won.

    (1 - win_weight) * point_share + win_weight * (1 if A won else 0),
    where point_share is team A's share of all points across the games and
    A won when it took more games. See docs/model.md.
    """
    points_a = sum(a for a, _ in match.games)
    points_b = sum(b for _, b in match.games)
    total = points_a + points_b
    if total <= 0:
        raise InvalidMatchError("Match must contain at least one point")
    games_a = sum(a > b for a, b in match.games)
    games_b = len(match.games) - games_a
    a_won = games_a > games_b
    point_share = points_a / total
    actual = (1.0 - cfg.win_weight) * point_share + cfg.win_weight * float(a_won)
    return actual, a_won


def _rolling_unique_count(interactions: dict[str, date], on: date, window_days: int) -> int:
    """The number of ids last seen on or after window_days before `on`."""
    cutoff = _days_before(on, window_days)
    return sum(1 for last_seen in interactions.values() if last_seen >= cutoff)


def _latest(current: date | None, on: date) -> date:
    """The later of a stored date and a match date, so a date never moves back."""
    return on if current is None else max(current, on)


def _is_late(state: RatingState, on: date) -> bool:
    """
    True when a match on `on` is dated before what the state already reflects.

    That is its last match, or its rd_as_of when that is not just the
    imported capture date: a category rating copied from overall doubles
    keeps the doubles rd_as_of but has no last_played.
    """
    if state.last_played is not None and on < state.last_played:
        return True
    return (
        state.rd_as_of is not None
        and state.rd_as_of != state.initial_source_date
        and on < state.rd_as_of
    )


def _seen(interactions: dict[str, date], ids: Iterable[str], on: date) -> None:
    """Record each id as seen on `on`, keeping a later date already stored."""
    for other in ids:
        interactions[other] = _latest(interactions.get(other), on)


def _check_reliability_state(state: object) -> None:
    """Raise EvaluationError unless state and the fields reliability reads have exact types."""
    if type(state) is not RatingState:
        raise EvaluationError(f"state must be a RatingState, not {_type_name(state)}")
    problem = _number_broken("rd", state.rd)
    if problem is not None:
        raise EvaluationError(problem)
    if state.rd_as_of is not None and not _is_plain_date(state.rd_as_of):
        raise EvaluationError(f"rd_as_of must be a date or None, not {_type_name(state.rd_as_of)}")
    for name, seen in (("partners", state.partners), ("opponents", state.opponents)):
        if type(seen) is not dict:
            raise EvaluationError(f"{name} must be a dict, not {_type_name(seen)}")
        if not all(_is_plain_str(key) and _is_plain_date(on) for key, on in seen.items()):
            raise EvaluationError(f"{name} must map str ids to dates")


def reliability(state: RatingState, cfg: Config, as_of: date) -> float:
    """
    OPEN-earned reliability (0-100). Never a conversion of an external score.

    Combines rating certainty (from RD, inflated for inactivity) with rolling
    partner/opponent variety. See docs/model.md for the equation.

    Args:
        state: Exactly a RatingState.
        cfg: Exactly a Config with only its fields. It is rebuilt from its
            field values.
        as_of: The date inactivity and variety are measured at, exactly a date.

    Returns:
        The reliability, from 0 to 100.

    Raises:
        EvaluationError: state is not exactly a RatingState whose rd is
            exactly an int or float that is finite as a float, rd_as_of None
            or exactly a date, and partners and opponents exactly dicts with
            exactly str keys and exactly date values; or its rd is too large
            to inflate for inactivity.
        ConfigError: cfg is not valid.
        ReferenceDateError: as_of is not exactly a date.

    See docs/behavior.md, "Input types".
    """
    _check_reliability_state(state)
    return _reliability(state, _require_config(cfg), as_of)


def _reliability(state: RatingState, cfg: Config, as_of: date) -> float:
    """
    reliability under a checked Config, with the same state and as_of checks.

    The engine's own callers (player_summary) pass self.cfg, the copy
    already rebuilt, so it is not rebuilt on every call.
    """
    _check_reliability_state(state)
    if not _is_plain_date(as_of):
        raise ReferenceDateError(f"reliability as_of must be a date, not {_type_name(as_of)}")
    try:
        rd = inflated_rd(state, as_of, cfg)
    except OverflowError:
        raise EvaluationError("rd is too large to compute reliability") from None
    # See docs/model.md for the equation.
    certainty = 1.0 - (rd - cfg.rd_min) / (cfg.rd_max - cfg.rd_min)
    variety_count = _rolling_unique_count(
        state.partners, as_of, cfg.diversity_window_days
    ) + _rolling_unique_count(state.opponents, as_of, cfg.diversity_window_days)
    variety = min(1.0, variety_count / cfg.diversity_target)
    score = 100.0 * certainty * (_RELIABILITY_BASE_WEIGHT + _RELIABILITY_VARIETY_WEIGHT * variety)
    return round(max(0.0, min(100.0, score)), _RELIABILITY_PLACES)


def _clone_state(state: RatingState) -> RatingState:
    """A copy of state with its own partners and opponents dicts."""
    return replace(
        state,
        partners=dict(state.partners),
        opponents=dict(state.opponents),
    )


def clone_player(player: Player) -> Player:
    """A copy of player with its own ratings, states, and external_ids."""
    return Player(
        id=player.id,
        ratings={fmt: _clone_state(state) for fmt, state in player.ratings.items()},
        external_ids=dict(player.external_ids),
        account_user_id=player.account_user_id,
    )
