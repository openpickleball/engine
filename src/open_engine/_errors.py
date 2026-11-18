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
    A Config value is not valid, or a GameFormat is not valid when it is
    checked outside a match: by GameFormat.validate, the win probability
    functions, or RatingEngine.predict_win_probability.
    """


class InvalidMatchError(OpenEngineError):
    """A match, its teams, its scores, its played_at, or its GameFormat is not valid."""


class InvalidSeedError(OpenEngineError):
    """An external or replay seed cannot initialize a rating."""


class PlayerError(OpenEngineError):
    """A player identity, account link, or external identity is not valid."""


class UnknownPlayerError(PlayerError):
    """No player record exists for the player id."""


class EvaluationError(OpenEngineError):
    """An evaluation, calibration fit, or win probability cannot be computed."""


class SnapshotError(OpenEngineError):
    """A snapshot cannot be loaded or resumed from."""


class ReferenceDateError(OpenEngineError):
    """
    No usable reference date to measure inactivity and reliability at.

    player_summary raises it when it gets no as_of and the engine was never
    replayed, or when its as_of is not a plain date. replay raises it when
    its as_of is not a plain date.
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
