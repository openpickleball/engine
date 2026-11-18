# Plan: Self-reported matches alone can make a player established (issue #59), with issue #42 folded in

Risk tier: High. Both issues are in a security-sensitive area (hard rule 7: one party shaping how credible a rating looks, and rating created by colluding accounts). The change adds a `Config` field, a `RatingState` field, and a `ReplayInfo` field, and bumps the snapshot schema from 5 to 6, which makes every stored version 5 snapshot fail to load.

Domain skills: none apply. This is a standard-library Python package with no framework, UI, API, data store, authentication, payments, or email. superpowers:test-driven-development applies to the implementer.

## Problem

- #59: `PlayerSummary.established` depends only on OPEN-earned reliability. A group of new accounts that rotate partners and opponents can report their way to established with self-reported matches only. Reproduced on main (commit f1d9363): a player with 30 weekly self-reported doubles wins (11-9), each with a new partner and new opponents, is established after 25 matches (reliability 61.3), with no organizer, club, or partner ever confirming a match.
- #42: each player moves by their own K, so a match can add rating to the pool or remove it. `MatchResult.imbalance` reports it per match, but nothing totals it. Reproduced on main: a new account (3.5) beats an imported account (4.0, seeded at the capture date) 11-5 in ten daily `REC_VERIFIED` singles matches. The new account ends at 4.136, the feeder at 3.634, so the pool gains about +0.270, and `ReplayInfo` reports only dates, config, and counts.

## Decisions

1. Verified match types are `TOURNAMENT`, `LEAGUE`, and `REC_VERIFIED`. `SELF_REPORTED` never counts as verified. Keep the rule as a private module-level frozenset `_VERIFIED_MATCH_TYPES`, not a new public `MatchType` property, so the public API does not change for this.
2. New `RatingState.verified_matches_played: int = 0`: the number of counted verified matches that moved this state. It increments exactly where `matches_played` increments (in `_compute`, for each player who is not frozen), and only when `match.match_type` is verified. So a frozen player (match on or before their imported baseline) gains neither count.
3. Category formats: a verified category match (mixed, men's, or women's doubles) counts toward the category state and, when `Config.category_feeds_parent` is on and the parent update counts for that player, toward the overall doubles state, exactly as `matches_played` does. A category state derived from overall doubles starts with `verified_matches_played = 0`, as it starts with `matches_played = 0`: the parent's verified matches do not carry into the category.
4. Seeds: an imported seed (`seed_external_rating`, `add_player`, `replay` seeds) and a provisional seed start at 0 verified matches. The seed rules require `verified_matches_played == 0` (with an int type check), next to `matches_played == 0`. Imported reliability never counts toward it.
5. New `Config.established_min_verified_matches: int = 3`. `PlayerSummary.established` is True when reliability (as displayed) is at least `established_reliability_threshold` AND `state.verified_matches_played >= established_min_verified_matches`. Validation in `__post_init__`: a whole number (an int, not a bool) and at least 0, else `ConfigError("established_min_verified_matches must be a nonnegative int")`. It is a scalar int, so `norm()` already turns it into stable JSON, the fingerprint covers it, and `to_dict`/`from_dict` round-trip it with no change to `from_dict` (it is not a mapping field, so `_CONFIG_MAPPING_FIELDS` does not change). A stored `to_dict` from before this change lacks the key, so `from_dict` raises `ConfigError` for a missing key, as for any new field: say so in the changelog.
6. Default 3, chosen here. It is a label only: it changes no rating, RD, prediction, `evaluate`, `prequential_predictions`, `fit_scale`, or `fit_win_calibration` output, so it has no accuracy cost. Measured on main under the defaults, a player whose matches are all verified needs at least 3 counted verified matches to reach the reliability threshold in every tested case (new player: 7 to 13; imported seed: 3 to 4 counted), so a default of 3 never delays the label for such a player. It changes the label only for a player with fewer than 3 counted verified matches in the format, such as one whose matches are mostly self-reported. Zero keeps today's behavior. Record the default and its effect in the changelog as a change to `established`.
7. New `ReplayInfo.net_rating_created: Mapping[RatingFormat, float]`: per rating format, the sum over every update the replay counted of the unrounded rating changes of every player in it (`math.fsum` of the applied changes within one update, frozen players adding 0.0). A category match adds its category update to its format and its parent update (when it counts) to `doubles`. Positive means rating was added to that pool, negative means removed. It has a key for every `RatingFormat` (0.0 where nothing counted), stored as a read-only `_FrozenMapping` built in `RatingFormat` definition order. `ReplayInfo` gets a `__post_init__` that stores a read-only copy, and the field has a default factory of all-zero values so code that builds a `ReplayInfo` positionally keeps working.
8. Running total: an ordered sum. `_replay` starts each format at 0.0 (or, on resume, at the checkpoint's stored value) and adds each counted update's `fsum` in ledger order. Ledger order is fixed by `_ledger`, so the total is deterministic for the same ledger, config, and date, and a resume continues from the exact float a full replay had at the checkpoint, so resume equals full replay bit for bit. The per-update value must come from `record`'s own computation, so add a private `RatingEngine._record(match, *, as_of)` that returns the `MatchResult` and a per-format dict of the update's unrounded net; `record` returns `_record(...)[0]`, and `_replay` calls `_record`. Do not rebuild the value from the rounded `deltas` or `imbalance`.
9. Live updates between replays do not change `replay_info` (as with `matches_counted`): the total covers the matches the replay counted. The backend adds `team_size * imbalance` of each live `MatchResult` (and its `parent`) between replays. The next replay rebuilds the total from the ledger.
10. #42 limits: this PR reports the signal only. A cap per match, account pair, or period, or a repeat discount for verified match types, would discard evidence and must be tuned out of sample with the evaluation harness before it is chosen (engine rules). File a follow-up issue for the limit and reference it in the PR body. The monitoring signal meets every acceptance criterion of #42, so the PR closes #42.

## Acceptance criteria

#59:
1. With `established_min_verified_matches` above zero, a player with only self-reported matches is never established, even at reliability above the threshold; the same player is established once enough counted verified matches are added (test with the rotating self-reported scenario above, then verified matches).
2. With `established_min_verified_matches=0`, `established` is exactly what it was on main for the same inputs.
3. Parametrized tests cover the field's validation with `pytest.raises(ConfigError, match=...)`: -1, a bool (True), a float (3.0), a str; and accept 0 and a large int. The default is 3, the field changes the fingerprint, and `to_dict`/`from_dict` round-trips it (also after `json`).
4. `verified_matches_played` counts tournament, league, and rec_verified matches and never self-reported ones; a frozen player gains none; a verified category match counts in the category and in overall doubles; a derived category state starts at 0; a seed with a nonzero value raises `InvalidSeedError`.
5. Snapshot: schema version 6; `verified_matches_played` is in every state; it round-trips; resume carries it (resume equals a full replay); loading rejects a value that is not a nonnegative int and one larger than `matches_played` (`SnapshotError`, matching the message). `tests/test_snapshot.py` is updated.
6. Ratings are unchanged: a synthetic ledger (singles, doubles, and a category, all match types, frozen seeds, clamps) replays to byte-identical rating states (every field except the new one) and identical `MatchResult`s on main and on this branch. Recorded under Proof.

#42:
7. A test reproduces the repeated-win scenario (new account beats an imported account ten times, 11-5, `REC_VERIFIED`) and shows `replay_info.net_rating_created[singles]` positive and equal to the sum of every player's rating change from their starting rating (within 1e-12), and 0.0 for every other format.
8. For any synthetic ledger, each format's total equals the sum of `team_size * imbalance` of the counted results (and parents) within the rounding bound, a category match adds to both its format and `doubles`, and frozen players and rejected matches add nothing.
9. Determinism: shuffled ledger order gives the identical `ReplayInfo` (equality, not approx). Resume from a checkpoint gives a `ReplayInfo` equal to a full replay's (exact float equality), also after a JSON round trip of the snapshot.
10. Snapshot `replay_info` has `net_rating_created` with exactly the `RatingFormat` values as keys and finite float values; loading rejects a missing or unknown key, a non-float value (an int, a bool, NaN), a nonzero value when `matches_counted` is 0, and a nonzero value for a format in which no player has a played state (`SnapshotError`, matching the message).

Both:
11. Module docstring, `Config`, `RatingState`, `PlayerSummary`, `ReplayInfo`, `to_snapshot`, and `from_snapshot` docstrings, the README (established text, Public API `ReplayInfo` line, snapshot schema section with version 6), and `CHANGELOG.md` under `## [Unreleased]` describe the new fields, the snapshot bump, and the default change to `established`. Claims of "always" or "never" hold for every input their stated conditions allow.
12. The full gate passes: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`. The property tests in `tests/test_properties.py` pass unchanged, or any change is explained in the PR.

## Files that change

- `src/open_engine/engine.py`: `_VERIFIED_MATCH_TYPES`; `Config.established_min_verified_matches` and its validation; `RatingState.verified_matches_played`; `_compute` increments it; `_seed_type_broken` and `_seed_rule_broken`; `PlayerSummary.established` rule and docstring; `ReplayInfo.net_rating_created` with `__post_init__`; `RatingEngine._record`; `_replay` running totals and resume start; `SNAPSHOT_VERSION = 6`; `_STATE_KEYS`, `_REPLAY_INFO_KEYS`; `_snap_state_fields`, `_check_state_invariants`, `_snap_replay_info` (new rules), and an engine-level check that a nonzero total has a played state in that format (in `_read_snapshot`, after players load); `to_snapshot` form of the mapping; docstrings of the module (established note, imbalance note, production notes), `to_snapshot`, `from_snapshot`.
- `README.md`: established paragraph, Public API `ReplayInfo` line, snapshot schema section (version 6 and what it adds, the new state field, the new replay_info key).
- `CHANGELOG.md`: entries under Unreleased (Added: the three fields; Changed: established needs verified matches by default, snapshot version 6 so version 5 snapshots do not load, stored `Config.to_dict` output from before lacks the new key).
- Tests: new `tests/test_verified_established.py` (#59) and `tests/test_net_rating_created.py` (#42); update `tests/test_snapshot.py` (schema and attribute lists), `tests/test_established.py` only where it asserts the self-reported count, and any test that builds a snapshot with version 5 or with the exact old key sets. Snapshot helper tests that construct raw snapshot dicts need the new keys.
- Follow-up GitHub issue for a #42 limit (no code).

## Order of work

1. Red tests for #59 (new file): established requires verified matches; validation table; fingerprint and round trip. Commit red alone, then implement the Config field, RatingState field, counting, seed rules, and summary rule.
2. Snapshot for #59: bump to 6, add the state key, validation, and the rule `verified_matches_played <= matches_played`. Red test first for the new rejection rules and resume carrying the count.
3. Red tests for #42 (new file): scenario total, per-format totals, shuffled determinism, resume equality; then `_record`, `_replay` totals, `ReplayInfo` field.
4. Snapshot for #42: replay_info key, form, validation rules; red test first.
5. Fix existing tests that hard-code version 5 or old key sets; check `tests/test_properties.py` passes unchanged.
6. Docs and changelog.
7. Byte-identical rating proof script (in the scratch directory, not committed): run on main and on the branch, compare.
8. Full gate.

## Risks

- Snapshot validation drift: every new rule must be one the engine never breaks. The rule "nonzero total needs a played state in that format" must hold for live engines too: players and played states are never removed, and seeds cannot replace a played state, so it holds. `verified_matches_played <= matches_played` holds because both increment together and seeds start at 0.
- Rejected rule: checking `matches_played - verified_matches_played` against the self-reported log. An unplayed imported state can be reseeded with a different capture date, so a logged self-reported match in which the player was frozen cannot be told apart from one that counted. Not checkable from the snapshot alone, so not added.
- Float exactness on resume: the running total must be stored unrounded and as a float. JSON writes floats with `repr`, which round-trips. Reject an int value on load, so a resume never starts from an int where a full replay had a float.
- `established` changes for existing players with fewer than 3 verified matches. Ratings do not change; prove it (criterion 6).
- `ReplayInfo` was hashable (frozen dataclass); the new field is a `_FrozenMapping`, which is hashable, so it stays hashable.
- `test_properties.py` uses self-reported matches; it does not assert `established`, so it should pass unchanged.

## Departures from the plan

- The snapshot tests for the new fields went into two new files, `tests/test_snapshot_verified_matches.py` (#59) and `tests/test_snapshot_net_rating_created.py` (#42), instead of new cases in `tests/test_snapshot.py`. `tests/test_snapshot.py` changed only where it pinned version 5 (it also gains a version 5 rejection case).
- `_compute` returns a third value, the update's `math.fsum` of applied changes, which `_record` collects per format. This is how decision 8's per-update value comes from `record`'s own computation.
- Existing tests that changed: the snapshot version pins in `tests/test_snapshot.py`, `tests/test_match_status.py`, `tests/test_inherited_live_updates.py`, `tests/test_content_duplicates.py`, and `tests/test_reserved_match_ids.py` (5 to 6); the default fingerprint pins in `tests/test_config_immutable.py` and `tests/test_float_summation.py` (`Config()` now gives `d921695e186f6bc5`); `tests/test_established.py::test_threshold_zero_and_hundred` sets `established_min_verified_matches=0`, since an unplayed player has no verified matches; `tests/test_addendum_review_fixes.py::test_inherited_live_updates_is_the_last_field` becomes `..._comes_after_opponents`, since the new field is now last.
- The `CHANGELOG.md` entry for issue #25 said `Config()` "still gives" `c5bb49f7c37a013a`. The new field changes the default fingerprint, so that line now says it gave that value after that change.
- Interim commits: the commit that adds the Config and state fields leaves the snapshot round-trip tests red until the schema commit, and the commit that adds the replay total leaves the resume tests red until the snapshot commit. Each red stretch ends in the next implementation commit.

## Proof

- Criterion 1: `tests/test_verified_established.py::test_self_reported_only_never_established` and `..._established_after_verified_matches`.
- Criterion 2: `..._zero_minimum_keeps_reliability_rule` (parametrized over the scenario dates).
- Criterion 3: validation table, default, fingerprint, round-trip tests.
- Criterion 4: counting tests per match type, frozen, category, derived state, seed rule.
- Criterion 5: `tests/test_snapshot.py` updates plus new rejection tests.
- Criterion 6: script output, both sides' SHA-256 of the canonical JSON, recorded below and in the PR.
- Criteria 7 to 10: `tests/test_net_rating_created.py`.
- Measured on main (f1d9363) with the scratch scripts `repro.py` and `measure.py`: self-reported-only player established after 25 matches; feeder scenario pool gain about +0.270, `ReplayInfo` has no total; established counts under the defaults for all-verified play: new player 7 to 13 matches, imported seed (captured on the first match date, which is frozen) 4 to 5 matches including the frozen one, so 3 to 4 counted.
- No live drive: this is a library with no app; the gate and the proof script exercise the changed paths, including failure paths (validation and snapshot rejections).
