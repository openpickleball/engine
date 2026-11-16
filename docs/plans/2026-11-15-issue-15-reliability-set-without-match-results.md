# Plan: Reliability can be set without match results (issue #15)

Risk tier: High. The change is in the handling of imported external ratings and in who can set another player's reliability, both security-sensitive areas (CLAUDE.md hard rule 7). It also breaks the public API (`seed_external_rating`, `Config`, `PlayerSummary`, `ReliabilityBasis`).

Domain skills: none apply. This is a standard-library math package with no web, API, data store, authentication, payments, or email layer. The implementer uses superpowers:test-driven-development.

Decisions approved by the maintainer before this plan was committed:

- `rd_override` is removed from `seed_external_rating`, not kept for tests.
- `Config.external_reliability_maps_to_rd` is removed, not gated behind a flag. An external score never becomes OPEN RD.
- `PlayerSummary.reliability` is always OPEN-earned. `open_reliability` and `reliability_basis` are removed, and so is the `ReliabilityBasis` enum (added in this unreleased cycle, never published). A new field, `imported_reliability: float | None`, holds the imported reliability while it is carried (same carry rules as today) and is `None` otherwise.
- `add_player` validates every rating state as a seed. Restoring a played state through `add_player` is no longer possible. Nothing in the engine, its tests, or the backend relies on that; the backend rebuilds played states through `replay`.

## Acceptance criteria

1. No public API sets RD or reliability to a caller-chosen value without matches:
   - `seed_external_rating` has no `rd_override` parameter; passing it raises `TypeError`.
   - `Config` has no `external_reliability_maps_to_rd` field; passing it raises `TypeError`. A seeded state's RD is always `cfg.external_seed_rd`, whatever the snapshot's reliability.
   - `add_player`, `replay(seed_players=...)`, and `prequential_predictions` (and so `evaluate`, `fit_scale`, `fit_win_calibration`) reject a seed state that the engine itself could not have produced (criterion 2).
2. A seed state is valid only when all of these hold. Anything else raises `InvalidSeedError` naming the player id and format, before any state changes (the engine's `players` dict is unchanged after a rejected `add_player`):
   - `matches_played == 0`, `last_played is None`, `partners` and `opponents` empty, `live_updates_since_replay == 0`.
   - Provisional (`initial_source_date is None`): `rd == cfg.new_player_rd`, `rd_as_of is None`, `initial_source_reliability is None`.
   - Imported, or a category state derived from an imported overall doubles rating (`initial_source_date` set): `rd` is `cfg.external_seed_rd` or `max(cfg.external_seed_rd, cfg.category_seed_rd)`, `rd_as_of == initial_source_date`, and `initial_source_reliability` is `None` or between 0 and 100.
3. Every state the engine produces before any match passes criterion 2: a `seed_external_rating` state, a provisional state, and a category state derived from an imported or a provisional parent. Seeding through `replay` with such a player gives the same ratings as today.
4. `PlayerSummary.reliability` always equals `reliability(state, cfg, reference)` (OPEN-earned). `imported_reliability` is the rounded float imported reliability while carried (fresh within `import_reliability_max_age_days`, fewer than `import_reliability_carry_matches` OPEN matches, higher than the earned value), else `None`.
5. Tests for each rejected path: one parametrized row per seed rule in criterion 2, through `add_player` and through `replay`; the removed `rd_override` and Config field; the summary fields.
6. README (quick start, Public API), `examples/basic_match.py`, the `engine.py` module docstring, the `replay` and `add_player` docstrings, and `CHANGELOG.md` describe the new behavior.

7. Seed dates (review round 1, scope widened with maintainer approval). `replay` raises `InvalidSeedError` for a seed state whose `initial_source_date` (and so `rd_as_of`) is after `as_of`. `prequential_predictions` validates every seed up front, in both modes, and raises `InvalidSeedError` for one dated after the last `played_on` in its ledger; its scheduled windows replay through a private `_replay(..., check_seed_dates=False)`, because a seed captured mid-ledger is legitimate there. `player_summary` carries an imported reliability only when `0 <= (reference - initial_source_date).days <= import_reliability_max_age_days`. `add_player` and `seed_external_rating` have no reference date, so they make no date check, and this work adds no wall-clock read.

8. Seed rating and field types (review round 1; the rating bound is scope widened with maintainer approval). A seed state's `rating` must be within `[cfg.min_rating, cfg.max_rating]`. Every ratings key must be a `RatingFormat`; `rating`, `rd`, and a non-None `initial_source_reliability` must be finite ints or floats, not bool; `matches_played` and `live_updates_since_replay` ints, not bool; `partners` and `opponents` dicts; `initial_source_date` and `rd_as_of` dates, not datetimes. Each breach raises `InvalidSeedError` naming the player and format, never `TypeError`. Review round 2 adds: a seed must be a `Player` with a `str` id (checked before `add_player`'s duplicate lookup), its `ratings` a dict, and every value a `RatingState`; a huge int is finite and meets the bounds and seed RD rules instead of raising `OverflowError`; `prequential_predictions` raises `PlayerError("Player already exists: <id>")` for a repeated seed id up front, in both modes. Blank ids stay accepted.

## Files that change

- `src/open_engine/engine.py`
  - `Config`: delete `external_reliability_maps_to_rd`.
  - Criterion 7: private `_check_seed_date` and `_replay(..., check_seed_dates)`, which `replay` wraps; `prequential_predictions` validates seeds up front and its scheduled windows call `_replay` with the check off; `player_summary` adds the `0 <=` age floor; the `add_player`, `seed_external_rating`, `replay`, and `prequential_predictions` docstrings say where dates are checked.
  - New private `_validate_seed(player: Player, cfg: Config) -> None` implementing criterion 2 for every `(format, state)` in `player.ratings`, iterated in sorted format order so the first error is deterministic. Raises `InvalidSeedError(f"Seed for {player.id} in {fmt}: <rule>")`.
  - `add_player`: call `_validate_seed(player, self.cfg)` after the duplicate-id check and before inserting. Docstring: names the seed rules and `InvalidSeedError`.
  - `replay`: drop its own `matches_played` check (now in `add_player`); keep the docstring's "Raises InvalidSeedError" line, reworded to "for a seed state the engine could not have produced before any match (see add_player)".
  - `seed_external_rating`: delete `rd_override` and the reliability-to-RD branch; `rd = cfg.external_seed_rd`. The `Seed RD is outside configured bounds` check becomes dead (Config already bounds `external_seed_rd`) and is removed.
  - `PlayerSummary`: fields become `..., rating, reliability, imported_reliability, rd, ...`; remove `reliability_basis` and `open_reliability`; docstring updated.
  - `ReliabilityBasis`: deleted.
  - `player_summary`: `reliability=earned`; `imported_reliability` per criterion 4.
  - Module docstring: the imported reliability note names `imported_reliability` and states the headline is always OPEN-earned; the `InvalidSeedError` note covers seed state validation.
- `src/open_engine/__init__.py`: remove `ReliabilityBasis` from imports and `__all__`.
- `README.md`: quick start line prints `summary.imported_reliability`; Public API drops `ReliabilityBasis`.
- `examples/basic_match.py`: print `reliability` and `imported_reliability`.
- `CHANGELOG.md` under `[Unreleased]`: remove `ReliabilityBasis` from the Added line about `PlayerSummary`; amend the `player_summary` breaking entry; new Breaking entries for `rd_override`, the Config field (fingerprint changes for every config), and seed validation in `add_player`/`replay`/`prequential_predictions`; a Security or Fixed entry for issue #15.
- Tests:
  - New `tests/test_seed_validation.py`: criteria 1 to 3 and 5.
  - New `tests/test_future_seed.py`: criterion 7.
  - New `tests/test_seed_types.py` and `tests/test_seed_rating_bounds.py`: criterion 8.
  - New `tests/test_seed_rd.py`: criterion 1's removed `rd_override` and Config field, and seed RD whatever the snapshot's reliability.
  - New `tests/test_imported_reliability.py` and `tests/test_imported_reliability_rounding.py`: criterion 4 (the second compares the rounded imported value with earned).
  - New `tests/test_seed_huge_int.py`, `tests/test_seed_containers.py`, and `tests/test_prequential_duplicate_seed.py`: criterion 8's round 2 additions.
  - New `tests/test_prequential_seed_validation.py`: scheduled `prequential_predictions` validates seeds with an empty ledger or every window skipped.
  - New `tests/test_seed_replay_pinned.py`: criterion 3, replayed ratings and RDs pinned from origin/main.
  - `tests/test_enum_str.py`: drop `ReliabilityBasis`.
  - `tests/test_engine.py` (lines 59 to 78), `tests/test_summary_reliability.py`, `tests/test_api.py` (`SUMMARY_FIELDS`, `test_reliability_basis_members` removed, the summary value tests): move to `imported_reliability` and the always-earned `reliability`.

## Order of work

1. Red tests for seed validation (add_player, replay, prequential), then `_validate_seed` and the wiring.
2. Red tests for `rd_override` and the Config field, then remove them.
3. Red tests for the summary fields, then change `PlayerSummary`, `player_summary`, remove `ReliabilityBasis`, update existing tests. The README quick start line and Public API list and `examples/basic_match.py` change in this step too, because `tests/test_readme.py` runs the quick start and `tests/test_api.py` checks the README API list; the suite is red without them.
4. Docs, example, changelog. Full gate.

## Risks

- A seed check that is too strict rejects a legitimate seed. Criterion 3 tests every state the engine produces before a match, including categories derived from an imported parent and under a config where `external_seed_rd > category_seed_rd`.
- Float equality on RD: seeds copy the exact Config value, and a database double round-trips exactly, so `==` is safe. A backend that rounds RD before storing would be rejected; that is intended (store what the engine produced).
- Seeds are validated against the replaying config. A seed made under a config with different seed RDs is rejected. That is correct: seed RD is a config choice and the seed must be rebuilt under the new config. `fit_scale` and `fit_win_calibration` change only scale and calibration, so their trial configs still accept the caller's seeds.
- Not done, by choice: `RatingEngine.players` and the `RatingState` returned by `state()` are mutable, so code holding the engine object can still assign `rd` directly. The in-memory engine is not a security boundary (see the module's production notes); the backend's authoritative path is ledger plus validated seeds through `replay`. Seed `initial_source` strings are not validated here: they are not reliability (seed rating bounds were added by criterion 8).

- Seed rating bounds: a seed made under a config with wider rating bounds is rejected under narrower ones, like seed RD. The `initial_source` strings and `initial_rating` are still not validated: they are provenance, not rating or reliability.
- Seed date checks need a reference date, which only `replay` (`as_of`) and `prequential_predictions` (its last match) have. A future-dated seed can sit in an engine built with `add_player` or `seed_external_rating` until the next replay rejects it; `player_summary` does not carry its imported reliability when called with an `as_of` before the capture date, but without `as_of` its reference is the state's own `rd_as_of` (the capture date), so it does show it. The backend must pass `as_of` (for example today) on the live path; the engine does not read the wall clock for this.

## Proof

- Criterion 1 to 2: `tests/test_seed_validation.py` parametrized rejection table, each row a valid seed with one field changed, through both `add_player` and `replay`; `pytest.raises(InvalidSeedError, match=...)`. The issue's repro (RD 0.10, 20 partners, zero matches) is one row.
- Criterion 1 removals: `pytest.raises(TypeError)` for `rd_override=` and `Config(external_reliability_maps_to_rd=True)`; a snapshot with reliability 99 seeds at `external_seed_rd`.
- Criterion 3: states from `seed_external_rating`, `state()` (provisional and derived category, default config and `Config(external_seed_rd=0.6, category_seed_rd=0.5)`) pass through `replay` without error.
- Criterion 7: `tests/test_future_seed.py`: replay rejects a doubles and a derived mixed seed dated after `as_of` and accepts one dated on it; prequential (both modes) rejects a seed dated after the last match and accepts one dated on it; a scheduled run with a mid-ledger seed matches values pinned from origin/main; the summary does not carry a future import.
- Criterion 8: `tests/test_seed_types.py` (each wrong type through `add_player` and `replay`, and non-RatingFormat keys) and `tests/test_seed_rating_bounds.py` (out-of-bounds ratings rejected, ratings on the bounds accepted).
- Criterion 4: summary tests for the carried, expired-by-age, expired-by-matches, and earned-higher cases.
- No live drive beyond running `python examples/basic_match.py`: the package has no app; the gate plus the example cover it.

Measured on main (214674b) with a scratch script applying criterion 2's predicate:

- `seed_external_rating` state: passes. Category state derived from it: passes, rd 0.5. Provisional doubles and mixed states: pass.
- Issue repro: `replay([], [Player with RD 0.10 and 20 partners])` gives `reliability` 100.0 with 0 matches. `seed_external_rating(..., rd_override=0.10)` gives 60.0.
