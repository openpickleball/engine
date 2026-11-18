# Plan: External sources can use the engine's reserved provenance names (issue #97)

Risk tier: High. It touches the handling of imported external ratings and identity links, which CLAUDE.md hard rule 7 names as security-sensitive, it changes existing behavior at several public entry points (a breaking change), and it touches the snapshot loader.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-allauth, django-celery, python-fastapi, React and Expo skills: not applicable because this is a standard-library Python package with no web, UI, or framework code.

Decision from the maintainer (recorded on #97, counts as plan approval): reserve the `open_` prefix. An external source that starts with `open_` is rejected in `seed_external_rating`, seeds, `attach_external_id`, and snapshot loading (`InvalidSeedError`, `PlayerError` for `attach_external_id`, `SnapshotError` on load), before any state changes. `initial_evidence_reference` gets a type and non-blank check only (str or None, not blank or whitespace-only when given), with no uniqueness rule.

## Reproduction on main (7e29f0b)

Script (synthetic ids): `seed_external_rating("syn-a", MIXED_DOUBLES, ExternalRatingSnapshot("open_parent:doubles", 4.0, date(2026, 10, 1), external_player_id="Q"))` stores `initial_source` `open_parent:doubles`; `attach_external_id("syn-b", "open_provisional", "Z")` succeeds; `add_player(Player(id="syn-c", external_ids={"open_x": "W"}))` succeeds; `seed_external_rating(..., ExternalRatingSnapshot("ext", 4.0, date(2026, 10, 1), evidence_reference="  "))` succeeds; the snapshot of all of that loads; and a reseed of syn-a's mixed doubles state raises "Cannot replace a rating derived from OPEN overall doubles", which is wrong for an import.

## How engine-written provenance is told apart from an import

The engine writes a reserved `initial_source` in exactly two places, both in `_new_state`:

- `open_provisional`: `initial_source_date` None, and no `initial_source_reliability`, `initial_external_player_id`, or `initial_evidence_reference`.
- `open_parent:<parent.value>`: only in a category format whose `parent` is that format, with `initial_source_date` copied from the parent (None or a date), and no imported reliability, external player id, or evidence reference.

Every other state with an `initial_source` that starts with `open_` comes from an import (`seed_external_rating` always sets `initial_source_date` to the capture date) or from a caller-built seed claiming the name. So a state's reserved `initial_source` is accepted only in one of the two shapes above; any other reserved `initial_source` is treated as an imported one and rejected. Review finding F1 tightened `open_parent:<parent>` to how `_new_state` derives it, in seeds (the engine's config, the parent in the same seed's ratings) and snapshots (the snapshot's config): (a) `category_feeds_parent` is on; (b) the same player has a state in the parent format; (c) the state is dated exactly when the parent state is (equal dates are not required: a parent reseed with `replace_unplayed_seed` leaves the child's older date), and an undated one needs a played parent, so an undated `open_parent:` seed is always rejected; (d) while the state is unplayed, `rd >= category_seed_rd`. A fuzz of engine operations (live, replay, resume, prequential) confirmed every engine-produced state meets (a) to (d). `open_provisional` stays as is: no date and no import-only field, with any provisional rating, since it is `RatingState`'s default. A seed cloned from an engine with `category_feeds_parent` on is rejected by an engine with it off.

Residual: a dated `open_parent:<parent>` state that meets (a) to (d) and has none of the three import-only fields, played or not, with any rating in range, has the same shape whether the engine derived it, a caller built it as a seed, or an older version imported it under that name. A new engine can no longer import one, but such a seed is accepted and a stored snapshot from before this change that holds one still loads. An `open_provisional` state can carry any provisional rating. This is stated in the changelog.

## Acceptance criteria

1. `seed_external_rating` raises `InvalidSeedError` for a snapshot `source` that starts with `open_` (for example `open_parent:doubles`, `open_provisional`, `open_x`), after the existing source type and blank checks and before any state changes. No player is created.
2. `attach_external_id` raises `PlayerError` for a `source` that starts with `open_`, before any state changes. No player is created.
3. `add_player`, `replay(seed_players=...)`, and `prequential_predictions(seed_players=...)` in both `"live"` and `"scheduled"` modes raise `InvalidSeedError` before any state changes for a seed whose `external_ids` has a key that starts with `open_`, and for a seed state whose `initial_source` starts with `open_` and is not in one of the two engine shapes (for example a dated `open_provisional`, an `open_parent:doubles` state in `doubles` or `singles`, an `open_parent:` state with an imported reliability, an external player id, or an evidence reference, an `open_parent:` state that breaks (a) to (d) above, or `open_x`). A dated seed that never set `initial_source` gets the default `open_provisional` and is rejected.
4. Those same entry points still accept the engine's own shapes: a provisional seed with the default `initial_source` (`open_provisional`, no date), and an unplayed dated `open_parent:doubles` category state with no import-only field that meets (a) to (d) (the seed's doubles state dated too, on any date), as long as it follows the other seed rules. An undated `open_parent:` seed is rejected, since a seed's parent is unplayed. Two frozen accept rows (`derived_undated`, `derived_dated` with no doubles state) were flipped to rejection rows with the coordinator's approval.
5. `RatingEngine.from_snapshot` (and so `replay(from_snapshot=...)`) raises `SnapshotError` naming the field for an `external_ids` key that starts with `open_`, and for a state whose reserved `initial_source` is not in one of the shapes above, including an `open_parent:` state that breaks (a) to (d) under the snapshot's config. `to_snapshot()` checks its own output the same way, as it already does for provenance.
6. `initial_evidence_reference`: a str or None (already checked since #96), and now also not blank or whitespace only when given. `seed_external_rating` raises `InvalidSeedError` for a blank or whitespace-only `evidence_reference` (`None` is fine); seeds raise `InvalidSeedError`; snapshot loading raises `SnapshotError`. No uniqueness rule.
7. Every snapshot the engine's own methods produce from accepted input still loads: a test builds an engine through `seed_external_rating` (with an external id and evidence reference), `seed_provisional_rating` for a category format after a doubles import (an `open_parent:doubles` dated state), `add_player` with a provisional seed, `attach_external_id`, `record`, and `replay` with a played doubles parent and derived category, and `from_snapshot(json.loads(json.dumps(snap)))` gives an equal snapshot.
8. The issue's reproduction raises `InvalidSeedError`. The #79 refusal ("Cannot replace a rating derived from OPEN overall doubles") can no longer be reached by an import created through this engine; docs and changelog say so.
9. Plain inputs that were valid and do not use the prefix behave as before: the existing suite passes unchanged.
10. Docs: the docstrings of `seed_external_rating`, `attach_external_id`, `add_player` (seed rules), `from_snapshot`, `_check_provenance`, and the module error notes; README's `InvalidSeedError`, `PlayerError`, and snapshot schema text; `CHANGELOG.md` gets a `**Breaking:**` entry with a Migration note. Snapshot schema and `snapshot_version` do not change; no `Config`, `RatingState`, or `Player` fields change.

## Files that change

- `src/open_engine/engine.py`:
  - New private constant `_RESERVED_SOURCE_PREFIX = "open_"` and helper `_reserved_source_broken(state: RatingState, fmt: RatingFormat) -> str | None` that returns None when `initial_source` does not start with the prefix or the state has one of the two engine shapes, else a message naming the value.
  - `_seed_rule_broken`: `initial_evidence_reference` not blank when set.
  - `_validate_seed`: reject an `external_ids` key with the prefix (after the blank check); call `_reserved_source_broken` per state (after `_seed_rule_broken`, before the link check).
  - `seed_external_rating`: reject a reserved `source` after the blank check; reject a blank `evidence_reference`.
  - `_check_external_id`: reject a reserved source with `PlayerError` after the blank check (used by `attach_external_id`; `seed_external_rating` rejects first with `InvalidSeedError`).
  - `_snap_player`: reject a reserved `external_ids` key; `_check_provenance`: reserved `initial_source` via the helper, and a blank `initial_evidence_reference`.
  - Docstrings listed in criterion 10.
- `tests/test_reserved_provenance.py` (new): criteria 1 to 8, parametrized per entry point, asserting the specific error subclass with `match=` and that state is unchanged (`to_snapshot()` equal before and after).
- `README.md`, `CHANGELOG.md`.

## Order of work

1. Red tests, committed alone (`test: failing tests for issue 97`).
2. Helper and checks in `engine.py`.
3. Docstrings, README, changelog.
4. Gate: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.

## Risks

- Rejecting a seed state that the engine itself produced. Mitigated by criterion 4 and 7 tests that round-trip every engine path that writes `open_provisional` or `open_parent:`.
- Existing tests that use an `open_`-prefixed external source would break; search the suite and change only test data that is not about this rule (none expected).
- The prefix is case sensitive and exact: `OPEN_x` or ` open_x` is not reserved. The engine compares `initial_source` exactly, so those names cannot be mistaken for engine provenance.
- Not done: no uniqueness rule for evidence references; no check of an `open_parent:` state's rating or date against its parent state's (a parent reseed changes both).

## Proof

Red tests (`tests/test_reserved_provenance.py`) on 47274fd: 77 failed, 39 passed. The failures: 54 `DID NOT RAISE InvalidSeedError` (reserved sources and blank evidence references in `seed_external_rating` and in seeds through `add_player`, `replay`, and both `prequential_predictions` modes), 5 `DID NOT RAISE PlayerError` (`attach_external_id`), 14 `DID NOT RAISE SnapshotError` (`from_snapshot`, `replay(from_snapshot=...)`, `to_snapshot`), and 4 regex mismatches where an `open_parent:doubles` seed state with an external player id fails only the later link check today. The 39 that pass are the criterion 4 and 7 tests (engine shapes accepted and round tripped), the blank-before-prefix order, sources without the exact prefix, a repeated evidence reference, and the #79 refusal for an engine-derived state.

Review fixes (F1 to F5), red tests on f78b498: 52 failed, 128 passed across `tests/test_reserved_provenance_shapes.py` (new) and `tests/test_reserved_provenance.py`. The failures: 32 `DID NOT RAISE InvalidSeedError` for `open_parent:` seed shapes breaking (a) to (d) at all four entry points, 8 for the two flipped frozen rows (`derived_undated`, `derived_dated`), 7 `DID NOT RAISE SnapshotError` for those shapes in snapshots and `to_snapshot()`, 2 more `DID NOT RAISE InvalidSeedError` (the cloned seed under `category_feeds_parent=False`, and the unchanged-state check), and 3 message mismatches where the rejection had no detail suffix yet. The F5 resume, unchanged-state, and `category_feeds_parent=False` prefix tests already pass on f78b498 and go in alongside.
