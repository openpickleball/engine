# Plan: Break up the longest engine functions (issue #128)

Risk tier: High. The change moves no behavior, but it restructures the rating core (`RatingEngine._compute`) and snapshot validation (`_read_snapshot`), which CLAUDE.md treats as security-sensitive (hard rule 7: determinism, provenance, and anything that could let a snapshot set ratings). A dropped or reordered check there would be a silent security defect.

Domain skills: superpowers:test-driven-development, invoke (the proof is a differential check against `main`, plus a test that pins the per-player update to `docs/model.md`). Framework skills: not applicable, a standard-library Python package.

## Principle

Extract, do not rewrite. Each long function becomes a short function that calls named helpers, each helper holding a contiguous block of the original body, moved as written. Every check runs in the same order, raises the same error class with the same message, and changes state at the same point. No helper changes what a function returns.

## Scope

Functions, by body length on `main` today:

| Function | Module | Body lines | Split into |
| --- | --- | --- | --- |
| `Config.__post_init__` | `_config.py` | 221 | one helper per contiguous check group, in the existing order: scalar types and plain values; mapping pairs; game lengths; game formats; enum mappings; mapping values; rating range; K and RD; step size (match performance and evidence weights, with the reachability check); day windows and counts; labels and filters |
| `_read_snapshot` | `_snapshot.py` | 127 | top-level shape and version (`_snap_top`); config check, `calculated_as_of`, and `replay_info` (`_snap_header`); players (`_snap_players`); the self-reported log (`_snap_log`); ledger digests and ledger player ids (one helper each); the match id sets, the cross-field rules, and the resumable check stay in `_read_snapshot` as the calls they already are |
| `RatingEngine.seed_external_rating` | `_engine.py` | 124 | `_checked_external_snapshot` (the argument and snapshot field checks, returning the plain snapshot); `_check_seed_allowed` (the player id, then existing rating, counted matches, category parent, seed-sensitive rejections, replace flag); the external identity check stays in the body; `_store_external_seed` (the commit and the derived category refresh) |
| `RatingEngine._compute` | `_engine.py` | 124 | `_rejection_before_update` (frozen-all and the two gap filters); `_apply_update` (the per-player update, one player); `_build_result` (the `MatchResult` and imbalance) |
| `RatingEngine._consider` | `_engine.py` | 83 | the self-reported checks (`_self_reported_factor`); the rest stays |

`_apply_update` is the core of the engine. Its body must read as the update rule in `docs/model.md` ("The update"), line for line: RD inflation already applied by the caller, K from RD, the raw step, the clamp, the `clamped` rule, the RD shrink, the date and count updates, and the partner and opponent marks. `docs/model.md` names it as the function the rule comes from.

Smaller items from the issue:

- `reliability()` checks its state twice: once itself, and once in `_reliability`. Keep the check in `reliability()` and remove it from `_reliability`, so the order (state, then `cfg`, then `as_of`) is unchanged. `player_summary` passes only engine-held states, which `from_snapshot` and the seed rules already validate.

Left out, with the reason recorded in the PR:

- Renaming `clone_player`: it is re-exported by `open_engine.engine` without an underscore and tests import it from there; renaming it is an API change for no gain.
- Moving `MatchupKey`: #127 already put it next to `_matchup_key` in `_ledger.py`.
- Unifying error-message capitalization: tests match messages, and the backend may too; changing them is a behavior change that needs its own decision.
- The other functions between 55 and 78 lines (`_check_resumable`, `_snap_seed_sensitive_rejections`, `_prequential`, `player_summary`, `fit_scale`, `fit_win_calibration`, `Match.validate`, `_validate_field_types`, `_network_connectivity`): out of scope unless a split falls out naturally while doing the above; a follow-up issue can take them.

Departure recorded during the work: the Config groups follow the code's check order. The K bounds check runs before the RD checks, and the weight and reachability checks run after them, so "K and weights" cannot be one contiguous helper without reordering a check. K joins RD, and the weight checks form the step size helper. `_check_k_and_rd` repeats the one line `limit = _RATING_LIMIT_TEXT`, since the rating range and the RD checks both use it. In `_read_snapshot`, the two match id set reads sit between the players and the log, and the cross-field rules are already one call each, so they stay in its body rather than in helpers of their own. In `seed_external_rating`, the player id check runs right before the existing-rating checks, so it opens `_check_seed_allowed`, and `_store_external_seed` reads `self.cfg` again, as the argument checks do.

## Acceptance criteria

1. Each function in the table has a body of at most about 60 lines, and each new helper has a docstring that says what it checks or computes and in what order.
2. No behavior change: for every input, each public entry point returns an equal result and raises the same error class with the same message as on `main`, and leaves the same state after a failed call. `Config.fingerprint()`, match digests, snapshots, and every rating are unchanged.
3. `_apply_update`'s body matches the update rule in `docs/model.md`, and `docs/model.md` names `_apply_update` (and the other new helpers it cites) as the source of each equation.
4. No existing test changes. New tests only.
5. `CHANGELOG.md`: an entry under `## [Unreleased]`, `### Changed`: internal restructuring, no behavior change.

## Files that change

- `src/open_engine/_config.py`, `_snapshot.py`, `_engine.py`, `_math.py` (`_reliability`).
- `docs/model.md` (function names only).
- `CHANGELOG.md`.
- New: `tests/test_refactor_parity.py` (the differential corpus, see Proof), committed before the refactor so it runs on `main`'s code first.

## Order of work

1. Build the differential corpus in the scratch directory, from `main`'s code: for each refactored entry point (`Config(...)`, `Config.from_dict`, `RatingEngine.from_snapshot`, `replay` with `from_snapshot`, `seed_external_rating`, `add_player`, `record`, `reliability`), a large set of valid and invalid inputs, including two-failure inputs for every adjacent pair of checks, and record each outcome (result repr or error class and message, plus the engine snapshot after a failed call). Sources: every invalid input the existing tests build (collected by running the suite with a recording hook, or by reading the tests), mutated snapshots (each key removed, each value's type changed, each rule broken), and random valid ledgers.
2. Commit a small, fast subset of that corpus as `tests/test_refactor_parity.py` (pinned expected outcomes from `main`), passing on `main`.
3. Refactor one function at a time, in the table's order. After each, run the full corpus and the gate.
4. `reliability` double check.
5. `docs/model.md` names, changelog. Full gate.

## Risks

- A check moved into a helper runs in a different order, or a helper returns early where the original fell through. Mitigation: the two-failure inputs for every adjacent pair of checks, and the full corpus after each function.
- A helper captures state differently (for example the working copies in `_compute`, or the state a failed `seed_external_rating` must not touch). Mitigation: the corpus records the engine snapshot after every failed call.
- `_read_snapshot` mutation coverage misses a rule. Mitigation: generate mutations from the schema (each key and each rule in `_check_*`), not by hand.

## Proof

- The full differential corpus: every outcome identical to `main` (count of inputs per entry point in the PR).
- `tests/test_refactor_parity.py` passes on `main` and on the branch.
- The gate; `examples/basic_match.py` identical to `main`.
- A reading check, recorded in the PR: `_apply_update` beside the `docs/model.md` update rule.
