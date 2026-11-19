# Plan: Stored unplayed category state does not follow later doubles changes (issue #112)

Risk tier: High. Hard rule 7 (live must equal replay, determinism and provenance), and it changes the existing behavior of a public method (breaking).

Domain skills: django, django-allauth, django-celery, django-ninja-api, python-fastapi, React and Expo skills: not applicable because this is a standard-library Python package with no framework, UI, API, data store, auth, payments, or email.

## Decision (from the user, recorded on #112)

Option 1. `seed_provisional_rating` in a category format returns the starting state without storing it, as `state` does. Breaking: changelog entry with a migration note.

"Category format" here means exactly: a `RatingFormat` whose `parent` is not `None` (mixed, men's, and women's doubles) while `Config.category_feeds_parent` is on. Only then does `_new_state` read the parent, so only then can a stored copy go stale. With `category_feeds_parent` off, a category format's starting state is always the provisional default, which no later change moves, so it keeps storing as before. Singles and overall doubles keep storing as before.

## Acceptance criteria

1. Under `category_feeds_parent`, `seed_provisional_rating(p, fmt)` for a category `fmt` the player has no stored state in returns the same state `state(p, fmt)` returns (a new object each call) and stores no rating state. For an existing player, `to_snapshot()` is unchanged by the call and a replayed engine stays resumable. For an unknown player id it still creates the empty player record, as before, and nothing else. A stored state (a seed, or a played state) is returned as before.
2. With `category_feeds_parent` off, and for singles and overall doubles in any config, `seed_provisional_rating` stores the state as before.
3. Each path in the issue gives live equal to a replay, for every player and format:
   - Path 1, the parent plays first: doubles seed, `seed_provisional_rating(p, category)`, a counted doubles match (and, separately, another category's match whose doubles update moves the parent), then the category match.
   - Path 2: `seed_provisional_rating(p, DOUBLES)`, `seed_provisional_rating(p, category)`, a doubles seed (first seed, and a reseed of the provisional doubles state with `replace_unplayed_seed=True`), then the category match.
   - The #99 comment case: `max_doubles_partner_gap` set, `seed_provisional_rating(p, category)` after p played counted doubles matches, more doubles matches, then a category match: live and replay agree on whether it counts and on every rating.
   For each, the replay "without" the category state uses seeds taken from the engine's own players before the first match (the engine now stores none), and the replay "with" the category state uses a backend seed that carries it, added live by `add_player` and passed to the replay, where such a seed is valid under the seed rules. Both equal live.
4. The #101 refresh in `seed_external_rating` is kept, because `add_player` and replay seeds can still put a stored, unplayed `open_parent:doubles` category state in the engine (and `from_snapshot` can load one). Its tests now build that state through `add_player`, and still show live equals replay with and without the state passed.
5. Seeds passed by a backend (`add_player`, `replay` seeds) carrying an unplayed category state are accepted exactly as now. Snapshot validation is unchanged: snapshots that hold an unplayed derived category state an earlier release's `seed_provisional_rating` stored (for example with `inherited_live_updates` or an `rd_as_of` later than its source date) still load, so a stored snapshot from the previous release is not stranded without a `snapshot_version` bump.
6. A fuzz test: random sequences of engine calls (seeds through every entry point, `seed_provisional_rating` in every format, counted and rejected matches in every format, `replay`) under random configs, including `category_feeds_parent` on and off and `max_doubles_partner_gap` set, check that every `to_snapshot()` loads with `from_snapshot` and round trips.
7. Docs and CHANGELOG say the new contract, with a **Breaking:** entry and a Migration note. No `Config`, `RatingState`, or snapshot format change; `snapshot_version` stays 8.

## Files that change

- `src/open_engine/engine.py`
  - `seed_provisional_rating`: after `_player_or_new`, when the format has no stored state and `rating_format.parent is not None and self.cfg.category_feeds_parent`, return `self._new_state(player_id, rating_format)` without `_put_state`. Docstring: the new contract and why (a stored copy would not follow later doubles changes, so live and a replay could disagree).
  - `state` docstring: drop "Use seed_provisional_rating to store it"; say where it stores and where it does not.
  - `seed_external_rating` docstring and the comment at the refresh: the refresh now applies to a stored unplayed derived state that a seed (`add_player`, replay seeds) or a loaded snapshot holds; drop the "(issue #112)" sentence that says such a state does not follow match changes, or restate it for seeds only.
  - `add_player` docstring and `_reserved_source_broken` docstring only where their text about "the engine's own states" becomes wrong.
- `tests/test_category_seed_not_stored.py` (new, committed red and then frozen): criteria 1, 2, 3, plus `seed_external_rating(p, category)` after `seed_provisional_rating(p, category)` no longer needs `replace_unplayed_seed`.
- `tests/test_category_seed_fuzz.py` (new): criterion 6. Random call sequences under random configs check that every snapshot loads and round trips. A second test seeds first, then records matches in date order with `seed_provisional_rating` calls between them that must store nothing, and checks that live equals a replay given the engine's own players, taken before the first match, as seeds (processed and reserved ids and every state). It is in its own file because the criterion 1 to 3 file was frozen after its red commit.
- `tests/test_category_seed_import.py` (new): after `seed_provisional_rating(p, category)`, `seed_external_rating(p, category, ...)` is accepted with or without `replace_unplayed_seed`, with no doubles state, a dated doubles seed, or a played doubles state. The last is the issue #79 repro, now accepted through the public API, per the #79 decision that seeding a category format with no stored state stays allowed. A stored derived state from an `add_player` seed is still refused.
- `tests/helpers.py` (new): `store_state`, which stores `engine.state(p, fmt)` with `engine._put_state` (documented as a state an earlier release's `seed_provisional_rating` stored), and `derived_seed`, a backend seed holding a doubles seed and its derived category state.
- Existing tests that relied on `seed_provisional_rating` storing a category state (43 fail with the change; measured below). Each is updated to the new contract, not weakened:
  - Tests that assert the old storing behavior (`test_resume_after_changes.py` case "seed_provisional_rating creates a state", `test_read_only_lookups.py` "category with played parent") change to assert the new behavior (or use a non-category format where the test is about storing in general).
  - Tests that need a stored unplayed derived category state for another purpose get it through `add_player` with a seed-shaped state where the state is seed-shaped (`test_reseed_refreshes_derived.py`, `test_seed_keeps_open_history.py`, `test_reserved_provenance.py`, `test_reserved_provenance_shapes.py`, `test_seed_validation.py`, `test_future_seed.py`, `test_seed_sensitive_rejections.py`).
  - Snapshot-load tests whose state is not seed-shaped (`inherited_live_updates` nonzero, `rd_as_of` after the source date, undated derived with a played parent: `test_inherited_live_updates.py`, `test_inherited_rd_checks.py`, `test_addendum_review_fixes.py`) get it from one shared test helper that stores `engine.state(p, fmt)` with `engine._put_state`, documented as "a state an earlier release's seed_provisional_rating stored", since those snapshots must still load (criterion 5).
  - Test helpers that set a category rating by assigning to the state `seed_provisional_rating` returned (`test_winprob.py`, `test_own_k.py`, `test_properties.py`) store it explicitly with `store_state` instead, so the assignment is not silently lost.
  - Tests that still passed but whose setup no longer stored a category state get it explicitly: seeds carrying the state `state` returns (`test_seed_replay_pinned.py`, `test_external_seed_types.py`, `test_replay_resume.py`, `test_seed_provenance_entry_points.py`) or `store_state` (`test_snapshot_invariants.py`). `test_resume_after_changes.py` also gains a case for a category `seed_provisional_rating` that keeps the engine resumable. Stale test names in `test_seed_keeps_open_history.py`, `test_reserved_provenance.py`, and `test_read_only_lookups.py` are renamed to say what they test.
- `CHANGELOG.md`: **Breaking:** entry under `## [Unreleased]` with Migration.
- `README.md`: the `InvalidSeedError` refresh sentence and any line that says `seed_provisional_rating` stores in every format.

## Order of work

1. Red: commit the new test file with the criterion 1 and criterion 3 tests failing on main (they reproduce: measured below).
2. Implement the `seed_provisional_rating` change.
3. Update each existing test listed above; rerun the suite.
4. Fuzz test (criterion 6).
5. Docstrings, README, CHANGELOG.
6. Full gate.

## Risks

- A backend that stored a category state through `seed_provisional_rating` and then read it from `players` or the snapshot will no longer find it. Migration note covers it.
- Tests that mutate the returned state to set up a rating would silently stop setting it in category formats. Every such helper must be found (grep for `seed_provisional_rating(` followed by `.rating =` or by mutation of the result).
- Removing the #101 refresh would reopen #101 for `add_player` seeds; it stays.
- Tightening snapshot validation would strand stored snapshots from the previous release; not done.

## Proof

- Criterion 1, 2: new tests in `tests/test_category_seed_not_stored.py`.
- Criterion 3: new live versus replay tests, one per path, with and without the category seed.
- Criterion 4: rewritten `tests/test_reseed_refreshes_derived.py`.
- Criterion 5: the existing snapshot rule tests, kept passing through the helper.
- Criterion 6: the fuzz test in `tests/test_category_seed_fuzz.py`. Its live-versus-replay test fails 39 of 60 runs on the engine before this change.
- Live drive: run the issue's repros as scripts against the branch and show live equals both replays.

Measured on main (e38aeca), scratch scripts in the session scratchpad:

- Path 1: `live=4.0559 with=4.0559 without=4.1800 stored=yes`. (An earlier scratch run recorded 4.1687 for "without"; it used a slightly different setup. The setup described in the issue gives 4.1800 on main and on the branch.)
- Path 2: `live=3.6066 with=3.6066 without=2.5990 stored=yes`.
- Comment case (`max_doubles_partner_gap=0.2`): live counts the mixed match (stale stored copy at 4.128), a replay from the engine's seeds rejects it (fresh doubles 3.705).
- Applying only the `seed_provisional_rating` change on main: 43 failed, 3667 passed (the tests listed above).
