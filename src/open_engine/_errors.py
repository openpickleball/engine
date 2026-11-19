"""Exception classes for the OPEN rating engine.

Demonstration only: not validated on real match data, outputs are not OPEN
ratings, and the API may change without notice.

Every class is a ValueError subclass, so code that catches ValueError keeps
working.
"""

from __future__ import annotations

from collections.abc import Iterable


class OpenEngineError(ValueError):
    """Base class for every error the engine raises on purpose."""


class ConfigError(OpenEngineError):
    """
    A Config value is not valid (also one rewritten after the Config was
    built), a cfg is not None or exactly a Config with only its fields, or
    a GameFormat is not valid when it is checked
    outside a match: by GameFormat.validate, the win probability
    functions, or RatingEngine.predict_win_probability.
    """


class InvalidMatchError(OpenEngineError):
    """A match, its teams, its scores, its played_at, or its GameFormat is not valid."""


class InvalidSeedError(OpenEngineError):
    """An external or replay seed cannot initialize a rating."""


class PlayerError(OpenEngineError):
    """
    A player identity, account link, or external identity is not valid.

    RatingEngine.state and player_summary also raise it for a rating_format
    that is not a RatingFormat member.
    """


class UnknownPlayerError(PlayerError):
    """No player record exists for the player id."""


class EvaluationError(OpenEngineError):
    """
    An evaluation, calibration fit, or win probability cannot be computed.

    It covers argument types too: reliability for a state or field of the
    wrong type, and expected_point_share, the win probability functions,
    and calibrate_win_probability for a number that is not exactly an int
    or float finite as a float.
    """


class SnapshotError(OpenEngineError):
    """A snapshot cannot be loaded or resumed from."""


class ReferenceDateError(OpenEngineError):
    """
    No usable reference date to measure inactivity and reliability at.

    player_summary raises it when it gets no as_of and the engine was never
    replayed, or when its as_of is not a plain date. replay and reliability
    raise it when their as_of is not a plain date. A plain date is exactly
    a date: a datetime or a date subclass is not one.
    """


class LedgerConflictError(OpenEngineError):
    """Two or more ledger rows share a match_id but differ."""

    def __init__(self, match_ids: Iterable[str]) -> None:
        self.match_ids: tuple[str, ...] = tuple(sorted(set(match_ids)))
        super().__init__("Conflicting ledger rows for match_id: " + ", ".join(self.match_ids))

    def __reduce__(
        self,
    ) -> tuple[type[LedgerConflictError], tuple[tuple[str, ...]], dict[str, object]]:
        # Rebuild from match_ids, not from the message, so pickle and copy work.
        return (type(self), (self.match_ids,), self.__dict__)
