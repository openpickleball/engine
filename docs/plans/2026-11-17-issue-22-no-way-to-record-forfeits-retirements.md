# Plan: No way to record forfeits, retirements, or incomplete matches (issue #22)

Risk tier: High. It adds a field to the public `Match` dataclass and a new public enum, changes `Match.validate` (existing behavior), changes the replay row digest that checkpoints store (a snapshot version bump), and spans the engine, README, changelog, and several test files. A status lets whoever files a match keep it from moving ratings, which touches the security-sensitive area "anything that lets one party change another player's rating".

Domain skills: none apply. This is a standard-library Python package, so django, django-ninja, allauth, celery, fastapi, and the frontend and Expo skills are not applicable.

## Problem

`Match.validate()` requires a decided winner and at least one game. A forfeit, a retirement, or a match stopped by time or weather cannot be stored in the ledger, so a platform must drop it or invent scores, and invented scores corrupt ratings. Measured on `origin/main` (see Proof): a best of 3 singles row with no games, or with one game of a best of 3, makes `replay` raise `InvalidMatchError`.

## Decisions (maintainer's choices, 2026-11-17)

- **Policy: never rated.** A match whose status is not `completed` is a valid ledger row that `replay` accepts, but `record` never counts it. No rating math changes, so no out-of-sample evidence is needed (CLAUDE.md "Changes must improve predictions"). Rating a retirement on its completed games would change how ratings move and is not done here; it could come later as a declared `Config` parameter backed by evaluation.
- **Games by status.** `forfeit`: no game was played, so `games` must be empty. `retired` and `incomplete`: zero or more games, each a fully completed game valid in the game format (a partial game in progress is not recorded), and no decided winner. `completed`: unchanged (at least one game and a decided winner).
- **Enum.** `MatchStatus(_StrEnum)`: `COMPLETED = "completed"`, `FORFEIT = "forfeit"`, `RETIRED = "retired"`, `INCOMPLETE = "incomplete"`. Exported from `open_engine`.
- **Field.** `Match.status: MatchStatus = MatchStatus.COMPLETED`, the last field (after `confirmed_by`), so code that builds a `Match` by position or without the field keeps working and completed matches behave exactly as before.
- **Type check.** `validate` raises `InvalidMatchError` when `status` is not a `MatchStatus` member. The str `"completed"` compares equal to the member (StrEnum), so without this check a str status would slip through identity tests.
- **Rejection.** A new `RejectionReason.MATCH_NOT_COMPLETED = "match_not_completed"`. One member covers all three statuses; the status is on the match.
- **Check order in `record`.** `match.validate()`, then the `match_id` check (`DUPLICATE_MATCH_ID`), then the status check, before the game format check, the `as_of` check, the content duplicate check, and the self-reported checks. The status rejection changes nothing: it does not call `_changed`, does not add the id to `processed_match_ids`, does not register a content key, does not touch the self-reported log or `possible_duplicates`, and creates no player or rating state. So a later completed row with the same `match_id` can still count live (a status correction), and the snapshot stays resumable.
- **Replay.** No change to `_replay`. A non-completed row counts toward `ReplayInfo.matches_considered`, not `matches_counted`, and its digest and players are tracked like any rejected row (so resume refuses a new seed for a player named only in such a row, as it already does for any rejected row). `_ledger` already raises `LedgerConflictError` for a non-completed row and a completed row with the same `match_id`, since they differ in a field; the backend replaces the row instead.
- **Prequential.** `prequential_predictions` (and so `evaluate`, `fit_scale`, `fit_win_calibration`) never predicts a non-completed match: eligibility adds `match.status is MatchStatus.COMPLETED`. Today `_predict_match` runs before `record` and divides by the total points, so a zero-game forfeit between established players would raise `ZeroDivisionError`.
- **Snapshot version 3.** `_match_digest` hashes every `Match` field, so adding `status` changes every row's digest. A version 2 checkpoint loads but resume would then raise a misleading `LedgerConflictError` for every row. Bumping `SNAPSHOT_VERSION` to 3 makes such a snapshot fail to load with `SnapshotError` instead. No schema key changes. Snapshots were never in a release, so this is not marked Breaking.
- **Not done:** recording which team forfeited or retired (the engine does not need it to rate; the backend stores it), partial game scores, rating retirements on completed games, and a `Config` switch for any of these.

## Acceptance criteria

1. `MatchStatus` is a public str enum with members `COMPLETED`, `FORFEIT`, `RETIRED`, `INCOMPLETE` (values `"completed"`, `"forfeit"`, `"retired"`, `"incomplete"`), exported in `open_engine.__all__`, listed in the README "Public API" section, with a docstring.
2. `Match.status` defaults to `MatchStatus.COMPLETED` and is the last field. A completed match validates and rates exactly as before (the existing suite passes unchanged except for the snapshot version tests).
3. `Match.validate` raises `InvalidMatchError` for: a `status` that is not a `MatchStatus` member (including the str `"forfeit"` and `None`); a `FORFEIT` with any game; a `RETIRED` or `INCOMPLETE` match with a decided winner; a `RETIRED` or `INCOMPLETE` game that is not a valid completed game in its game format (for example `(7, 4)` to 11); more games than `best_of`. Every other check (ids, team sizes, `confirmed_by`, `played_at`, `sequence`, `best_of`, game format) still applies to every status. Valid: `FORFEIT` with no games; `RETIRED` and `INCOMPLETE` with no games, or with completed games and no decided winner (for example `((11, 5),)` in a best of 3, or `((11, 5), (5, 11))` in a best of 3).
4. `record` on a valid non-completed match with a new `match_id` returns `counted is False`, `reason is RejectionReason.MATCH_NOT_COMPLETED`, the match's `rating_format`, and `possible_duplicate_of is None`, for each of the three statuses, live and with `as_of`. `to_snapshot()` is equal before and after the call (so nothing changed, the resumable mark included), and no player is created. A later completed match with the same `match_id` counts. A non-completed match whose `match_id` was already processed returns `DUPLICATE_MATCH_ID`.
5. `replay` accepts non-completed rows. Each rating (the snapshot's `players`) equals that of a replay of the same ledger without them; `matches_considered` includes them and `matches_counted` does not. The result is the same for any input order. A non-completed row and a completed row with the same `match_id` raise `LedgerConflictError`. Resuming from a checkpoint with non-completed rows before and after it equals a full replay.
6. `prequential_predictions` and `evaluate` with non-completed rows (including a zero-game forfeit between established players) do not raise and give the same predictions and report as the same ledger without them.
7. `SNAPSHOT_VERSION` is 3; a snapshot with `snapshot_version` 2 raises `SnapshotError`.
8. Documented: a module docstring note on match status and its policy, plus a production note that a status needs the same verification as a score (an unrated status keeps a match from moving any rating, so a team could misreport a loss as unfinished) and that the backend stores who forfeited or retired; the `Match`, `MatchStatus`, `RejectionReason`, and `record` docstrings; the README (Public API lines for `MatchStatus` and `Match`, a new "Match status" section with the policy, the snapshot schema version 3); CHANGELOG `[Unreleased]` entries under Added (`MatchStatus`, `Match.status`, `RejectionReason.MATCH_NOT_COMPLETED`, the policy) and Changed (snapshot version 3, why, and that a version 2 snapshot no longer loads).

## Files that change

- `src/open_engine/engine.py`: `MatchStatus` enum (after `ScoringType`); `Match.status` field and docstring; `Match.validate` status rules; `RejectionReason.MATCH_NOT_COMPLETED`; `record` status check and docstring; `prequential_predictions` eligibility (in `step`) and docstring; `SNAPSHOT_VERSION = 3` and the "schema version 2" mentions in the module docstring and `to_snapshot` docstring; module docstring notes.
- `src/open_engine/__init__.py`: import and `__all__` entry for `MatchStatus`.
- `README.md`: Public API lines, "Match status" section, snapshot schema version.
- `CHANGELOG.md`: `[Unreleased]` entries.
- `tests/test_match_status.py` (new): criteria 1 to 7.
- `tests/test_snapshot.py`, `tests/test_content_duplicates.py`: snapshot version 2 to 3 in the existing version assertions and messages.
- `tests/test_api.py`: `MatchStatus` in its `PUBLIC_API` list, and `MATCH_NOT_COMPLETED` in its `REJECTIONS` list with a case in `REJECTION_CASES`. Those tests pin the public names and every rejection reason, so they must change with criterion 1 and the new reason (added during the work; the first draft missed them).

## Order of work

1. Write `tests/test_match_status.py` with the tests for criteria 1 to 6, and update the version tests for criterion 7. Run them and see them fail (import error or wrong behavior). Freeze them.
2. Add `MatchStatus`, `Match.status`, and the validation rules. Export the enum.
3. Add `RejectionReason.MATCH_NOT_COMPLETED` and the `record` check.
4. Add the prequential eligibility rule.
5. Bump `SNAPSHOT_VERSION` to 3.
6. Docstrings, README, CHANGELOG.
7. Full gate: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`. Then the live drive.

## Risks

- **Validation regressions for completed matches.** The validate rewrite must keep every existing message and order for `COMPLETED`. The existing suite covers it; run it after step 2.
- **StrEnum identity.** Comparisons must use `is` on members, after the type check, so a str status never takes the completed path.
- **Prequential crash.** Covered by criterion 6 with a zero-game forfeit between established players.
- **Integrity.** An unrated status lets a filer keep a match from moving ratings. The engine cannot verify it; the production note tells the backend to verify status like scores. The `retired` and `incomplete` rules (no decided winner) stop the simplest relabel of a finished match.
- **Riskiest step:** the validate rewrite, since `Match.validate` guards every code path that consumes a ledger.

## Proof

- Criterion 1: test of the members, values, export, and README (the existing API tests check `__all__`, README, and docstrings).
- Criterion 2: the existing suite, unchanged.
- Criterion 3: a parametrized table of invalid and valid matches, with `pytest.raises(InvalidMatchError, match=...)`.
- Criterion 4: a parametrized test per status, live and with `as_of`, comparing `to_snapshot()` before and after; a status correction test; a duplicate id test.
- Criterion 5: replay with and without non-completed rows, compared on `players` and the counts; shuffled input orders; the conflict case; resume equals full replay.
- Criterion 6: `prequential_predictions` and `evaluate` with and without the rows.
- Criterion 7: the updated version tests.
- Live drive: a script that records each status live and in a replay and prints the results, plus the failure paths (a forfeit with a game, a retired match with a decided winner, a str status).

Measured on `origin/main` (739faa7), the gap the issue names:

```
.venv/bin/python -c "...replay([Match('syn-r1',('syn-a',),('syn-b',),g,date(2025,1,1),RatingFormat.SINGLES,best_of=3)],as_of=date(2025,2,1))" for g in [(), ((11,5),)]
InvalidMatchError At least one game score is required
InvalidMatchError Match score does not contain a completed winner
```

No planned test asserts that two inputs behave alike on `origin/main`: the "same ratings with and without the rows" tests need the new field, which `origin/main` lacks.
