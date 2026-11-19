"""
Snapshot schema: the writers and the readers that check every rule.

Demonstration only: not validated on real match data, outputs are not OPEN
ratings, and the API may change without notice.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from ._config import Config
from ._errors import SnapshotError
from ._ledger import MatchupKey
from ._model import _FORMAT_VALUES, Player, RatingState, ReplayInfo
from ._seeds import (
    _RESERVED_SOURCE_PREFIX,
    _HeldIdentities,
    _provenance_linked,
    _reserved_source_broken,
)
from ._types import RatingFormat, _safe_shown
from ._validation import (
    _is_finite_number,
    _is_id,
    _is_plain_str,
    _is_whole_number,
    _plain_number,
    _type_name,
)

# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

SNAPSHOT_VERSION = 8

_TOP_KEYS = frozenset(
    {
        "snapshot_version",
        "engine_version",
        "config_version",
        "config_fingerprint",
        "calculated_as_of",
        "replay_info",
        "players",
        "processed_match_ids",
        "reserved_match_ids",
        "self_reported_log",
        "content_keys",
        "possible_duplicates",
        "ledger_digests",
        "ledger_player_ids",
        "seed_sensitive_rejections",
        "resumable",
    }
)
_REJECTION_KEYS = frozenset({"rating_format", "players"})
_REPLAY_INFO_KEYS = frozenset(
    {
        "as_of",
        "config_version",
        "config_fingerprint",
        "matches_considered",
        "matches_counted",
        "net_rating_created",
    }
)
_PLAYER_KEYS = frozenset({"id", "account_user_id", "external_ids", "ratings"})
_STATE_KEYS = frozenset(
    {
        "rating",
        "rd",
        "matches_played",
        "verified_matches_played",
        "last_played",
        "rd_as_of",
        "initial_rating",
        "initial_source",
        "initial_source_date",
        "initial_source_reliability",
        "initial_external_player_id",
        "initial_evidence_reference",
        "live_updates_since_replay",
        "inherited_live_updates",
        "partners",
        "opponents",
    }
)
_LOG_KEYS = frozenset({"rating_format", "teams", "dates"})
_HEX = frozenset("0123456789abcdef")


@dataclass
class _Snapshot:
    """Engine state read from a validated snapshot."""

    players: dict[str, Player]
    processed_match_ids: set[str]
    reserved_match_ids: set[str]
    self_reported_log: dict[MatchupKey, list[date]]
    content_keys: dict[str, str]
    possible_duplicates: dict[str, str]
    calculated_as_of: date | None
    replay_info: ReplayInfo | None
    ledger_digests: dict[str, str]
    ledger_player_ids: set[str]
    seed_sensitive_rejections: dict[str, tuple[RatingFormat, tuple[str, ...]]]
    resumable: bool


def _iso(value: object) -> object:
    """A plain date as ISO text. Anything else is left for the validator to reject."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    return value


def _sorted_dict(items: Iterable[tuple[object, object]]) -> dict[object, object]:
    """A dict with keys inserted in sorted order (by their text), so output is stable."""
    return dict(sorted(items, key=lambda item: str(item[0])))


def _date_map_form(value: object) -> object:
    if not isinstance(value, dict):
        return value
    return _sorted_dict((key, _iso(on)) for key, on in value.items())


def _state_form(state: RatingState) -> dict[object, object]:
    items: list[tuple[object, object]] = [
        (name, getattr(state, name)) for name in sorted(_STATE_KEYS)
    ]
    return _sorted_dict(
        (name, _date_map_form(value) if name in ("partners", "opponents") else _iso(value))
        for name, value in items
    )


def _replay_info_value(name: str, value: object) -> object:
    """A ReplayInfo field in snapshot form: format values as keys, dates as ISO text."""
    if name == "net_rating_created" and isinstance(value, Mapping):
        return _sorted_dict(
            (key.value if isinstance(key, RatingFormat) else key, item)
            for key, item in value.items()
        )
    return _iso(value)


def _player_form(player: Player) -> dict[object, object]:
    ratings: object = player.ratings
    if isinstance(ratings, dict):
        ratings = _sorted_dict(
            (fmt.value if isinstance(fmt, RatingFormat) else fmt, _state_form(state))
            for fmt, state in ratings.items()
        )
    external_ids: object = player.external_ids
    if isinstance(external_ids, dict):
        external_ids = _sorted_dict(external_ids.items())
    return _sorted_dict(
        [
            ("account_user_id", player.account_user_id),
            ("external_ids", external_ids),
            ("id", player.id),
            ("ratings", ratings),
        ]
    )


def _log_form(log: dict[MatchupKey, list[date]]) -> list[dict[object, object]]:
    rows: list[tuple[str, list[list[str]], list[object]]] = []
    for (fmt, teams), dates in log.items():
        rows.append(
            (
                str(fmt),
                sorted(sorted(team) for team in teams),
                [_iso(on) for on in dates],
            )
        )
    rows.sort(key=lambda row: (row[0], row[1]))
    return [
        _sorted_dict([("dates", dates), ("rating_format", fmt), ("teams", teams)])
        for fmt, teams, dates in rows
    ]


def _snap_object(value: object, path: str, keys: frozenset[str]) -> Mapping[object, object]:
    """value as exactly a dict with exactly keys; SnapshotError otherwise."""
    if type(value) is not dict:
        raise SnapshotError(f"{path} must be an object")
    _snap_str_keys(value, path)
    missing = sorted(keys - set(value))
    if missing:
        raise SnapshotError(f"{path}: missing key {missing[0]!r}")
    unknown = sorted(repr(key) for key in value if key not in keys)
    if unknown:
        raise SnapshotError(f"{path}: unknown key {unknown[0]}")
    return value


def _snap_str_keys(value: dict[object, object], path: str) -> None:
    """
    Raise SnapshotError unless every key is exactly a str.

    Checked before any set operation or lookup on value, since a key type's
    own __hash__ and __eq__ would run in both.
    """
    for key in value:
        if type(key) is not str:
            raise SnapshotError(f"{path} has a key that is not a str: {_type_name(key)}")


def _snap_list(value: object, path: str) -> Sequence[object]:
    """value as exactly a list or tuple; SnapshotError otherwise."""
    if type(value) is not list and type(value) is not tuple:
        raise SnapshotError(f"{path} must be a list")
    return value


def _snap_str(value: object, path: str) -> str:
    if not _is_plain_str(value):
        raise SnapshotError(f"{path} must be a str")
    return value


def _snap_opt_str(value: object, path: str) -> str | None:
    if value is not None and not _is_plain_str(value):
        raise SnapshotError(f"{path} must be a str or null")
    return value


def _snap_id(value: object, path: str) -> str:
    if not _is_id(value):
        raise SnapshotError(f"{path} must be a non-blank str")
    return value


def _snap_date(value: object, path: str) -> date:
    """A date written as exact YYYY-MM-DD text."""
    message = f"{path} must be a date in YYYY-MM-DD form"
    if not _is_plain_str(value):
        raise SnapshotError(message)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise SnapshotError(message) from None
    if parsed.isoformat() != value:
        raise SnapshotError(message)
    return parsed


def _snap_opt_date(value: object, path: str) -> date | None:
    return None if value is None else _snap_date(value, path)


def _snap_number(value: object, path: str) -> float:
    value = _plain_number(value)
    if isinstance(value, (int, float)) and _is_finite_number(value):
        return value
    raise SnapshotError(f"{path} must be a finite number (an int or float, not bool)")


# The largest count a snapshot holds. json.dumps writes any int up to it.
_MAX_COUNT = 2**63 - 1


def _snap_count(value: object, path: str) -> int:
    count = _plain_number(value)
    if not _is_whole_number(count) or not 0 <= count <= _MAX_COUNT:
        raise SnapshotError(f"{path} must be a nonnegative int no larger than 2**63 - 1")
    return count


def _snap_format(value: object, path: str) -> RatingFormat:
    if not _is_plain_str(value) or value not in {fmt.value for fmt in RatingFormat}:
        raise SnapshotError(f"{path} {_safe_shown(value)} is not a RatingFormat")
    return RatingFormat(value)


def _snap_date_map(value: object, path: str) -> dict[str, date]:
    if type(value) is not dict:
        raise SnapshotError(f"{path} must be an object")
    result: dict[str, date] = {}
    for key, on in value.items():
        if not _is_id(key):
            raise SnapshotError(f"{path} has an id that is not a non-blank str")
        result[key] = _snap_date(on, f"{path}[{key!r}]")
    return result


def _snap_state(value: object, path: str, player_id: str, cfg: Config) -> RatingState:
    """A rating state with the right types, checked against the engine's invariants."""
    state = _snap_state_fields(value, path, cfg)
    _check_state_invariants(state, path, player_id, cfg)
    return state


def _check_state_invariants(state: RatingState, path: str, player_id: str, cfg: Config) -> None:
    """
    Raise SnapshotError for a state the engine cannot produce.

    Only an engine-made state may load, because a loaded state sets the
    rating and the reliability shown for it: a low RD with no matches
    would show earned reliability nobody earned, and a played state
    relabeled unplayed would let an external seed replace an OPEN-earned
    rating.
    """
    if not cfg.min_rating <= state.initial_rating <= cfg.max_rating:
        raise SnapshotError(f"{path}.initial_rating must be between min_rating and max_rating")
    if (state.matches_played == 0) != (state.last_played is None):
        raise SnapshotError(f"{path}: matches_played must be 0 exactly when last_played is null")
    if state.live_updates_since_replay > state.matches_played:
        raise SnapshotError(f"{path}.live_updates_since_replay must not exceed matches_played")
    if state.verified_matches_played > state.matches_played:
        raise SnapshotError(f"{path}.verified_matches_played must not exceed matches_played")
    if player_id in state.partners:
        raise SnapshotError(f"{path}.partners: a player cannot be their own partner")
    if player_id in state.opponents:
        raise SnapshotError(f"{path}.opponents: a player cannot be their own opponent")
    if state.initial_source_reliability is not None and state.initial_source_date is None:
        raise SnapshotError(f"{path}.initial_source_reliability needs an initial_source_date")
    if state.matches_played:
        return
    if state.partners:
        raise SnapshotError(f"{path}: an unplayed state must have no partners")
    if state.opponents:
        raise SnapshotError(f"{path}: an unplayed state must have no opponents")
    # A seed or new state has one of these RDs; a category state derived
    # from its parent has max(parent RD, category_seed_rd). Only a counted
    # match lowers RD from there.
    if state.rd not in (cfg.new_player_rd, cfg.external_seed_rd) and (
        state.rd < cfg.category_seed_rd
    ):
        raise SnapshotError(
            f"{path}: rd of an unplayed state must be new_player_rd, external_seed_rd, "
            "or at least category_seed_rd"
        )


def _snap_state_fields(value: object, path: str, cfg: Config) -> RatingState:
    raw = _snap_object(value, path, _STATE_KEYS)
    rating = _snap_number(raw["rating"], f"{path}.rating")
    if not cfg.min_rating <= rating <= cfg.max_rating:
        raise SnapshotError(f"{path}.rating must be between min_rating and max_rating")
    rd = _snap_number(raw["rd"], f"{path}.rd")
    if not cfg.rd_min <= rd <= cfg.rd_max:
        raise SnapshotError(f"{path}.rd must be between rd_min and rd_max")
    imported = _plain_number(raw["initial_source_reliability"])
    if imported is not None:
        if not isinstance(imported, (int, float)) or not _is_finite_number(imported):
            raise SnapshotError(
                f"{path}.initial_source_reliability must be a finite number or null"
            )
        if not 0 <= imported <= 100:
            raise SnapshotError(f"{path}.initial_source_reliability must be between 0 and 100")
    return RatingState(
        rating=rating,
        rd=rd,
        matches_played=_snap_count(raw["matches_played"], f"{path}.matches_played"),
        verified_matches_played=_snap_count(
            raw["verified_matches_played"], f"{path}.verified_matches_played"
        ),
        last_played=_snap_opt_date(raw["last_played"], f"{path}.last_played"),
        rd_as_of=_snap_opt_date(raw["rd_as_of"], f"{path}.rd_as_of"),
        initial_rating=_snap_number(raw["initial_rating"], f"{path}.initial_rating"),
        initial_source=_snap_str(raw["initial_source"], f"{path}.initial_source"),
        initial_source_date=_snap_opt_date(
            raw["initial_source_date"], f"{path}.initial_source_date"
        ),
        initial_source_reliability=imported,
        initial_external_player_id=_snap_opt_str(
            raw["initial_external_player_id"], f"{path}.initial_external_player_id"
        ),
        initial_evidence_reference=_snap_opt_str(
            raw["initial_evidence_reference"], f"{path}.initial_evidence_reference"
        ),
        live_updates_since_replay=_snap_count(
            raw["live_updates_since_replay"], f"{path}.live_updates_since_replay"
        ),
        inherited_live_updates=_snap_count(
            raw["inherited_live_updates"], f"{path}.inherited_live_updates"
        ),
        partners=_snap_date_map(raw["partners"], f"{path}.partners"),
        opponents=_snap_date_map(raw["opponents"], f"{path}.opponents"),
    )


def _snap_player(value: object, path: str, cfg: Config) -> Player:
    raw = _snap_object(value, path, _PLAYER_KEYS)
    player_id = _snap_id(raw["id"], f"{path}.id")
    external_raw = raw["external_ids"]
    if type(external_raw) is not dict:
        raise SnapshotError(f"{path}.external_ids must map str to str")
    external_ids: dict[str, str] = {}
    for source, external_id in external_raw.items():
        if not _is_plain_str(source) or not _is_plain_str(external_id):
            raise SnapshotError(f"{path}.external_ids must map str to str")
        if not _is_id(source) or not _is_id(external_id):
            raise SnapshotError(f"{path}.external_ids must map non-blank str to non-blank str")
        if source.startswith(_RESERVED_SOURCE_PREFIX):
            raise SnapshotError(
                f"{path}.external_ids source must not start with {_RESERVED_SOURCE_PREFIX}"
            )
        external_ids[source] = external_id
    account_user_id = _snap_opt_str(raw["account_user_id"], f"{path}.account_user_id")
    if account_user_id is not None and not _is_id(account_user_id):
        raise SnapshotError(f"{path}.account_user_id must be null or a non-blank str")
    ratings = raw["ratings"]
    if type(ratings) is not dict:
        raise SnapshotError(f"{path}.ratings must be an object")
    states: dict[RatingFormat, RatingState] = {}
    for key, state in ratings.items():
        fmt = _snap_format(key, f"{path}.ratings key")
        states[fmt] = _snap_state(state, f"{path}.ratings.{fmt.value}", player_id, cfg)
    _check_inherited_live_updates(states, path, cfg)
    _check_provenance(states, external_ids, path, cfg)
    return Player(
        id=player_id,
        ratings=states,
        external_ids=external_ids,
        account_user_id=account_user_id,
    )


def _check_provenance(
    states: Mapping[RatingFormat, RatingState],
    external_ids: Mapping[str, str],
    path: str,
    cfg: Config,
) -> None:
    """
    Raise SnapshotError for a state's provenance the engine cannot produce.

    Every initial_source and initial_evidence_reference the engine writes is
    non-blank. Every entry point rejects an external source that starts with
    the reserved prefix, and a state's reserved initial_source must pass
    _reserved_source_broken under the snapshot's config, which checks the
    name, the import-only fields, and, for "open_parent:", the rules of
    how _new_state derives a category state. It does not check the rating
    or the date against the parent state's, so a state that passes can
    still be one a caller built or an older version imported. A state that names
    an external id comes in through seed_external_rating, which links the
    (source, id) pair to the same player, or through a seed (add_player,
    replay seeds, resume), which must already list the pair in its own
    external_ids. No engine method unlinks an id, so the pair stays in the
    player's external_ids. Every entry point requires the seed, its
    states, an external snapshot, and the ids and provenance strs checked
    here to be exact types (not subclasses), so a subclass of one of them
    cannot pass these provenance checks there and then load as different
    values. Number fields can hold an int or float subclass, but every
    entry point checks and stores them as plain int or float values
    (issue #107).
    """
    for fmt in sorted(states, key=lambda f: f.value):
        state = states[fmt]
        state_path = f"{path}.ratings.{fmt.value}"
        if not _is_id(state.initial_source):
            raise SnapshotError(f"{state_path}.initial_source must not be blank")
        external_id = state.initial_external_player_id
        if external_id is not None and not _is_id(external_id):
            raise SnapshotError(
                f"{state_path}.initial_external_player_id must be null or not blank"
            )
        reference = state.initial_evidence_reference
        if reference is not None and not _is_id(reference):
            raise SnapshotError(
                f"{state_path}.initial_evidence_reference must be null or not blank"
            )
        reserved = _reserved_source_broken(state, fmt, states, cfg)
        if reserved is not None:
            raise SnapshotError(f"{state_path}.{reserved}")
        if not _provenance_linked(state, external_ids):
            raise SnapshotError(
                f"{state_path}.initial_external_player_id must be linked in "
                f"{path}.external_ids under initial_source"
            )


def _check_inherited_live_updates(
    states: Mapping[RatingFormat, RatingState], path: str, cfg: Config
) -> None:
    """
    Raise SnapshotError for an inherited live count no engine release produces.

    Only a category state derived from its parent inherits a count, and the
    parent's own count only grows until the next replay, so it is never
    below what the category inherited. A derived state takes the parent's
    initial_source_date and no other provenance, and a parent with live
    updates has been played, so it can no longer be reseeded: a state with
    a nonzero count still has its parent's initial_source_date. Until it
    plays, it also keeps the RD it was derived with, max(parent RD,
    category_seed_rd), and the parent's rd_as_of at derivation, which a
    played parent has set and never moves back. This engine no longer
    creates an unplayed state with a nonzero count (issue #112): only an
    earlier release's seed_provisional_rating did. The unplayed rules
    accept such a state as that release stored it, so its snapshots still
    load.
    """
    for fmt in sorted(states, key=lambda f: f.value):
        inherited = states[fmt].inherited_live_updates
        if not inherited:
            continue
        state_path = f"{path}.ratings.{fmt.value}.inherited_live_updates"
        parent = fmt.parent
        if (
            parent is None
            or not cfg.category_feeds_parent
            or states[fmt].initial_source != f"open_parent:{parent.value}"
        ):
            raise SnapshotError(
                f"{state_path} must be 0 unless the state is derived from its parent"
            )
        base = states.get(parent)
        if base is None:
            raise SnapshotError(f"{state_path} needs a {parent.value} state, which is missing")
        if inherited > base.live_updates_since_replay:
            raise SnapshotError(
                f"{state_path} must not exceed the parent's live_updates_since_replay"
            )
        state = states[fmt]
        if (
            state.initial_source_reliability is not None
            or state.initial_external_player_id is not None
            or state.initial_evidence_reference is not None
        ):
            raise SnapshotError(
                f"{path}.ratings.{fmt.value}: a state with inherited_live_updates must have "
                "no initial_source_reliability, initial_external_player_id, or "
                "initial_evidence_reference"
            )
        if state.initial_source_date != base.initial_source_date:
            raise SnapshotError(
                f"{path}.ratings.{fmt.value}.initial_source_date must equal the parent "
                "state's initial_source_date when inherited_live_updates is nonzero"
            )
        if state.matches_played:
            continue
        state_path = f"{path}.ratings.{fmt.value}"
        if state.rd < cfg.category_seed_rd:
            raise SnapshotError(
                f"{state_path}.rd must be at least category_seed_rd for an unplayed state "
                "with inherited_live_updates"
            )
        if state.rd_as_of is None:
            raise SnapshotError(
                f"{state_path}.rd_as_of must be set for an unplayed state with "
                "inherited_live_updates"
            )
        if base.rd_as_of is None or state.rd_as_of > base.rd_as_of:
            raise SnapshotError(
                f"{state_path}.rd_as_of must not be after the parent state's rd_as_of for an "
                "unplayed state with inherited_live_updates"
            )


def _check_resumable_dates(snap: _Snapshot, paths: Mapping[str, str], as_of: date) -> None:
    """
    _check_resumable's first pass: nothing after as_of, and the per-player rules.

    For each player in order: a player with a played state is in
    ledger_player_ids; then, per state, initial_source_date, last_played,
    and rd_as_of are not after as_of, inherited_live_updates is 0, and no
    partner or opponent date is after as_of. Then no self_reported_log
    date is after as_of.
    """

    def check(on: date | None, path: str) -> None:
        if on is not None and on > as_of:
            raise SnapshotError(f"{path} is after calculated_as_of in a resumable snapshot")

    for player_id, player in snap.players.items():
        played = any(state.matches_played for state in player.ratings.values())
        if played and player_id not in snap.ledger_player_ids:
            raise SnapshotError(
                f"{paths[player_id]}.id {player_id!r} has played matches but is not in "
                "ledger_player_ids in a resumable snapshot"
            )
        for fmt, state in player.ratings.items():
            path = f"{paths[player_id]}.ratings.{fmt.value}"
            check(state.initial_source_date, f"{path}.initial_source_date")
            check(state.last_played, f"{path}.last_played")
            check(state.rd_as_of, f"{path}.rd_as_of")
            if state.inherited_live_updates:
                raise SnapshotError(
                    f"{path}.inherited_live_updates must be 0 in a resumable snapshot"
                )
            for name, seen in (("partners", state.partners), ("opponents", state.opponents)):
                for other, on in seen.items():
                    check(on, f"{path}.{name}[{other!r}]")
    for index, dates in enumerate(snap.self_reported_log.values()):
        for position, on in enumerate(dates):
            check(on, f"snapshot.self_reported_log[{index}].dates[{position}]")


def _check_resumable(snap: _Snapshot, paths: Mapping[str, str]) -> None:
    """
    Raise SnapshotError unless a resumable snapshot is consistent with its replay.

    A replay never records anything dated after its as_of, and it considers
    and counts exactly the rows behind its digests and processed ids. A
    live engine can hold later dates, so only resumable snapshots are held
    to this.
    """
    as_of = snap.calculated_as_of
    info = snap.replay_info
    if as_of is None or info is None:
        raise SnapshotError("snapshot.resumable must be false when calculated_as_of is null")

    _check_resumable_dates(snap, paths, as_of)
    for player_id, player in snap.players.items():
        for fmt in sorted(player.ratings, key=lambda f: f.value):
            state = player.ratings[fmt]
            source_date = state.initial_source_date
            if not state.matches_played or source_date is None:
                continue
            # A replay's seeds precede every row, and a row on or before a
            # state's initial_source_date freezes that state, so a moved
            # dated state was last played after its date.
            if state.last_played is None or state.last_played <= source_date:
                raise SnapshotError(
                    f"{paths[player_id]}.ratings.{fmt.value}.last_played must be after "
                    "initial_source_date in a resumable snapshot"
                )
            # Partners and opponents are written only on a move, so they
            # too are dated after the state's initial_source_date.
            path = f"{paths[player_id]}.ratings.{fmt.value}"
            for name, seen in (("partners", state.partners), ("opponents", state.opponents)):
                for other_id in sorted(seen):
                    if seen[other_id] <= source_date:
                        raise SnapshotError(
                            f"{path}.{name}[{other_id!r}] must be after initial_source_date "
                            "in a resumable snapshot"
                        )
    for player_id, player in snap.players.items():
        for fmt in sorted(player.ratings, key=lambda f: f.value):
            if player.ratings[fmt].live_updates_since_replay:
                raise SnapshotError(
                    f"{paths[player_id]}.ratings.{fmt.value}.live_updates_since_replay "
                    "must be 0 in a resumable snapshot"
                )
    if info.matches_considered != len(snap.ledger_digests):
        raise SnapshotError(
            "snapshot.replay_info.matches_considered must equal the number of "
            "ledger_digests in a resumable snapshot"
        )
    if info.matches_counted != len(snap.processed_match_ids):
        raise SnapshotError(
            "snapshot.replay_info.matches_counted must equal the number of "
            "processed_match_ids in a resumable snapshot"
        )
    undigested = sorted(snap.processed_match_ids - snap.ledger_digests.keys())
    if undigested:
        raise SnapshotError(
            f"snapshot.processed_match_ids names {undigested[0]!r}, which is not in "
            "ledger_digests in a resumable snapshot"
        )


def _snap_log_entry(value: object, path: str) -> tuple[MatchupKey, list[date]]:
    raw = _snap_object(value, path, _LOG_KEYS)
    fmt = _snap_format(raw["rating_format"], f"{path}.rating_format")
    teams = _snap_list(raw["teams"], f"{path}.teams")
    if len(teams) != 2:
        raise SnapshotError(f"{path}.teams must be two teams")
    members: list[frozenset[str]] = []
    for index, team in enumerate(teams):
        team_path = f"{path}.teams[{index}]"
        message = f"{team_path} must be a team of {fmt.team_size} player id(s)"
        if (type(team) is not list and type(team) is not tuple) or len(team) != fmt.team_size:
            raise SnapshotError(message)
        if not all(_is_id(pid) for pid in team):
            raise SnapshotError(message)
        ids = frozenset(str(pid) for pid in team)
        if len(ids) != fmt.team_size:
            raise SnapshotError(message)
        members.append(ids)
    if members[0] & members[1]:
        raise SnapshotError(f"{path}.teams has a player in both teams")
    dates = raw["dates"]
    if (type(dates) is not list and type(dates) is not tuple) or not dates:
        raise SnapshotError(f"{path}.dates must be a non-empty list")
    return (
        (fmt, frozenset(members)),
        [_snap_date(on, f"{path}.dates[{index}]") for index, on in enumerate(dates)],
    )


def _snap_replay_info(
    value: object, calculated_as_of: date | None, version: str, fingerprint: str
) -> ReplayInfo | None:
    path = "snapshot.replay_info"
    if calculated_as_of is None:
        if value is not None:
            raise SnapshotError(f"{path} must be null when calculated_as_of is null")
        return None
    if value is None:
        raise SnapshotError(f"{path} is required when calculated_as_of is set")
    raw = _snap_object(value, path, _REPLAY_INFO_KEYS)
    if _snap_date(raw["as_of"], f"{path}.as_of") != calculated_as_of:
        raise SnapshotError(f"{path}.as_of must equal calculated_as_of")
    if raw["config_version"] != version:
        raise SnapshotError(f"{path}.config_version must equal config_version")
    if raw["config_fingerprint"] != fingerprint:
        raise SnapshotError(f"{path}.config_fingerprint must equal config_fingerprint")
    considered = _snap_count(raw["matches_considered"], f"{path}.matches_considered")
    counted = _snap_count(raw["matches_counted"], f"{path}.matches_counted")
    if counted > considered:
        raise SnapshotError(f"{path}.matches_counted must not exceed matches_considered")
    created_path = f"{path}.net_rating_created"
    created_raw = _snap_object(raw["net_rating_created"], created_path, _FORMAT_VALUES)
    created: dict[RatingFormat, float] = {}
    for fmt in RatingFormat:
        value = created_raw[fmt.value]
        # A float only: a resume must continue from the exact float a full
        # replay had, and an int would change its type.
        if type(value) is not float or not math.isfinite(value):
            raise SnapshotError(f"{created_path}.{fmt.value} must be a finite float")
        if value == 0.0 and math.copysign(1.0, value) < 0:
            # Totals start at 0.0 and every term adds 0.0, so replay never
            # writes -0.0, and a resume from it would write different text.
            raise SnapshotError(f"{created_path}.{fmt.value} must not be a negative zero")
        if counted == 0 and value != 0.0:
            raise SnapshotError(f"{created_path}.{fmt.value} must be 0.0 when matches_counted is 0")
        created[fmt] = value
    return ReplayInfo(calculated_as_of, version, fingerprint, considered, counted, created)


def _check_net_rating_created(info: ReplayInfo | None, players: Mapping[str, Player]) -> None:
    """
    Raise SnapshotError for a nonzero total in a format nobody has played.

    A counted update moves at least one player who is not frozen, and a
    played state is never removed or replaced, so a nonzero total needs a
    played state in its format.
    """
    if info is None:
        return
    for fmt in RatingFormat:
        if info.net_rating_created[fmt] == 0.0:
            continue
        if not any(
            fmt in player.ratings and player.ratings[fmt].matches_played
            for player in players.values()
        ):
            raise SnapshotError(
                f"snapshot.replay_info.net_rating_created.{fmt.value} must be 0.0 when no "
                f"player has a played {fmt.value} state"
            )


def _snap_content_keys(value: object, processed: set[str]) -> dict[str, str]:
    """
    Content keys read from a snapshot: digest to the match_id first counted with it.

    Each key is 64 lowercase hex characters, each id a processed match, and
    no id appears twice, since a match has one content key.
    """
    if type(value) is not dict:
        raise SnapshotError("snapshot.content_keys must be an object")
    keys: dict[str, str] = {}
    owners: set[str] = set()
    for key, match_id in value.items():
        if not _is_plain_str(key) or len(key) != 64 or not set(key) <= _HEX:
            raise SnapshotError(
                "snapshot.content_keys has a key that is not 64 lowercase hex characters: "
                f"{_safe_shown(key)}"
            )
        path = f"snapshot.content_keys[{key!r}]"
        match_id = _snap_id(match_id, path)
        if match_id not in processed:
            raise SnapshotError(f"{path} names {match_id!r}, which is not in processed_match_ids")
        if match_id in owners:
            raise SnapshotError(f"snapshot.content_keys maps more than one key to {match_id!r}")
        owners.add(match_id)
        keys[key] = match_id
    return keys


def _snap_possible_duplicates(
    value: object,
    twins: set[str],
    processed: set[str],
    reject: bool,
    considered: Mapping[str, str] | None,
) -> dict[str, str]:
    """
    Possible duplicates read from a snapshot: copy match_id to the match it repeats.

    Each id is a non-blank str, a copy never names itself, and each twin is
    a content_keys value (twins). A copy is a processed match id under the
    default config, which counts it, and is not one under
    reject_content_duplicates, which does not. A copy never registers its
    own content, so it is never a content_keys value. considered is the
    ledger digests of a resumable snapshot, else None: a replay records a
    copy only for a row it considered, so each copy must be one of them.
    """
    if type(value) is not dict:
        raise SnapshotError("snapshot.possible_duplicates must be an object")
    duplicates: dict[str, str] = {}
    for key, twin in value.items():
        if not _is_id(key):
            raise SnapshotError(
                "snapshot.possible_duplicates has a key that is not a non-blank str"
            )
        path = f"snapshot.possible_duplicates[{key!r}]"
        twin = _snap_id(twin, path)
        if twin == key:
            raise SnapshotError(f"{path} names the match itself")
        if twin not in twins:
            raise SnapshotError(f"{path} names {twin!r}, which is not a content_keys value")
        where = "snapshot.possible_duplicates has key"
        if reject and key in processed:
            raise SnapshotError(
                f"{where} {key!r}, which is in processed_match_ids; under "
                "reject_content_duplicates a possible duplicate is never counted"
            )
        if not reject and key not in processed:
            raise SnapshotError(
                f"{where} {key!r}, which is not in processed_match_ids; under the "
                "default config a possible duplicate is counted"
            )
        if key in twins:
            raise SnapshotError(f"{where} {key!r}, which is a content_keys value")
        if considered is not None and key not in considered:
            raise SnapshotError(f"{where} {key!r}, which is not in ledger_digests")
        duplicates[key] = twin
    return duplicates


def _baseline_date(player: Player | None, fmt: RatingFormat, cfg: Config) -> date | None:
    """
    The date on or before which a fmt match would freeze this player, or None.

    That is a stored fmt state's initial_source_date, or, with none
    stored, under category_feeds_parent in a category format, the parent
    state's: the state a category match starts from carries the parent's
    date (see RatingEngine._new_state).
    """
    if player is None:
        return None
    state = player.ratings.get(fmt)
    if state is not None:
        return state.initial_source_date
    parent = fmt.parent
    if parent is None or not cfg.category_feeds_parent:
        return None
    base = player.ratings.get(parent)
    return None if base is None else base.initial_source_date


def _has_dated_baseline(player: Player | None, fmt: RatingFormat, cfg: Config) -> bool:
    """True when a fmt match would freeze this player for a date on or before an import."""
    return _baseline_date(player, fmt, cfg) is not None


def _names_category_match(named: Sequence[str], players: Mapping[str, Player]) -> bool:
    """
    True when named could be the players of a counted category match.

    A counted match moves at least one player, and that player's played
    state in the match's category records one other player as a partner
    and the other two as opponents. Those maps are never pruned. Not every
    player needs a state there: a frozen player can be frozen through a
    category state derived from overall doubles that was never stored.
    """
    for pid in named:
        player = players[pid]
        others = set(named) - {pid}
        for fmt in RatingFormat:
            if fmt.parent is not RatingFormat.DOUBLES:
                continue
            state = player.ratings.get(fmt)
            if state is None or state.matches_played == 0:
                continue
            # The maps span every match the state played, so a named
            # player can be in both: try each one as the partner.
            for partner in sorted(others & set(state.partners)):
                if others - {partner} <= set(state.opponents):
                    return True
    return False


def _check_rejection_key(
    key: str,
    path: str,
    fmt: RatingFormat,
    named: list[str],
    players: Mapping[str, Player],
    processed: set[str],
    reserved: set[str],
    duplicates: Mapping[str, str],
    cfg: Config,
) -> None:
    """
    Raise SnapshotError unless a rejection entry's key fits the processed and reserved ids.

    _snap_seed_sensitive_rejections's key checks, after the entry's players.
    A processed key needs, in order: a doubles entry, category_feeds_parent,
    every named player in players, and a named player whose played
    category state has one other named player as a partner and the other
    two as opponents (_names_category_match). A reserved key must not be a
    possible_duplicates key. Any other key is rejected.
    """
    if key in processed:
        if fmt is not RatingFormat.DOUBLES:
            raise SnapshotError(
                f"{path} is a processed match id, so its rating_format must be doubles: "
                "a counted match records only a rejected overall doubles update"
            )
        if not cfg.category_feeds_parent:
            raise SnapshotError(
                f"{path} is a processed match id, which needs category_feeds_parent: "
                "only then does a counted match send a doubles update"
            )
        for pid in named:
            if pid not in players:
                raise SnapshotError(f"{path}.players names {pid!r}, which is not in players")
        if not _names_category_match(named, players):
            raise SnapshotError(
                f"{path} names no player with a played category state that has one "
                "other named player as a partner and the other two as opponents, as a "
                "counted category match leaves"
            )
    elif key in reserved:
        if key in duplicates:
            raise SnapshotError(
                f"{path} is a possible_duplicates key; a possible duplicate is rejected "
                "before any rating is read"
            )
    else:
        raise SnapshotError(
            f"snapshot.seed_sensitive_rejections has key {key!r}, which is in neither "
            "processed_match_ids nor reserved_match_ids"
        )


def _snap_seed_sensitive_rejections(
    value: object,
    players: Mapping[str, Player],
    processed: set[str],
    reserved: set[str],
    duplicates: Mapping[str, str],
    ledger_player_ids: set[str] | None,
    cfg: Config,
) -> dict[str, tuple[RatingFormat, tuple[str, ...]]]:
    """
    Seed-sensitive rejections read from a snapshot: match_id to (format, sorted player ids).

    record adds an entry only for an update rejected for
    BEFORE_IMPORTED_BASELINE, SINGLES_RATING_GAP, or DOUBLES_PARTNER_GAP,
    naming every player of the match. A match's own rejected update
    reserves its id. A counted match's rejected update is its overall
    doubles update under category_feeds_parent, and every player of a
    counted match has a player record (a frozen one already had a state).
    A possible duplicate is rejected before any rating is read, so a
    reserved one is never an entry. With no gap rule for the format, only
    BEFORE_IMPORTED_BASELINE can add an entry, which needs every player
    frozen, and the seed methods refuse to replace those states, so each
    named player still has a dated baseline there (_has_dated_baseline).
    ledger_player_ids is that set in a resumable snapshot, else None: a
    replay names only players in rows it considered.
    """
    if type(value) is not dict:
        raise SnapshotError("snapshot.seed_sensitive_rejections must be an object")
    rejections: dict[str, tuple[RatingFormat, tuple[str, ...]]] = {}
    for key, entry in value.items():
        if not _is_id(key):
            raise SnapshotError(
                "snapshot.seed_sensitive_rejections has a key that is not a non-blank str"
            )
        path = f"snapshot.seed_sensitive_rejections[{key!r}]"
        raw = _snap_object(entry, path, _REJECTION_KEYS)
        fmt_value = raw["rating_format"]
        if not _is_plain_str(fmt_value) or fmt_value not in _FORMAT_VALUES:
            raise SnapshotError(f"{path}.rating_format must be a RatingFormat value")
        fmt = RatingFormat(fmt_value)
        named = [
            _snap_id(item, f"{path}.players[{index}]")
            for index, item in enumerate(_snap_list(raw["players"], f"{path}.players"))
        ]
        if len(named) != 2 * fmt.team_size:
            raise SnapshotError(
                f"{path}.players must name {2 * fmt.team_size} players for {fmt.value}"
            )
        if named != sorted(set(named)):
            raise SnapshotError(f"{path}.players must be sorted with no repeats")
        _check_rejection_key(key, path, fmt, named, players, processed, reserved, duplicates, cfg)
        gap = (
            cfg.max_singles_rating_gap
            if fmt is RatingFormat.SINGLES
            else (cfg.max_doubles_partner_gap)
        )
        if gap is None:
            for pid in named:
                if not _has_dated_baseline(players.get(pid), fmt, cfg):
                    raise SnapshotError(
                        f"{path}.players names {pid!r}, which has no dated {fmt.value} "
                        f"baseline; with no gap rule for {fmt.value}, only "
                        "before_imported_baseline is recorded, and it freezes every player"
                    )
        if ledger_player_ids is not None:
            for pid in named:
                if pid not in ledger_player_ids:
                    raise SnapshotError(
                        f"{path}.players names {pid!r}, which is not in ledger_player_ids "
                        "in a resumable snapshot"
                    )
        rejections[key] = (fmt, tuple(named))
    return rejections


def _check_reserved(
    reserved: set[str],
    processed: set[str],
    duplicates: Mapping[str, str],
    reject: bool,
    digests: Mapping[str, str],
    considered: Mapping[str, str] | None,
) -> None:
    """
    Raise SnapshotError unless the reserved match ids fit the rest of the snapshot.

    record checks both sets before it adds an id to either, so no reserved
    id is processed. record reserves the id of every match it considers and
    does not count, so under reject_content_duplicates, which never counts a
    possible duplicate, every possible_duplicates key is reserved. digests
    is the snapshot's ledger digests: a replay counts or reserves every row
    it digests, so each digest id is processed or reserved in any snapshot.
    considered is the ledger digests of a resumable snapshot, else None: a replay
    reserves only a row it considered, so there each reserved id is a
    ledger_digests id.
    """
    where = "snapshot.reserved_match_ids names"
    for match_id in sorted(reserved):
        if match_id in processed:
            raise SnapshotError(f"{where} {match_id!r}, which is in processed_match_ids")
        if considered is not None and match_id not in considered:
            raise SnapshotError(f"{where} {match_id!r}, which is not in ledger_digests")
    if reject:
        for match_id in sorted(duplicates):
            if match_id not in reserved:
                raise SnapshotError(
                    f"snapshot.possible_duplicates has key {match_id!r}, which is not in "
                    "reserved_match_ids; under reject_content_duplicates a rejected "
                    "possible duplicate reserves its match_id"
                )
    # A replay counts or reserves every row it digests, and no id ever
    # leaves either set, live or not.
    for match_id in sorted(digests):
        if match_id not in processed and match_id not in reserved:
            raise SnapshotError(
                f"snapshot.ledger_digests names {match_id!r}, which is in neither "
                "processed_match_ids nor reserved_match_ids"
            )


def _snap_match_ids(value: object, path: str) -> set[str]:
    """A list of non-blank str match ids with no repeats, read as a set."""
    ids: set[str] = set()
    for index, item in enumerate(_snap_list(value, path)):
        item_path = f"{path}[{index}]"
        match_id = _snap_id(item, item_path)
        if match_id in ids:
            raise SnapshotError(f"{item_path}: duplicate match id {match_id!r}")
        ids.add(match_id)
    return ids


def _check_held_identities(players: Mapping[str, Player], paths: Mapping[str, str]) -> None:
    """
    Raise SnapshotError when two players hold the same account or external id.

    The seed and link methods give each identity to one player, so a
    snapshot names the later holder in player order.
    """
    held = _HeldIdentities()
    for player_id, player in players.items():
        path = paths[player_id]
        if player.account_user_id is not None and player.account_user_id in held.accounts:
            raise SnapshotError(f"{path}.account_user_id is held by another player")
        for source in sorted(player.external_ids):
            if (source, player.external_ids[source]) in held.external_ids:
                raise SnapshotError(f"{path}.external_ids[{source!r}] is held by another player")
        held.add(player)


def _check_named_players(players: Mapping[str, Player], paths: Mapping[str, str]) -> None:
    """
    Raise SnapshotError for a partner or opponent that is not a player in the snapshot.

    record creates every match player before it writes partners and
    opponents, and no method removes a player.
    """
    for player_id, player in players.items():
        for fmt in sorted(player.ratings, key=lambda f: f.value):
            state = player.ratings[fmt]
            path = f"{paths[player_id]}.ratings.{fmt.value}"
            for name, seen in (("partners", state.partners), ("opponents", state.opponents)):
                for other in sorted(seen):
                    if other not in players:
                        raise SnapshotError(
                            f"{path}.{name} names {other!r}, which is not in players"
                        )


def _check_log_players(
    players: Mapping[str, Player],
    log: Sequence[tuple[MatchupKey, list[date]]],
    cfg: Config,
) -> None:
    """
    Raise SnapshotError for a log entry its named players' states rule out.

    record appends a date to the log, under the match's own format, only
    when that format's update counts. In that update a named player is
    frozen exactly when the state it starts from has an
    initial_source_date on or after the date: the stored state in the
    format, or, with none stored, under category_feeds_parent in a
    category format, the one derived from the stored doubles state, which
    carries its date (_baseline_date). Every other named player moves, and
    at least one does, or the update would not count. A moved player's
    stored state in the format is played and records every teammate in
    partners and every player of the other team in opponents, each dated
    on or after the match (_seen keeps the latest date). Once a counted
    match names a player, that baseline does not change and those link
    dates never move back (a later match can move them later): a played
    state is never reseeded, and a frozen player's baseline is found
    through the movers' links (RatingEngine._in_counted_match). So
    for an entry's latest date, every named player has a baseline on or
    after it or a played state last played on or after it with those
    links, and some named player has
    no such baseline. Entries are checked in snapshot order, and within an
    entry every named player's state first, then the movers' links, each
    in sorted order, so the first error is the same for the same snapshot.
    """
    for index, ((fmt, teams), dates) in enumerate(log):
        last = max(dates)
        on = last.isoformat()
        where = f"snapshot.self_reported_log[{index}].teams"
        movers: list[tuple[str, RatingState]] = []
        for pid in sorted(pid for team in teams for pid in team):
            player = players[pid]
            baseline = _baseline_date(player, fmt, cfg)
            if baseline is not None and baseline >= last:
                continue
            state = player.ratings.get(fmt)
            # A stored state has a last_played exactly when it is played.
            if state is None or state.last_played is None or state.last_played < last:
                raise SnapshotError(
                    f"{where} names {pid!r}, who has no {fmt.value} state that a match on "
                    f"{on} could have moved or frozen"
                )
            movers.append((pid, state))
        for pid, state in movers:
            own = next(team for team in teams if pid in team)
            other = next(team for team in teams if pid not in team)
            for role, seen, ids in (
                ("a partner", state.partners, own - {pid}),
                ("an opponent", state.opponents, other),
            ):
                for linked in sorted(ids):
                    recorded = seen.get(linked)
                    if recorded is None or recorded < last:
                        raise SnapshotError(
                            f"{where} names {pid!r}, whose {fmt.value} state does not record "
                            f"{linked!r} as {role} on or after {on}"
                        )
        if not movers:
            raise SnapshotError(f"{where} names no player who moved in the match on {on}")


def _check_verified_against_log(
    players: Mapping[str, Player],
    paths: Mapping[str, str],
    log: Mapping[MatchupKey, list[date]],
    cfg: Config,
    resumable: bool,
) -> None:
    """
    Raise SnapshotError for a verified_matches_played the self-reported log rules out.

    record appends one date to the log, under the match's own format, for
    every counted self-reported match, and the log is never pruned. So
    matches_played - verified_matches_played (the self-reported updates
    that moved a state) is at most the logged matches that could move it:
    those in its format, plus, for doubles under category_feeds_parent,
    those in the category formats. A frozen player is logged but not
    moved, so that is only an upper bound. A state with no
    initial_source_date is never frozen and, once logged, never reseeded.
    For a singles or category state, or for doubles with
    category_feeds_parent off, its count then equals the logged matches
    in its format. For doubles under category_feeds_parent with
    max_doubles_partner_gap unset, a counted category match's doubles
    update is rejected only when every player is frozen in doubles, so
    the count equals the logged doubles and category matches (#84). With
    max_doubles_partner_gap set, the gap can also reject that doubles
    update, so the doubles count is only at least the doubles ones and at
    most the upper bound. A resumable snapshot is exactly a replay, whose
    seeds precede every row, so a logged match dated after a state's
    initial_source_date moved that state: the same rules then hold
    counting only those dates. A snapshot that is not resumable does not
    record when each seed was set relative to the logged matches, so for
    a state with an initial_source_date there the check keeps only the
    upper bound.

    A snapshot edited in a second field as well is held to more rules,
    checked elsewhere: the players a log entry names match the movers'
    partners and opponents, or were frozen, in the entry's latest match
    (_check_log_players), and in a resumable snapshot a played dated state
    was last played, and met every partner and opponent, after its
    initial_source_date (_check_resumable). Some edits to several fields
    still load, for example: in a snapshot that is not resumable, a played
    state given an initial_source_date before every logged date that moved
    it, with verified_matches_played raised to match, which cannot be told
    apart from a live import followed by those matches (it needs an
    external initial_source or an "open_parent:" one dated with its
    parent, since the reserved-name rules reject a dated
    "open_provisional" state); the dated-state edits _check_resumable
    rejects, in a snapshot that is not resumable; in a resumable
    snapshot, a state dated on or after an earlier match that moved it,
    with verified_matches_played raised to match, when its last_played
    and every partner and opponent date are still after the new date (for
    example, because it met the same partners and opponents again later),
    since those maps keep only the latest date; a
    log name swapped to a player who later played in that format with the
    same teammates and opponents, on or after the entry's latest date,
    with the verified counts adjusted to match; a whole log entry
    deleted with the named players' verified counts raised, since the
    snapshot does not tie a match id to its match type; and edits that
    also rewrite the partner and opponent names or dates to match.
    """
    logged: dict[tuple[str, RatingFormat], list[date]] = {}
    for (fmt, teams), dates in log.items():
        for team in teams:
            for pid in team:
                logged.setdefault((pid, fmt), []).extend(dates)
    categories = [f for f in RatingFormat if f.parent is RatingFormat.DOUBLES]

    def count(pid: str, fmt: RatingFormat, after: date | None) -> int:
        dates = logged.get((pid, fmt), [])
        return len(dates) if after is None else sum(1 for on in dates if on > after)

    for player_id, player in players.items():
        for fmt in sorted(player.ratings, key=lambda f: f.value):
            state = player.ratings[fmt]
            path = f"{paths[player_id]}.ratings.{fmt.value}"
            unverified = state.matches_played - state.verified_matches_played
            exact = state.initial_source_date is None or resumable
            after = state.initial_source_date if exact else None
            own = count(player_id, fmt, after)
            prefix = f"{path}: matches_played - verified_matches_played ({unverified})"
            if exact and fmt is not RatingFormat.DOUBLES:
                if unverified != own:
                    raise SnapshotError(
                        f"{prefix} must equal the self-reported {fmt.value} matches logged for "
                        f"this player ({own})"
                    )
                continue
            feeds = fmt is RatingFormat.DOUBLES and cfg.category_feeds_parent
            upper = own + (sum(count(player_id, c, after) for c in categories) if feeds else 0)
            what = "doubles and category" if feeds else fmt.value
            if unverified > upper:
                raise SnapshotError(
                    f"{prefix} must not exceed the self-reported {what} matches logged for "
                    f"this player ({upper})"
                )
            if exact and unverified < own:
                raise SnapshotError(
                    f"{prefix} must be at least the self-reported doubles matches logged "
                    f"for this player ({own})"
                )
            if exact and feeds and cfg.max_doubles_partner_gap is None and unverified != upper:
                raise SnapshotError(
                    f"{prefix} must equal the self-reported doubles and category matches "
                    f"logged for this player ({upper})"
                )


def _snap_top(snapshot: object) -> Mapping[object, object]:
    """
    The snapshot's top-level object, checked for shape and version.

    In order: snapshot is exactly a dict, its keys are str, a
    snapshot_version key holds SNAPSHOT_VERSION, then the keys are
    exactly the top-level keys.
    """
    # Exactly dict and list at every level: json.loads and to_snapshot give
    # them, and another container type runs its own code on every read.
    if type(snapshot) is not dict:
        raise SnapshotError("snapshot must be an object")
    _snap_str_keys(snapshot, "snapshot")
    if "snapshot_version" in snapshot:
        version = snapshot["snapshot_version"]
        if type(version) is not int or version != SNAPSHOT_VERSION:
            raise SnapshotError(
                f"snapshot.snapshot_version must be {SNAPSHOT_VERSION}, not {_safe_shown(version)}"
            )
    raw = _snap_object(snapshot, "snapshot", _TOP_KEYS)
    return raw


def _snap_header(
    raw: Mapping[object, object], cfg: Config
) -> tuple[date | None, ReplayInfo | None]:
    """
    calculated_as_of and replay_info, after the version and config checks.

    In order: engine_version is a str, config_version and
    config_fingerprint are strs equal to cfg's, then calculated_as_of,
    then replay_info.
    """
    _snap_str(raw["engine_version"], "snapshot.engine_version")
    config_version = _snap_str(raw["config_version"], "snapshot.config_version")
    if config_version != cfg.version:
        raise SnapshotError(
            f"snapshot.config_version {config_version!r} does not match the Config "
            f"({cfg.version!r})"
        )
    fingerprint = _snap_str(raw["config_fingerprint"], "snapshot.config_fingerprint")
    if fingerprint != cfg.fingerprint():
        raise SnapshotError(
            f"snapshot.config_fingerprint {fingerprint!r} does not match the Config "
            f"({cfg.fingerprint()!r})"
        )
    calculated_as_of = _snap_opt_date(raw["calculated_as_of"], "snapshot.calculated_as_of")
    replay_info = _snap_replay_info(
        raw["replay_info"], calculated_as_of, config_version, fingerprint
    )
    return calculated_as_of, replay_info


def _snap_players(
    raw: Mapping[object, object], cfg: Config, replay_info: ReplayInfo | None
) -> tuple[dict[str, Player], dict[str, str]]:
    """
    The players by id, and each player's path in the snapshot.

    In order: each player in list order, with no repeated id, then the
    held identities, the named players, and net_rating_created against
    replay_info.
    """
    players: dict[str, Player] = {}
    player_paths: dict[str, str] = {}
    for index, value in enumerate(_snap_list(raw["players"], "snapshot.players")):
        path = f"snapshot.players[{index}]"
        player = _snap_player(value, path, cfg)
        if player.id in players:
            raise SnapshotError(f"{path}: duplicate player id {player.id!r}")
        players[player.id] = player
        player_paths[player.id] = path
    _check_held_identities(players, player_paths)
    _check_named_players(players, player_paths)
    _check_net_rating_created(replay_info, players)
    return players, player_paths


def _snap_log(
    raw: Mapping[object, object], players: Mapping[str, Player], cfg: Config
) -> dict[MatchupKey, list[date]]:
    """
    The self-reported log by matchup.

    In order: each entry in list order, with no repeated matchup and
    every named player in players, then the log against the players'
    states.
    """
    log: dict[MatchupKey, list[date]] = {}
    entries = _snap_list(raw["self_reported_log"], "snapshot.self_reported_log")
    for index, value in enumerate(entries):
        path = f"snapshot.self_reported_log[{index}]"
        key, dates = _snap_log_entry(value, path)
        if key in log:
            raise SnapshotError(f"{path} repeats a matchup")
        for pid in sorted(pid for team in key[1] for pid in team):
            if pid not in players:
                raise SnapshotError(f"{path}.teams names {pid!r}, which is not in players")
        log[key] = dates
    _check_log_players(players, list(log.items()), cfg)
    return log


def _snap_ledger_digests(raw: Mapping[object, object]) -> dict[str, str]:
    """
    The ledger digests by match id.

    ledger_digests is a dict, then each entry in order: its id is a
    non-blank str and its digest 64 lowercase hex characters.
    """
    digests_raw = raw["ledger_digests"]
    if type(digests_raw) is not dict:
        raise SnapshotError("snapshot.ledger_digests must be an object")
    digests: dict[str, str] = {}
    for key, digest in digests_raw.items():
        if not _is_id(key):
            raise SnapshotError("snapshot.ledger_digests has an id that is not a non-blank str")
        if not _is_plain_str(digest) or len(digest) != 64 or not set(digest) <= _HEX:
            raise SnapshotError(
                f"snapshot.ledger_digests[{key!r}] must be 64 lowercase hex characters"
            )
        digests[key] = digest
    return digests


def _snap_ledger_player_ids(raw: Mapping[object, object]) -> set[str]:
    """The ledger player ids, each a non-blank str named once, in list order."""
    ledger_player_ids: set[str] = set()
    named = _snap_list(raw["ledger_player_ids"], "snapshot.ledger_player_ids")
    for index, value in enumerate(named):
        path = f"snapshot.ledger_player_ids[{index}]"
        player_id = _snap_id(value, path)
        if player_id in ledger_player_ids:
            raise SnapshotError(f"{path}: duplicate player id {player_id!r}")
        ledger_player_ids.add(player_id)
    return ledger_player_ids


def _read_snapshot(snapshot: object, cfg: Config) -> _Snapshot:
    """Validate a snapshot against cfg and read it. Raises SnapshotError naming the bad value."""
    raw = _snap_top(snapshot)
    calculated_as_of, replay_info = _snap_header(raw, cfg)
    players, player_paths = _snap_players(raw, cfg, replay_info)

    processed = _snap_match_ids(raw["processed_match_ids"], "snapshot.processed_match_ids")
    reserved = _snap_match_ids(raw["reserved_match_ids"], "snapshot.reserved_match_ids")

    log = _snap_log(raw, players, cfg)

    content_keys = _snap_content_keys(raw["content_keys"], processed)
    resumable = raw["resumable"]
    if type(resumable) is not bool:
        raise SnapshotError("snapshot.resumable must be a bool")
    _check_verified_against_log(players, player_paths, log, cfg, resumable)
    digests = _snap_ledger_digests(raw)
    ledger_player_ids = _snap_ledger_player_ids(raw)

    possible_duplicates = _snap_possible_duplicates(
        raw["possible_duplicates"],
        set(content_keys.values()),
        processed,
        cfg.reject_content_duplicates,
        digests if resumable else None,
    )
    _check_reserved(
        reserved,
        processed,
        possible_duplicates,
        cfg.reject_content_duplicates,
        digests,
        digests if resumable else None,
    )

    seed_sensitive = _snap_seed_sensitive_rejections(
        raw["seed_sensitive_rejections"],
        players,
        processed,
        reserved,
        possible_duplicates,
        ledger_player_ids if resumable else None,
        cfg,
    )

    snap = _Snapshot(
        players,
        processed,
        reserved,
        log,
        content_keys,
        possible_duplicates,
        calculated_as_of,
        replay_info,
        digests,
        ledger_player_ids,
        seed_sensitive,
        resumable,
    )
    if resumable:
        _check_resumable(snap, player_paths)
    return snap
