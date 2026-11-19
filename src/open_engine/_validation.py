"""
Type and number predicates and helpers shared by the engine modules.

Demonstration only: not validated on real match data, outputs are not OPEN
ratings, and the API may change without notice.
"""

from __future__ import annotations

import math
from datetime import date
from typing import TypeGuard, TypeVar, cast

# The predicates, what each accepts, and when to use it:
# - _type_is(value, *kinds): value's type is one of kinds, by identity. The
#   exact type check for a caller's value; isinstance and `in` can run caller
#   code. _type_name(value) names that type in a rejection message.
# - _is_plain_str: exactly a str. _is_id: exactly a str with a character that
#   is not whitespace. _is_plain_date: exactly a date, not a datetime.
# - _is_exact_int: exactly an int. _is_exact_number: exactly an int or a float.
#   For a caller's value that no rebuild has converted.
# - _number_broken(name, value): why value is not exactly an int or a float
#   that is finite as a float, or None. Its messages are the ones every
#   exact number check raises (winprob._check_number among them).
# - _plain_number(value): an int or float subclass as exactly int or float,
#   and a value that only claims to be one as a marker that is neither. The
#   predicates below use isinstance, so call them only on values that are
#   already exactly int or float, by _plain_number or because json.loads
#   made them.
# - _is_config_number: an int (not bool), or a float, finite as a float.
#   _is_config_whole: an int (not bool) that is also a config number.
# - _is_whole_number: an int, not bool, of any size.
# - _is_finite_number: an int, not bool, of any size, or a finite float.


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


# The longest day window a Config accepts: the days from date.min to
# date.max, which timedelta holds. A window that reaches back before date.min
# from a match or reference date stops at date.min (_days_before, issue #92).
_MAX_DAYS = (date.max - date.min).days
_MAX_ORDINAL = date.max.toordinal()


def _days_before(day: date, days: int) -> date:
    """
    The date `days` days before `day`, clamped to date.min and date.max.

    Window cutoffs use it, so a match or reference date near date.min, or a
    very long window, never raises OverflowError. Every cutoff is compared
    inclusively (a date on or after it is in the window), so a cutoff at
    date.min keeps every date, as one before it would: no result changes.
    A negative `days` moves forward and stops at date.max; the one caller
    that moves forward (a scheduled window's grid date) is always before a
    match date, so it never reaches that bound.
    """
    return date.fromordinal(min(max(1, day.toordinal() - days), _MAX_ORDINAL))


def _is_config_number(value: object) -> bool:
    """
    True for an int (not bool) or a float that is finite as a float.

    Unlike _is_finite_number, an int too large to convert to a float is not
    a number here. An int is judged by its own value, not by what a
    subclass's __float__ says.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(_as_float(value))
    except OverflowError:
        return False


def _as_float(value: float) -> float:
    """value as a plain float, from its own value, never a subclass's __float__."""
    if isinstance(value, int):
        return int.__float__(value)
    return float.__float__(value)


_N = TypeVar("_N")


class _SpoofedNumber:
    """Stands in for a value whose __class__ claims int or float but is not."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<not a number>"


_SPOOFED_NUMBER = _SpoofedNumber()


def _plain_number(value: _N) -> _N:
    """
    An int or float as exactly int or float, from its own value (issue #107).

    It tests the value's real type, not isinstance, which a __class__
    property can fool. A bool is returned as is, a real int subclass gives
    int.__int__(value), and a real float subclass float.__float__(value),
    so a subclass's operators never reach the caller's checks. A value that
    only claims to be an int or float is replaced with a marker that is
    neither, so the caller's type check rejects it. Anything else is
    returned as is, for the caller's type check to reject.
    """
    real = type(value)
    if issubclass(real, bool):
        return value
    if issubclass(real, int):
        return cast(_N, int.__int__(cast(int, value)))
    if issubclass(real, float):
        return cast(_N, float.__float__(cast(float, value)))
    if isinstance(value, (int, float)):
        return cast(_N, _SPOOFED_NUMBER)
    return value


def _is_config_whole(value: object) -> bool:
    """True for an int (not bool) that is also a config number."""
    return _is_whole_number(value) and _is_config_number(value)


def _is_whole_number(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_plain_str(value: object) -> TypeGuard[str]:
    """
    True for a str that is not a subclass.

    A subclass can override __eq__, __hash__, or strip and so pass a check
    that the plain value would fail, so identity checks accept only str.
    """
    return type(value) is str


def _is_id(value: object) -> TypeGuard[str]:
    """True for a plain str (not a subclass) with a non-whitespace character."""
    return _is_plain_str(value) and bool(value.strip())


def _is_finite_number(value: object) -> bool:
    """
    True for an int (not bool) or a finite float.

    Every int is finite. math.isfinite would overflow on a huge int, so ints
    skip it and the seed rules' bounds reject out-of-range values instead.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _is_plain_date(value: object) -> TypeGuard[date]:
    """
    True for exactly a date: not a datetime (the two do not compare), not a subclass.

    A subclass can override comparison or arithmetic and so pass a check
    the plain date would fail.
    """
    return type(value) is date


def _is_exact_int(value: object) -> TypeGuard[int]:
    """True for exactly an int: not a bool, not a subclass."""
    return type(value) is int


def _is_exact_number(value: object) -> TypeGuard[int | float]:
    """True for exactly an int or a float: not a bool, not a subclass."""
    return type(value) is int or type(value) is float


def _number_broken(name: str, value: object) -> str | None:
    """
    Why value is not exactly an int or float that is finite as a float, or None.

    An int too large for a float, or a float that is NaN or infinite, is
    rejected. Arithmetic on values that pass can still overflow, so each
    public function that reads them turns OverflowError into
    EvaluationError.
    """
    if not _is_exact_number(value):
        return f"{name} must be an int or float, not {_type_name(value)}"
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        return f"{name} must be finite as a float"
    return None
