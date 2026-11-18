# Plan: An external seed can replace a rating that came from OPEN doubles (issue #79)

Risk tier: High. Handling of imported external ratings is security-sensitive (CLAUDE.md hard rule 7), the change alters existing behavior of a public method, and it guards live and replay agreement.

Domain skills: superpowers:test-driven-development, invoke. Django, FastAPI, React, Expo, payments, and email skills: not applicable because this is a pure Python package with no framework, UI, or service.

## Decision (recorded on #79)

Option 1, refuse. `seed_external_rating` refuses to replace a state that carries OPEN history: one whose `initial_source` starts with `open_parent:`, and one where an OPEN match was counted for the player in that format even if it was frozen. Seeding a category format that has no stored state stays allowed. The docstring, README, and CLAUDE.md wording stay as the rule for every format.

## How the frozen case is detected without new state

A frozen player's state is never written, so the player's own state shows no trace of the match. But a match counts only when at least one player is not frozen (otherwise it is rejected with `BEFORE_IMPORTED_BASELINE`), and `_compute` records every other player in the match in each moved player's `partners` or `opponents` for that format. Those maps are never pruned and are written nowhere else. So "an OPEN match was counted for player P in format F" is exactly "some other player's state in F lists P in `partners` or `opponents`", or P's own state in F has `matches_played > 0`. This uses existing engine state only: no new snapshot field.

The check does not trust `initial_source`. The `open_parent:` prefix check only adds refusals: a state whose source an external import spoofed as `open_parent:doubles` (issue #97) is refused more, never accepted more. A state derived from overall doubles always gets that prefix from `_new_state`, so no derived state escapes the prefix check, and any derived state that also took part in a counted match is caught by the history check as well.

One case follows from the decision's rationale and is called out in the PR: a category format with no stored state where the player was frozen in a counted category match (the derived starting state carries the parent's import date and is not stored when frozen). That player has a counted OPEN match in that format, so the seed is refused. Allowing it would let the new seed's capture date change which past matches count, so live and replay could disagree. Seeding a category format with no stored state and no counted match there stays allowed.

## Acceptance criteria

1. `seed_external_rating(..., replace_unplayed_seed=True)` raises `InvalidSeedError` when the stored state's `initial_source` starts with `open_parent:` (the issue's repro), and nothing changes (the snapshot before and after is equal).
2. It raises `InvalidSeedError` when the player was in a counted match in that format, frozen included (the issue comment's repro, in singles and in doubles), and nothing changes.
3. It raises `InvalidSeedError` for a category format with no stored state when the player was frozen in a counted match in that format.
4. After each refusal, the live engine's ratings agree with `replay` of the same ledger and the original seeds.
5. Seeding a category format with no stored state and no counted match there still works (also when the parent has OPEN matches), and replacing an unplayed seed with no counted match still works.
6. Also fixed: a `snapshot` that is not an `ExternalRatingSnapshot` raises `InvalidSeedError` instead of `AttributeError`.
7. Docstring, README (`InvalidSeedError` bullet), and CHANGELOG (`**Breaking:**` with a migration note) describe the rule. CLAUDE.md's rule already reads as every format and needs no change.

## Files that change

- `tests/test_seed_keeps_open_history.py` (new): every test for the criteria above.
- `tests/test_snapshot_verified_log.py`: `test_reseeded_live_import_with_frozen_logged_matches_loads` reseeds after a frozen counted match, which this change refuses. It becomes a test that the reseed is refused and the snapshot still loads.
- `src/open_engine/engine.py`: in `seed_external_rating`, an `isinstance(snapshot, ExternalRatingSnapshot)` check first among the snapshot checks; after `_check_player_id`, refuse when the existing state's `initial_source` starts with `open_parent:`, and refuse (with or without a stored state) when `_has_counted_match(player_id, rating_format)` is true. Order: existing `matches_played > 0` message stays; the new refusals come before the `replace_unplayed_seed` check, so the message names the real reason. A new private helper `_in_counted_match` scans other players' states in that format. Docstring update.
- `README.md`: the `InvalidSeedError` bullet.
- `CHANGELOG.md`: a `**Breaking:**` entry under `### Security`, with "Migration:".

## Revision after review

Review found a gap: under `category_feeds_parent`, a counted category match in which the player has no stored category state is rated from a state derived from the overall doubles seed, with its date. If that match's doubles update was rejected (every doubles player frozen), no doubles state names the player, so a doubles reseed with an earlier date passed and changed whether the player was frozen in the category match. `seed_external_rating` now also refuses to replace an overall doubles rating in that case (new message naming the category), with tests for the refusal and for the stored-category-state case that stays allowed. A delta review found the same gap when the category state is stored but derived from overall doubles (it was built from the doubles seed's rating, for example a counted match whose doubles update was rejected by `max_doubles_partner_gap`), so the refusal covers a missing or derived category state. A second delta review found the same for a provisional category state (no `initial_source_date`) that a player built without a doubles rating, followed by a first doubles seed: a replay would derive the category state from that seed. The refusal now covers any category state that is not the player's own import, for a first doubles seed as well as a replacement. Review also found that a match that did not count at all leaves no record of its players, so a reseed after it can still make live and replay disagree. That needs new engine state and is filed as #99; the docstring, README, and CHANGELOG say so. The `_check_verified_against_log` docstring is narrowed to the reseeds still possible.

## Order of work

1. Write `tests/test_seed_keeps_open_history.py`, run it on this branch with no engine change, confirm the new tests fail for the expected reason, and commit it alone.
2. Engine change and docstring; new tests green; update the existing snapshot test; full gate.
3. README and CHANGELOG.

## Risks

- The scan is O(players) per seed call. Seeding is a rare, administrative call; acceptable. Its result is a boolean, so iteration order does not matter.
- A crafted snapshot can list a player in another's `partners` without a match. Snapshots are trusted backend input checked for shape, not authenticity; that only adds refusals.
- Existing tests that reseed after a counted frozen match change expectations: only the one named above, found by running the suite.

## Proof

- Criteria 1 to 3, 6: new tests with `pytest.raises(InvalidSeedError, match=...)` and snapshot equality.
- Criterion 4: new tests comparing live states to `replay` states (rating, RD, matches played, source date).
- Criterion 5: new tests that the allowed seeds still succeed.
- Measured on main (before the fix): the frozen category case is accepted (`seed_external_rating("syn-a", MIXED_DOUBLES, ...)` after a counted mixed match with `frozen=('syn-a',)` returned without raising).
