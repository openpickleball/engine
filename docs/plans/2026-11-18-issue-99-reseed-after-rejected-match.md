# Plan: Reseed after a rejected match can make live and replay disagree (issue #99)

Risk tier: High. Security-sensitive under CLAUDE.md hard rule 7 (live must equal replay), new engine state, and a breaking snapshot schema change (version 7 to 8).

Domain skills: superpowers:test-driven-development, invoke. Django, FastAPI, React, and the other listed framework skills: not applicable because this is a standard-library Python package with no web, UI, data store, auth, payments, or email code.

Decision (recorded on #99 by the maintainer): option 1, record and refuse.

## Problem

`record` rejects a match, or the doubles update of a counted category match, for a reason whose outcome depends on a seed's rating or date. The engine reserves the `match_id` but keeps no record of who played. A later `seed_external_rating` (with or without `replace_unplayed_seed=True`) or `add_player` can then give a player in that row a different seed. A replay of the same ledger with the new seed counts the row; live never did. Reproduced on main (a3a0fe5) for `BEFORE_IMPORTED_BASELINE` (singles), `SINGLES_RATING_GAP`, `DOUBLES_PARTNER_GAP`, and a mixed match whose derived category states were all frozen (doubles reseed). A first seed for a player with no record also reproduces it after a gap rejection (the unknown player rated at the default). The counted-category-match case whose doubles update is rejected is already refused on main by the #79 rule (any counted category match blocks a doubles seed).

## Audit of every RejectionReason

| Reason | Outcome depends on a seed? | In scope |
| --- | --- | --- |
| `DUPLICATE_MATCH_ID` | No: only the processed and reserved ids. | No |
| `GAME_FORMAT_NOT_ACCEPTED` | No: the match and `Config`. | No |
| `SELF_REPORTED_UNCONFIRMED` | No: the match and `Config`. | No |
| `SELF_REPORTED_REPEAT_LIMIT` | Only through the self-reported log, which holds counted matches. A seed that could change whether a logged match counted is already refused (counted rule), and a row that a seed could make count is recorded by this change. | No |
| `BEFORE_IMPORTED_BASELINE` | Yes: each player's `initial_source_date`, or the overall doubles seed a derived category state comes from. | Yes |
| `SINGLES_RATING_GAP` | Yes: the players' ratings. | Yes |
| `DOUBLES_PARTNER_GAP` | Yes: the players' ratings. | Yes |
| `POSSIBLE_DUPLICATE` | Only through `content_keys`, which hold counted matches; the check runs before any rating is read. A seed that could change whether the twin counted is already refused. | No |
| `MATCH_NOT_COMPLETED` | No: the match status. | No |

## Acceptance criteria

1. `RatingEngine` keeps a new attribute `_seed_sensitive_rejections: dict[str, tuple[RatingFormat, tuple[str, ...]]]`: match_id to the rating format of the rejected update and the sorted ids of its players. `record` (live and in replay, through `_consider`) adds an entry when the match's own update is rejected for `BEFORE_IMPORTED_BASELINE`, `SINGLES_RATING_GAP`, or `DOUBLES_PARTNER_GAP` (format: the match's format), and when the match counts but its overall doubles update under `category_feeds_parent` is rejected for one of those (format: the parent, doubles). No entry for any other reason. Adding an entry clears the resumable mark (through `_changed`).
2. `seed_external_rating` raises `InvalidSeedError` and changes nothing (no player created, no state, no identity link) when the player is named in an entry whose format is the seeded format, or, under `category_feeds_parent`, an entry whose format's parent is the seeded format. This holds with or without `replace_unplayed_seed`, and for a first seed of a player with no record. The check runs after the existing counted-match checks and before the `replace_unplayed_seed` check.
3. `add_player` raises `InvalidSeedError`, changing nothing, for a seed whose id is named in such an entry for any format in its `ratings` (same rule as 2). Only the public `add_player` checks it; `replay` and resume keep their current behavior (a fresh replay has no entries when it adds seeds; resume already refuses a seed for any player named in a considered row with `SnapshotError`).
4. The issue repro is refused and the engine's snapshot is unchanged by the refused call. Each in-scope reason has a test: `BEFORE_IMPORTED_BASELINE` in singles and doubles, the all-frozen derived category case (doubles reseed under `category_feeds_parent`, and a direct category seed), `SINGLES_RATING_GAP` (including a first seed for an unknown player, and `add_player`), `DOUBLES_PARTNER_GAP`, and the rejected doubles update of a counted category match (recorded under doubles; doubles seed refused). Out-of-scope reasons record nothing.
5. Snapshot: new top-level key `seed_sensitive_rejections`, an object sorted by match_id, each value `{"players": [sorted ids], "rating_format": value}`. `SNAPSHOT_VERSION` 7 to 8; a version 7 snapshot fails to load. `from_snapshot` restores it. `from_snapshot` validates every rule the engine keeps that the snapshot can check:
   - keys non-blank str ids; value an object with exactly those two keys; `rating_format` a `RatingFormat` value; `players` a list of non-blank str ids, sorted, no repeats, exactly twice the format's team size;
   - each key is in `reserved_match_ids` or `processed_match_ids`;
   - a processed key has `rating_format` doubles, needs `category_feeds_parent` on, and every named player is in `players`;
   - a reserved key is never a `possible_duplicates` key;
   - a key (reserved or processed) whose format has no gap rule under the config (singles without `max_singles_rating_gap`, a doubles or category format without `max_doubles_partner_gap`) can only be a baseline rejection, so every named player is in `players` and each has, in that format, a state with an `initial_source_date`, or no state there and, under `category_feeds_parent` in a category format, an overall doubles state with an `initial_source_date`;
   - in a resumable snapshot, every named player is in `ledger_player_ids` (key in `ledger_digests` already follows from the existing reserved and processed rules).
   Every rule raises `SnapshotError` naming the field, with a test per rule.
6. Replay produces the same record as live for the same ledger in ledger order, and resume equals full replay (snapshots compared whole).
7. `tests/test_snapshot.py` attribute list and top-level keys include the new attribute and key. A fuzz test (seeded `random`, synthetic ids) builds engines through live `record`, `replay`, resume, and seeds under configs with and without the gaps and `category_feeds_parent`, and asserts every snapshot the engine produces round-trips through `from_snapshot`.
8. Docs: `seed_external_rating` and `add_player` docstrings, `to_snapshot` and `from_snapshot` docstrings, README (`InvalidSeedError` entry, schema section, version 8 note), CHANGELOG `**Breaking:**` entry under Unreleased with a "Migration:" note (rebuild by full replay). The "leaves no record" sentences are removed.

## Files that change

- `src/open_engine/engine.py`: new constant `_SEED_SENSITIVE_REASONS` (frozenset of the three reasons); new attribute in `__init__`; recording in `_consider`; helper `_in_seed_sensitive_rejection(player_id, rating_format) -> bool` that applies the format rule in criterion 2; checks in `seed_external_rating` and `add_player` (via a keyword-only flag on `_add_seed`, default off, so replay and resume are unchanged); `SNAPSHOT_VERSION = 8`; `_TOP_KEYS`; `_Snapshot` field; `to_snapshot` emits it; `_read_snapshot` validates and returns it (new `_snap_seed_sensitive_rejections`); `from_snapshot` restores it; `_check_resumable` or the new reader checks `ledger_player_ids`; docstrings.
- `tests/test_snapshot.py`: attribute list, top-level keys, `_engine_state`.
- New `tests/test_seed_sensitive_rejections.py`: refusal tests, recording tests, live versus replay, resume, round trip, snapshot validation rules.
- New fuzz test (in the same file or `tests/test_snapshot_engine_made.py`).
- Any existing test that hard-codes `snapshot_version` 7 or the schema text.
- `README.md`, `CHANGELOG.md`.

## Order of work

1. Red test for the issue repro and each in-scope reason (refusal plus state unchanged). Commit alone, red.
2. Engine attribute, recording, refusal in `seed_external_rating` and `add_player`. Green.
3. Snapshot: version bump, key, reader, validation, restore; update `tests/test_snapshot.py` and any version-7 tests. Tests for each validation rule, round trip, live versus replay, resume, fuzz.
4. Docstrings, README, CHANGELOG.
5. Full gate.

## Risks

- A validation rule stricter than what the engine keeps would make engine-made snapshots fail to load. The fuzz test and the baseline-only rule's derivation guard this; if any rule cannot be shown to hold for every engine-made state, drop it rather than weaken the fuzz.
- Over-refusal: the refusal is conservative (any seed in the format, or the parent format), consistent with the #79 counted-match rule. A backend that needs a new seed for such a player rebuilds with a full replay from corrected seeds.
- Resume error type must not change: `_add_seed` gets the check only from `add_player`.
- Performance: the refusal scans the record per seed call, like `_in_counted_match` scans players.

## Proof

Measured on origin/main (a3a0fe5) with a scratch script: the repro and the singles gap, partner gap, and all-derived-frozen mixed cases each accept the reseed and a replay with the new seed counts the row (`matches_counted` 1) while live did not. The rejected doubles update of a counted mixed match is already refused ("Cannot seed a rating that a counted mixed_doubles match for this player depends on"). A first seed for an unknown player after a singles gap rejection is accepted and replay counts the row.

Tests: criterion 1 and 4 by the refusal and recording tests; 2 and 3 by the refusal tests that compare `to_snapshot()` before and after the refused call; 5 by one test per validation rule plus the version-7 rejection; 6 by live versus replay and resume tests comparing whole snapshots (normalizing only the fields that differ by design between live and replay); 7 by the attribute test and the fuzz.
