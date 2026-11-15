"""Convert an expected point share into game and match win probabilities.

Model: points are independent and team A wins each with probability p. This is
exact for rally scoring and an approximation for side-out scoring, where only
the serving side can score.
"""

from __future__ import annotations

import math
from math import comb
from typing import TYPE_CHECKING

from ._errors import EvaluationError

if TYPE_CHECKING:
    from .engine import GameFormat

PROBABILITY_CLIP = 1e-6


def _first_to(p: float, n: int) -> float:
    """Probability of winning n points before the opponent does (no win-by rule)."""
    q = 1 - p
    return sum(comb(n - 1 + k, k) * p**n * q**k for k in range(n))


def _capped(p: float, points_to: int, win_by: int, cap: int) -> float:
    """Dynamic program over score states for a game with a hard cap.

    The game ends when a side has at least points_to and leads by win_by, or when
    a side reaches cap (matching GameFormat.validate_game).
    """
    q = 1 - p

    def winner(a: int, b: int) -> int | None:
        for side, high, low in ((1, a, b), (0, b, a)):
            if high >= points_to and high - low >= win_by:
                return side
            if high >= cap:
                return side
        return None

    # reach[(a, b)]: probability of passing through the live state (a, b).
    reach = {(0, 0): 1.0}
    total = 0.0
    for s in range(2 * cap):
        for a in range(max(0, s - cap), min(s, cap) + 1):
            b = s - a
            prob = reach.pop((a, b), 0.0)
            if prob == 0.0:
                continue
            for da, db, pr in ((1, 0, p), (0, 1, q)):
                na, nb = a + da, b + db
                w = winner(na, nb)
                if w is None:
                    reach[(na, nb)] = reach.get((na, nb), 0.0) + prob * pr
                elif w == 1:
                    total += prob * pr
    return total


def game_win_probability(p: float, game_format: GameFormat) -> float:
    """Probability that team A wins a game when it wins each point with probability p.

    Exact closed form when the format has no cap, score-state DP when it does.
    """
    win_by = game_format.win_by
    if win_by not in (1, 2):
        raise EvaluationError("win_by must be 1 or 2")
    n = game_format.points_to
    if game_format.cap is not None:
        return _capped(p, n, win_by, game_format.cap)
    if win_by == 1:
        return _first_to(p, n)
    q = 1 - p
    direct = sum(comb(n - 1 + k, k) * p**n * q**k for k in range(n - 1))
    reach_deuce = comb(2 * (n - 1), n - 1) * (p * q) ** (n - 1)
    return direct + reach_deuce * p**2 / (p**2 + q**2)


def match_win_probability(p: float, game_format: GameFormat, best_of: int) -> float:
    """Probability of winning a best-of-N match from a per-point win probability."""
    if not isinstance(best_of, int) or isinstance(best_of, bool) or best_of < 1 or best_of % 2 == 0:
        raise EvaluationError(f"best_of must be an odd integer >= 1, got {best_of!r}")
    g = game_win_probability(p, game_format)
    w = best_of // 2 + 1
    return sum(comb(w - 1 + k, k) * g**w * (1 - g) ** k for k in range(w))


def calibrate_win_probability(p: float, a: float) -> float:
    """Apply sigmoid(a * logit(p)) to a win probability clipped to [1e-6, 1 - 1e-6]."""
    q = min(max(p, PROBABILITY_CLIP), 1 - PROBABILITY_CLIP)
    z = a * math.log(q / (1 - q))
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)
    return e / (1 + e)
