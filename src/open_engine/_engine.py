"""
RatingEngine, which records matches and takes and loads snapshots.

Demonstration only: not validated on real match data, outputs are not OPEN
ratings, and the API may change without notice.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import fields
from datetime import date

from ._config import Config, _config_or_default
from ._connectivity import _CONNECTIVITY_CACHE_SIZE, _network_connectivity
from ._errors import (
    InvalidMatchError,
    InvalidSeedError,
    PlayerError,
    ReferenceDateError,
    UnknownPlayerError,
)
from ._ledger import MatchupKey, _content_key, _matchup_key
from ._math import (
    _CLAMP_TOLERANCE,
    _RELIABILITY_PLACES,
    _RELIABILITY_STEP,
    _RESULT_PLACES,
    _SUMMARY_RATING_PLACES,
    _SUMMARY_RD_PLACES,
    _VISIBLE_LOSS,
    _actual_point_share,
    _clone_state,
    _is_late,
    _latest,
    _reliability,
    _seen,
    clone_player,
    expected_point_share,
    inflated_rd,
    information_multiplier,
    player_k,
)
from ._model import (
    _SEED_SENSITIVE_REASONS,
    ExternalRatingSnapshot,
    Match,
    MatchResult,
    NetworkConnectivity,
    Player,
    PlayerSummary,
    RatingState,
    RejectionReason,
    ReplayInfo,
    _validate_match,
)
from ._seeds import (
    _RESERVED_SOURCE_PREFIX,
    _check_seed_identity,
    _HeldIdentities,
    _plain_seed,
    _validate_seed,
)
from ._snapshot import (
    SNAPSHOT_VERSION,
    _iso,
    _log_form,
    _player_form,
    _read_snapshot,
    _replay_info_value,
    _sorted_dict,
)
from ._types import (
    _VERIFIED_MATCH_TYPES,
    GameFormat,
    MatchStatus,
    MatchType,
    RatingFormat,
    ScoringType,
    _enum_shown,
    _is_member,
)
from ._validation import (
    _days_before,
    _is_finite_number,
    _is_id,
    _is_plain_date,
    _is_plain_str,
    _plain_number,
    _type_is,
    _type_name,
)
from ._version import __version__
from .winprob import _check_best_of, calibrate_win_probability, match_win_probability

# ---------------------------------------------------------------------------
# Rating engine
# ---------------------------------------------------------------------------


def _check_lookup_format(rating_format: object) -> None:
    """Raise PlayerError unless rating_format is exactly a RatingFormat (a str is rejected)."""
    if not _is_member(rating_format, RatingFormat):
        raise PlayerError(
            f"rating_format must be a RatingFormat, not {_enum_shown(rating_format, RatingFormat)}"
        )


def _imported_reliability(
    state: RatingState, cfg: Config, reference: date, earned: float
) -> float | None:
    """
    player_summary's imported_reliability: the imported value, rounded to 1 place, or None.

    It is carried while the state has an initial_source_reliability and an
    initial_source_date, has fewer than import_reliability_carry_matches
    matches, was captured on or before reference and at most
    import_reliability_max_age_days before it, and, rounded, is above earned.
    """
    # Imported reliability is shown, separately, only while it is fresh,
    # higher than what OPEN has earned, and within the carry match limit.
    # Both values are compared as displayed, rounded to 1 place.
    imported: float | None = None
    seed_reliability = state.initial_source_reliability
    if (
        seed_reliability is not None
        and state.initial_source_date is not None
        and state.matches_played < cfg.import_reliability_carry_matches
        and 0 <= (reference - state.initial_source_date).days <= cfg.import_reliability_max_age_days
        and round(float(seed_reliability), _RELIABILITY_PLACES) > earned
    ):
        imported = round(float(seed_reliability), _RELIABILITY_PLACES)
    return imported


class RatingEngine:
    """
    Holds players and processed matches, and records matches one at a time.

    Ratings recorded live are provisional. Use replay() for authoritative ratings.
    """

    def __init__(self, cfg: Config | None = None):
        """
        An empty engine under cfg (default Config()).

        The engine keeps a fresh copy of cfg, rebuilt from its field values
        and checked again as Config(...) checks them, so self.cfg is never
        cfg. For a Config that was not rewritten after it was built, self.cfg
        equals cfg and has the same fingerprint.

        Args:
            cfg: None or exactly a Config (not a subclass) holding only its own
                fields.

        Raises:
            ConfigError: cfg is not None or exactly a Config holding only its own
                fields, before anything is built, or a value in it fails Config's
                checks when it is rebuilt.
        """
        self._start(_config_or_default(cfg))

    def _start(self, cfg: Config) -> None:
        """Set every attribute of an empty engine under an already checked cfg."""
        self.cfg = cfg
        self.players: dict[str, Player] = {}
        self.processed_match_ids: set[str] = set()
        # match_id of each match record considered and did not count. A later
        # record with one of these ids that does not raise is a duplicate,
        # as in replay, where a second row with the id is a conflict or
        # collapses into the first.
        # Never in processed_match_ids.
        self._reserved_match_ids: set[str] = set()
        self.self_reported_log: dict[MatchupKey, list[date]] = {}
        self.calculated_as_of: date | None = None
        self.replay_info: ReplayInfo | None = None
        # Content key (see _content_key) of each counted match, to the
        # match_id of the first one counted with it. Never overwritten.
        self._content_keys: dict[str, str] = {}
        # match_id of each match flagged or rejected as a possible
        # duplicate, to the match_id it repeats (its possible_duplicate_of).
        # An entry never clears: its match_id is processed or reserved.
        self.possible_duplicates: dict[str, str] = {}
        # Digest of each ledger row a replay considered, by match_id. Only
        # replay fills it; resume compares rows against it.
        self._ledger_digests: dict[str, str] = {}
        # Every player id named in a row a replay considered, counted or
        # not. Resume refuses a seed for one of them.
        self._ledger_player_ids: set[str] = set()
        # match_id of each update record rejected for a reason a seed can
        # change (_SEED_SENSITIVE_REASONS), to the rating format of that
        # update and the sorted ids of the match's players: a match's own
        # update (its id is reserved), or the overall doubles update of a
        # counted category match (its id is processed). The seed methods
        # refuse a seed that could change one of these. Never cleared.
        self._seed_sensitive_rejections: dict[str, tuple[RatingFormat, tuple[str, ...]]] = {}
        # True only while the state is exactly what replay produced, so a
        # snapshot of it can be resumed from. Replay sets it at the end.
        self._resumable = False
        # NetworkConnectivity by (format, reference date), with the main
        # network size: derived from the rating states, never engine state,
        # so it is not in the snapshot. _changed clears it.
        self._connectivity: dict[
            tuple[RatingFormat, date], tuple[dict[str, NetworkConnectivity], int]
        ] = {}

    # -- state changes ---------------------------------------------------------
    #
    # Every change to engine state outside replay goes through these three
    # methods, which clear the resumable mark. A new method that changes a
    # player record, a rating state, or an identity must use them too, or a
    # snapshot taken after it could resume to a result a full replay would
    # not give.

    def _changed(self) -> None:
        """Mark the engine as no longer exactly what a replay produced."""
        self._resumable = False
        self._connectivity.clear()

    def _put_player(self, player: Player) -> None:
        """Store a new player record."""
        self._changed()
        self.players[player.id] = player

    def _put_state(self, player: Player, rating_format: RatingFormat, state: RatingState) -> None:
        """Store a player's rating state in one format."""
        self._changed()
        player.ratings[rating_format] = state

    # -- snapshots -------------------------------------------------------------

    def to_snapshot(self) -> dict[str, object]:
        """
        The engine's whole state as JSON-safe data (schema version 8).

        json.dumps(snapshot, allow_nan=False) works with no custom encoder,
        and RatingEngine.from_snapshot rebuilds the engine from it, or from
        json.loads of its text. Every dict is emitted with sorted keys and
        every list in a fixed order, so the same state always gives the same
        text. docs/behavior.md, "Snapshot schema", lists the schema in full.

        To use the snapshot as a checkpoint, take it right after replay,
        before any other call on the engine.

        Returns:
            The snapshot, as plain JSON data.

        Raises:
            SnapshotError: the state could not load back (a value of the wrong
                type, a rating out of bounds); the message names the field.

        See docs/behavior.md, "Snapshot schema and checks".
        """
        info = self.replay_info
        replay_info: object = None
        if info is not None:
            replay_info = _sorted_dict(
                (f.name, _replay_info_value(f.name, getattr(info, f.name))) for f in fields(info)
            )
        snapshot: dict[str, object] = {
            "calculated_as_of": _iso(self.calculated_as_of),
            "config_fingerprint": self.cfg.fingerprint(),
            "config_version": self.cfg.version,
            "content_keys": _sorted_dict(self._content_keys.items()),
            "engine_version": __version__,
            "ledger_digests": _sorted_dict(self._ledger_digests.items()),
            "ledger_player_ids": sorted(self._ledger_player_ids, key=str),
            "players": [
                _player_form(player)
                for player in sorted(self.players.values(), key=lambda p: str(p.id))
            ],
            "possible_duplicates": _sorted_dict(self.possible_duplicates.items()),
            "processed_match_ids": sorted(self.processed_match_ids, key=str),
            "replay_info": replay_info,
            "reserved_match_ids": sorted(self._reserved_match_ids, key=str),
            "resumable": self._resumable,
            "seed_sensitive_rejections": _sorted_dict(
                (match_id, {"players": list(players), "rating_format": fmt.value})
                for match_id, (fmt, players) in self._seed_sensitive_rejections.items()
            ),
            "self_reported_log": _log_form(self.self_reported_log),
            "snapshot_version": SNAPSHOT_VERSION,
        }
        _read_snapshot(snapshot, self.cfg)
        return snapshot

    @classmethod
    def from_snapshot(
        cls, snapshot: Mapping[str, object], cfg: Config | None = None
    ) -> RatingEngine:
        """
        Rebuild an engine from to_snapshot() output, or json.loads of its text.

        The whole snapshot is validated before anything is built, and the engine
        shares no state with the snapshot. These checks cover shape and
        invariants, not authenticity.

        Args:
            snapshot: to_snapshot() output, or json.loads of its text.
            cfg: The Config the snapshot was taken under (default Config()): its
                version and fingerprint must equal the snapshot's. The engine keeps
                a copy of cfg rebuilt from its field values.

        Returns:
            The rebuilt RatingEngine.

        Raises:
            ConfigError: cfg is not None or exactly a Config with no attributes
                beyond its fields (checked before the snapshot is read), or a value
                in it fails Config's checks when it is rebuilt.
            SnapshotError: the snapshot breaks a rule; the message names the first
                bad value.

        See docs/behavior.md, "Snapshot schema and checks", for every rule.
        """
        engine = cls(cfg)
        _load_snapshot(engine, snapshot)
        return engine

    # -- players and identity ------------------------------------------------

    def player(self, player_id: str) -> Player:
        """
        The player record for player_id. Read-only: it never creates a player.

        A player is created by add_player, seed_provisional_rating,
        seed_external_rating, link_account, attach_external_id, or a counted
        record (and so replay). An id named only in uncounted rows, or in rows
        after a replay's as_of, has no player record.

        Args:
            player_id: The player's id, exactly a str (a subclass is rejected).

        Returns:
            The stored Player record.

        Raises:
            PlayerError: player_id is blank or not exactly a str.
            UnknownPlayerError: no player record exists for it.
        """
        if not _is_id(player_id):
            raise PlayerError("player_id is required")
        if player_id not in self.players:
            raise UnknownPlayerError(f"Unknown player: {player_id}")
        return self.players[player_id]

    def _player_or_new(self, player_id: str) -> Player:
        """The player record for player_id, created empty if it does not exist."""
        self._check_player_id(player_id)
        if player_id not in self.players:
            self._put_player(Player(id=player_id))
        return self.players[player_id]

    def add_player(self, player: Player) -> None:
        """
        Add a copy of a player record as a seed.

        The seed and every rating state in it must follow the seed rules. Played
        states are rebuilt through replay instead. The caller's Player and
        RatingState are not changed.

        Args:
            player: The seed, exactly a Player (not a subclass).

        Raises:
            InvalidSeedError: the seed breaks the seed rules.
            PlayerError: the id exists, or another player already holds the
                seed's account_user_id or one of its (source, external id) pairs.

        Nothing changes when either is raised. See docs/behavior.md, "Seed rules".
        """
        self._add_seed(player, self._held_identities(), check_rejections=True)

    def _held_identities(self) -> _HeldIdentities:
        """The accounts and (source, external id) pairs the engine's players hold."""
        held = _HeldIdentities()
        for p in self.players.values():
            held.add(p)
        return held

    def _add_seed(
        self, player: Player, held: _HeldIdentities, *, check_rejections: bool = False
    ) -> None:
        """
        add_player, with the identities already held passed in.

        held must hold every identity of the engine's players; it gains the
        seed's identities once the seed is added. A batch of seeds builds it
        once, so each seed's uniqueness check is a set lookup instead of a
        scan of every player. check_rejections (add_player only) refuses a
        seed that could change a seed-sensitive rejection. Replay adds its
        seeds before any row, and resume refuses a seed for any player named
        in a row the checkpoint considered, so neither needs it.
        """
        _check_seed_identity(player)
        if player.id in self.players:
            raise PlayerError(f"Player already exists: {player.id}")
        # Check and store plain numbers, never the caller's objects.
        player = _plain_seed(player)
        _validate_seed(player, self.cfg)
        if check_rejections:
            for fmt in sorted(player.ratings, key=lambda f: f.value):
                self._check_seed_sensitive(player.id, fmt)
        # The same uniqueness rules, with the same messages, as link_account
        # and attach_external_id.
        account = player.account_user_id
        if account is not None and account in held.accounts:
            raise PlayerError("This account is already linked to another player profile")
        for source in sorted(player.external_ids):
            if (source, player.external_ids[source]) in held.external_ids:
                raise PlayerError("This external identity is already linked to another player")
        self._put_player(clone_player(player))
        held.add(player)

    @staticmethod
    def _check_player_id(player_id: str) -> None:
        """Raise PlayerError for a player id that is blank or not exactly a str."""
        if not _is_id(player_id):
            raise PlayerError("player_id is required")

    def _check_account(self, player_id: str, account_user_id: str) -> None:
        """Raise PlayerError unless the account can link to player_id. Changes nothing."""
        if not _is_id(account_user_id):
            raise PlayerError("account_user_id is required")
        self._check_player_id(player_id)
        player = self.players.get(player_id)
        if player is not None and player.account_user_id not in (None, account_user_id):
            raise PlayerError("Player profile is already linked to another account")
        if any(
            p.account_user_id == account_user_id and p.id != player_id
            for p in self.players.values()
        ):
            raise PlayerError("This account is already linked to another player profile")

    def _check_external_id(self, player_id: str, source: str, external_player_id: str) -> None:
        """Raise PlayerError unless the identity can link to player_id. Changes nothing."""
        if not _is_id(source) or not _is_id(external_player_id):
            raise PlayerError("source and external_player_id are required")
        if source.startswith(_RESERVED_SOURCE_PREFIX):
            raise PlayerError(f"source must not start with {_RESERVED_SOURCE_PREFIX}")
        self._check_player_id(player_id)
        player = self.players.get(player_id)
        if player is not None and player.external_ids.get(source) not in (
            None,
            external_player_id,
        ):
            raise PlayerError("A different external identity is already linked")
        for other in self.players.values():
            if other.id != player_id and other.external_ids.get(source) == external_player_id:
                raise PlayerError("This external identity is already linked to another player")

    def link_account(self, player_id: str, account_user_id: str) -> None:
        """
        Claim an existing external-only player profile for an OPEN account.

        Args:
            player_id: The player, exactly a str (not a subclass).
            account_user_id: The OPEN account, exactly a str (not a subclass).

        Raises:
            PlayerError: a blank or non-str player id, a blank, whitespace-only,
                or non-str account, or when the player or the account is already
                linked elsewhere.

        Every check runs before anything changes, so a failed call creates no
        player.
        """
        self._check_account(player_id, account_user_id)
        player = self._player_or_new(player_id)
        self._changed()
        player.account_user_id = account_user_id

    def attach_external_id(self, player_id: str, source: str, external_player_id: str) -> None:
        """
        Link an external identity to a player. Each identity links to one player.

        Args:
            player_id: The player, exactly a str (not a subclass).
            source: The external source, exactly a str (not a subclass). It must
                not start with "open_", the prefix reserved for provenance the
                engine writes.
            external_player_id: The id at that source, exactly a str (not a
                subclass).

        Raises:
            PlayerError: a blank or non-str player id, a blank, whitespace-only,
                or non-str source or external id, a source that starts with
                "open_", when the player already has a different id from that
                source, or when the identity is linked to another player.

        Every check runs before anything changes, so a failed call creates no
        player.
        """
        self._check_external_id(player_id, source, external_player_id)
        player = self._player_or_new(player_id)
        self._changed()
        player.external_ids[source] = external_player_id

    # -- seeding ---------------------------------------------------------------

    def seed_external_rating(
        self,
        player_id: str,
        rating_format: RatingFormat,
        snapshot: ExternalRatingSnapshot,
        *,
        replace_unplayed_seed: bool = False,
    ) -> RatingState:
        """
        Initialize an OPEN rating from a verified external snapshot.

        The external rating is an initial condition, not a live dependency.
        Once OPEN matches exist, external refreshes cannot overwrite the rating.
        The seed's RD is always Config.external_seed_rd.

        Args:
            player_id: The player to seed, exactly a str that is not blank.
            rating_format: Exactly a RatingFormat (a str is not converted).
            snapshot: Exactly an ExternalRatingSnapshot (not a subclass).
            replace_unplayed_seed: Exactly a bool. Without True, a state the player
                already has in rating_format is not replaced.

        Returns:
            The new stored RatingState.

        Raises:
            InvalidSeedError: first, the rating format, then replace_unplayed_seed
                (exactly a bool), then the snapshot or a checked snapshot field;
                after the player id, the existing rating or the player's OPEN
                matches (counted, or rejected for a reason a seed can change) do
                not allow the seed.
            PlayerError: player_id is blank or not a str (checked after the
                snapshot); or, last, the snapshot's external identity is linked to
                another player or the player already has a different id from that
                source.

        Every check runs before anything changes, so a failed call creates no
        player. See docs/behavior.md, "External seeds".
        """
        snapshot = self._checked_external_snapshot(rating_format, snapshot, replace_unplayed_seed)
        self._check_seed_allowed(player_id, rating_format, replace_unplayed_seed)
        if snapshot.external_player_id is not None:
            self._check_external_id(player_id, snapshot.source, snapshot.external_player_id)
        return self._store_external_seed(player_id, rating_format, snapshot)

    def _checked_external_snapshot(
        self,
        rating_format: RatingFormat,
        snapshot: ExternalRatingSnapshot,
        replace_unplayed_seed: bool,
    ) -> ExternalRatingSnapshot:
        """
        seed_external_rating's argument and snapshot checks: a copy of snapshot with plain numbers.

        In order: rating_format, replace_unplayed_seed, the snapshot's type,
        then the snapshot's fields: the type of captured_on, source,
        external_player_id, and evidence_reference; then source not blank
        and not reserved, external_player_id and evidence_reference not
        blank; then rating and reliability finite, rating within the
        rating range, and reliability from 0 to 100. Changes nothing.
        """
        cfg = self.cfg
        # The same type rules the seed check applies, so every state seeded
        # here is one add_player and replay accept.
        if not _is_member(rating_format, RatingFormat):
            raise InvalidSeedError("rating_format must be a RatingFormat")
        if type(replace_unplayed_seed) is not bool:
            raise InvalidSeedError(
                f"replace_unplayed_seed must be a bool, not {_type_name(replace_unplayed_seed)}"
            )
        # Exactly ExternalRatingSnapshot: a subclass can return different
        # values on later reads than the ones checked here.
        if type(snapshot) is not ExternalRatingSnapshot:
            raise InvalidSeedError("snapshot must be an ExternalRatingSnapshot")
        # A new snapshot with plain numbers: the checks and the stored state
        # read the same values, and the caller's snapshot is not changed.
        # Fields read from the class, not dataclasses.replace, which reads
        # an instance __dataclass_fields__ that the caller can set.
        copied: dict[str, object] = {
            f.name: getattr(snapshot, f.name) for f in fields(ExternalRatingSnapshot)
        }
        copied["rating"] = _plain_number(snapshot.rating)
        copied["reliability"] = _plain_number(snapshot.reliability)
        snapshot = ExternalRatingSnapshot(**copied)  # type: ignore[arg-type]
        if not _is_plain_date(snapshot.captured_on):
            raise InvalidSeedError("External snapshot captured_on must be a date")
        if not _is_plain_str(snapshot.source):
            raise InvalidSeedError("External snapshot source must be a str")
        if snapshot.external_player_id is not None and not _is_plain_str(
            snapshot.external_player_id
        ):
            raise InvalidSeedError("External snapshot external_player_id must be a str or None")
        if snapshot.evidence_reference is not None and not _is_plain_str(
            snapshot.evidence_reference
        ):
            raise InvalidSeedError("External snapshot evidence_reference must be a str or None")
        if not _is_id(snapshot.source):
            raise InvalidSeedError("External snapshot source must not be blank")
        if snapshot.source.startswith(_RESERVED_SOURCE_PREFIX):
            raise InvalidSeedError(
                f"External snapshot source must not start with {_RESERVED_SOURCE_PREFIX}"
            )
        if snapshot.external_player_id is not None and not _is_id(snapshot.external_player_id):
            raise InvalidSeedError("External snapshot external_player_id must not be blank")
        if snapshot.evidence_reference is not None and not _is_id(snapshot.evidence_reference):
            raise InvalidSeedError("External snapshot evidence_reference must not be blank")
        if not _is_finite_number(snapshot.rating):
            raise InvalidSeedError("External rating must be a finite number")
        if snapshot.reliability is not None and not _is_finite_number(snapshot.reliability):
            raise InvalidSeedError("External reliability must be a finite number")
        if not cfg.min_rating <= snapshot.rating <= cfg.max_rating:
            raise InvalidSeedError("External rating is outside the configured scale")
        if snapshot.reliability is not None and not 0 <= snapshot.reliability <= 100:
            raise InvalidSeedError("External reliability must be between 0 and 100")
        return snapshot

    def _check_seed_allowed(
        self, player_id: str, rating_format: RatingFormat, replace_unplayed_seed: bool
    ) -> None:
        """
        Raise unless the player's state and matches allow a seed in rating_format.

        In order: the player id (PlayerError); then InvalidSeedError for an
        existing state with OPEN matches or derived from overall doubles, a
        counted match in the format, a counted category match that depends
        on it, a seed-sensitive rejection, and last an existing state without
        replace_unplayed_seed. Changes nothing.
        """
        self._check_player_id(player_id)
        known = self.players.get(player_id)
        existing = known.ratings.get(rating_format) if known is not None else None
        if existing is not None:
            if existing.matches_played > 0:
                raise InvalidSeedError(
                    "Cannot replace an OPEN rating after OPEN matches have been processed"
                )
            if existing.initial_source.startswith("open_parent:"):
                raise InvalidSeedError("Cannot replace a rating derived from OPEN overall doubles")
        if self._in_counted_match(player_id, rating_format):
            raise InvalidSeedError(
                "Cannot seed a rating format in which OPEN matches have counted for this player"
            )
        # A counted category match sends a doubles update for its players,
        # and may start the player's category state from this format. A new
        # seed rating or date could change either, so refuse whenever a
        # counted category match included the player, whatever that
        # category state is.
        if self.cfg.category_feeds_parent and known is not None:
            for child in RatingFormat:
                if child.parent is rating_format and self._in_counted_match(player_id, child):
                    raise InvalidSeedError(
                        f"Cannot seed a rating that a counted {child.value} match for this "
                        "player depends on"
                    )
        self._check_seed_sensitive(player_id, rating_format)
        if existing is not None:
            if not replace_unplayed_seed:
                raise InvalidSeedError(
                    "A rating already exists; explicitly allow replacing an unplayed seed"
                )

    def _store_external_seed(
        self, player_id: str, rating_format: RatingFormat, snapshot: ExternalRatingSnapshot
    ) -> RatingState:
        """
        Store a checked external seed and return its new state.

        Creates the player if needed, links the snapshot's external
        identity, stores the seed state, then, under category_feeds_parent,
        rebuilds each stored, unplayed category state derived from
        rating_format. Every check must have passed.
        """
        cfg = self.cfg
        # Every check has passed: create the player and link the identity.
        player = self._player_or_new(player_id)
        if snapshot.external_player_id is not None:
            self.attach_external_id(player_id, snapshot.source, snapshot.external_player_id)

        state = RatingState(
            rating=snapshot.rating,
            rd=cfg.external_seed_rd,
            rd_as_of=snapshot.captured_on,
            initial_rating=snapshot.rating,
            initial_source=snapshot.source,
            initial_source_date=snapshot.captured_on,
            initial_source_reliability=snapshot.reliability,
            initial_external_player_id=snapshot.external_player_id,
            initial_evidence_reference=snapshot.evidence_reference,
        )
        self._put_state(player, rating_format, state)
        # A stored, unplayed category state derived from the format just
        # seeded must start from the new seed, as _new_state would build it
        # now, or live recording and a replay of later matches disagree.
        # The engine creates no such state itself (issue #112), but a seed
        # (add_player, replay seeds) or a loaded snapshot can hold one.
        if cfg.category_feeds_parent:
            for child in RatingFormat:
                if child.parent is not rating_format:
                    continue
                derived = player.ratings.get(child)
                if (
                    derived is not None
                    and derived.matches_played == 0
                    and derived.initial_source == f"open_parent:{rating_format.value}"
                ):
                    self._put_state(player, child, self._new_state(player_id, child))
        return state

    def _check_seed_sensitive(self, player_id: str, rating_format: RatingFormat) -> None:
        """
        Raise InvalidSeedError when a seed could change a seed-sensitive rejection.

        A rejected update in rating_format depends on the player's state
        there, and, under category_feeds_parent, a rejected category update
        also depends on the player's parent state, from which a category
        state without a stored one starts. A replay adds every seed before
        any row, so a new seed could make it count a row live rejected.
        """
        feeds = self.cfg.category_feeds_parent
        blocking = [
            match_id
            for match_id, (fmt, players) in self._seed_sensitive_rejections.items()
            if player_id in players
            and (fmt is rating_format or (feeds and fmt.parent is rating_format))
        ]
        if blocking:
            # The smallest id, so the message does not depend on dict order.
            match_id = min(blocking)
            fmt = self._seed_sensitive_rejections[match_id][0]
            raise InvalidSeedError(
                f"Cannot seed: a rejected {fmt.value} match that named this player "
                f"({match_id}) could count with a new seed; a new seed for this player "
                "needs a full replay from corrected seeds"
            )

    def _in_counted_match(self, player_id: str, rating_format: RatingFormat) -> bool:
        """
        True when player_id was in a counted match in rating_format, frozen or not.

        A frozen player's own state is not written, but a match counts only
        when some player in it moves, and each moved player records every
        other player in the match in partners or opponents. Those maps are
        never pruned, so another state naming player_id finds the match.
        """
        for other_id, other in self.players.items():
            state = other.ratings.get(rating_format)
            if state is None:
                continue
            if other_id == player_id:
                if state.matches_played > 0:
                    return True
            elif player_id in state.partners or player_id in state.opponents:
                return True
        return False

    def seed_provisional_rating(self, player_id: str, rating_format: RatingFormat) -> RatingState:
        """
        The player's state; stores a missing one, except in a category under category_feeds_parent.

        Creates an empty player record for an unknown player_id. A state the
        player already has in the format is returned as stored. Otherwise it
        returns the starting state the player's first match in that format would
        use if played now, as state does, and stores it, except in a category
        format while Config.category_feeds_parent is on.

        Args:
            player_id: The player, exactly a str (a subclass is rejected).
            rating_format: Exactly a RatingFormat (a str is not converted).

        Returns:
            The stored state, or, in a category format while category_feeds_parent
            is on, a new copy that is not engine state.

        Raises:
            InvalidSeedError: rating_format is not exactly a RatingFormat.
            PlayerError: player_id is blank or not exactly a str.

        Both are checked before anything changes, so a failed call creates no
        player. See docs/behavior.md, "Provisional states".
        """
        if not _is_member(rating_format, RatingFormat):
            raise InvalidSeedError("rating_format must be a RatingFormat")
        player = self._player_or_new(player_id)
        if rating_format not in player.ratings:
            new_state = self._new_state(player_id, rating_format)
            if rating_format.parent is not None and self.cfg.category_feeds_parent:
                # The state derives from doubles. A stored copy would not
                # follow later doubles changes, so live and a replay could
                # disagree (issue #112).
                return new_state
            self._put_state(player, rating_format, new_state)
        return player.ratings[rating_format]

    def _new_state(self, player_id: str, rating_format: RatingFormat) -> RatingState:
        """
        Starting state for a format the player has no rating in.

        A category rating (mixed, men's, women's doubles) starts from the
        player's overall doubles rating when one exists, with extra RD.
        """
        cfg = self.cfg
        parent = rating_format.parent
        player = self.players.get(player_id)
        if parent and cfg.category_feeds_parent and player and parent in player.ratings:
            base = player.ratings[parent]
            if base.matches_played > 0 or base.initial_source_date is not None:
                return RatingState(
                    rating=base.rating,
                    rd=max(base.rd, cfg.category_seed_rd),
                    rd_as_of=base.rd_as_of,
                    initial_rating=base.rating,
                    initial_source=f"open_parent:{parent.value}",
                    initial_source_date=base.initial_source_date,
                    inherited_live_updates=(
                        base.live_updates_since_replay + base.inherited_live_updates
                    ),
                )
        return RatingState(
            rating=cfg.default_rating,
            rd=cfg.new_player_rd,
            initial_rating=cfg.default_rating,
            initial_source="open_provisional",
        )

    def _peek_state(self, player_id: str, rating_format: RatingFormat) -> RatingState:
        """Current state without creating anything."""
        player = self.players.get(player_id)
        if player and rating_format in player.ratings:
            return player.ratings[rating_format]
        return self._new_state(player_id, rating_format)

    def state(self, player_id: str, rating_format: RatingFormat) -> RatingState:
        """
        The player's current state in a format. Read-only: it never stores.

        For a format the player has no state in, it returns the starting state
        the player's first match in that format would use if played now: a
        category state derived from overall doubles where that applies, else a
        provisional default. That object is a new copy each call and is not
        engine state, so changing it changes nothing. seed_provisional_rating
        stores it in singles and overall doubles, and in a category format
        while Config.category_feeds_parent is off; in a category format while
        it is on, seed_provisional_rating returns it without storing it.

        Args:
            player_id: The player, exactly a str (a subclass is rejected).
            rating_format: Exactly a RatingFormat.

        Returns:
            The stored state, or the starting state described above.

        Raises:
            UnknownPlayerError: no player record exists for the id.
            PlayerError: the id is blank or not exactly a str, and then a
                rating_format that is not exactly a RatingFormat.
        """
        player = self.player(player_id)
        _check_lookup_format(rating_format)
        if rating_format in player.ratings:
            return player.ratings[rating_format]
        return self._new_state(player_id, rating_format)

    # -- prediction ------------------------------------------------------------

    def predict_point_share(
        self,
        team_a: Sequence[str],
        team_b: Sequence[str],
        rating_format: RatingFormat,
        scoring: ScoringType = ScoringType.SIDE_OUT,
    ) -> float:
        """
        Expected share of points won by team A, from current team-average ratings.

        Args:
            team_a: Team A's ids, exactly a tuple or list of distinct non-blank
                str ids of the format's team size.
            team_b: Team B's ids, under the same rule.
            rating_format: Exactly a RatingFormat.
            scoring: Exactly a ScoringType (a str is rejected).

        Returns:
            Team A's expected share of points.

        Raises:
            InvalidMatchError: an argument breaks its rule above.
        """
        self._validate_teams(team_a, team_b, rating_format)
        if not _is_member(scoring, ScoringType):
            raise InvalidMatchError(
                f"scoring must be a ScoringType, not {_enum_shown(scoring, ScoringType)}"
            )
        ratings_a = [self._peek_state(pid, rating_format).rating for pid in team_a]
        ratings_b = [self._peek_state(pid, rating_format).rating for pid in team_b]
        return expected_point_share(
            math.fsum(ratings_a) / len(ratings_a),
            math.fsum(ratings_b) / len(ratings_b),
            self.cfg.scale_by_scoring[scoring],
        )

    def predict_win_probability(
        self,
        team_a: Sequence[str],
        team_b: Sequence[str],
        rating_format: RatingFormat,
        game_format: GameFormat,
        best_of: int = 1,
    ) -> float:
        """
        Calibrated probability that team A wins the match.

        Args:
            team_a: Team A's ids, as for predict_point_share.
            team_b: Team B's ids, as for predict_point_share.
            rating_format: Exactly a RatingFormat.
            game_format: The game format, exactly a GameFormat (see
                GameFormat.validate).
            best_of: Exactly an int that is positive and odd.

        Returns:
            The calibrated probability that team A wins the match.

        Raises:
            ConfigError: game_format is not valid.
            InvalidMatchError: the teams or rating_format, as predict_point_share
                checks them.
            EvaluationError: best_of is not exactly an int that is positive and
                odd.
        """
        GameFormat.validate(game_format)
        # Before any prediction work (issue #119).
        _check_best_of(best_of)
        share = self.predict_point_share(team_a, team_b, rating_format, game_format.scoring)
        raw = match_win_probability(share, game_format, best_of)
        return calibrate_win_probability(
            raw, self.cfg.win_calibration_by_scoring[game_format.scoring]
        )

    def _validate_teams(
        self, team_a: Sequence[str], team_b: Sequence[str], rating_format: RatingFormat
    ) -> None:
        for name, team in (("team_a", team_a), ("team_b", team_b)):
            # Exactly tuple or list: a subclass can report one length and
            # iterate other ids.
            if not _type_is(team, tuple, list):
                raise InvalidMatchError(f"{name} must be a tuple or list, not {_type_name(team)}")
        if not _is_member(rating_format, RatingFormat):
            shown = _enum_shown(rating_format, RatingFormat)
            raise InvalidMatchError(f"rating_format must be a RatingFormat, not {shown}")
        size = rating_format.team_size
        if len(team_a) != size or len(team_b) != size:
            raise InvalidMatchError(f"{rating_format.value} requires {size} player(s) per team")
        ids = list(team_a) + list(team_b)
        if not all(_is_id(pid) for pid in ids):
            raise InvalidMatchError("Player ids must be non-empty strings")
        if len(set(ids)) != len(ids):
            raise InvalidMatchError("A player cannot appear more than once in a match")

    # -- recording -------------------------------------------------------------

    def record(self, match: Match, *, as_of: date | None = None) -> MatchResult:
        """
        Process one match exactly once.

        Leave `as_of` unset for live processing (labeled provisional until the
        next replay). A match that does not count is not an error: the result
        names its RejectionReason. Live results apply in arrival order.

        Args:
            match: The match, exactly a Match. It is validated before any state
                changes.
            as_of: None for live processing. replay() passes a fixed as_of for
                authoritative rebuilds. It only labels the update and must not be
                earlier than the match date.

        Returns:
            A MatchResult. Its reason is None when the match counted.

        Raises:
            InvalidMatchError: the match is not valid, or as_of is not a plain
                date or is earlier than played_on.

        See docs/behavior.md, "Recording a match" and "Match ids, rejections, and
        resubmission".
        """
        return self._record(match, as_of=as_of)[0]

    def _record(
        self, match: Match, *, as_of: date | None = None
    ) -> tuple[MatchResult, dict[RatingFormat, float]]:
        """
        record, also returning the net rating each counted update added, by format.

        The net of one update is math.fsum of the unrounded rating changes
        it applied, a frozen player adding 0.0. A format appears only when
        its update counted. replay totals these into
        ReplayInfo.net_rating_created.
        """
        net: dict[RatingFormat, float] = {}
        _validate_match(match)
        if as_of is not None and not _is_plain_date(as_of):
            raise InvalidMatchError(
                f"as_of must be a plain date (not a datetime), not {_type_name(as_of)}"
            )
        if as_of is not None and as_of < match.played_on:
            raise InvalidMatchError("as_of cannot be earlier than the match date")
        match_id = match.match_id
        if match_id in self.processed_match_ids or match_id in self._reserved_match_ids:
            return (
                MatchResult(False, match.rating_format, RejectionReason.DUPLICATE_MATCH_ID),
                net,
            )
        result, net = self._consider(match, as_of)
        if not result.counted:
            # Reserve the id of every row considered and not counted, so a
            # later record with it that does not raise is a duplicate, as in
            # replay, where a match_id names one row. No rating, count, or
            # content key changes.
            self._changed()
            self._reserved_match_ids.add(match_id)
        return result, net

    def _consider(
        self, match: Match, as_of: date | None
    ) -> tuple[MatchResult, dict[RatingFormat, float]]:
        """
        _record after the match_id check: the result and net of a match not seen before.

        Changes state only when the match counts, when it is rejected as
        a possible duplicate (possible_duplicates), or when an update is
        rejected for a reason in _SEED_SENSITIVE_REASONS
        (_seed_sensitive_rejections: the match's own update, or the
        overall doubles update of a counted category match). _record
        reserves the match_id of every result that does not count.
        """
        net: dict[RatingFormat, float] = {}
        if match.status is not MatchStatus.COMPLETED:
            return (
                MatchResult(False, match.rating_format, RejectionReason.MATCH_NOT_COMPLETED),
                net,
            )

        cfg = self.cfg
        format_key = (match.game_format.scoring, match.game_format.points_to)
        if format_key not in cfg.allowed_game_formats:
            return (
                MatchResult(False, match.rating_format, RejectionReason.GAME_FORMAT_NOT_ACCEPTED),
                net,
            )

        live = as_of is None

        content_key = _content_key(match)
        twin = self._content_keys.get(content_key)
        if twin is not None and cfg.reject_content_duplicates:
            self._changed()
            self.possible_duplicates[match.match_id] = twin
            rejected = MatchResult(
                False,
                match.rating_format,
                RejectionReason.POSSIBLE_DUPLICATE,
                possible_duplicate_of=twin,
            )
            return rejected, net

        refused, repeat_factor, matchup = self._self_reported_factor(match)
        if refused is not None:
            return refused, net

        primary, primary_updates, primary_net = self._compute(
            match, match.rating_format, repeat_factor
        )
        if not primary.counted or primary_updates is None:
            self._note_seed_sensitive(match, primary)
            return primary, net
        primary.possible_duplicate_of = twin
        net[match.rating_format] = primary_net

        parent_updates = None
        parent_format = match.rating_format.parent if cfg.category_feeds_parent else None
        if parent_format is not None:
            # A parent rejection (baseline or filter) never blocks the category.
            primary.parent, parent_updates, parent_net = self._compute(
                match, parent_format, repeat_factor
            )
            if parent_updates is not None:
                net[parent_format] = parent_net
            else:
                self._note_seed_sensitive(match, primary.parent)

        self._apply_counted(
            match, primary_updates, parent_format, parent_updates, live, matchup, content_key, twin
        )
        return primary, net

    def _apply_counted(
        self,
        match: Match,
        primary_updates: dict[str, RatingState],
        parent_format: RatingFormat | None,
        parent_updates: dict[str, RatingState] | None,
        live: bool,
        matchup: MatchupKey | None,
        content_key: str,
        twin: str | None,
    ) -> None:
        """
        _consider's commit of a counted match, after every update is computed.

        In order: the match's own format's states, the parent format's
        states when that update counted, the self-reported log, the content
        key (kept for the first match with it), the possible duplicate, and
        the processed match_id.
        """
        self._commit(match.rating_format, primary_updates, live)
        if parent_updates and parent_format is not None:
            self._commit(parent_format, parent_updates, live)
        if matchup is not None:
            self.self_reported_log.setdefault(matchup, []).append(match.played_on)
        self._content_keys.setdefault(content_key, match.match_id)
        if twin is not None:
            self.possible_duplicates[match.match_id] = twin
        self.processed_match_ids.add(match.match_id)

    def _self_reported_factor(
        self, match: Match
    ) -> tuple[MatchResult | None, float, MatchupKey | None]:
        """
        _consider's self-reported checks: (rejection or None, repeat_factor, matchup).

        For a match that is not self-reported: (None, 1.0, None). For a
        self-reported one, in order: under self_reported_requires_both_teams,
        confirmed_by must name a player from each team
        (SELF_REPORTED_UNCONFIRMED); then the counted self-reported matches
        between the same two teams in the repeat window must be below
        self_reported_max_repeats (SELF_REPORTED_REPEAT_LIMIT). Otherwise
        repeat_factor is self_reported_repeat_factor**prior. Changes nothing.
        """
        cfg = self.cfg
        repeat_factor = 1.0
        matchup = None
        if match.match_type == MatchType.SELF_REPORTED:
            if cfg.self_reported_requires_both_teams:
                confirmed = set(match.confirmed_by)
                if not (confirmed & set(match.team_a) and confirmed & set(match.team_b)):
                    unconfirmed = MatchResult(
                        False, match.rating_format, RejectionReason.SELF_REPORTED_UNCONFIRMED
                    )
                    return unconfirmed, repeat_factor, matchup
            matchup = _matchup_key(match)
            cutoff = _days_before(match.played_on, cfg.self_reported_repeat_window_days)
            prior = sum(
                1 for d in self.self_reported_log.get(matchup, []) if cutoff <= d <= match.played_on
            )
            if prior >= cfg.self_reported_max_repeats:
                limited = MatchResult(
                    False, match.rating_format, RejectionReason.SELF_REPORTED_REPEAT_LIMIT
                )
                return limited, repeat_factor, matchup
            repeat_factor = cfg.self_reported_repeat_factor**prior
        return None, repeat_factor, matchup

    def _note_seed_sensitive(self, match: Match, result: MatchResult) -> None:
        """Record a rejected update whose outcome a new seed could change."""
        if result.reason in _SEED_SENSITIVE_REASONS:
            self._changed()
            players = tuple(sorted(match.team_a + match.team_b))
            self._seed_sensitive_rejections[match.match_id] = (result.rating_format, players)

    def _commit(
        self, rating_format: RatingFormat, updates: dict[str, RatingState], live: bool
    ) -> None:
        for player_id, state in updates.items():
            if live:
                state.live_updates_since_replay += 1
            self._put_state(self._player_or_new(player_id), rating_format, state)

    def _compute(
        self, match: Match, rating_format: RatingFormat, repeat_factor: float
    ) -> tuple[MatchResult, dict[str, RatingState] | None, float]:
        """
        Compute one format's update on working copies. Mutates nothing.

        Returns the result, the new states of the players who moved (None
        when the update does not count), and the net rating the update adds:
        math.fsum of the unrounded applied changes (0.0 when it does not
        count).
        """
        cfg = self.cfg
        self._validate_teams(match.team_a, match.team_b, rating_format)
        ids = match.team_a + match.team_b
        work = {pid: _clone_state(self._peek_state(pid, rating_format)) for pid in ids}

        # A player whose imported baseline is dated on or after this match
        # already has it reflected in that baseline: use, but do not move.
        frozen = {
            pid
            for pid in ids
            if (baseline_date := work[pid].initial_source_date) is not None
            and match.played_on <= baseline_date
        }
        if len(frozen) == len(ids):
            return (
                MatchResult(False, rating_format, RejectionReason.BEFORE_IMPORTED_BASELINE),
                None,
                0.0,
            )

        # Live results apply in arrival order. A match dated before any
        # player's last match here, or before the date a copied category
        # rating already reflects (frozen players included), arrived late.
        late = any(_is_late(work[pid], match.played_on) for pid in ids)

        states_a = [work[pid] for pid in match.team_a]
        states_b = [work[pid] for pid in match.team_b]
        rating_a = math.fsum(s.rating for s in states_a) / len(states_a)
        rating_b = math.fsum(s.rating for s in states_b) / len(states_b)
        rejected = self._gap_rejection(rating_format, states_a, states_b, rating_a, rating_b)
        if rejected is not None:
            return rejected

        for pid in ids:
            if pid not in frozen:
                work[pid].rd = inflated_rd(work[pid], match.played_on, cfg)

        scale = cfg.scale_by_scoring[match.game_format.scoring]
        actual, _ = _actual_point_share(match, cfg)
        expected = expected_point_share(rating_a, rating_b, scale)
        surprise = actual - expected

        effective_weight = (
            cfg.match_weights[match.match_type] * repeat_factor * information_multiplier(match, cfg)
        )

        deltas, applied, clamped = self._update_players(
            match, work, frozen, effective_weight, surprise
        )

        result = self._build_result(
            match,
            rating_format,
            expected,
            actual,
            surprise,
            effective_weight,
            deltas,
            applied,
            frozen,
            clamped,
            late,
        )
        moved = {pid: work[pid] for pid in ids if pid not in frozen}
        return result, moved, math.fsum(applied.values()) + 0.0

    def _gap_rejection(
        self,
        rating_format: RatingFormat,
        states_a: list[RatingState],
        states_b: list[RatingState],
        rating_a: float,
        rating_b: float,
    ) -> tuple[MatchResult, None, float] | None:
        """
        _compute's rejection by an eligibility filter, or None when both pass.

        In order: in singles, the team rating gap against
        max_singles_rating_gap; in a doubles format, each team's partner gap
        (team A first) against max_doubles_partner_gap.
        """
        cfg = self.cfg
        if (
            rating_format is RatingFormat.SINGLES
            and cfg.max_singles_rating_gap is not None
            and abs(rating_a - rating_b) > cfg.max_singles_rating_gap
        ):
            return MatchResult(False, rating_format, RejectionReason.SINGLES_RATING_GAP), None, 0.0
        if cfg.max_doubles_partner_gap is not None and rating_format.team_size == 2:
            for team_states in (states_a, states_b):
                if abs(team_states[0].rating - team_states[1].rating) > cfg.max_doubles_partner_gap:
                    return (
                        MatchResult(False, rating_format, RejectionReason.DOUBLES_PARTNER_GAP),
                        None,
                        0.0,
                    )
        return None

    def _update_players(
        self,
        match: Match,
        work: dict[str, RatingState],
        frozen: set[str],
        effective_weight: float,
        surprise: float,
    ) -> tuple[dict[str, float], dict[str, float], list[str]]:
        """
        Apply _apply_update to each player who is not frozen, on the working states.

        Team A's players first, then team B's, each in team order. A frozen
        player's change is 0.0. Returns each player's change rounded to 5
        places, each unrounded change, and the clamped players in update
        order.
        """
        # Each player moves by their own K, so team-average movement is not
        # always zero-sum; imbalance reports the net.
        deltas: dict[str, float] = {}
        applied: dict[str, float] = {}  # unrounded, for imbalance
        clamped: list[str] = []
        for team_ids, opponent_ids, sign in (
            (match.team_a, match.team_b, 1.0),
            (match.team_b, match.team_a, -1.0),
        ):
            for pid in team_ids:
                if pid in frozen:
                    deltas[pid] = applied[pid] = 0.0
                    continue
                applied[pid] = self._apply_update(
                    work[pid],
                    pid,
                    match,
                    team_ids,
                    opponent_ids,
                    sign,
                    effective_weight,
                    surprise,
                    clamped,
                )
                deltas[pid] = round(applied[pid], _RESULT_PLACES)
        return deltas, applied, clamped

    def _apply_update(
        self,
        state: RatingState,
        pid: str,
        match: Match,
        team_ids: tuple[str, ...],
        opponent_ids: tuple[str, ...],
        sign: float,
        effective_weight: float,
        surprise: float,
        clamped: list[str],
    ) -> float:
        """
        Apply the update rule to one player's working state; return the unrounded change.

        The rule in docs/model.md, "The update". The caller has already
        grown state.rd for inactivity and skips frozen players. In order: K
        from that RD, the raw step, the clamp to the rating range, the
        clamped rule (pid is appended to clamped), the RD shrink, the date
        and count updates, and the partner and opponent marks.
        """
        cfg = self.cfg
        raw_delta = sign * player_k(state, cfg) * effective_weight * surprise
        old_rating = state.rating
        state.rating = max(cfg.min_rating, min(cfg.max_rating, old_rating + raw_delta))
        if (
            abs(state.rating - (old_rating + raw_delta)) > _CLAMP_TOLERANCE
            or abs((state.rating - old_rating) - raw_delta) >= _VISIBLE_LOSS
        ):
            clamped.append(pid)
        state.rd = max(
            cfg.rd_min,
            state.rd * (1.0 - cfg.rd_shrink_per_match * effective_weight),
        )
        # Dates never move back, so a late match does not rerun
        # inactivity or drop a recent partner from the window.
        state.rd_as_of = _latest(state.rd_as_of, match.played_on)
        state.matches_played += 1
        if match.match_type in _VERIFIED_MATCH_TYPES:
            state.verified_matches_played += 1
        state.last_played = _latest(state.last_played, match.played_on)
        _seen(state.partners, (other for other in team_ids if other != pid), match.played_on)
        _seen(state.opponents, opponent_ids, match.played_on)
        return state.rating - old_rating

    def _build_result(
        self,
        match: Match,
        rating_format: RatingFormat,
        expected: float,
        actual: float,
        surprise: float,
        effective_weight: float,
        deltas: dict[str, float],
        applied: dict[str, float],
        frozen: set[str],
        clamped: list[str],
        late: bool,
    ) -> MatchResult:
        """
        The counted MatchResult of _compute.

        First the imbalance, from the unrounded changes, then the result,
        with expected_a, actual_a, surprise, and effective_weight rounded.
        """
        # From the unrounded changes, so rounding residue never shows. Adding
        # 0.0 turns a negative zero into 0.0.
        imbalance = (
            round(
                math.fsum(applied[pid] for pid in match.team_a) / len(match.team_a)
                + math.fsum(applied[pid] for pid in match.team_b) / len(match.team_b),
                _RESULT_PLACES,
            )
            + 0.0
        )

        result = MatchResult(
            counted=True,
            rating_format=rating_format,
            expected_a=round(expected, _RESULT_PLACES),
            actual_a=round(actual, _RESULT_PLACES),
            surprise=round(surprise, _RESULT_PLACES),
            effective_weight=round(effective_weight, _RESULT_PLACES),
            deltas=deltas,
            frozen=tuple(sorted(frozen)),
            clamped=tuple(clamped),
            imbalance=imbalance,
            late=late,
        )
        return result

    # -- reporting -------------------------------------------------------------

    def player_summary(
        self, player_id: str, rating_format: RatingFormat, as_of: date | None = None
    ) -> PlayerSummary:
        """
        The player's rating, reliability, and provenance in one format.

        It never changes engine state; it only fills a private cache of
        connectivity results, derived from the stored states. The engine never
        reads the wall clock.

        Args:
            player_id: The player, exactly a str (a subclass is rejected).
            rating_format: Exactly a RatingFormat.
            as_of: The reference date that inactivity and reliability are
                measured at. With no as_of, it is the later of calculated_as_of
                (the last replay date) and the player's last_played.

        Returns:
            A PlayerSummary. For a known player with no state in the format, it
            reports the starting state that format's first match would use if
            played now, without storing it.

        Raises:
            ReferenceDateError: as_of is None and the engine was never replayed,
                or as_of is not a plain date. These checks come first.
            PlayerError: the id is blank or not exactly a str. This check comes
                before the UnknownPlayerError check.
            UnknownPlayerError: no player record exists for the id.
            PlayerError: once the player is found, a rating_format that is not
                exactly a RatingFormat.

        See docs/behavior.md, "Player summaries" and "Reference dates".
        """
        if as_of is not None and not _is_plain_date(as_of):
            raise ReferenceDateError(
                f"player_summary as_of must be exactly a date, not {_type_name(as_of)}"
            )
        reference = as_of if as_of is not None else self.calculated_as_of
        if reference is None:
            raise ReferenceDateError(
                "player_summary needs as_of when the engine has not been replayed:"
                " pass as_of or replay first"
            )
        cfg = self.cfg
        player = self.player(player_id)
        state = self.state(player_id, rating_format)
        if as_of is None and state.last_played is not None:
            reference = max(reference, state.last_played)
        connectivity = self._connectivity_of(player_id, rating_format, reference)
        earned = _reliability(state, cfg, reference)
        cap = cfg.connectivity_reliability_cap
        if cap is not None and not connectivity.connected:
            # The cap as displayed, 1 place, never above the cap itself.
            shown = round(float(cap), _RELIABILITY_PLACES)
            if shown > cap:
                shown = round(shown - _RELIABILITY_STEP, _RELIABILITY_PLACES)
            earned = min(earned, shown)

        imported = _imported_reliability(state, cfg, reference, earned)

        return PlayerSummary(
            player_id=player.id,
            rating_format=rating_format,
            rating=round(state.rating, _SUMMARY_RATING_PLACES),
            reliability=earned,
            imported_reliability=imported,
            established=(
                earned >= cfg.established_reliability_threshold
                and state.verified_matches_played >= cfg.established_min_verified_matches
            ),
            rd=round(inflated_rd(state, reference, cfg), _SUMMARY_RD_PLACES),
            matches_played=state.matches_played,
            last_played=state.last_played,
            initial_rating=state.initial_rating,
            initial_source=state.initial_source,
            initial_source_reliability=state.initial_source_reliability,
            initial_source_date=state.initial_source_date,
            calculated_as_of=self.calculated_as_of,
            live_updates_since_recalculation=(
                state.live_updates_since_replay + state.inherited_live_updates
            ),
            config_version=cfg.version,
            account_linked=player.account_user_id is not None,
            connectivity=connectivity,
        )

    def _connectivity_of(
        self, player_id: str, rating_format: RatingFormat, reference: date
    ) -> NetworkConnectivity:
        """The player's NetworkConnectivity, from the cache or a new build of the format."""
        key = (rating_format, reference)
        if key not in self._connectivity:
            if len(self._connectivity) >= _CONNECTIVITY_CACHE_SIZE:
                # Drop the oldest entry. Insertion order is fixed by the calls
                # made, and a cache entry never changes a result.
                del self._connectivity[next(iter(self._connectivity))]
            self._connectivity[key] = _network_connectivity(
                self.players, rating_format, reference, self.cfg
            )
        found, largest = self._connectivity[key]
        if player_id in found:
            return found[player_id]
        # No stored state: measure the starting state state() returns, a
        # player with no contacts and a group of one.
        return NetworkConnectivity(
            pool_size=1,
            fringe_players=0,
            outside_players=0,
            outside_days=0,
            group_size=1,
            in_main_network=2 >= max(largest, 1),
            connected=False,
        )


def _load_snapshot(engine: RatingEngine, snapshot: object) -> None:
    """Load a snapshot, checked under engine.cfg, into a new empty engine."""
    state = _read_snapshot(snapshot, engine.cfg)
    engine.players = state.players
    engine.processed_match_ids = state.processed_match_ids
    engine._reserved_match_ids = state.reserved_match_ids
    engine.self_reported_log = state.self_reported_log
    engine._content_keys = state.content_keys
    engine.possible_duplicates = state.possible_duplicates
    engine.calculated_as_of = state.calculated_as_of
    engine.replay_info = state.replay_info
    engine._ledger_digests = state.ledger_digests
    engine._ledger_player_ids = state.ledger_player_ids
    engine._seed_sensitive_rejections = state.seed_sensitive_rejections
    engine._resumable = state.resumable
