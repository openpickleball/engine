"""Convert an expected point share into game and match win probabilities.

Model: points are independent and team A wins each with probability p. The
result is exact under the independent-points assumption, which suits rally
scoring and only approximates side-out scoring, where only the serving side can
score.

Demonstration only: not validated on real match data, outputs are not OPEN
ratings, and the API may change without notice.
"""

from __future__ import annotations

import math
from math import comb
from typing import TYPE_CHECKING

from ._errors import EvaluationError

if TYPE_CHECKING:
    from .engine import GameFormat

PROBABILITY_CLIP = 1e-6


def _type_name(value: object) -> str:
    """
    The name of value's type, as an exact str, read without running caller code.

    type(value).__name__ would run a __name__ property defined on the
    type's metaclass, so the name is read through type's own descriptor.
    A class's __name__ can be set to a str subclass, so the result is
    copied into an exact str through str.__str__, which runs none of the
    subclass's methods.
    """
    name: str = type.__dict__["__name__"].__get__(type(value))
    return str.__str__(name)


def _type_is(value: object, *kinds: type) -> bool:
    """
    True when value's type is one of kinds, compared by identity.

    type(value) in kinds would compare with ==, which runs an __eq__
    defined on the type's metaclass.
    """
    actual = type(value)
    return any(actual is kind for kind in kinds)


def _check_number(name: str, value: object) -> None:
    """
    Raise EvaluationError unless value is exactly an int or a float, finite as a float.

    A bool or a subclass is rejected, and so is an int too large for a float
    or a float that is NaN or infinite. The arithmetic on a value that
    passes can still overflow; each public function turns that into
    EvaluationError.
    """
    if type(value) is not int and type(value) is not float:
        raise EvaluationError(f"{name} must be an int or float, not {_type_name(value)}")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise EvaluationError(f"{name} must be finite as a float")


def _check_best_of(best_of: object) -> None:
    """Raise EvaluationError unless best_of is exactly an int that is positive and odd."""
    if type(best_of) is not int:
        raise EvaluationError(f"best_of must be an odd integer >= 1, got {_type_name(best_of)}")
    if best_of < 1 or best_of % 2 == 0:
        shown = repr(best_of) if best_of.bit_length() <= 64 else "a large int"
        raise EvaluationError(f"best_of must be an odd integer >= 1, got {shown}")


def _check_format(game_format: object) -> None:
    """GameFormat.validate through the class: ConfigError unless exactly a valid GameFormat."""
    from .engine import GameFormat

    GameFormat.validate(game_format)  # type: ignore[arg-type]


def _first_to(p: float, n: int) -> float:
    """Probability of winning n points before the opponent does (no win-by rule)."""
    q = 1 - p
    return math.fsum(comb(n - 1 + k, k) * p**n * q**k for k in range(n))


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
    Raises EvaluationError unless p is exactly an int or a float that is
    finite as a float, ConfigError for an invalid format (it must be
    exactly a GameFormat; see GameFormat.validate), and EvaluationError
    when win_by is not 1 or 2 or the probability is too large to compute
    for the format (a long game can overflow a float).
    """
    _check_number("p", p)
    _check_format(game_format)
    win_by = game_format.win_by
    if win_by not in (1, 2):
        raise EvaluationError("win_by must be 1 or 2")
    try:
        return _game_win(p, game_format.points_to, win_by, game_format.cap)
    except OverflowError:
        raise EvaluationError(
            "the game win probability is too large to compute for this format"
        ) from None


def _game_win(p: float, n: int, win_by: int, cap: int | None) -> float:
    """game_win_probability after its checks; the arithmetic can raise OverflowError."""
    if cap is not None:
        return _capped(p, n, win_by, cap)
    if win_by == 1:
        return _first_to(p, n)
    q = 1 - p
    direct = math.fsum(comb(n - 1 + k, k) * p**n * q**k for k in range(n - 1))
    reach_deuce = comb(2 * (n - 1), n - 1) * (p * q) ** (n - 1)
    return direct + reach_deuce * p**2 / (p**2 + q**2)


def match_win_probability(p: float, game_format: GameFormat, best_of: int) -> float:
    """
    Probability of winning a best-of-N match from a per-point win probability.

    Raises EvaluationError unless p is exactly an int or a float that is
    finite as a float and best_of is exactly an int that is positive and
    odd (a bool or an int subclass is rejected), ConfigError for an
    invalid game_format, and EvaluationError when the probability is too
    large to compute for the format or best_of.
    """
    _check_number("p", p)
    _check_best_of(best_of)
    g = game_win_probability(p, game_format)
    w = best_of // 2 + 1
    try:
        return math.fsum(comb(w - 1 + k, k) * g**w * (1 - g) ** k for k in range(w))
    except OverflowError:
        raise EvaluationError("best_of is too large to compute a win probability") from None


def calibrate_win_probability(p: float, a: float) -> float:
    """
    Apply sigmoid(a * logit(p)) to a win probability clipped to [1e-6, 1 - 1e-6].

    Raises EvaluationError unless p and a are each exactly an int or a float
    that is finite as a float.
    """
    _check_number("p", p)
    _check_number("a", a)
    q = min(max(p, PROBABILITY_CLIP), 1 - PROBABILITY_CLIP)
    z = a * math.log(q / (1 - q))
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)
    return e / (1 + e)
