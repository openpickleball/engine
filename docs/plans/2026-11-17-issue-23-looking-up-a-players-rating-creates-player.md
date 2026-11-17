# Plan: Looking up a player's rating creates player records (issue #23)

Risk tier: High. The change alters the contract of three public `RatingEngine` methods (`player`, `state`, `player_summary`), changes existing behavior rather than adding, touches the resumable-snapshot mark (replay provenance, a security-sensitive area), and spans the engine, the error module, the exports, the README, the changelog, and several test files. It is fully unit-testable.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-celery, django-allauth, python-fastapi, React and Expo skills: not applicable because this is a standard-library-only Python package with no framework, API layer, or UI.

Decisions taken with the maintainer:

- An unknown player id raises a new `UnknownPlayerError` (a `PlayerError` subclass) from `player()`, `state()`, and `player_summary()`. They never return `None`.
- `player()` becomes read-only too. The methods that create on purpose keep creating through an internal helper: `seed_provisional_rating`, `seed_external_rating`, `link_account`, `attach_external_id`, `add_player`, and `record` (and so `replay`).
- A known player with no state in the requested format: `state()` returns the starting state the player's first match in that format would use (`_new_state`: a category state derived from overall doubles where that applies, else a provisional default), and `player_summary()` reports it. Neither stores it.

## Acceptance criteria

1. No reporting call changes engine state. `player()`, `state()`, `player_summary()`, `predict_point_share()`, `predict_win_probability()`, and `to_snapshot()` leave `to_snapshot()` output (which includes the `resumable` mark) and the `players` keys exactly as they were, for a known player in a format they have, a known player in a format they lack (category derived from parent, and a plain provisional format), and an unknown player id (where the call raises). A test compares state before and after.
2. `player(id)`, `state(id, fmt)`, and `player_summary(id, fmt, ...)` raise `UnknownPlayerError` for an id with no player record, and the engine is unchanged after the raise. The issue's reproduction, `player_summary("ghost", DOUBLES, as_of=...)` on an empty engine, raises and leaves `players` empty.
3. `UnknownPlayerError` subclasses `PlayerError` (so `OpenEngineError` and `ValueError`), is exported from `open_engine` and listed in `__all__`, has a docstring, and is in the README "Public API" errors list.
4. A blank or whitespace player id still raises `PlayerError` from `player()` (existing message "player_id is required"), not `UnknownPlayerError`.
5. For a known player with no state in a format, `state()` equals the state `seed_provisional_rating` would create, and `player_summary()` equals the summary after `seed_provisional_rating` on a copy of the engine, with nothing stored. Mutating the returned state changes nothing in the engine.
6. Explicit creation still works for a new id: `seed_provisional_rating`, `seed_external_rating`, `link_account`, `attach_external_id`, `add_player`, and `record` create the player as before, and still clear the resumable mark.
7. The unknown-player behavior is documented: the docstrings of `player`, `state`, and `player_summary`, the README (errors list, the `player_summary` paragraph where it applies, and the snapshot note that today says `player_summary` and `state` create a state), and a `CHANGELOG.md` entry under `[Unreleased]` that calls the change breaking.
8. No replay, resume, or evaluation output changes for any ledger that worked before. A read no longer leaves a player or rating state behind, so every later call sees the engine as it was before the read, and live behavior after a read can change. Examples: a live engine that read a missing format before that format's first match used to store a state frozen at the read, and now rates that first match from the current parent, as a replay does; `seed_external_rating` after a `state` read used to raise `InvalidSeedError` and now seeds; `add_player` after a `player_summary` of an unknown id used to raise `PlayerError` and now adds the player. A snapshot taken after a read is now resumable. The existing suite passes, with tests changed only where they relied on a read creating a player or state.

## Files that change

- `src/open_engine/_errors.py`: add `class UnknownPlayerError(PlayerError)` with a docstring ("No player record exists for the player id.").
- `src/open_engine/engine.py`:
  - Re-export `UnknownPlayerError` with the other errors (follow how `PlayerError` is imported and re-exported).
  - New private `_player_or_new(self, player_id: str) -> Player`: today's `player()` body (blank check, then create through `_put_player`).
  - `player()`: blank check as today (`PlayerError("player_id is required")`), then `UnknownPlayerError(f"Unknown player: {player_id}")` when the id is not in `self.players`. Never creates. Docstring says so and names both errors.
  - `link_account`, `attach_external_id`, `seed_external_rating`, `seed_provisional_rating`, `_commit`: call `_player_or_new` where they call `self.player` today. Behavior unchanged.
  - `state()`: `player = self.player(player_id)` (raises for unknown), then return `player.ratings[fmt]` if present, else `self._new_state(player_id, fmt)` without storing. Docstring: read-only, never stores, what it returns for a missing format, that the returned object for a missing format is not engine state, raises `UnknownPlayerError`. It no longer says "Same as seed_provisional_rating".
  - `player_summary()`: keep the as_of type and reference checks first, then `self.player(player_id)` and `self.state(...)`. Docstring: never changes engine state, raises `UnknownPlayerError` for an unknown id (after the `ReferenceDateError` checks), and for a known player with no state in the format it reports the starting state that format's first match would use. Replace the sentence "These checks come first, so a failed call creates no player or rating state."
  - `to_snapshot` docstring (around line 2113): drop "including one player_summary or state() creates" from the list of state changes.
  - Module docstring: edit only if a note there says reads create state (grep it).
- `src/open_engine/__init__.py`: import and export `UnknownPlayerError`, add to `__all__` next to `PlayerError`.
- `README.md`: add `UnknownPlayerError` to the Public API errors list right after `PlayerError`; fix line ~140 (`ReferenceDateError` "before it creates any player or rating state") and line ~196 (the snapshot note "`player_summary` and `state` create one for a format the player has no rating in"); add one sentence in the `player_summary` paragraph on unknown ids and missing formats.
- `CHANGELOG.md` under `## [Unreleased]`: `### Added` entry for `UnknownPlayerError`; `### Changed` entry, marked **Breaking**, for `player()`, `state()`, and `player_summary()` no longer creating anything (issue #23). Correct the existing Unreleased snapshot entry's "including one `player_summary` creates", which becomes false.
- Tests:
  - New `tests/test_read_only_lookups.py` for criteria 1 to 6.
  - `tests/test_resume_after_changes.py`: in `CHANGES`, remove "player_summary creates a category state", "state creates a state", and "player creates a player"; add "player_summary of a missing category state" and "state of a missing category state" to `READS`; add one test that `player("syn-n")` raises `UnknownPlayerError` and the snapshot stays resumable. Keep "seed_provisional_rating creates a state".
  - Any other existing test that fails because it read an unknown player or relied on a read creating a state: create the player or state explicitly (`seed_provisional_rating`, `add_player`) before the read, so the assertion keeps its meaning. Never weaken or delete an assertion; list every such edit in the report.

## Changes from the plan during the work

- `UnknownPlayerError` goes in `__all__` in sorted order (after `SnapshotError`), not next to `PlayerError`, since the list is sorted. `tests/test_api.py` `PUBLIC_API` gains the name, as every new export does.
- The engine module docstring's error list names `UnknownPlayerError` under `PlayerError`.
- Three tests that still passed relied on `state()` storing a category seed, and lost their meaning without it: `tests/test_snapshot_invariants.py` (a derived category state loads), `tests/test_external_seed_types.py` (accepted seeds replay), and `tests/test_replay_resume.py` (a category seed). They now call `seed_provisional_rating`, as the other adjusted tests do.

- Review round 1. The maintainer approved keeping the live behavior change found in review (a read of a missing category format no longer freezes that format's starting state, so the first category match rates from the current parent, equal to a full replay) and narrowing criterion 8 and the changelog claim to match. The docs now say only a counted `record` creates a player, list `link_account` and `attach_external_id` as creating calls, and say an id named only in uncounted rows or rows after `as_of` has no player record. New tests go in `tests/test_read_only_lookups_followup.py`, since the first two test files are frozen: the first-category-match guard, `predict_win_probability` before and after, and the uncounted or later row ids.

- Review round 2. The changelog entry and criterion 8 state one general rule (a read leaves no player or rating state behind, so every later call sees the engine as it was before the read) with three examples, instead of presenting the category case as the only live change. Two more tests in `tests/test_read_only_lookups_followup.py` pin the seed-after-read and add-after-failed-summary cases.

## Order of work

1. Red: write `tests/test_read_only_lookups.py` and the `test_resume_after_changes.py` changes. Run them and save the failing output. Commit as the red test, then freeze them.
2. Add `UnknownPlayerError` to `_errors.py`, `engine.py` re-export, `__init__.py`, and the README Public API list (the public API tests need all three together).
3. Add `_player_or_new`, switch the creating methods to it, then make `player()` read-only.
4. Make `state()` read-only, then `player_summary()` picks it up.
5. Run the suite; fix tests that relied on reads creating (see Files that change). Update docstrings.
6. README and CHANGELOG text.
7. Full gate and live drive.

## Risks

- Riskiest: an internal caller that relied on `player()` creating, missed in the switch to `_player_or_new`. The grep at plan time found five (`link_account`, `attach_external_id`, `seed_external_rating`, `seed_provisional_rating`, `_commit`); recheck with `grep -n "self.player(" src/open_engine/engine.py` after the switch. A missed one in `_commit` would make `record` raise for a new player, which the existing suite catches.
- Resume: reads no longer clear the resumable mark. That is correct, because they change nothing, but a read that does change state would now be a provenance hole. Criterion 1 compares the full snapshot, including `resumable`, so it catches one.
- `state()` for a missing format returns a fresh object each call. A caller that mutated it to change engine state silently gets nothing. That is the intended behavior and is documented.
- Backend callers that used `player()`/`state()` to create a player break loudly with `UnknownPlayerError`. That is the point of the issue; the changelog calls it breaking and names `seed_provisional_rating` and `add_player` as the explicit way to create.
- Not done: no change to `player()`'s handling of non-str ids, to `seed_provisional_rating` storing a state for a format passed as a plain str, or to `link_account` creating a player (its docstring says "existing" profile). Out of scope for this issue.

## Proof

- Criterion 1: `test_read_only_lookups.py`, a parametrized test over (call, case) comparing `to_snapshot()` and `list(engine.players)` before and after, on a replayed engine (so `resumable` starts `True`), plus `test_resume_after_changes.py` READS.
- Criterion 2: parametrized `pytest.raises(UnknownPlayerError, match="Unknown player")` over the three calls, with the snapshot unchanged; the issue's repro as its own test.
- Criterion 3: an `issubclass` test; the existing public API tests check `__all__`, README, and docstring.
- Criterion 4: existing `tests/test_api.py` blank id test plus a parametrized test that a blank id raises `PlayerError` and not `UnknownPlayerError`.
- Criterion 5: compare against a deep copy after `seed_provisional_rating`, for a category format with a played parent and for a format with no parent; mutate the returned state and compare the snapshot.
- Criterion 6: parametrized over the six creating calls on a new id: the player exists after, and the snapshot's `resumable` is `False`.
- Criterion 7: the doc finder in Phase 8 checks the docstrings, README, and changelog against the code.
- Criterion 8: the full gate (`ruff check .`, `ruff format --check .`, `mypy src`, `pytest`) and `python examples/basic_match.py`.
- Live drive: in a scratch script outside the repo, show (a) the issue repro now raises `UnknownPlayerError` with `players == {}`, (b) a known player's missing category format reports a derived rating with nothing stored, (c) a replayed engine stays resumable after reads and `replay(..., from_snapshot=...)` accepts its snapshot, and (d) `record` with brand-new ids still works.

Measured on `origin/main` (878f2fa), with `/tmp/claude-1000/.../scratchpad/measure.py`: one doubles match recorded, then `player_summary("syn-a", MIXED_DOUBLES, as_of=2026-02-01)` on one deep copy vs `seed_provisional_rating` then `player_summary` on another. Result: `summary alike: True 3.55 0.7546 open_parent:doubles`, `state alike: True`. The issue repro on an empty engine: `ghost players: ['ghost']`.

Review round 1, measured against `origin/main` (878f2fa) source extracted to the scratchpad and put on `PYTHONPATH`, running `test_a_read_of_a_missing_category_does_not_change_its_first_match` from `tests/test_read_only_lookups_followup.py` (import of `UnknownPlayerError` stripped):

```
E       assert 3.684435341259454 == 3.928180391780873
E       assert 3.599565978285813 == 3.8714260743043063
FAILED test_followup_on_main.py::test_a_read_of_a_missing_category_does_not_change_its_first_match[read after a parent match]
FAILED test_followup_on_main.py::test_a_read_of_a_missing_category_does_not_change_its_first_match[read before any parent match]
2 failed, 4 deselected in 0.02s
```

On the branch both cases pass: the rating with the read equals the rating without it and equals a full replay.

Review round 2, the same `origin/main` source on `PYTHONPATH`, scratch script `drive23_c1.py`:

```
add_player(Player('syn-a')) -> None
state('syn-a', DOUBLES).rating -> 3.5
seed_external_rating('syn-a', DOUBLES, 4.0) -> raised InvalidSeedError: A rating already exists; explicitly allow replacing an unplayed seed
stored doubles rating: 3.5
player_summary('syn-g', DOUBLES, as_of=2025-03-01).rating -> 3.5
add_player(Player('syn-g')) -> raised PlayerError: Player already exists: syn-g
```

On the branch the seed stores 4.0, `player_summary` raises `UnknownPlayerError`, and `add_player` then succeeds. `test_a_seed_after_a_state_read_needs_no_replace` and `test_add_player_after_a_failed_summary_succeeds` pin this.
