# Plan: Live record does not reserve the match_id of other rejected rows (issue #76)

Risk tier: High. The change touches who can move other players' ratings live and the replay's determinism (CLAUDE.md hard rule 7), changes existing behavior, and changes the snapshot rules.

Domain skills: django, django-ninja-api, django-allauth, django-celery, python-fastapi: not applicable because this is a standard-library Python package with no web framework. No other framework, UI, API, data, auth, payments, or email skill applies.

Decision (recorded on #76): option 1. Live `record` reserves the `match_id` of every row it considers, counted or not, as #73 does for matches that are not completed. A resubmission (for example a self-reported match after confirmation) must use a new `match_id`. A `possible_duplicates` entry no longer clears.

## Acceptance criteria

1. After any rejection other than `DUPLICATE_MATCH_ID`, live `record` reserves the match's `match_id`: every later `record` call with that id that does not raise returns `DUPLICATE_MATCH_ID`, whatever the new row holds. This covers `GAME_FORMAT_NOT_ACCEPTED`, `POSSIBLE_DUPLICATE`, `SELF_REPORTED_UNCONFIRMED`, `SELF_REPORTED_REPEAT_LIMIT`, `BEFORE_IMPORTED_BASELINE`, `SINGLES_RATING_GAP`, `DOUBLES_PARTNER_GAP`, and `MATCH_NOT_COMPLETED` (already reserved).
2. Live recording in ledger order and `replay` of the same ledger give identical state (the whole snapshot, less the fields only a replay sets and the live update labels) after a sequence that includes every `RejectionReason` that can occur live, including `DUPLICATE_MATCH_ID` for an identical resubmission. A resubmission that differs from the rejected row is `DUPLICATE_MATCH_ID` live and `LedgerConflictError` in replay, so neither counts it.
3. A rejection that reserves an id clears the snapshot's `resumable` mark (through `_changed`), as `MATCH_NOT_COMPLETED` does today.
4. A `possible_duplicates` entry never clears: the `pop` path for an id that later counts with no twin is gone, since that id is reserved.
5. Snapshot validation: the rule that a reserved id is never a `possible_duplicates` key is replaced. Under `reject_content_duplicates=True` every `possible_duplicates` key must be a reserved id (live and resumable). Every `ledger_digests` id must be processed or reserved, in any snapshot: only a replay writes digests, it counts or reserves every row it digests, and no id ever leaves either set, so a live snapshot is held to it too (revised after review: first scoped to resumable snapshots, which let an edited live snapshot free a rejected id). Each violation raises `SnapshotError` naming the field.
6. `SNAPSHOT_VERSION` goes from 6 to 7, because the meaning of `reserved_match_ids` changed: a version 6 snapshot leaves rejected ids free and would recreate the bug. A version 6 snapshot fails to load with `SnapshotError`.
7. Docs: module docstring (behavior notes and production notes: a corrected resubmission needs a new `match_id`, and only after a rejection that comes from the row itself; a row rejected for engine state is not resubmitted, since replay in ledger order can count it), `record`, `RejectionReason`, `to_snapshot`/`from_snapshot` docstrings, README (`record` notes, Public API `possible_duplicates` text, snapshot schema section), and a `**Breaking:**` CHANGELOG entry with a migration note.

## Files that change

- `src/open_engine/engine.py`: `_record` reserves on every rejection after the id check (the checks after the id check move into a new `_consider`); drop the `possible_duplicates.pop`; `_check_reserved` new rules; `_check_resumable` new rule; `SNAPSHOT_VERSION = 7`; docstrings.
- `tests/test_reject_reserves_match_id.py` (new): red test of the issue's reproduction, per-reason reservation tests, the live versus replay parity test, snapshot validation tests.
- Existing tests that assert the old behavior (the `possible_duplicates` clear rule, the reserved-and-duplicate live snapshot, version 6) are updated.
- `README.md`, `CHANGELOG.md`.

## Order of work

1. Commit this plan.
2. Commit the red test (`tests/test_reject_reserves_match_id.py`) reproducing the issue on main, and show it failing.
3. Fix `_record`, update snapshot validation and version, update tests that encoded the old rule, docs, changelog. Run the gate.

## Risks

- Replay calls `_record`, so rejected rows in a replay now reserve too. `_replay` resets `_resumable` to `track` at the end, so the `_changed` call does not break checkpoints.
- Old live snapshots under `reject_content_duplicates=True` whose copies are not reserved now fail to load; covered by the version bump.
- `DUPLICATE_MATCH_ID` itself must not change state (the id is already held).

## Proof

- Red test: issue reproduction (`GAME_FORMAT_NOT_ACCEPTED` then a valid row under the same id counts live, while replay raises `LedgerConflictError`). Measured on main: live second record returns `counted=True`.
- Criterion 1: parametrized test over each reason.
- Criterion 2: parity test comparing normalized snapshots.
- Criteria 3 to 6: snapshot tests with `pytest.raises(SnapshotError, match=...)`.
