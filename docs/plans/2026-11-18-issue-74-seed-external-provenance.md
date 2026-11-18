# Plan: Seed rating states can carry another player's or a blank external provenance (issue #74)

Risk tier: High. Handling of imported external ratings is security-sensitive (CLAUDE.md hard rule 7), and the change tightens seed and snapshot validation (breaking).

Domain skills: superpowers:test-driven-development, invoke. Django, FastAPI, React, Expo, payments, and email skills: not applicable because this is a pure Python package with no framework, UI, or service.

## Decision (recorded on #74)

Check the seed's own `external_ids`. A rating state with `initial_external_player_id` set must have that `(initial_source, initial_external_player_id)` pair in the same player's `external_ids`, so the #52 uniqueness check covers it. That is the state `seed_external_rating` always produces, the engine has no way to unlink an id, and the rule is per player, so snapshot loading enforces it too. A blank or whitespace-only `source` and an empty (or whitespace-only) `external_player_id` raise `InvalidSeedError`.

## Acceptance criteria

1. `seed_external_rating` raises `InvalidSeedError` for a snapshot whose `source` is blank or whitespace only, and for an `external_player_id` that is `""` or whitespace only (whitespace only used to raise `PlayerError`). Nothing changes when it raises. `None` still means "no external id" and links nothing.
2. A seed passed to `add_player`, `replay(seed_players=...)`, or `prequential_predictions` (both modes, and so `evaluate`) raises `InvalidSeedError` before any state changes when a rating state has:
   - an `initial_source` that is not a str, or is blank or whitespace only;
   - an `initial_external_player_id` that is not a str or `None`, or is blank or whitespace only;
   - an `initial_external_player_id` set while the seed's own `external_ids` does not map `initial_source` to that id.
3. A seed whose state names a `(source, id)` pair that it also lists in its own `external_ids`, where another player (or an earlier seed in the same batch) holds the pair, raises `PlayerError` ("This external identity is already linked to another player"), in every seed entry point, before any state changes (the existing #52 check, now covering the provenance).
4. `RatingEngine.from_snapshot` (and so `replay(from_snapshot=...)`) raises `SnapshotError` naming the field for a state with a blank or whitespace-only `initial_source`, a blank or whitespace-only `initial_external_player_id`, or an `initial_external_player_id` the player's `external_ids` does not map `initial_source` to. Every snapshot the engine's own methods produce still loads (a seeded, played, and replayed engine round-trips). `snapshot_version` stays 7 and the schema does not change.
5. Tests for each rule with synthetic ids, asserting the exception class and message. CHANGELOG entry marked **Breaking** with a migration note. Docstrings (`add_player` seed rules, `seed_external_rating`, `from_snapshot`, the module docstring's error notes if needed) and the README (errors list and snapshot schema) describe the new rules.

## Files that change

- `tests/test_seed_provenance.py` (new): every test below.
- `src/open_engine/engine.py`:
  - `_seed_type_broken`: return `"initial_source must be a str"` when `initial_source` is not a str, and `"initial_external_player_id must be a str or None"` when it is neither.
  - `_seed_rule_broken`: right after the type check, return `"initial_source must not be blank"` when `not _is_id(state.initial_source)`, and `"initial_external_player_id must not be blank"` when it is set and `not _is_id(...)`. These apply to every seed state (the engine never makes a blank `initial_source`: it uses `open_provisional`, `open_parent:<format>`, or a now non-blank external source).
  - `_validate_seed`: after the per-state rule check in the same loop, raise `InvalidSeedError(f"Seed for {player.id} in {fmt}: initial_external_player_id must be linked in external_ids under initial_source")` when `state.initial_external_player_id is not None and player.external_ids.get(state.initial_source) != state.initial_external_player_id`. `_add_seed` then checks the pair against held identities as today.
  - `seed_external_rating`: after the existing type checks, raise `InvalidSeedError("External snapshot source must not be blank")` when `not _is_id(snapshot.source)`, and `InvalidSeedError("External snapshot external_player_id must not be blank")` when it is not `None` and `not _is_id(...)`. Change the two `if snapshot.external_player_id:` tests to `is not None` (the same thing once blanks raise). Update the docstring.
  - `_snap_player`: after `_check_inherited_live_updates` (so existing derived-state messages keep precedence), for each format in sorted order with `p = f"{path}.ratings.{fmt.value}"`: `SnapshotError(f"{p}.initial_source must not be blank")`; `SnapshotError(f"{p}.initial_external_player_id must be null or not blank")`; `SnapshotError(f"{p}.initial_external_player_id must be linked in {path}.external_ids under initial_source")`.
  - Docstrings: `add_player` seed rules list, `seed_external_rating`, `from_snapshot`, and the module docstring's `InvalidSeedError` note.
- `README.md`: the `InvalidSeedError` bullet (blank external source or id in `seed_external_rating`, and seed provenance) and the snapshot schema's "Identities follow the seed and link rules" sentence.
- `README.md` also: the `PlayerError` bullet drops the whitespace-only source or id case for `seed_external_rating`, which now raises `InvalidSeedError`.
- `CHANGELOG.md`: a `**Breaking:**` entry under `### Security` in `## [Unreleased]`, with a "Migration:" note. Also a closing sentence on the issue #52 entry under `### Changed`, whose `seed_external_rating` rules the new entry replaces, so the unreleased notes do not contradict each other.

## Order of work

1. Write `tests/test_seed_provenance.py`. Run it on this branch with no engine change and confirm every new test fails for the expected reason (the call is accepted, or raises `PlayerError` where `InvalidSeedError` is expected). Commit it alone, red: `Add failing tests for seed external provenance (#74)`.
2. Engine changes above, then docstrings. Run the new tests green, then the full suite, and update any existing test whose expectation the decision changes (none expected: the existing derived-state provenance messages in `tests/test_addendum_review_fixes.py` keep precedence because the new snapshot check runs after `_check_inherited_live_updates`). If an existing test must change, note it here in the same commit.

   Done: five existing cases encoded the old `PlayerError` for a blank source or external id in `seed_external_rating`, which criterion 1 changes to `InvalidSeedError`. In `tests/test_failed_calls_create_nothing.py`, the two "seed with an identity and no source" cases now expect `InvalidSeedError("External snapshot source must not be blank")`, and "blank: seed_external_rating with no source" (blank player id and blank source) does too, because the snapshot field checks run before the player id check, as they already did for the other snapshot fields. In `tests/test_seed_identity_values.py`, the two `seed_external_rating` rows moved out of the `PlayerError` whitespace table into a new `InvalidSeedError` table with the new messages. Each still checks that nothing changes.
3. README and CHANGELOG.
4. Full gate.

## Tests (all in `tests/test_seed_provenance.py`)

Helpers: `D = RatingFormat.DOUBLES`; an engine with `syn-a` seeded through `seed_external_rating` with `ExternalRatingSnapshot("ext", 4.0, date(2026, 10, 1), external_player_id="X")`, giving state `s`. Ledger for replay and prequential: one completed synthetic doubles match after 2026-10-01 between four other synthetic ids.

- Another player's identity, not listed in the seed's own `external_ids`: `Player("syn-b", {D: replace(s)})`:
  - `add_player` raises `InvalidSeedError` matching `initial_external_player_id must be linked`, and `syn-b` is not added.
  - `replay(ledger, as_of=..., seed_players=[a_seed, b_seed])` and `prequential_predictions(..., mode="live")` and `mode="scheduled"` raise `InvalidSeedError`.
- Same, but listed: `Player("syn-b", {D: replace(s)}, external_ids={"ext": "X"})`:
  - `add_player` on the engine holding `syn-a` raises `PlayerError` matching `already linked to another player`, engine unchanged.
  - `replay` and both `prequential_predictions` modes with seeds `[a_seed, b_seed]` (where `a_seed` is a copy of the seeded `syn-a`) raise `PlayerError`.
- Own identity is accepted: `Player("syn-b", {D: replace(s, initial_external_player_id="Y")}, external_ids={"ext": "Y"})` adds, in `add_player`, `replay`, and `prequential_predictions`.
- Mismatch inside the player: external_ids `{"ext": "Y"}` with state id `"X"`, and external_ids `{"other": "X"}` with state id `"X"` under source `"ext"`: `InvalidSeedError`.
- Blank provenance, parametrized over `add_player`, `replay`, and both `prequential_predictions` modes: `initial_source` in `"", "  "`; `initial_external_player_id` in `"", " "`: `InvalidSeedError` matching the blank message. Also a provisional seed (no `initial_source_date`) with `initial_source="  "`.
- Wrong types: `initial_source=3` and `initial_external_player_id=3` on a seed: `InvalidSeedError` with the type message.
- `seed_external_rating`: `source` in `"", "  "` (with and without an external id), and `external_player_id` in `"", "  "`: `InvalidSeedError`; the engine has no player afterwards and its snapshot equals the one before. `external_player_id=None` with a non-blank source still seeds and links nothing.
- Snapshots: take `RatingEngine().to_snapshot()` of an engine with `syn-a` seeded (and one with a played match, replayed), JSON round-trip, and confirm it loads. Then tamper the JSON: `initial_source` to `"  "`; `initial_external_player_id` to `""` and `" "`; `external_ids` to `{}` (state still says `X`); `external_ids` to `{"ext": "Z"}`; state's `initial_source` to `"other"` while `external_ids` is `{"ext": "X"}`. Each raises `SnapshotError` matching the field path. Also a second player whose state names `ext:X` with empty `external_ids` raises `SnapshotError` (the case from the issue).
- A played state keeps its provenance: seed `syn-a`, record a match, snapshot, reload: loads.

## Risks

- Stored v7 snapshots that hold a blank `initial_source` or `initial_external_player_id` (which `seed_external_rating` could write on main for `source="  "` or `external_player_id=""`), or a state whose external id the player's `external_ids` lacks (only reachable through the seed bug this fixes), fail to load. `snapshot_version` stays 7, as with the issue #61 checks: the schema does not change, and bumping it would reject every v7 snapshot, including the valid ones. The CHANGELOG and PR body say so.
- `to_snapshot` checks its own output through the same loader, so it raises `SnapshotError` for such a state set directly on engine attributes. No engine method produces one after this change.
- Error ordering: a seed naming another player's pair without listing it gets `InvalidSeedError`, not `PlayerError`, since the seed rules run before the uniqueness check. This follows the decision; the PR body says so against the issue's acceptance criterion.
- Out of scope: #79 (category replacement in `seed_external_rating`), and whether a provisional state may carry an external id at all.

## Proof

- Criteria 1: the `seed_external_rating` tests. Criteria 2 and 3: the seed tests in every entry point. Criterion 4: the snapshot tests and the existing snapshot suites. Criterion 5: README, CHANGELOG, docstring review.
- Measured on main (6171807): the issue's reproduction plus a snapshot round-trip. `add_player` accepts `syn-b` with `syn-a`'s `ext:X` state and empty `external_ids`; accepts `initial_source="  "`, `initial_external_player_id=" "`; `seed_external_rating` accepts `source="  "`, `external_player_id=""`; `from_snapshot` loads the resulting snapshot. `seed_external_rating` with `external_player_id="  "` raises `PlayerError: source and external_player_id are required`.
- Live drive: this is a library. The drive is a script in the worktree's venv that runs the issue's reproduction and shows each call now raising the expected class, plus a seed, record, replay, snapshot, reload round-trip that still works.
