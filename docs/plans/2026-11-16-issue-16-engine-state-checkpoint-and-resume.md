# Plan: Engine state cannot be checkpointed, and replay cannot resume (issue #16)

Risk tier: High. It changes the replay path, which CLAUDE.md lists as security-sensitive (determinism and provenance). It adds a public API and an exported error. A loaded snapshot sets every rating, so loose validation would let stored data change ratings.

Domain skills: superpowers:test-driven-development: invoke (implementer). Django, Celery, Ninja, FastAPI, allauth, React, Expo: not applicable because this is a standard-library-only Python package with no framework, UI, auth, payments, or email.

## Acceptance criteria

1. `RatingEngine.to_snapshot()` returns a JSON-safe dict (`json.dumps(..., allow_nan=False)` works with no custom encoder). `RatingEngine.from_snapshot(snapshot, cfg=None)` rebuilds an engine from it, or from `json.loads` of its JSON text.
2. Round trip preserves every player's every `RatingState` field (rating, RD, `rd_as_of`, `matches_played`, `last_played`, provenance fields, `live_updates_since_replay`, partner and opponent dates), each player's `external_ids` and `account_user_id`, `processed_match_ids`, `self_reported_log`, `calculated_as_of`, and `replay_info`. `to_snapshot(from_snapshot(s)) == s`. Numbers keep their type (an int stays an int).
3. The output is deterministic: two engines built from the same ledger in different input orders give identical `json.dumps(snapshot, sort_keys=True)` text.
4. The schema is versioned (`snapshot_version`, 1) and carries `config_version`, `config_fingerprint`, and `calculated_as_of`. `from_snapshot` raises `SnapshotError` for another `snapshot_version`, for a fingerprint or config version that does not match the `Config` it is given, and for any missing key, unknown key, or value of the wrong type or out of range.
5. `replay(matches, seed_players, *, cfg, as_of, from_snapshot=snapshot)` continues from a checkpoint. For any ledger and any checkpoint taken by `replay(..., as_of=C)` with `C <= as_of`, the resumed engine's snapshot equals the snapshot of a full `replay(..., as_of=as_of)`, whether the caller passes the whole ledger or only the rows after `C`. A property-style test checks this over many random synthetic ledgers and cut dates.
6. Resume gives the same result as a full replay of the ledger and seeds the rows come from. It detects every row it is given that disagrees with the checkpoint. With `whole_ledger=True` it also detects checkpointed rows removed from the ledger. It cannot detect a seed that was withdrawn or corrected after the checkpoint, with or without `whole_ledger`. Without `whole_ledger` it cannot detect a removed row, or a late row the caller filtered out. Each of those cases needs a full replay. It raises instead in these cases:
   - `SnapshotError` when the snapshot is not from a replay, when it has live updates or any other change since that replay, when `as_of` is before the checkpoint date, or when a ledger row dated on or before the checkpoint date is not in the checkpoint.
   - `LedgerConflictError` when a row dated on or before the checkpoint date differs from the row the checkpoint counted under that `match_id`, or a row dated after it reuses one of those ids.
   - With `whole_ledger=True`, `SnapshotError` naming (sorted) every `match_id` in the snapshot's `ledger_digests` that is not among the rows passed. `whole_ledger=True` without `from_snapshot` raises `SnapshotError`. Order: `LedgerConflictError`, then rows not in the checkpoint, then rows missing from the ledger.
   - `PlayerError` for a seed whose player is already in the snapshot, as `add_player` does, and `SnapshotError` for a seed whose player was named in a row the checkpoint considered (every such row may have been rejected, so the player is not in the snapshot, but a full replay with the seed would count them).
7. The snapshot schema is documented (README section, and the `to_snapshot` docstring). `SnapshotError` is exported, listed in `__all__` and the README "Public API", and has a docstring. `CHANGELOG.md` has an Added entry. The module docstring's behavior notes and production notes describe checkpoints.

## Design

### New error

`SnapshotError(OpenEngineError)` in `_errors.py`: "A snapshot cannot be loaded or resumed from." Exported from `engine.py` (explicit re-export, like the others) and `open_engine`.

### Engine state additions

`RatingEngine.__init__` gains one internal attribute, `self._ledger_digests: dict[str, str] = {}`: for each ledger row a replay considered (`played_on <= as_of`), its `match_id` mapped to `_match_digest(row)`. Only `_replay` fills it. Live `record` does not.

`_match_digest(match: Match) -> str`: SHA-256 hex (64 chars) of `json.dumps(form, sort_keys=True, separators=(",", ":"), allow_nan=False)`, where `form` mirrors `_canonical`: every field except `played_at`, each value tagged with its exact type's `__module__` and `__qualname__`, recursing into dataclasses (as a list of `[name, form]` pairs), tuples and lists, and sets and frozensets (members sorted by their own JSON text, so the digest never depends on the hash seed); enums as `[type, value]`; dates as `[type, isoformat]`; ints in hex (no digit limit); floats with negative zero folded, and a non-finite float as its repr; `played_at` as `None` or `[UTC instant isoformat, utcoffset total seconds]`. Two validated rows get the same digest exactly when `_canonical` treats them as equal. A test checks pairs that `_canonical` separates (int vs float `best_of`, str vs enum, different UTC offsets for the same instant) and pairs it joins (same instant and offset with different zone objects).

Fix round 1 (T3): `_strict` used to compare a list or set field with `==` without recursing, so `games=([11, 1],)` and `games=([11, True],)` collapsed in `_ledger` while their digests differed, and a plain `set` in `confirmed_by` fell to `repr`, which depends on PYTHONHASHSEED. `_strict` now recurses into lists and sets with their exact type, as it does for tuples and frozensets, and `_json_form` mirrors it. This changes full replay: rows that differ only in a list or set member's type now raise `LedgerConflictError`, as the documented rule (every field matches in type and value) says. A test computes a set row's digest under several PYTHONHASHSEED values in subprocesses.

### `RatingEngine.to_snapshot(self) -> dict[str, object]`

Schema version 1. Keys:

```
snapshot_version: 1
engine_version: open_engine.__version__ (informational, not checked)
config_version: cfg.version
config_fingerprint: cfg.fingerprint()
calculated_as_of: ISO date or null
replay_info: null or {as_of, config_version, config_fingerprint, matches_considered, matches_counted}
players: list sorted by id of
  {id, account_user_id (str or null), external_ids {str: str},
   ratings {RatingFormat value: state}}  (ratings keys sorted)
  state: {rating, rd, matches_played, last_played, rd_as_of, initial_rating,
          initial_source, initial_source_date, initial_source_reliability,
          initial_external_player_id, initial_evidence_reference,
          live_updates_since_replay, partners {id: ISO date}, opponents {id: ISO date}}
processed_match_ids: sorted list
self_reported_log: list sorted by (rating_format, teams) of
  {rating_format, teams: [[ids sorted], [ids sorted]] (the two teams sorted), dates: [ISO dates, in stored order]}
ledger_digests: {match_id: digest}
ledger_player_ids: sorted list of every player id named in a row a replay considered (cumulative across resumes)
resumable: bool, true only while the engine is exactly what a replay produced
```

Dicts are emitted with sorted keys so output is deterministic. After building the dict, `to_snapshot` runs the same validator `from_snapshot` uses, so it raises `SnapshotError` (naming the field) instead of returning a snapshot that cannot load (for example a non-str `initial_evidence_reference` set by hand).

### `RatingEngine.from_snapshot(cls, snapshot: Mapping[str, object], cfg: Config | None = None) -> RatingEngine` (classmethod)

Validates everything before building, raising `SnapshotError` with the path of the bad value:
- `snapshot` is a Mapping with exactly the schema's keys (no missing, no unknown), at every level.
- `snapshot_version == 1` (an int, not bool).
- `config_fingerprint == cfg.fingerprint()` and `config_version == cfg.version`.
- Dates are str in exact `YYYY-MM-DD` form (`date.fromisoformat(s).isoformat() == s`).
- Ids (`id`, partner and opponent keys, team members, match ids) satisfy `_is_id`. Player ids are unique.
- Rating format keys are `RatingFormat` values. Each `self_reported_log` entry has two teams of the format's team size, no player in both, and nonempty `dates`.
- `rating` and `initial_rating`: int or finite float, not bool, `rating` within `min_rating`/`max_rating`. `rd` within `rd_min`/`rd_max`. `initial_source_reliability`: null or finite number in 0..100. Counts: int, not bool, nonnegative. `initial_source`: str. `initial_external_player_id`, `initial_evidence_reference`, `account_user_id`: str or null. `external_ids`: str to str.
- `replay_info` is null exactly when `calculated_as_of` is null; otherwise its `as_of` equals `calculated_as_of`, its config fields equal the snapshot's, its counts are nonnegative ints with `matches_counted <= matches_considered`.
- `ledger_digests`: str id to 64-char lowercase hex.
- Fix round 1 (T4), engine invariants, so only a state the engine can produce loads:
  - Unplayed state (`matches_played` 0): `rd` is `new_player_rd` or `external_seed_rd`, or at least `category_seed_rd` (a derived category state); no partners, opponents, or live updates. An imported reliability only with an `initial_source_date`.
  - `matches_played == 0` exactly when `last_played` is null; `live_updates_since_replay <= matches_played`; a player is never their own partner or opponent.
  - `initial_rating` within `min_rating`/`max_rating`. Every count at most `2**63 - 1`, so `json.dumps` can always write it.
  - (Removed in fix round 2, D2-F1: blank-id and uniqueness checks for `account_user_id` and `external_ids`. Only their types are checked, str or null and str to str, because the engine's own methods build shared and blank identities and identities do not affect ratings.)
  - For a resumable snapshot only: no state, partner, opponent, or self-reported date after `calculated_as_of`; `matches_considered == len(ledger_digests)` and `matches_counted == len(processed_match_ids)`. A live engine can hold later dates, so a non-resumable snapshot is not held to this.

Then builds `Player`/`RatingState` objects, sets `processed_match_ids`, `self_reported_log` (keys `(RatingFormat, frozenset({frozenset(team_a), frozenset(team_b)}))`), `calculated_as_of`, `replay_info`, `_ledger_digests`.

### `replay(..., from_snapshot: Mapping[str, object] | None = None)`

`_replay` gains an optional starting engine. With `from_snapshot`:
1. `engine = RatingEngine.from_snapshot(from_snapshot, cfg)`.
2. `SnapshotError` unless `engine.calculated_as_of` is set, every `processed_match_id` is in `_ledger_digests`, every state has `live_updates_since_replay == 0`, and the snapshot's `resumable` mark is true. Fix round 1 (T2): `RatingEngine._resumable` is set by `_replay` (replay path only) at the end and cleared by every state change outside it. The clears sit at the lowest shared mutation points: `_put_player` (used by `player()` creating a record and `add_player`), `_put_state` (used by `seed_external_rating`, `seed_provisional_rating`/`state()` creating a state, and `_commit` for a counted live `record`), and `_changed` (used by `link_account` and `attach_external_id`). A read that creates nothing leaves it set. `from_snapshot` restores it, and validation rejects a non-bool or a true mark without `calculated_as_of`.
3. `SnapshotError` if `as_of < checkpoint`.
4. Seeds go through `add_player` and the `as_of` date check, as in a full replay (`PlayerError` for an id already in the snapshot). Fix round 1 (T1): `SnapshotError` for a seed whose id is in the snapshot's `ledger_player_ids`, which `_replay(track=True)` fills from every considered row, counted or not.
5. `rows = _ledger(matches)`. Pre-pass over every row before any `record`: a row dated on or before the checkpoint must have its id in `_ledger_digests` (else collect as missing) with the same digest (else collect as conflict); a row dated after the checkpoint whose id is in `_ledger_digests` is a conflict. Raise `LedgerConflictError(conflicts)` first if any, then `SnapshotError` naming the missing ids (sorted).
6. Record each row dated after the checkpoint and on or before `as_of`, add its digest, count it.
7. `calculated_as_of = as_of`; `replay_info` counts are the snapshot's plus this run's.

Fix round 2 (D2-F2, user decision): `replay` gains keyword-only `whole_ledger: bool = False`. With `from_snapshot`, `_check_resume_rows` then also raises `SnapshotError` ("rows the checkpoint considered are missing from the ledger: ...", ids sorted) for every `ledger_digests` id not among the rows, after the conflict and not-in-checkpoint checks. Without `from_snapshot`, `whole_ledger=True` raises `SnapshotError` before anything else. Fix round 3: a `whole_ledger` that is not a bool (an int, str, or None) raises `SnapshotError` first, as a usage error of the resume API. The property test also resumes with `whole_ledger=True`.

A seed check order note: seeds are added before the ledger pre-pass, matching full replay, where seeds are checked before the ledger.

The full replay path also fills `_ledger_digests`, so a full replay's snapshot and a resumed one are byte-identical (criterion 5). `prequential_predictions` is unchanged in behavior. Fix round 1 (T6): only the `replay` path computes digests (`_replay(track=True)`); scheduled prequential windows pass `track=False`, so evaluation does no hashing. `_replay` checks resume state with an explicit `if` and `SnapshotError`, not `assert`, which `python -O` removes.

## Files that change

- `src/open_engine/_errors.py`: `SnapshotError`.
- `src/open_engine/engine.py`: re-export, `_match_digest`, `_ledger_digests`, `to_snapshot`, `from_snapshot`, validator helpers, `replay`/`_replay` resume, module docstring (Errors note gets `SnapshotError`; new behavior note on snapshots and resume; production note on persisting snapshots for live state).
- `src/open_engine/__init__.py`: export `SnapshotError`.
- `tests/test_snapshot.py`: new (round trip, schema, validation, determinism, coverage guard).
- `tests/test_replay_resume.py`: new (property test and resume rejections).
- `README.md`: Public API entries (`SnapshotError`; `RatingEngine` and `replay` lines mention the new methods/argument), new "Snapshots and checkpoints" section with schema v1.
- `CHANGELOG.md`: Added entry under Unreleased.
- `CLAUDE.md` (Phase 8): rule that new engine state goes into the snapshot schema with a `snapshot_version` bump.

No new module, so `scripts/check_wheel.py` `PACKAGE_FILES` does not change. No new dependency: the property test uses `random.Random(seed)` in a parametrized test, not a new dev package.

## Order of work

1. Red tests (test_snapshot.py) for the `SnapshotError` export, the round trip, determinism, the digest pairs, the coverage guard (`set(vars(RatingEngine()))` equals the attributes the snapshot covers, and `RatingState` fields equal the state keys), and the validation table. Commit failing tests. (The coverage guard and validation table were step 3; they move here so they fail before the code that makes them pass.)
2. `SnapshotError`, `_match_digest`, `_ledger_digests` filled by `_replay`, `to_snapshot`, `from_snapshot` with validation. Green.
3. (Folded into step 1.)
4. Red resume tests (test_replay_resume.py), then `replay(from_snapshot=...)`. Green.
5. Docs: module docstring, README, CHANGELOG.
6. Full gate.

## Risks

- Riskiest step: the resume pre-pass. A wrong boundary (`<` vs `<=` at the checkpoint date) silently double-counts or drops a day's matches. The property test cuts at dates that have matches on them, and a targeted test puts matches on the checkpoint date itself.
- Digest drift from `_canonical`: if they disagree, resume would accept a row full replay rejects, or the reverse. Covered by paired tests.
- A snapshot taken after a live update and then resumed would double-count; criterion 6 rejects it.
- JSON float round trip: Python's `repr` round trips floats exactly; ints stay ints. A test asserts type and value after `json.loads`.
- Unordered iteration: `to_snapshot` sorts every set and dict, so the hash-seeded order of `processed_match_ids` and frozensets never reaches output.
- Not done: snapshots of `prequential_predictions` state, compression, schema migration from future versions (version 1 only), checkpoint support in `record` itself, and storing the full `Config` in the snapshot (the caller passes it; the fingerprint proves it matches).

## Proof

- Criteria 1, 2: `test_snapshot.py` round trip through `json.dumps`/`json.loads`, field-by-field equality, type checks, `to_snapshot(from_snapshot(s)) == s`.
- Criterion 3: two input orders, identical JSON text.
- Criterion 4: parametrized malformed-snapshot table, config mismatch, version mismatch, each `pytest.raises(SnapshotError, match=...)`.
- Criterion 5: parametrized property test over random seeds: random synthetic ledger (singles, doubles, categories, every match type, self-reported repeats, an external seed and a category seed), random checkpoint date, compare `to_snapshot()` of the full replay to the resumed replay, both with the whole ledger and with only rows after the checkpoint, including a checkpoint date that has matches on it.
- Criterion 6: one test per rejection.
- Criterion 7: existing `tests/test_api.py` checks of `__all__`, README, and docstrings; changelog entry by review.
- Live drive: run a script that replays a synthetic ledger to a checkpoint, writes `to_snapshot()` to a JSON file, loads it in a new process, resumes with the remaining rows, and prints that the resumed and full snapshots are equal; then shows each rejection path (live update, late row, changed row, config mismatch) raising the documented error.

Measured on the default branch (b5fe113), for the "checkpoint plus remaining matches equals full replay" assumption: a scratch script built a 400-match synthetic ledger (singles, doubles, mixed doubles, every match type, an external doubles seed), ran `replay(ms, [seed], as_of=cut)` for cuts 2025-06-15 and 2025-10-01, then `record(m, as_of=2025-12-31)` for every `_ledger(ms)` row after the cut, and compared every player's `ratings`, `processed_match_ids`, and `self_reported_log` with `replay(ms, [seed], as_of=2025-12-31)`. Result: `players 12 counted 381 diffs 0`. A cut before the seed's capture date raised `InvalidSeedError` (seed dated after `as_of`), which is why resume adds seeds through the same check.

Fix round 2 (D2-F1), frozen test rewrite. The user approved on 2026-11-16 rewriting the round 1 frozen test `tests/test_snapshot_invariants.py`: its five identity rows (blank account, shared account, blank external source, blank external id, shared external id) are removed. Why: the public API builds these states (two replay seeds that share an `account_user_id` or an external id, a seed with blank ids, `link_account` and `attach_external_id` with whitespace), so `to_snapshot()` raised on engines the engine itself made. Identity values do not affect ratings. Tightening those entry points goes to a follow-up issue. The new frozen test `tests/test_snapshot_identities.py` round-trips each case. On the code before the fix it fails for the expected reason, `SnapshotError` from `to_snapshot`:

```
E                   open_engine._errors.SnapshotError: snapshot.players[1]: account_user_id 'acct-1' is linked to more than one player
E                   open_engine._errors.SnapshotError: snapshot.players[1]: external id 'x1' from 'src' is linked to more than one player
E               open_engine._errors.SnapshotError: snapshot.players[0].external_ids must not hold a blank source or id
E           open_engine._errors.SnapshotError: snapshot.players[0].account_user_id must not be blank
E               open_engine._errors.SnapshotError: snapshot.players[0].external_ids must not hold a blank source or id
FAILED tests/test_snapshot_identities.py::test_api_built_identities_round_trip[A shared account from seeds]
FAILED tests/test_snapshot_identities.py::test_api_built_identities_round_trip[A2 shared external id from seeds]
FAILED tests/test_snapshot_identities.py::test_api_built_identities_round_trip[A3 blank ids on a seed]
FAILED tests/test_snapshot_identities.py::test_api_built_identities_round_trip[B blank account from link_account]
FAILED tests/test_snapshot_identities.py::test_api_built_identities_round_trip[B2 blank source from attach_external_id]
5 failed in 0.10s
```

Fix round 3, criterion 6 wording. The user approved rewording criterion 6 (and the matching README sentence) on 2026-11-16. Why: the old wording, "never silently differs from a full replay of the rows it is given", was not true for seeds. A seed withdrawn or corrected after the checkpoint stays in the snapshot, and no check can see it, with or without `whole_ledger`. Repro (`scratchpad/delta3/d.py`): a checkpoint taken with an external seed for p-a, then resumed with the whole ledger, `whole_ledger=True`, and no seeds, gives p-a 5.0011, while a full replay without the seed gives 3.6179. The criterion now states what resume does guarantee and lists each case that needs a full replay, the withdrawn or corrected seed included.
