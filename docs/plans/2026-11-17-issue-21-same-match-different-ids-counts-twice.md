# Plan: The same match under different match IDs counts twice (issue #21)

Risk tier: High. Duplicate counting lets one party change other players' ratings (a security-sensitive area in CLAUDE.md). The fix adds engine state that the snapshot must carry (a schema bump), a `Config` field, a `MatchResult` field, and a `RejectionReason` member, and it must keep replay deterministic.

Domain skills: none apply. This is a standard-library Python package, so django, django-ninja, allauth, celery, fastapi, and the frontend and Expo skills are not applicable.

## Problem

Duplicate protection is keyed only on `match_id`. The same match reported twice under different ids counts twice. Measured on `origin/main` (see Proof): the same 11-0 doubles match under `dup` and `dup2`, the second listed from the other team's side, gives `matches_counted == 2` in a replay, and both count live.

Two real matches with the same players and the same scores on one day are possible, so the engine cannot know for sure. It surfaces the signal and lets a config choose whether to reject.

## Decisions

- **Content key.** Two matches are likely duplicates when they have the same `played_on`, the same two teams as sets of players (either side may be listed as team A), and the same game scores in order, read from the same side. Everything else is left out on purpose: `match_id`, `match_type`, `rating_format`, `game_format`, `best_of`, `played_at`, `sequence`, and `confirmed_by`. Two reports of one match often disagree on labels (a player self-reports a league match, a mixed match is filed as doubles, times differ or are missing). The teams' sizes already separate singles from doubles. Under the default policy a false match costs nothing, since it only flags.
- **Normalization.** Each team is a sorted tuple of ids. The two teams are put in sorted order; when that swaps them, every game score is swapped too. The key is the SHA-256 hex digest of compact JSON of `[played_on ISO, team1, team2, games]`, so it is JSON safe and stable across runs.
- **Signal.** `MatchResult.possible_duplicate_of: str | None`, the last field (after `parent`), default `None`. Placed last so code that builds a `MatchResult` by position keeps working and the existing `late`-then-`parent` field order test stays as it is (changed from "after `late`, before `parent`" during implementation). It is the `match_id` of the earliest counted match the engine has seen with the same content key. It is set on a counted match and on a match rejected as a content duplicate. It is `None` for every other result, and on `parent` (the key does not depend on rating format, so the top-level result carries it).
- **Policy.** `Config.reject_content_duplicates: bool = False`. False (the default) counts the match and flags it. True rejects it with a new `RejectionReason.POSSIBLE_DUPLICATE`, not counted, with `possible_duplicate_of` set. The default changes no rating, so published ratings do not move. A bool is in the fingerprint already (`norm` handles it), so a config that rejects has a different fingerprint.
- **Which one is the duplicate.** The later one in processing order: arrival order live, ledger order in a replay. Ledger order ends on `match_id`, so replay stays deterministic whatever order rows arrive in.
- **Check order in `record`.** After validation, the `match_id` check, and the game format check, before the self-reported checks. So a content duplicate under reject never touches the self-reported log, and a disallowed game format still reports `GAME_FORMAT_NOT_ACCEPTED`.
- **Registration.** A key is stored only when a match counts, mapped to its `match_id`, and never overwritten. So a third copy points to the first, a rejected match (for any reason) is never a duplicate target, and in flag mode the duplicate counts but does not replace the stored id.
- **Same-day rematches.** Documented: a real rematch with the same players and the same game scores on the same day is flagged (default) or, with `reject_content_duplicates=True`, not counted. Use the default, and let the backend confirm, where that matters. Different dates are never matched, so reports that disagree on the date are not caught.
- **Engine state and snapshot.** `RatingEngine._content_keys: dict[str, str]` (key digest to `match_id`). It changes later results, so it goes into the snapshot as `content_keys` (an object, key digest to match id, sorted keys). `SNAPSHOT_VERSION` goes from 1 to 2; older snapshots fail to load, as CLAUDE.md requires. Validation: an object; each key 64 lowercase hex characters; each value a non-blank str that is in `processed_match_ids`; no two keys may map to the same id (a match has one content key). Resume needs nothing more: a row after the checkpoint has a later `played_on`, so it can never match a key from before it, and a resumed replay ends with the same keys as a full one.
- **Not chosen:** fuzzy matching (dates a day apart, partial scores), a three-way policy enum, and comparing `played_at` when both rows have one (two reporters often give different times, which would defeat the check).

## Acceptance criteria

1. With the default config, a match whose content key equals that of an earlier counted match counts, and its result has `possible_duplicate_of` set to the earlier match's id. This holds live and in a replay, and when the second report lists the teams in the other order with the scores swapped, or the players within a team in another order.
2. With `Config(reject_content_duplicates=True)`, that match is not counted: `counted is False`, `reason is RejectionReason.POSSIBLE_DUPLICATE`, `possible_duplicate_of` is the earlier id, no rating state changes, and `replay_info.matches_counted` counts it out.
3. Not flagged: a match with a different date, a different player on either team, different game scores, or the same scores from the other side without swapping (a different result). A first match's `possible_duplicate_of` is `None`. A match whose earlier twin was rejected for another reason is not flagged.
4. A third copy points to the first id. `parent` of a category match has `possible_duplicate_of is None`. Rejections for other reasons leave it `None`.
5. Replay is deterministic: the same ledger in any input order gives the same flagged and rejected ids and the same snapshot.
6. The snapshot carries `content_keys` and `snapshot_version` 2. `from_snapshot(to_snapshot())` round-trips it, a live duplicate after loading is still flagged, a version 1 snapshot raises `SnapshotError`, and malformed `content_keys` (not an object, a bad digest, a blank id, an id not in `processed_match_ids`, two keys for one id) raise `SnapshotError` naming the field. A resumed replay equals a full replay, including `content_keys`.
7. `Config.reject_content_duplicates` is a bool; a non-bool raises `ConfigError`. Default and True configs have different fingerprints.
8. Documented: a module docstring note (content key, flag versus reject, which report is the duplicate, same-day rematches, dates must agree), the `MatchResult` docstring, the `record` docstring, the `Config` comment, `to_snapshot` and `from_snapshot` docstrings, the README (quick start text, Public API not changing names, the snapshot schema section with `content_keys` and version 2), and CHANGELOG `[Unreleased]` entries: Added (`possible_duplicate_of`, `reject_content_duplicates`, `POSSIBLE_DUPLICATE`) and Changed, marked **Breaking** (snapshot version 2; older snapshots no longer load). Ratings under the default config do not change.

## Files that change

- `src/open_engine/engine.py`
  - `Config`: add `reject_content_duplicates: bool = False` with a comment, after the self-reported safeguards. `__post_init__`: `ConfigError` unless it is a `bool`.
  - New helper `_content_key(match: Match) -> str` next to `_matchup_key` (or `_match_digest`), with a docstring stating the fields used and left out.
  - `RejectionReason.POSSIBLE_DUPLICATE = "possible_duplicate"`.
  - `MatchResult`: `possible_duplicate_of: str | None = None` after `parent`; docstring.
  - `RatingEngine.__init__`: `self._content_keys: dict[str, str] = {}` with a comment.
  - `record`: after the game format check, `key = _content_key(match)`, `twin = self._content_keys.get(key)`. If `twin` and `cfg.reject_content_duplicates`: return the rejection with `possible_duplicate_of=twin`. After `_compute`, when counted, set `primary.possible_duplicate_of = twin`. At commit, `self._content_keys.setdefault(key, match.match_id)` (the commit path already clears the resumable mark through `_put_state`; replay sets it again at the end).
  - Snapshot: `_Snapshot.content_keys`, `_TOP_KEYS` gains `content_keys`, `to_snapshot` emits `_sorted_dict(self._content_keys.items())`, `_read_snapshot` validates as in the Decisions, `from_snapshot` assigns it. `SNAPSHOT_VERSION = 2`. Docstrings say version 2 and describe the key.
  - Module docstring: a new published behavior note, and the `record` note at the end of the list mentions content duplicates.
- `tests/test_content_duplicates.py` (new): the tests in Proof.
- `tests/test_api.py`: `POSSIBLE_DUPLICATE` in the rejection member list and a case that produces it (added during implementation: the existing member test lists every reason).
- `tests/test_snapshot.py`: version 2, the new top-level key in its key list and attribute list, the version test cases.
- `README.md`: quick start paragraph sentence; snapshot schema section (`content_keys`, `snapshot_version: 2`).
- `CHANGELOG.md`: the entries in criterion 8.

`__all__` does not change. `examples/basic_match.py` does not change.

## Order of work

1. Commit this plan alone as `docs: plan for issue 21`.
2. Worktree setup is done: `.venv` (Python 3.12), editable install. Use `.venv/bin/` for every command.
3. TDD: write `tests/test_content_duplicates.py`, run it red, freeze it (`.claude/fix-lock`), commit the red tests.
4. Implement the `Config` field, key, reason, result field, and `record` changes. Green. Commit.
5. Snapshot: state, schema, validation, version bump, `tests/test_snapshot.py` updates. Green. Commit.
6. Docs: module docstring, docstrings, README, CHANGELOG. Commit.
7. Gate: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.
8. Live drive (High: changed path plus failure and edge paths): a script that records live and replays a ledger with an original, a side-swapped duplicate, a third copy, a same-day rematch with different scores, a different-date copy, a category copy of a doubles match, and a duplicate whose twin was rejected; prints `counted`, `reason`, `possible_duplicate_of` under both policies; round-trips a snapshot and records a live duplicate after loading; resumes from a checkpoint and compares to a full replay; loads a version 1 snapshot to show the error. Also run `python examples/basic_match.py`.

## Risks

- **Changing default ratings.** Riskiest step: `record`. Under the default, the only change is the new field. A test asserts the default-config deltas of the measured repro equal the values main gives (see Proof). The existing suite must stay green except the snapshot version tests.
- **Determinism.** The key must not depend on set order or hash seed: teams are sorted tuples, the digest is over JSON text. A test permutes input order.
- **Snapshot bump.** Older stored snapshots stop loading. Required by CLAUDE.md and called out as breaking in the changelog. The backend reruns a full replay once.
- **Resume parity.** Resumed replays restore the keys from the snapshot. A test compares resumed and full snapshots with duplicates on both sides of the checkpoint.
- **Self-reported interaction.** In flag mode a duplicate self-report counts and enters the repeat log as before. Under reject it never reaches the log. Tested.
- **False positives under reject.** A real identical same-day rematch is lost. Documented; the default only flags.

## Proof

Measured on `origin/main` (8af31c0):

```
ms = [Match('dup', ('a','b'), ('c','d'), ((11,0),), date(2026,3,1), RatingFormat.DOUBLES),
      Match('dup2', ('c','d'), ('b','a'), ((0,11),), date(2026,3,1), RatingFormat.DOUBLES)]
replay(ms, as_of=date(2026,3,2)).replay_info.matches_counted  -> 2
[RatingEngine().record(m).counted for m in ms] (one engine)    -> [True, True]
live deltas: dup {a: 0.225, b: 0.225, c: -0.225, d: -0.225}, dup2 {a: 0.17507, b: 0.17507, c: -0.17507, d: -0.17507}
```

| Criterion | Test in `tests/test_content_duplicates.py` (unless noted) |
| --- | --- |
| 1 | flag live, flag in replay, side swap, in-team order (parametrized) |
| 2 | reject live and in replay: reason, unchanged states, matches_counted |
| 3 | parametrized non-duplicates; first match None; twin rejected for another reason |
| 4 | third copy; category parent None; other rejections None |
| 5 | permuted input order gives identical snapshots and flags |
| 6 | round trip, live dup after load, resume equals full replay; malformed cases in `tests/test_snapshot.py` or the new file |
| 7 | config type check and fingerprint differs |
| 8 | default config: the live deltas of `dup` and `dup2` equal the values measured on main (above); docs reviewed in Phase 8 |

Live drive must show both policies on the cases in Order of work step 8, the snapshot round trip, resume parity, and the version 1 error.
