# Plan: Snapshot loading accepts states the engine cannot produce (issue #61)

Risk tier: Medium. It changes existing behavior (from_snapshot rejects more
snapshots) in replay and resume validation, which CLAUDE.md lists as
security-sensitive, but it only tightens validation, adds no state, and unit
tests exercise every branch.

Domain skills: superpowers:test-driven-development, invoke. Django, Celery,
FastAPI, React, Expo, and the other framework skills: not applicable because
this is a pure Python package with no framework, UI, API, data store, auth, or
email.

## Acceptance criteria

`RatingEngine.from_snapshot` (and so `replay(from_snapshot=...)`) raises
`SnapshotError` naming the field for each of these states, all of which load
on main today:

1. A resumable snapshot with a `processed_match_ids` entry that is not a key of
   `ledger_digests`.
2. A resumable snapshot with a rating state whose `live_updates_since_replay`
   is above 0.
3. A resumable snapshot where a player with a state with `matches_played > 0`
   is not in `ledger_player_ids`.
4. Any snapshot (resumable or not) where a `partners` or `opponents` id of a
   rating state is not a player id in `players`.
5. Any snapshot where `account_user_id` is not null and is blank or
   whitespace only.
6. Any snapshot where an `external_ids` source or id is blank or whitespace
   only.
7. Any snapshot where two players hold the same `account_user_id`.
8. Any snapshot where two players hold the same `(source, external id)` pair.
9. (Also fixed, same class as item 4.) Any snapshot where a
   `self_reported_log` team names a player id not in `players`.

And:

- Every snapshot the engine's own methods produce still loads: a replay with
  seeds (imported and provisional), a chained resume, a live `record` after a
  replay, `link_account`, `attach_external_id`, `seed_external_rating` with an
  external id, `add_player` seeds with identities, and rejected matches.
- `tests/test_snapshot_identities.py` is rewritten to the new rule: blank and
  shared identities are rejected; distinct, non-blank identities round trip.
- The `from_snapshot` docstring, the README snapshot schema text, and
  `CHANGELOG.md` (under `## [Unreleased]`, marked **Breaking** since stored
  snapshots that loaded before can now fail) describe the new rules.
- The snapshot schema does not change, so `SNAPSHOT_VERSION` stays 5.

Out of scope: issue #74 (provenance fields on rating states such as
`initial_source` and `initial_external_player_id`). `seed_external_rating`
can still store a blank `initial_source` or an empty
`initial_external_player_id` today, so snapshot loading cannot reject them
until #74 decides the entry-point rule. Leave #74 open.

## Why no engine method produces these states

- 1: replay adds a digest for every considered row and records only those
  rows, so every counted (processed) id has a digest. Resume carries both
  sets from the checkpoint. A live `record` adds a processed id without a
  digest but clears `_resumable` first.
- 2: replay calls `record(..., as_of=...)`, which is not live and never
  increments `live_updates_since_replay`. Seeds must have 0. Resume refuses a
  checkpoint with live updates. A live record clears `_resumable`.
- 3: replay adds every player named in a considered row to
  `_ledger_player_ids` before recording it, and only recording plays a state.
  Seeds have no matches. Resume carries the set.
- 4 and 9: `record` creates every match player through `_player_or_new`
  before it writes partners, opponents, or the self-reported log, and no
  method removes a player. Seeds have empty partner and opponent maps.
- 5 to 8: since #52, `link_account`, `attach_external_id`,
  `seed_external_rating` (which links through `attach_external_id`, and only
  when `external_player_id` is truthy), `add_player`, and replay seeds all
  reject blank ids and identities another player holds. `record` and
  `seed_provisional_rating` create players with no identity. Lookups are
  read-only since #23.

## Files that change

- `src/open_engine/engine.py`:
  - `_snap_player`: external_ids source and id via `_is_id`, and
    `account_user_id` null or `_is_id`, raising `SnapshotError` naming the
    path (for example `snapshot.players[0].account_user_id must be null or a
    non-blank str`, `snapshot.players[0].external_ids must map non-blank str
    to non-blank str`). Keep the type error messages that exist.
  - `_read_snapshot`: after reading players, check identities are unique in
    player order, naming the later path
    (`snapshot.players[1].account_user_id is held by another player`,
    `snapshot.players[1].external_ids[<source>] is held by another player`).
    Then check every partners and opponents key of every state is a key of
    `players` (`snapshot.players[0].ratings.doubles.partners names
    'syn-x', which is not in players`), and every self_reported_log team
    member is a player (`snapshot.self_reported_log[0].teams names 'syn-x',
    which is not in players`). Iterate in deterministic order (player list
    order, `sorted` over dict keys where messages depend on it).
  - `_check_resumable`: for each state, `live_updates_since_replay` must be 0
    (`...live_updates_since_replay must be 0 in a resumable snapshot`), and a
    player with any state with `matches_played > 0` must be in
    `ledger_player_ids` (`snapshot.players[0].id 'syn-a' has played matches
    but is not in ledger_player_ids in a resumable snapshot`). After the count
    checks, every processed id must be a ledger_digests key (name the first,
    sorted: `snapshot.processed_match_ids names 'syn-x', which is not in
    ledger_digests in a resumable snapshot`).
  - `from_snapshot` docstring: replace "Identities are only type checked ...
    still loads." with the new identity rules, and add the partner, opponent,
    log, and resumable rules.
- `tests/test_snapshot_identities.py`: rewrite (see Proof).
- `tests/test_snapshot_invariants.py`: add TAMPERED rows for 1 to 4 and 9, and
  update its module docstring line about identities.
- `README.md`: the snapshot schema / loading rules text, where it says
  identities are only type checked or lists the load rules.
- `CHANGELOG.md`: one entry under `## [Unreleased]`, **Breaking**.
- `tests/test_replay_resume.py`: (added during the work)
  `test_resume_rejects_a_live_update_count_without_a_live_match` matched
  "live update" on the resume message. The load check now rejects that
  resumable snapshot first, so its match changes to the
  `live_updates_since_replay must be 0 in a resumable snapshot` message.
  The behavior it pins (resume refuses) is the same.
- `tests/test_snapshot_engine_made.py` (added during the work): proof
  tests that a replay with rejected rows (a forfeit, an unconfirmed
  self-reported match, and a content duplicate copy, under both
  `reject_content_duplicates` values) round trips and resumes, and that an
  engine seeded by `seed_external_rating` with no external id round trips.
  They live in a new file because the two red-test files are frozen.
- Check order (added during the work): the live update check runs after
  the date and inherited checks in `_check_resumable`, so a snapshot with
  both a parent live count and an inherited count still reports
  `inherited_live_updates must be 0 in a resumable snapshot`, as
  `tests/test_inherited_live_updates.py` expects.

## Order of work

1. Red tests (one commit): new TAMPERED rows and the rewritten identity tests,
   plus the "engine-produced snapshots load" tests. Confirm each new rejection
   test fails on the current code because the snapshot loads.
2. Production change in `engine.py` until green.
3. Docstring, README, CHANGELOG.
4. Full gate.

## Risks

- Over-strict rules that reject an engine-made snapshot. Mitigated by the
  load tests below and by the existing suite (chained resumes, live work,
  category states, rejected matches).
- Message wording: existing tests match on SnapshotError messages; keep old
  messages for the type errors.
- Error order: a snapshot with several faults reports the first; keep checks
  in a fixed order.

## Proof

- Criteria 1 to 4 and 9: new `TAMPERED` rows in
  `tests/test_snapshot_invariants.py`, each mutating a fresh resumable
  snapshot. Criterion 4 also gets a non-resumable case (set `resumable` to
  false) to show it applies to any snapshot.
- Criteria 5 to 8: `tests/test_snapshot_identities.py`, parametrized over a
  shared account, a shared external id, a blank account, a blank source, and a
  blank external id, built by editing a snapshot's player dicts, each
  expecting `SnapshotError` with its message. Plus a round-trip test for an
  engine with distinct identities made by `link_account`,
  `attach_external_id`, and `seed_external_rating`.
- "Engine-made snapshots still load": a test that builds snapshots with
  `link_account`, `attach_external_id`, `seed_external_rating` (with and
  without an external id), a replay, a resume from it, a chained resume, and
  a live `record` after a replay, and asserts each round trips through
  `json.dumps` / `from_snapshot` with an equal `to_snapshot()`.
- Measured on main (9c38ec0), each of states 1 to 9 built from the
  `test_snapshot_invariants.py` snapshot loads without error (scratch script
  `repro.py`: all nine printed `LOADS`).
- No live drive: this is a library, and the automated gate covers the change.
