# Plan: Identity checks trust str and dict subclass overrides (issue #96)

Risk tier: High. It touches identity links and imported provenance, which CLAUDE.md hard rule 7 names as security-sensitive, it changes existing behavior at seven public entry points (a breaking change), and it touches the snapshot loader.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-allauth, django-celery, python-fastapi, React and Expo skills: not applicable because this is a standard-library Python package with no web, UI, or framework code.

Decision from the maintainer (recorded on #96, counts as plan approval): exact types. Player ids, account ids, external sources, external player ids, the provenance strings on seed states (`initial_source`, `initial_external_player_id`), and `Player.external_ids` (its keys and values) must be exactly `str` and `dict` (`type(x) is str`, `type(d) is dict`). A subclass raises `InvalidSeedError` or `PlayerError` (whichever the entry point already raises for a bad value of that field) before any state changes, and `SnapshotError` on load. Config number subclasses are out of scope (issue #107).

## Reproduction on main (7bb8180)

The issue's two reproductions plus `attach_external_id("syn-d", Strip("  "), "Y")` all succeed, and `from_snapshot(json.loads(json.dumps(e.to_snapshot())))` then raises `SnapshotError` (provenance not linked; `initial_source must not be blank`; `external_ids must map non-blank str to non-blank str`).

Also found in Phase 1: `ExternalRatingSnapshot.evidence_reference` and a seed state's `initial_evidence_reference` are never type-checked. `seed_external_rating(..., ExternalRatingSnapshot("ext", 4.0, date(2026, 10, 1), evidence_reference=123))` succeeds, and the snapshot then fails to load with `initial_evidence_reference must be a str or null`. Same functions, a few lines: fixed here with the same exact-type rule.

## Acceptance criteria

1. A private helper `_is_plain_str(value) -> TypeGuard[str]` returns `type(value) is str`. `_is_id` is `_is_plain_str(value) and bool(value.strip())`, so `strip` is only ever called on a plain str.
2. `add_player`, `replay(seed_players=...)`, and `prequential_predictions(seed_players=...)` in both `"live"` and `"scheduled"` modes raise `InvalidSeedError` before any state changes for a seed whose:
   - `id` is a str subclass;
   - `account_user_id` is a str subclass;
   - `external_ids` is a dict subclass, or has a str subclass key or value;
   - `ratings` is a dict subclass (also fixed: a subclass overriding `items` could hand `clone_player` states the seed check never saw);
   - state's `initial_source`, `initial_external_player_id`, or `initial_evidence_reference` is a str subclass (and `initial_evidence_reference` that is not a str or None, also fixed).
   The issue's AlwaysEqual reproduction raises `InvalidSeedError`. The engine's players and `to_snapshot()` are unchanged after each failed call.
3. `seed_external_rating` raises `InvalidSeedError` before anything changes for a snapshot `source`, `external_player_id`, or `evidence_reference` (also fixed: any non-str non-None too) that is a str subclass, and `PlayerError` for a `player_id` that is a str subclass. The issue's Strip reproduction raises `InvalidSeedError`.
4. `attach_external_id` raises `PlayerError` for a str subclass `player_id`, `source`, or `external_player_id`; `link_account` raises `PlayerError` for a str subclass `player_id` or `account_user_id`. No player is created.
5. `RatingEngine.from_snapshot` (and so `replay(from_snapshot=...)`) raises `SnapshotError` for a snapshot mapping whose player `id`, `account_user_id`, `external_ids` key or value, state `initial_source`, `initial_external_player_id`, or `initial_evidence_reference` is a str subclass. Container types read from a snapshot stay `Mapping` as today (the loader copies them into plain dicts).
6. Also fixed, through the same `_is_id`: `Match.validate` and `record` raise `InvalidMatchError` for a `match_id` or a team player id that is a str subclass; the read lookups (`player`, `state`, `player_summary`) and `seed_provisional_rating` raise `PlayerError` for a str subclass player id.
7. Every snapshot the engine writes loads: a test builds an engine through every identity entry point (add_player with external_ids and an account, seed_external_rating with an external id and evidence reference, attach_external_id, link_account, record, replay) with synthetic ids, and `from_snapshot(json.loads(json.dumps(snap)))` succeeds and gives an equal snapshot.
8. Plain `str` and `dict` values behave as before: the existing suite passes unchanged.
9. Docstrings of the entry points say "a str (not a subclass)" where they state types; the `(issue #96 covers ...)` caveats in `add_player` and `_check_provenance` are removed. README, if it states these types, likewise. `CHANGELOG.md`: a `**Breaking:**` entry under `### Security` with a Migration note (convert with `str(x)` / `dict(d)` before calling); the #74 entry's "issue #96" caveat is updated to say it is fixed.

## Files that change

- `src/open_engine/engine.py`: `_is_plain_str`, `_is_id`; `_seed_type_broken` (initial_source, initial_external_player_id, initial_evidence_reference); `_check_seed_identity`; `_validate_seed` (ratings and external_ids `type(...) is dict`, keys and values, account_user_id); `seed_external_rating` (source, external_player_id, evidence_reference); `_snap_str`, `_snap_opt_str`, `_snap_id`, `_snap_player`, and the other loader `isinstance(key, str)` id checks use `_is_plain_str`; docstrings.
- `tests/test_exact_identity_types.py` (new): criteria 2 to 7, parametrized per entry point and field, asserting the specific error subclass with `match=`, and that state is unchanged.
- `README.md`, `CHANGELOG.md`.

## Order of work

1. Red tests, committed alone (`test: failing tests for issue 96`).
2. The helper and the checks.
3. Docstrings, README, changelog.
4. Gate: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.

## Risks

- Stricter `_is_id` reaches every id check, including Match and read lookups. That is intended (criterion 6) and listed under Also fixed; a caller passing a str subclass (for example an enum-backed str or a ORM proxy string) now gets an error. The Migration note covers it.
- Error messages: keep the existing messages ("must be a str", "is required", "must not be blank") so existing tests and callers' matches still hold; a subclass gets the type message where one exists.
- Snapshot schema and `snapshot_version` do not change; no Config, RatingState, or Player fields change.
- Not done: Config number subclasses (#107); `confirmed_by` element types; `_is_plain_date` stays as it is.

## Proof

- `tests/test_exact_identity_types.py` covers criteria 2 to 7; the existing suite covers 8.
- Live drive: run the issue's reproduction script against the branch and show each call raises the named error and leaves `to_snapshot()` unchanged; then build an engine through every entry point with plain values and round-trip its snapshot through JSON.
