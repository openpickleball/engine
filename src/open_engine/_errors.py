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
    """A Config or GameFormat value is out of range or incomplete."""


class InvalidMatchError(OpenEngineError):
    """A match, its teams, its scores, or its played_at is not valid."""


class InvalidSeedError(OpenEngineError):
    """An external or replay seed cannot initialize a rating."""


class PlayerError(OpenEngineError):
    """A player identity, account link, or external identity is not valid."""


class EvaluationError(OpenEngineError):
    """An evaluation, calibration fit, or win probability cannot be computed."""


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
