# Plan: Seed and identity entry points accept blank or shared identities and out-of-range initial_rating (issue #52)

Risk tier: High. Identity links are a security-sensitive area (CLAUDE.md hard rule 7: who may claim a profile, which external rating seeds which player). The change tightens existing behavior across several entry points, but every rule is unit-testable and no rating math changes.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-celery, django-allauth, python-fastapi, React and Expo skills: not applicable because this is a standard-library-only Python package with no framework, API layer, or UI.

Also covers closed issues #44 (seeds bypass account and external identity uniqueness) and #45 (seeds accept blank player, account, and external ids).

## What main does today

Measured on `origin/main` (690cd11) with synthetic ids. Each of these is accepted:

- `replay([], [Player("syn-a", account_user_id="acct-1"), Player("syn-b", account_user_id="acct-1")], as_of=...)`
- `replay([], [Player("syn-a", external_ids={"src": "x1"}), Player("syn-b", external_ids={"src": "x1"})], as_of=...)`
- `RatingEngine().link_account("syn-c", "   ")` and `RatingEngine().attach_external_id("syn-c", " ", "id")`
- `RatingEngine().add_player(Player("", {}))`, `Player("syn-c", {}, account_user_id="   ")`, `Player("syn-c", {}, external_ids={"": ""})`
- `seed_external_rating("syn-a", ..., external_player_id="X")` then `add_player(Player("syn-b", {}, external_ids={"ext": "X"}))`
- `prequential_predictions([], [two seeds sharing an account], mode="scheduled")`

A replay seeded with `initial_rating=99.0` succeeds, and `to_snapshot()` on it raises `SnapshotError`.

## Acceptance criteria

1. `add_player`, `replay`, and `prequential_predictions` (both modes) reject a seed with a blank or whitespace-only player id, `account_user_id` (when not `None`), external source, or external id, with `InvalidSeedError`. Nothing changes when they raise.
2. They reject a seed whose `initial_rating` is not an int or finite float (not bool), or is outside `min_rating`..`max_rating`, with `InvalidSeedError`.
3. Two seeds in one batch, or a seed and an existing player (in `add_player`, or a checkpoint's player in `replay(from_snapshot=...)`), that share an `account_user_id` or a `(source, external id)` pair raise `PlayerError` with the messages `link_account` and `attach_external_id` use. All checks run before any state changes, in `add_player`, `replay`, and both `prequential_predictions` modes.
4. `link_account` and `attach_external_id` (and so `seed_external_rating`'s external identity) reject whitespace-only values with `PlayerError`.
5. Each rule has a test with synthetic ids, and `pytest.raises(..., match=...)` names the error class and message.
6. CHANGELOG entry under Unreleased, marked **Breaking**. README and docstrings for the seed rules are updated. Snapshot validation does not change (issue #61); the docs that say the engine can build shared or blank identities are corrected.

Error choice: #52 asks for `InvalidSeedError` for blank values on a seed and `PlayerError` for uniqueness conflicts. #45 asked for `PlayerError` for blank seed values. This plan follows #52, the open issue that supersedes #45, because a blank value is a malformed seed (the existing seed type checks raise `InvalidSeedError`), while a conflict is about other players, as in `link_account`.

## Files that change

- `src/open_engine/engine.py`
  - `_check_seed_identity`: after the str check, raise `InvalidSeedError("Seed player id must not be blank")` unless `_is_id(player.id)`.
  - `_validate_seed`: after the type checks, raise `InvalidSeedError(f"Seed for {id}: account_user_id must not be blank")` for a blank non-None account, and `InvalidSeedError(f"Seed for {id}: external_ids must not have a blank source or id")` for a blank key or value.
  - `_seed_type_broken`: check `initial_rating` with `_is_finite_number` alongside `rating` and `rd`. `_seed_rule_broken`: `"initial_rating must be between min_rating and max_rating"` after the `rating` range check.
  - New `RatingEngine._check_seed_links(player)` (superseded by `_add_seed` and `_HeldIdentities`, see Revision after review): `PlayerError("This account is already linked to another player profile")` when another player holds the seed's account, and `PlayerError("This external identity is already linked to another player")` when another player holds any of its `(source, id)` pairs. `add_player` calls it after `_validate_seed`, before `_put_player`.
  - `_check_account` and `_check_external_id`: `_is_id` instead of truthiness.
  - `prequential_predictions`: the up-front loop calls `add_player` on a scratch `RatingEngine(cfg)` for each seed (then the seed date check), so both modes apply the same checks, in the same order and with the same messages, as `add_player`, including batch uniqueness. `_replay` already calls `add_player` per seed on its own engine, so batch and checkpoint conflicts are covered there.
  - Docstrings: `add_player` seed rules, `link_account`, `attach_external_id`, `prequential_predictions`, `from_snapshot` (identities sentence), module docstring if it states the rules.
- `tests/test_seed_identity_values.py` (new): parametrized blank-value and conflict rules over `add_player`, `replay`, resume, and both prequential modes; `link_account`/`attach_external_id`/`seed_external_rating` whitespace; `initial_rating` range and type; nothing-changes assertions.
- `tests/test_seed_containers.py`: `test_blank_id_is_still_accepted` pins the old behavior and flips to a rejection test.
- `tests/test_snapshot_identities.py`: its builders used the API paths this change closes. They set the identities directly on `engine.players` instead, so the test still pins that the loader accepts them until #61.
- `README.md`: `InvalidSeedError` and `PlayerError` bullets, and the snapshot schema's identities sentence.
- `CHANGELOG.md`: a Breaking entry under Unreleased.

## Revision after review

- The first version scanned every player per seed, so a batch of N seeds took about N squared comparisons (20,000 seeds took about 9 s). `add_player` now calls `_add_seed(player, held)`, where `held` is a `_HeldIdentities` (sets of accounts and (source, external id) pairs). `add_player` builds it from the engine's players; `_replay` and both `prequential_predictions` modes build it once per batch and pass it to each seed. A test bounds 20,000 seeds at 3 s.
- `_check_player_id` and `RatingEngine.player` use `_is_id`, so a non-str player id raises `PlayerError` instead of `AttributeError` (folded in: same identity checks, and CLAUDE.md allows only `OpenEngineError`s).
- A seed state's `initial_source` and `initial_external_player_id` (imported provenance) are not checked against identities held by other players. That is out of this plan's scope and filed as issue #74.

## Order of work

1. Write `tests/test_seed_identity_values.py`; run it red on the current code.
2. Implement the engine changes; flip the two old tests.
3. Docs and changelog.
4. Gate: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.

## Risks

- Existing tests or examples that seed blank or shared ids break: grep shows only the two named above.
- `seed_external_rating` with a whitespace `external_player_id` now raises `PlayerError` (it went through `_check_external_id`'s truthiness check). Intended, and in the changelog.
- `link_account` with a non-str account now raises `PlayerError` instead of storing it. Intended.
- The scratch engine in `prequential_predictions` clones each seed once more. Cost is negligible.
- Not done here: snapshot loading stays loose on identities (issue #61).

## Proof

- Criterion 1, 2, 3: `tests/test_seed_identity_values.py` rows across every entry point; each fails on main (accepted, or `SnapshotError` only at snapshot time).
- Criterion 4: whitespace rows for `link_account`, `attach_external_id`, `seed_external_rating`.
- Live drive: rerun the issue's reproduction script; each call raises the planned error.
