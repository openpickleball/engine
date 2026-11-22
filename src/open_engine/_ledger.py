"""
Ledger order and the match ledger: validation, duplicate collapse, match
digests, matchup keys, and content keys.

Demonstration only: not validated on real match data, outputs are not OPEN
ratings, and the API may change without notice.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from types import NoneType

from ._errors import LedgerConflictError
from ._model import _UTC_MIN, Match, _require_plain_match, _utc_instant, _validate_match
from ._types import GameFormat, MatchStatus, MatchType, RatingFormat, ScoringType


def ledger_order(match: Match) -> tuple[date, int, datetime, int, str]:
    """
    Deterministic order of play.

    Matches sort by played_on. Within one day, untimed matches come first,
    by sequence then match_id. Timed matches follow, by the UTC instant of
    played_at, then sequence, then match_id.

    Args:
        match: Exactly a Match.

    Returns:
        The match's sort key. Compare keys only: the order they give is
        part of the stability promise, and the key's shape is not.

    Raises:
        InvalidMatchError: played_at must be timezone-aware; a naive value
            raises InvalidMatchError, and so does a match that is not exactly
            a Match (a subclass included), or whose match_id, status,
            rating_format, match_type, game_format, teams, confirmed_by,
            played_on, played_at, sequence, or best_of has the wrong type,
            checked exactly as Match.validate checks them first (a played_at
            tzinfo must be exactly datetime.timezone or zoneinfo.ZoneInfo).

    Value rules, such as a nonnegative sequence, and the games, team ids,
    and confirmed_by members are not checked.
    """
    _require_plain_match(match)
    Match._validate_field_types(match)
    if match.played_at is None:
        return (match.played_on, 0, _UTC_MIN, match.sequence, match.match_id)
    return (match.played_on, 1, _utc_instant(match.played_at), match.sequence, match.match_id)


def _ledger(matches: Iterable[Match]) -> list[Match]:
    """
    Unique ledger rows in ledger order.

    Every row is validated first, including rows after a replay's as_of, so
    an invalid row raises wherever it sits in the input. Rows that repeat a
    match_id collapse to one only when every field matches in type and
    value, with played_at compared by its UTC instant and UTC offset (see
    _canonical). Any other repeat raises
    LedgerConflictError naming every conflicting id, so the result never
    depends on input order.
    """
    by_id: dict[str, tuple[Match, tuple[object, ...]]] = {}
    conflicts: set[str] = set()
    for match in matches:
        _validate_match(match)
        key = _canonical(match)
        _, kept_key = by_id.setdefault(match.match_id, (match, key))
        if kept_key != key:
            conflicts.add(match.match_id)
    if conflicts:
        raise LedgerConflictError(conflicts)
    return sorted((match for match, _ in by_id.values()), key=ledger_order)


def _canonical(match: Match) -> tuple[object, ...]:
    """
    A form of a validated row that is equal only for interchangeable rows.

    Every field is paired with its exact type, recursively, so values that
    are equal under == but differ in type (3 and 3.0, a str and an Enum
    member) do not match. played_at is compared by its UTC instant and its
    UTC offset instead: Match equality ignores the offset, treats the two
    readings of a repeated wall time (a DST fold) as equal, and treats rows
    with distinct zone objects inside a fold as unequal. A validated
    played_at has exactly a datetime.timezone or zoneinfo.ZoneInfo whose
    offset is exactly a timedelta, so reading both runs no caller code.
    """
    others = tuple(
        (f.name, _strict(getattr(match, f.name))) for f in fields(Match) if f.name != "played_at"
    )
    if match.played_at is None:
        return (others, None, None)
    return (others, _utc_instant(match.played_at), match.played_at.utcoffset())


def _strict(value: object) -> object:
    """
    value paired with its exact type, recursing into dataclasses and collections.

    Tuples and lists recurse member by member, and so do sets and
    frozensets, so a member's type counts wherever it sits. _json_form
    mirrors this; change both together.
    """
    if is_dataclass(value) and not isinstance(value, type):
        return (
            type(value),
            tuple((f.name, _strict(getattr(value, f.name))) for f in fields(type(value))),
        )
    if isinstance(value, (tuple, list)):
        return (type(value), tuple(_strict(item) for item in value))
    if isinstance(value, (frozenset, set)):
        return (type(value), frozenset(_strict(item) for item in value))
    return (type(value), value)


def _match_digest(match: Match) -> str:
    """
    SHA-256 hex digest of a validated row, equal only for interchangeable rows.

    It mirrors _canonical in JSON form: every field but played_at, tagged
    with its exact type, and played_at as its UTC instant and UTC offset.
    A checkpoint stores it for each row it considered, so resume can tell
    whether a row it is given is the row the checkpoint saw.
    """
    others = [
        [f.name, _json_form(getattr(match, f.name))] for f in fields(Match) if f.name != "played_at"
    ]
    timing: list[object] | None = None
    if match.played_at is not None:
        instant = _utc_instant(match.played_at)
        offset = match.played_at.utcoffset()
        timing = [instant.isoformat(), None if offset is None else offset.total_seconds()]
    return hashlib.sha256(_json_text([others, timing]).encode()).hexdigest()


def _json_text(form: object) -> str:
    """Compact JSON text with sorted keys, for hashing and for sorting forms."""
    return json.dumps(form, sort_keys=True, separators=(",", ":"), allow_nan=False)


# The type tag _json_form gives each value in a match digest, by exact type.
# Each string is the type's module and qualified name as the type reported
# them when checkpoints first stored digests, so a class that moves to
# another module keeps its tag and every stored digest stays valid (#133).
# The table holds every type a validated Match can hold outside played_at,
# and is a superset: bool and float are here too, though a validated match
# never holds one. tests/test_digest_tags.py fails when a Match or
# GameFormat field's type has no entry. A changed string changes stored
# digests. _digest_tag reads it by identity, so no metaclass __eq__ or
# __hash__ runs.
_DIGEST_TAGS: dict[type, str] = {
    str: "builtins.str",
    int: "builtins.int",
    bool: "builtins.bool",
    NoneType: "builtins.NoneType",
    float: "builtins.float",
    tuple: "builtins.tuple",
    list: "builtins.list",
    frozenset: "builtins.frozenset",
    set: "builtins.set",
    date: "datetime.date",
    RatingFormat: "open_engine.engine.RatingFormat",
    MatchType: "open_engine.engine.MatchType",
    ScoringType: "open_engine.engine.ScoringType",
    MatchStatus: "open_engine.engine.MatchStatus",
    GameFormat: "open_engine.engine.GameFormat",
}


def _digest_tag(kind: type) -> str | None:
    """The _DIGEST_TAGS string for exactly kind, compared by identity, or None."""
    for known, tag in _DIGEST_TAGS.items():
        if kind is known:
            return tag
    return None


def _json_form(value: object) -> object:
    """
    value as JSON data tagged with its exact type (the tag from _DIGEST_TAGS),
    recursing like _strict.

    Ints are written in hex, which has no digit limit. A float is written as
    a number with negative zero folded into zero (as == does), or as its
    repr when it is not finite. Set and frozenset members are sorted by
    their JSON text, so the form never depends on set order or the hash
    seed.
    """
    tag = _digest_tag(type(value))
    if tag is None:
        # A validated match never reaches this: every type it can hold is
        # in _DIGEST_TAGS. Any other type keeps its module and name.
        tag = f"{type(value).__module__}.{type(value).__qualname__}"
    if is_dataclass(value) and not isinstance(value, type):
        return [tag, [[f.name, _json_form(getattr(value, f.name))] for f in fields(type(value))]]
    if isinstance(value, (tuple, list)):
        return [tag, [_json_form(item) for item in value]]
    if isinstance(value, (frozenset, set)):
        return [tag, sorted((_json_form(item) for item in value), key=_json_text)]
    if isinstance(value, Enum):
        return [tag, _json_form(value.value)]
    if isinstance(value, date):
        return [tag, value.isoformat()]
    if isinstance(value, bool) or value is None:
        return [tag, value]
    if isinstance(value, int):
        return [tag, hex(value)]
    if isinstance(value, float):
        return [tag, value + 0.0 if math.isfinite(value) else repr(value)]
    if isinstance(value, str):
        return [tag, str(value)]
    return [tag, repr(value)]


MatchupKey = tuple[RatingFormat, frozenset[frozenset[str]]]


def _matchup_key(match: Match) -> MatchupKey:
    return (
        match.rating_format,
        frozenset({frozenset(match.team_a), frozenset(match.team_b)}),
    )


# Scores below this are written as JSON numbers. It is under the lowest
# int to text digit limit Python allows (640 digits), so the key never
# depends on sys.set_int_max_str_digits.
_CONTENT_SCORE_DECIMAL_LIMIT = 10**600


def _content_score(score: int) -> int | str:
    """
    A validated game score in the content key's JSON form.

    A validated score is exactly an int (issue #109), so int() returns it
    unchanged. A score below 10**600 stays a JSON
    number; a larger one is hex text, which has no digit limit and never
    equals a number.
    """
    value = int(score)
    if value < _CONTENT_SCORE_DECIMAL_LIMIT:
        return value
    return hex(value)


def _content_key(match: Match) -> str:
    """
    SHA-256 hex digest of what makes two reports the same match.

    It uses played_on, the two teams as sets of players, and the game
    scores in order, read from the same side. Each team is a sorted list
    of ids, and the two teams are put in sorted order; when that swaps
    them, every game score is swapped too. Everything else is left out on
    purpose (match_id, match_type, rating_format, game_format, best_of,
    played_at, sequence, confirmed_by), since two reports of one match
    often disagree on labels. The digest is over JSON text, so it never
    depends on set order or the hash seed. Scores go through
    _content_score, so a huge score never hits the int to text digit
    limit.
    """
    team_a = sorted(match.team_a)
    team_b = sorted(match.team_b)
    games = [[_content_score(a), _content_score(b)] for a, b in match.games]
    if team_b < team_a:
        team_a, team_b = team_b, team_a
        games = [[score_b, score_a] for score_a, score_b in games]
    form = [match.played_on.isoformat(), team_a, team_b, games]
    return hashlib.sha256(_json_text(form).encode()).hexdigest()
