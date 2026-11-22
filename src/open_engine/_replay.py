"""
Deterministic historical replay and resume from a snapshot.

Demonstration only: not validated on real match data, outputs are not OPEN
ratings, and the API may change without notice.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date

from ._config import Config, _config_or_default, _config_shape
from ._engine import RatingEngine, _load_snapshot
from ._errors import LedgerConflictError, ReferenceDateError, SnapshotError
from ._ledger import _ledger, _match_digest
from ._model import Match, Player, ReplayInfo, _zero_by_format
from ._seeds import _check_seed_date
from ._validation import _is_plain_date, _type_name

# ---------------------------------------------------------------------------
# Deterministic historical replay
# ---------------------------------------------------------------------------


def _engine_under(cfg: Config) -> RatingEngine:
    """An empty engine under cfg, already checked and rebuilt: no second rebuild."""
    engine = RatingEngine.__new__(RatingEngine)
    engine._start(cfg)
    return engine


def replay(
    matches: Iterable[Match],
    seed_players: Iterable[Player] = (),
    *,
    cfg: Config | None = None,
    as_of: date,
    from_snapshot: Mapping[str, object] | None = None,
    whole_ledger: bool = False,
) -> RatingEngine:
    """
    Rebuild authoritative ratings from seeds plus the immutable match ledger.

    Same ledger, seeds, config and as_of always produce the same result.

    Args:
        matches: The ledger rows, any iterable, read once into a list.
        seed_players: Seeds that follow the seed rules, dated on or before
            as_of; any iterable, read once.
        cfg: None or exactly a Config with only its fields.
        as_of: The replay date, a plain date.
        from_snapshot: An earlier replay's to_snapshot() output, to resume from.
        whole_ledger: Exactly a bool. True, with from_snapshot, promises the whole ledger.

    Returns:
        The rebuilt RatingEngine.

    Raises:
        ReferenceDateError: as_of is not a plain date. It is checked first.
        SnapshotError: whole_ledger is not exactly a bool, then is True without
            from_snapshot (both before cfg); later, a malformed snapshot, one
            under another Config, or a resume that cannot equal a full replay.
        ConfigError: cfg is not valid: its type before matches, seed_players,
            and from_snapshot are read; its values after matches and
            seed_players are read and before from_snapshot is.
        InvalidMatchError, InvalidSeedError, PlayerError: a bad row or seed, a
            seed after as_of, or a shared identity, also with a checkpoint player.
        LedgerConflictError: rows with one match_id differ, including, on
            resume, an edited checkpoint row or a later row reusing its id.

    See docs/behavior.md, "Replay and resume".
    """
    if not _is_plain_date(as_of):
        raise ReferenceDateError(f"replay as_of must be exactly a date, not {_type_name(as_of)}")
    if type(whole_ledger) is not bool:
        raise SnapshotError(f"whole_ledger must be a bool, not {_type_name(whole_ledger)}")
    if whole_ledger and from_snapshot is None:
        raise SnapshotError("whole_ledger needs from_snapshot: it only applies to a resume")
    shape = _config_shape(cfg)
    # Read each caller iterable once, before any row or seed is checked, so
    # a generator cannot change a row between its check and its use.
    rows = list(matches)
    seeds = list(seed_players)
    # The copy is built only now, after matches and seed_players are read:
    # a generator above could rewrite any Config it reaches (issue #122).
    checked = _config_or_default(shape)
    start = None
    if from_snapshot is not None:
        start = _resume_start(from_snapshot, checked, as_of)
    return _replay(
        rows,
        seeds,
        cfg=checked,
        as_of=as_of,
        check_seed_dates=True,
        track=True,
        start=start,
        whole_ledger=whole_ledger,
    )


def _resume_start(snapshot: Mapping[str, object], cfg: Config, as_of: date) -> RatingEngine:
    """
    The engine a resumed replay starts from. SnapshotError when it cannot resume.

    cfg is the copy replay already rebuilt, so it is not rebuilt again.
    """
    engine = _engine_under(cfg)
    _load_snapshot(engine, snapshot)
    checkpoint = engine.calculated_as_of
    if checkpoint is None:
        raise SnapshotError("Cannot resume: the snapshot is not from a replay")
    live = sorted(engine.processed_match_ids - set(engine._ledger_digests))
    if live or any(
        state.live_updates_since_replay or state.inherited_live_updates
        for player in engine.players.values()
        for state in player.ratings.values()
    ):
        raise SnapshotError("Cannot resume: the snapshot has live updates since its replay")
    if not engine._resumable:
        raise SnapshotError(
            "Cannot resume: the engine changed after its replay. Take the checkpoint "
            "snapshot right after replay, before any other call on the engine"
        )
    if as_of < checkpoint:
        raise SnapshotError(
            f"Cannot resume: as_of {as_of.isoformat()} is before the checkpoint "
            f"date {checkpoint.isoformat()}"
        )
    return engine


def _replay(
    matches: Iterable[Match],
    seed_players: Iterable[Player],
    *,
    cfg: Config,
    as_of: date,
    check_seed_dates: bool,
    track: bool,
    start: RatingEngine | None = None,
    whole_ledger: bool = False,
) -> RatingEngine:
    """
    replay, with the seed date check optional, from a fresh engine or start.

    Scheduled prequential windows replay to dates before a seed captured
    mid-ledger, which is legitimate there (earlier matches are frozen for
    that player), so they skip the check. They also pass track=False: only
    replay records what a checkpoint needs (row digests), since hashing
    every row in every window would slow evaluation for nothing.

    cfg is a checked, rebuilt Config, used as it is. start is a checked
    checkpoint engine (see _resume_start). Every row is
    checked against its digests before any row is recorded, and only rows
    dated after its checkpoint are recorded.
    """
    engine = _engine_under(cfg) if start is None else start
    named_before = frozenset(engine._ledger_player_ids)
    held = engine._held_identities()
    for player in seed_players:
        engine._add_seed(player, held)
        if player.id in named_before:
            raise SnapshotError(
                f"Cannot resume: the seed for {player.id} names a player in rows the "
                "checkpoint considered; a new seed for that player needs a full replay"
            )
        if check_seed_dates:
            _check_seed_date(player, as_of, "as_of")

    rows = _ledger(matches)
    considered = counted = 0
    created = _zero_by_format()
    checkpoint: date | None = None
    if start is not None:
        checkpoint = start.calculated_as_of
        info = start.replay_info
        if checkpoint is None or info is None:
            raise SnapshotError("Cannot resume: the snapshot is not from a replay")
        _check_resume_rows(rows, checkpoint, start._ledger_digests, whole_ledger)
        considered = info.matches_considered
        counted = info.matches_counted
        created.update(info.net_rating_created)

    for match in rows:
        if match.played_on > as_of:
            continue
        if checkpoint is not None and match.played_on <= checkpoint:
            continue
        considered += 1
        if track:
            engine._ledger_digests[match.match_id] = _match_digest(match)
            engine._ledger_player_ids.update(match.team_a + match.team_b)
        result, net = engine._record(match, as_of=as_of)
        if result.counted:
            counted += 1
        # Added in ledger order, so a resume continues from the same float.
        for fmt, value in net.items():
            created[fmt] += value

    engine.calculated_as_of = as_of
    engine._resumable = track
    engine.replay_info = ReplayInfo(
        as_of=as_of,
        config_version=engine.cfg.version,
        config_fingerprint=engine.cfg.fingerprint(),
        params_version=engine.cfg.params_version,
        parameter_fingerprint=engine.cfg.parameter_fingerprint(),
        matches_considered=considered,
        matches_counted=counted,
        net_rating_created=created,
    )
    return engine


def _check_resume_rows(
    rows: list[Match], checkpoint: date, digests: Mapping[str, str], whole_ledger: bool
) -> None:
    """
    Raise unless every row agrees with the checkpoint.

    A row dated on or before the checkpoint date must be a row the
    checkpoint considered, unchanged. A row dated after it must not reuse
    one of those ids. With whole_ledger, every row the checkpoint
    considered must also be among the rows. Conflicts raise
    LedgerConflictError first, then rows not in the checkpoint raise
    SnapshotError, then (with whole_ledger) checkpointed rows missing from
    the ledger raise SnapshotError, each naming every id, sorted.
    """
    conflicts: set[str] = set()
    missing: set[str] = set()
    for match in rows:
        seen = digests.get(match.match_id)
        if match.played_on > checkpoint:
            if seen is not None:
                conflicts.add(match.match_id)
        elif seen is None:
            missing.add(match.match_id)
        elif seen != _match_digest(match):
            conflicts.add(match.match_id)
    if conflicts:
        raise LedgerConflictError(conflicts)
    if missing:
        raise SnapshotError(
            "Cannot resume: rows dated on or before the checkpoint date are not in the "
            "checkpoint: " + ", ".join(sorted(missing))
        )
    if whole_ledger:
        removed = set(digests) - {match.match_id for match in rows}
        if removed:
            raise SnapshotError(
                "Cannot resume: rows the checkpoint considered are missing from the ledger: "
                + ", ".join(sorted(removed))
            )
