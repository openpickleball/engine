# Plan: Live record reserves the match_id of a match that is not completed (issue #73, with #72)

Risk tier: High. The change touches the replay's determinism and the snapshot schema, and issue #73 is a path for one party to change other players' ratings live (CLAUDE.md hard rule 7). No rating math changes; every rule is unit-testable.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-celery, django-allauth, python-fastapi, React and Expo skills: not applicable because this is a standard-library-only Python package with no framework, API layer, or UI.

Issue #72 (record skips the as_of check for matches that are not completed) is folded in: both fixes touch the same early returns in `RatingEngine.record`. The PR closes both.

## What main does today

Measured on `origin/main` (54aab22) with synthetic ids (`syn-m1`, `syn-a` to `syn-d`, played_on 2026-10-29, doubles, side-out to 11, best of 1):

- `record(forfeit, as_of=2026-10-01).reason` is `MATCH_NOT_COMPLETED`; the same row completed with `(11, 5)` raises `InvalidMatchError("as_of cannot be earlier than the match date")`. A row in a game format that is not accepted, and a repeat of a processed `match_id`, also return before the `as_of` check.
- Live: `record(forfeit)` returns `MATCH_NOT_COMPLETED`, then `record(completed, same id).counted` is `True`. `replay([forfeit, completed], as_of=2026-11-01)` raises `LedgerConflictError`.

## Design

### #72

In `record`, right after the `as_of` type check, raise `InvalidMatchError("as_of cannot be earlier than the match date")` when `as_of is not None and as_of < match.played_on`. This runs before every rejection that returns a `MatchResult` (duplicate `match_id`, status, game format, possible duplicate, self-reported, baseline, gaps). Remove the later check. Valid replays are unaffected: `_replay` skips every row with `played_on > as_of` before it calls `record`, and the only other caller (`prequential_predictions`) records live with no `as_of`.

### #73 (option 1 in the issue)

- New engine attribute `self._reserved_match_ids: set[str]`: the `match_id` of every match `record` rejected with `MATCH_NOT_COMPLETED`. Private, like `_content_keys`; it is not a public API change.
- In `record`, the duplicate check becomes `match.match_id in self.processed_match_ids or match.match_id in self._reserved_match_ids` and returns `DUPLICATE_MATCH_ID`. So a repeat of the same forfeit, or a completed row reusing its id, is a duplicate, live and after a replay. This matches replay: identical rows collapse to one, and a differing row raises `LedgerConflictError`; in both cases the later row never counts.
- On `MATCH_NOT_COMPLETED`, call `self._changed()` and add the id to `_reserved_match_ids` before returning. A live reservation makes a snapshot not resumable, like any live state change. In a replay, `_replay` sets the resumable mark at the end, as now.
- The reservation does not touch `processed_match_ids`, `content_keys`, `possible_duplicates`, `self_reported_log`, players, or rating states. So no rating changes, `matches_counted` still equals `len(processed_match_ids)`, and content duplicate rules are unchanged (a non-completed row is never a content twin, and never checked for one).
- Snapshot key `reserved_match_ids`, a sorted list, `SNAPSHOT_VERSION` 4 to 5. Validation in `_read_snapshot` (a helper next to the processed ids reader is fine):
  - a list of non-blank str ids with no repeats (same messages as `processed_match_ids`, with the key name);
  - disjoint from `processed_match_ids` (the engine checks both sets before adding to either);
  - in a resumable snapshot: every reserved id is a `ledger_digests` id (a replay reserves only a row it considered), and no reserved id is a `possible_duplicates` key (in one ledger a completed row and a non-completed row with the same id conflict). A live engine can hold a reserved id that is also a `possible_duplicates` key (a copy rejected under `reject_content_duplicates=True`, then a non-completed row reusing its id), so that rule is resumable only.
- `from_snapshot` loads it; `to_snapshot` emits it. Resume needs no other change: rows on or before the checkpoint are not recorded again, a later row reusing a considered id already raises `LedgerConflictError` in `_check_resume_rows`, and the resumed engine carries the checkpoint's reservations plus the new ones, which equals a full replay.

## Acceptance criteria

1. `record` raises `InvalidMatchError` (message "as_of cannot be earlier than the match date") for an `as_of` earlier than `played_on`, whatever the match status, the game format, and whether the `match_id` was already processed or reserved. Nothing changes when it raises.
2. Replay of a valid ledger is unchanged, including rows dated after `as_of`, which replay skips.
3. Live: after `record` returns `MATCH_NOT_COMPLETED` for a `match_id`, a later `record` of any match with that id (completed or not, identical or not) returns `DUPLICATE_MATCH_ID`, is not counted, and changes no rating. `replay` of a ledger with a non-completed row and a completed row under one id raises `LedgerConflictError`, so neither path counts the completed row.
4. The same holds on an engine built by `replay` (a reserved row dated on or before `as_of`) and on one loaded with `from_snapshot`.
5. `to_snapshot` carries the reservation as `reserved_match_ids` (sorted), `snapshot_version` is 5, and `from_snapshot` rejects a bad value with `SnapshotError`: not a list, a blank or non-str id, a repeated id, an id also in `processed_match_ids`, and, in a resumable snapshot, an id not in `ledger_digests` or one that is a `possible_duplicates` key. A version 4 snapshot does not load.
6. A live reservation clears the resumable mark. Resume from a checkpoint whose rows include non-completed matches equals a full replay of the same ledger, `to_snapshot()` compared whole.
7. Completed-only ledgers replay and record live to the same state as before: the snapshot is identical apart from `snapshot_version` and the new empty `reserved_match_ids`.
8. CHANGELOG, README (match status section and snapshot schema), the module docstring's match status note, and the `record`, `to_snapshot`, and `from_snapshot` docstrings describe the new rules. No doc still says a later completed match with that id can count live.

## Files that change

- `src/open_engine/engine.py`: `record`; `RatingEngine.__init__`, `to_snapshot`, `from_snapshot`; `_TOP_KEYS`, `_Snapshot`, `_read_snapshot` and a reader for the new key; `_check_resumable` (or the reader) for the resumable rules; `SNAPSHOT_VERSION`; module docstring (match status note, "schema version 4"); `record`, `to_snapshot`, `from_snapshot` docstrings.
- `tests/test_snapshot.py`: `TOP_LEVEL_KEYS` and the engine attribute list (both fail until updated, by design).
- New `tests/test_record_as_of_order.py`: the #72 tests, committed red on their own, so the frozen #72 test is not in a file the #73 work edits.
- New `tests/test_reserved_match_ids.py`: the #73 live, replay, load, carriage, resumable, and resume tests, committed red.
- New `tests/test_snapshot_reserved_match_ids.py`: the snapshot validation tests (criterion 5), written after the #73 fix commit's reader, red for the rules not yet enforced.
- `src/open_engine/engine.py` docstrings of `Match`, `RejectionReason`, and `prequential_predictions` also said a non-completed match "changes nothing"; they now name the reservation.
- Existing tests that assert the old behavior (a completed row counting after a non-completed row with the same id, or version 4): update them to the new rule. Search `tests/test_match_status.py`, `tests/test_match_status_review.py`, and any test that hard-codes `snapshot_version` 4 or a snapshot dict.
- `README.md`: match status section (line ~181), snapshot schema (version 5, new key, its rules, "A version 1 to 4 snapshot does not load").
- `CHANGELOG.md`: #72 as a Breaking entry under Changed, next to the #24 `as_of` entry, since calls that returned a result now raise; #73 as a Breaking entry under Security, after #15, since it closes a path for one party to change other players' ratings live; correct the #22 "Added" entry and the snapshot entry's "schema version 4" so they describe the current behavior.

## Order of work

1. Red tests for #72 (parametrized: forfeit, retired, incomplete, unaccepted game format, a processed `match_id`, a reserved `match_id` once #73 lands), commit red, fix, green.
2. Red tests for #73 live behavior and snapshot carriage, commit red, fix (`_reserved_match_ids`, snapshot key and validation, version 5), green. Update `tests/test_snapshot.py` lists.
3. Snapshot validation tests (each rule in criterion 5, with `pytest.raises(SnapshotError, match=...)`), resume equality test, resumable mark test.
4. Docs and CHANGELOG.
5. Full gate.

## Risks

- A reserved id that also blocks a legitimate correction. A backend that relabels a forfeit as completed must now use a new `match_id`, which is already the replay rule. Documented.
- Snapshot validation that rejects a state the engine can produce. The live overlap of a reserved id with a `possible_duplicates` key is the one to keep accepting; a test records it live, takes a snapshot, and loads it.
- Moving the `as_of` check ahead of the duplicate check: a caller that re-records a processed id with an earlier `as_of` now gets an error. That is a caller error either way; replay never does it.
- Not done here: reserving the id of completed rows rejected for other reasons (game format, self-reported, baseline, gaps, possible duplicate). Filed as issue #76, since it changes the documented `possible_duplicates` rule.

## Proof

- Criterion 1: `test_record_rejects_early_as_of_for_every_rejection` (parametrized, in `tests/test_record_as_of_order.py`), and `test_early_as_of_raises_for_a_reserved_id` for a reserved `match_id`.
- Criterion 2: the existing suite, plus a test that a replay with a non-completed row dated after `as_of` still succeeds.
- Criterion 3: `test_live_reserves_not_completed_match_id` (completed reuse, identical repeat, ratings unchanged) and `test_live_and_replay_agree_on_reused_id`.
- Criterion 4: tests on a replayed engine and on a `from_snapshot` engine.
- Criterion 5: parametrized snapshot validation tests in `tests/test_snapshot_reserved_match_ids.py`, plus the live overlap round trip.
- Criterion 6: `test_live_reservation_clears_resumable`, `test_resume_with_not_completed_rows_equals_full_replay`.
- Criterion 7: measured with `scratchpad/digest.py` (replay and live of `multi_year_ledger()` from `tests/test_replay_live_parity.py`, snapshot hashed without `snapshot_version`, `engine_version`, and `reserved_match_ids`). On main (54aab22): completed-only replay `78523b1289e8cab8`, completed-only live `ed051854141775da`, ledger with every 7th row a forfeit replayed `19b1364891406cd0`. The branch must print the same three hashes.
- No live drive: this is a library with no app; the gate and the repro script (`scratchpad/repro.py`, the two issue repros) cover the changed paths, including failure paths.
