"""
Seed player validation: types, rules, provenance, and identities.

Demonstration only: not validated on real match data, outputs are not OPEN
ratings, and the API may change without notice.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import date

from ._config import Config
from ._errors import InvalidSeedError
from ._model import Player, RatingState
from ._types import RatingFormat, _is_member, _safe_shown
from ._validation import (
    _is_finite_number,
    _is_id,
    _is_plain_date,
    _is_plain_str,
    _plain_number,
    _type_name,
)


def _seed_type_broken(state: RatingState) -> str | None:
    """The first seed field with the wrong type, or None."""
    for name in (
        "matches_played",
        "verified_matches_played",
        "live_updates_since_replay",
        "inherited_live_updates",
    ):
        value = getattr(state, name)
        if not isinstance(value, int) or isinstance(value, bool):
            return f"{name} must be an int"
    for name in ("partners", "opponents"):
        # Exactly dict: a subclass can report itself empty while holding items.
        if type(getattr(state, name)) is not dict:
            return f"{name} must be a dict"
    for name in ("rating", "rd", "initial_rating"):
        if not _is_finite_number(getattr(state, name)):
            return f"{name} must be a finite number"
    imported = state.initial_source_reliability
    if imported is not None and not _is_finite_number(imported):
        return "initial_source_reliability must be a finite number"
    for name in ("initial_source_date", "rd_as_of"):
        value = getattr(state, name)
        if value is not None and not _is_plain_date(value):
            return f"{name} must be a date"
    if not _is_plain_str(state.initial_source):
        return "initial_source must be a str"
    external_id = state.initial_external_player_id
    if external_id is not None and not _is_plain_str(external_id):
        return "initial_external_player_id must be a str or None"
    reference = state.initial_evidence_reference
    if reference is not None and not _is_plain_str(reference):
        return "initial_evidence_reference must be a str or None"
    return None


# Provenance names that start with this prefix belong to the engine: no
# external source may use one (seed_external_rating, attach_external_id, seed
# external_ids, and snapshot external_ids reject it), and a seed or snapshot
# state may hold one only in the shapes _reserved_source_broken checks.
_RESERVED_SOURCE_PREFIX = "open_"


def _reserved_source_broken(
    state: RatingState,
    fmt: RatingFormat,
    states: Mapping[RatingFormat, RatingState],
    cfg: Config,
) -> str | None:
    """
    A message when the state's reserved initial_source breaks a shape rule, or None.

    states holds the same player's states (the seed's ratings, or the
    snapshot player's), and cfg is the config they are checked under. A
    reserved initial_source carries no imported reliability, external player
    id, or evidence reference, and is one of:
    - "open_provisional" with no initial_source_date (any provisional
      rating, since it is RatingState's default initial_source);
    - in a category format, "open_parent:" plus that format's parent's
      value, as _new_state derives it: category_feeds_parent is on, the
      player has a parent state, the state is dated exactly when the parent
      state is (the dates may differ: the engine creates a derived state
      only at the player's first category match, with the parent's date,
      and then refuses a parent reseed, and a parent reseed refreshes an
      unplayed derived state a seed or snapshot holds; but a seed or
      snapshot made before that refresh existed, or built by a caller, can
      hold another date, and keeps it once played), an
      undated state has a played parent, and an unplayed state has an RD
      of at least category_seed_rd.
    This does not check the rating or the date against the parent state's,
    so a caller can still build a state in these shapes.
    """
    source = state.initial_source
    if not source.startswith(_RESERVED_SOURCE_PREFIX):
        return None
    prefix = (
        f"initial_source {source!r} uses the reserved prefix {_RESERVED_SOURCE_PREFIX} "
        "but is not provenance the engine writes: "
    )
    if (
        state.initial_source_reliability is not None
        or state.initial_external_player_id is not None
        or state.initial_evidence_reference is not None
    ):
        return prefix + (
            "it has an imported reliability, initial_external_player_id, or "
            "initial_evidence_reference"
        )
    if source == "open_provisional":
        if state.initial_source_date is not None:
            return prefix + "open_provisional must have no initial_source_date"
        return None
    parent = fmt.parent
    if parent is None or source != f"open_parent:{parent.value}":
        return prefix + "the engine writes no such initial_source in this format"
    if not cfg.category_feeds_parent:
        return prefix + "category_feeds_parent is off"
    base = states.get(parent)
    if base is None:
        return prefix + f"the player has no {parent.value} state"
    if (state.initial_source_date is None) != (base.initial_source_date is None):
        return prefix + (
            f"initial_source_date must be set exactly when the {parent.value} state's is"
        )
    if state.initial_source_date is None and base.matches_played == 0:
        return prefix + f"an undated derived state needs a played {parent.value} state"
    if state.matches_played == 0 and state.rd < cfg.category_seed_rd:
        return prefix + "rd must be at least category_seed_rd while the state is unplayed"
    return None


def _seed_rule_broken(state: RatingState, cfg: Config) -> str | None:
    """The first seed rule a state breaks, or None when it follows every rule."""
    wrong_type = _seed_type_broken(state)
    if wrong_type is not None:
        return wrong_type
    if not _is_id(state.initial_source):
        return "initial_source must not be blank"
    external_id = state.initial_external_player_id
    if external_id is not None and not _is_id(external_id):
        return "initial_external_player_id must not be blank"
    reference = state.initial_evidence_reference
    if reference is not None and not _is_id(reference):
        return "initial_evidence_reference must not be blank"
    if state.matches_played != 0:
        return "matches_played must be 0"
    if state.verified_matches_played != 0:
        return "verified_matches_played must be 0"
    if state.last_played is not None:
        return "last_played must be None"
    if state.partners:
        return "partners must be empty"
    if state.opponents:
        return "opponents must be empty"
    if state.live_updates_since_replay != 0:
        return "live_updates_since_replay must be 0"
    if state.inherited_live_updates != 0:
        return "inherited_live_updates must be 0"
    if not cfg.min_rating <= state.rating <= cfg.max_rating:
        return "rating must be between min_rating and max_rating"
    if not cfg.min_rating <= state.initial_rating <= cfg.max_rating:
        return "initial_rating must be between min_rating and max_rating"
    if state.initial_source_date is None:
        if state.rd != cfg.new_player_rd:
            return "rd must equal new_player_rd for a provisional seed"
        if state.rd_as_of is not None:
            return "rd_as_of must be None for a provisional seed"
        if state.initial_source_reliability is not None:
            return "initial_source_reliability must be None for a provisional seed"
        return None
    seed_rds = (cfg.external_seed_rd, max(cfg.external_seed_rd, cfg.category_seed_rd))
    if state.rd not in seed_rds:
        return "rd must equal external_seed_rd or the category seed RD"
    if state.rd_as_of != state.initial_source_date:
        return "rd_as_of must equal initial_source_date"
    imported = state.initial_source_reliability
    if imported is not None and not 0 <= imported <= 100:
        return "initial_source_reliability must be between 0 and 100"
    return None


def _provenance_linked(state: RatingState, external_ids: Mapping[str, str]) -> bool:
    """True when the state names no external id, or external_ids links it under its source."""
    external_id = state.initial_external_player_id
    return external_id is None or external_ids.get(state.initial_source) == external_id


def _check_seed_date(player: Player, latest: date, limit: str) -> None:
    """Raise InvalidSeedError for a validated seed state dated after latest."""
    for fmt in sorted(player.ratings):
        source_date = player.ratings[fmt].initial_source_date
        if source_date is not None and source_date > latest:
            raise InvalidSeedError(
                f"Seed for {player.id} in {fmt}: initial_source_date must not be after {limit}"
            )


@dataclass
class _HeldIdentities:
    """Accounts and (source, external id) pairs held by players, for seed checks."""

    accounts: set[str] = field(default_factory=set)
    external_ids: set[tuple[str, str]] = field(default_factory=set)

    def add(self, player: Player) -> None:
        """Record the player's account and external identities as held."""
        if player.account_user_id is not None:
            self.accounts.add(player.account_user_id)
        self.external_ids.update(player.external_ids.items())


def _check_seed_identity(player: object) -> None:
    """
    Raise InvalidSeedError unless the seed is exactly a Player with a
    non-blank plain str id (not a subclass).
    """
    # Exactly Player: a subclass can return a different id on a later read.
    if type(player) is not Player:
        raise InvalidSeedError(f"Seed must be a Player, not {_type_name(player)}")
    if not _is_plain_str(player.id):
        raise InvalidSeedError(f"Seed player id must be a str, not {_type_name(player.id)}")
    if not _is_id(player.id):
        raise InvalidSeedError("Seed player id must not be blank")


_SEED_NUMBER_FIELDS = (
    "rating",
    "rd",
    "initial_rating",
    "initial_source_reliability",
    "matches_played",
    "verified_matches_played",
    "live_updates_since_replay",
    "inherited_live_updates",
)


def _plain_seed(player: Player) -> Player:
    """
    A copy of a seed whose rating states hold plain int and float numbers.

    Only a ratings dict that is exactly a dict, and states that are exactly
    RatingState, are copied; anything else is left for _validate_seed to
    reject. The caller's player and states are not changed (issue #107).
    """
    if type(player.ratings) is not dict:
        return player
    # Keys that are not RatingFormat members are not hashed here: a key
    # type's own __hash__ would run. _validate_seed rejects them.
    if not all(_is_member(key, RatingFormat) for key in player.ratings):
        return player
    ratings = {
        fmt: _plain_state(state) if type(state) is RatingState else state
        for fmt, state in player.ratings.items()
    }
    # Fields read from the class, not dataclasses.replace, which reads an
    # instance __dataclass_fields__ that the caller can set.
    copied: dict[str, object] = {f.name: getattr(player, f.name) for f in fields(Player)}
    copied["ratings"] = ratings
    return Player(**copied)  # type: ignore[arg-type]


def _plain_state(state: RatingState) -> RatingState:
    """A copy of a rating state with plain int and float number fields."""
    copied: dict[str, object] = {}
    for f in fields(RatingState):
        value = getattr(state, f.name)
        copied[f.name] = _plain_number(value) if f.name in _SEED_NUMBER_FIELDS else value
    return RatingState(**copied)  # type: ignore[arg-type]


def _validate_seed(player: Player, cfg: Config) -> None:
    """Raise InvalidSeedError unless the seed and every seed state follow the seed rules."""
    _check_seed_identity(player)
    # Exact dict and str: a subclass could hand the checks below, or
    # clone_player, values that differ from what it reports.
    if type(player.ratings) is not dict:
        raise InvalidSeedError(f"Seed for {player.id}: ratings must be a dict")
    if type(player.external_ids) is not dict:
        raise InvalidSeedError(f"Seed for {player.id}: external_ids must be a dict")
    if not all(_is_plain_str(k) and _is_plain_str(v) for k, v in player.external_ids.items()):
        raise InvalidSeedError(f"Seed for {player.id}: external_ids must map str to str")
    if player.account_user_id is not None and not _is_plain_str(player.account_user_id):
        raise InvalidSeedError(f"Seed for {player.id}: account_user_id must be a str or None")
    if player.account_user_id is not None and not _is_id(player.account_user_id):
        raise InvalidSeedError(f"Seed for {player.id}: account_user_id must not be blank")
    if not all(_is_id(k) and _is_id(v) for k, v in player.external_ids.items()):
        raise InvalidSeedError(
            f"Seed for {player.id}: external_ids must not have a blank source or id"
        )
    if any(k.startswith(_RESERVED_SOURCE_PREFIX) for k in player.external_ids):
        raise InvalidSeedError(
            f"Seed for {player.id}: external_ids source must not start with "
            f"{_RESERVED_SOURCE_PREFIX}"
        )
    for key in player.ratings:
        if not _is_member(key, RatingFormat):
            raise InvalidSeedError(
                f"Seed for {player.id}: ratings key {_safe_shown(key)} is not a RatingFormat"
            )
    for fmt in sorted(player.ratings):
        state = player.ratings[fmt]
        # Exactly RatingState: a subclass can return different values on
        # later reads, and _clone_state would keep the subclass.
        if type(state) is not RatingState:
            raise InvalidSeedError(
                f"Seed for {player.id} in {fmt}: rating state must be a RatingState"
            )
        rule = _seed_rule_broken(state, cfg)
        if rule is None:
            rule = _reserved_source_broken(state, fmt, player.ratings, cfg)
        if rule is not None:
            raise InvalidSeedError(f"Seed for {player.id} in {fmt}: {rule}")
        # The seed's own external_ids must hold its provenance pair, so the
        # identity uniqueness check in _add_seed covers it.
        if not _provenance_linked(state, player.external_ids):
            raise InvalidSeedError(
                f"Seed for {player.id} in {fmt}: initial_external_player_id must be linked "
                "in external_ids under initial_source"
            )
