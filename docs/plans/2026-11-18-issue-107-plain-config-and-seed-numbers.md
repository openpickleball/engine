# Plan: Config trusts int and float subclass operators in rating code (issue #107)

Risk tier: High. It touches replay provenance (the config fingerprint) and seed handling, both security-sensitive under CLAUDE.md hard rule 7, and changes existing behavior at several entry points.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-allauth, django-celery, python-fastapi, React and Expo skills: not applicable because this is a standard-library Python package with no web, UI, or framework code.

Decision from the maintainer, recorded on #107 (counts as plan approval): convert to plain. `Config.__post_init__` replaces every number field and every number inside a mapping value (and any number inside other containers such as `allowed_game_formats`) with `int.__int__(v)` or `float.__float__(v)`, keeping int as int and float as float, with bool still rejected, before any check, so checks, `fingerprint()`, and rating code all see the same plain value. Seed number fields are converted the same way at every entry point that stores them, and the engine stores the plain values, never the caller's objects, without mutating the caller's objects.

## Measured on main (c1e5a35)

Command: `python scratchpad/repro107.py` (the issue's two reproductions plus a fingerprint probe). Result:

- `Config(match_weights={..., TOURNAMENT: Big(1)})` and the plain config share a fingerprint (`True`) but give deltas `{'a': 4.5, ...}` versus `{'a': 0.1125, ...}`.
- `add_player` accepts a seed with `rating=InRange(99.0)` (the comment's repro), and `seed_external_rating` accepts `ExternalRatingSnapshot(rating=InRange(99.0))`.
- `object.__setattr__(cfg, "__dataclass_fields__", {})` changes `Config().fingerprint()` from `17f2981186edf96c` to `44136fa355b3678a`: `_payload` reads `fields(self)`, so an instance attribute (or a subclass) changes the field list.

## Acceptance criteria

1. The issue's repro gives identical deltas for the two configs, and equal fingerprints.
2. Every `Config` number field, whole-number field, optional number field, mapping value, `game_length_weights` key, and `allowed_game_formats` points value is stored as exactly `int` or `float` (the plain type of its own value), whatever subclass the caller passed. A bool is still rejected with the same message.
3. A subclass whose operators lie cannot pass a `Config` range check that its plain value fails: it raises `ConfigError`.
4. Seeds: `add_player`, `replay(seed_players=...)`, `prequential_predictions` (live and scheduled), and resume (`replay(from_snapshot=..., seed_players=...)`) store plain `int` and `float` values for `rating`, `rd`, `initial_rating`, `initial_source_reliability`, `matches_played`, `verified_matches_played`, `live_updates_since_replay`, and `inherited_live_updates`, check the plain values, and do not mutate the caller's `Player` or `RatingState`. The comment's repro raises `InvalidSeedError` at each entry point. An int subclass `matches_played` of 3 whose `__ne__` returns False raises `InvalidSeedError`.
5. `seed_external_rating` checks and stores plain `rating` and `reliability`; `InRange(99.0)` raises `InvalidSeedError`. The caller's snapshot is not changed.
6. `RatingEngine.from_snapshot` stores plain `int` and `float` for every number it loads (the same subclass weakness in a snapshot passed in memory), so it stays consistent with seeds. `Config.from_dict` goes through `__post_init__`, so it is covered by criterion 2.
7. `Config.fingerprint()` (and so `==` and `hash`) reads its field list from the `Config` class, so neither an instance `__dataclass_fields__` nor a subclass's extra fields change it.
8. No change to the fingerprint or ratings of a config or seed built from plain ints and floats: `Config().fingerprint()` stays `17f2981186edf96c`, and the existing suite passes unchanged.
9. Every new rejection raises an `OpenEngineError` subclass (`ConfigError`, `InvalidSeedError`, `SnapshotError`), asserted by type in tests.
10. CHANGELOG `### Security` gets a `**Breaking:**` entry naming issue #107 with a "Migration:" note; the #96 entry's "Not covered: int and float subclasses in seed states and `Config` (issue #107)" is updated. Config fields, `RatingState` fields, and the snapshot format do not change; `snapshot_version` stays 8.

## Files that change

- `src/open_engine/engine.py`:
  - New helper `_plain_number(value)` next to `_as_float`: a bool or a non-number is returned as is (later type checks reject it); an int gives `int.__int__(value)`; a float gives `float.__float__(value)`. Typed with a TypeVar so mypy strict accepts it at call sites.
  - `Config.__post_init__`: right after the `version` check and before the type checks, replace each field in `_CONFIG_NUMBER_FIELDS`, `_CONFIG_WHOLE_FIELDS`, and `_CONFIG_OPTIONAL_NUMBER_FIELDS` with `_plain_number` of itself (`object.__setattr__`). For each mapping field, after the `isinstance(value, Mapping)` check and before any value check, store a plain `dict` of `{key: _plain_number(item)}` built from one `items()` read, so every later check and the `_FrozenMapping` copy read that dict. `allowed_game_formats`: read each entry once, with `tuple.__len__` and `tuple.__getitem__` (a tuple subclass cannot give one value to the check and another to the stored set), check it, and build the stored frozenset in the same loop with `int.__int__` points. `game_length_weights` keys already use `int.__int__`. The rest of `__post_init__` is unchanged; the `plain()` helper in `_multiplier_reachable` may stay (now a no-op on plain values). Update the `Config` docstring: values are stored as plain int or float of their own value, so a subclass's operators never reach the checks, the fingerprint, or rating code.
  - `Config._payload`: `fields(Config)` instead of `fields(self)`.
  - Seeds: a helper `_plain_seed(player)` used in `RatingEngine._add_seed` right after `_check_seed_identity(player)` and the duplicate id check, before `_validate_seed`. When `type(player.ratings) is dict`, it returns a new `Player` (`dataclasses.replace`) whose ratings dict maps each key to `_plain_state(state)` for a state that is exactly a `RatingState` (others are left for `_validate_seed` to reject), where `_plain_state` is `dataclasses.replace(state, **{name: _plain_number(getattr(state, name)) for the eight number fields})`. Otherwise it returns the player unchanged. `_validate_seed`, the seed-sensitive check, the identity checks, and `clone_player` then use the plain player. Every seed entry point (`add_player`, `_replay` for replay and resume, both modes of `prequential_predictions`) goes through `_add_seed`, so this covers them all. `_check_seed_date` reads only dates, so it may keep the caller's player.
  - `seed_external_rating`: right after the `type(snapshot) is ExternalRatingSnapshot` check, `snapshot = replace(snapshot, rating=_plain_number(snapshot.rating), reliability=_plain_number(snapshot.reliability))` (a new frozen instance; the caller's is untouched), so the checks and the stored state read plain values. Docstring: note the conversion.
  - Snapshot loader: `_snap_number` returns `_plain_number(value)`, `_snap_count` returns `int.__int__(value)`, and the `initial_source_reliability` branch in `_snap_state_fields` stores `_plain_number(imported)`. Grep the loader for any other place that stores a number from snapshot data without these helpers (for example `net_rating_created`, `calculated_as_of` counts, log entries) and route it through them.
  - `add_player` docstring: number fields are checked and stored as plain int or float of their own value.
- `tests/test_plain_numbers.py` (new, frozen once red). Synthetic ids only (`syn-*`).
- `README.md`: the "Config values" section and the `InvalidSeedError` / seed text where they describe number types: say an int or float subclass is accepted and stored as the plain value of its own value. Only where current text becomes wrong or silent on a rule a reader needs.
- `CHANGELOG.md`: new `**Breaking:**` entry under `### Security` (issue #107) with Migration; update the #96 entry's "Not covered" sentence.

## Order of work

1. Commit this plan alone.
2. Red test commit: `tests/test_plain_numbers.py` with the cases below; confirm each fails on main for the expected reason (or passes where it pins unchanged behavior, which is fine and noted).
3. Implement in `engine.py`. Green.
4. Docstrings, README, CHANGELOG.
5. Full gate.

## Tests (tests/test_plain_numbers.py)

- Issue repro: the two configs give equal `record` deltas and equal fingerprints.
- Parametrized over every name in `_CONFIG_NUMBER_FIELDS`, `_CONFIG_WHOLE_FIELDS`, `_CONFIG_OPTIONAL_NUMBER_FIELDS` (the optional ones with a valid value): passing a subclass of the default value's type (int subclass for an int, float subclass for a float; for an optional field with default None use a valid float) with the same value stores exactly that plain type, and the fingerprint equals the plain config's.
- Each mapping field: a subclass value is stored as exactly the plain type; `game_length_weights` keys and `allowed_game_formats` points from an int subclass are exactly `int`.
- Lying comparisons: a float subclass whose comparison operators all return True, for example `win_weight=Lie(5.0)`, `k_max=Lie(-1.0)`, a `match_weights` value `Lie(-1.0)`, raises `ConfigError` (fails on main).
- An `allowed_game_formats` entry that is a tuple subclass whose `__iter__` yields a different pair than its items: the stored set holds the pair the check saw (or the call raises `ConfigError`), never a `ValueError`/`TypeError` outside the hierarchy.
- Bool still rejected for a number field and a mapping value, `ConfigError`.
- `Config().fingerprint() == "17f2981186edf96c"`; with `object.__setattr__(cfg, "__dataclass_fields__", {})` the fingerprint is unchanged; a frozen dataclass subclass of `Config` adding a field has the same fingerprint as `Config()`.
- Seeds, parametrized over entry points (`add_player`; `replay(seed_players=...)`; `prequential_predictions` live and scheduled with a small synthetic ledger; resume `replay(from_snapshot=snapshot_of_a_replay, seed_players=[...])`): the comment's `InRange(99.0)` seed raises `InvalidSeedError`; an int subclass `matches_played=Zero(3)` with `__ne__` returning False raises `InvalidSeedError`.
- Seeds stored plain: an in-range float subclass rating with a lying `__sub__`/`__mul__`/`__rsub__`/`__rmul__` seeded through `add_player` and through `replay`: the stored state's number fields are exactly `float`/`int`, the caller's `RatingState` still holds the subclass object, and after recording a match the ratings equal those of the same run with plain seeds.
- `seed_external_rating`: `InRange(99.0)` rating raises `InvalidSeedError`; an `InRange`-style reliability outside 0..100 raises `InvalidSeedError`; an honest subclass rating and reliability are stored as exactly `float` and the caller's snapshot is unchanged.
- `from_snapshot`: a snapshot dict whose state `rating`, `rd`, `initial_rating`, `initial_source_reliability`, and counts are replaced with subclass instances of the same value loads with exactly plain types; an `InRange(99.0)` rating raises `SnapshotError`.
- Every rejection test asserts the specific `OpenEngineError` subclass with `match=`.

## Risks

- Converting mapping values before checks changes when a non-Mapping or bad value is reported: keep every existing message and order (non-numbers and bools pass through `_plain_number` unchanged, so the same checks reject them with the same messages). The existing suite pins the messages.
- `replace(player, ratings=...)` on the seed must not break the error order: the identity and duplicate checks run first on the caller's player, as now.
- `fields(Config)` for a subclass drops subclass fields from the fingerprint. A subclass's extra fields are not engine parameters; record it in the changelog entry.
- Not covered and left as is: `RatingEngine(cfg)` still accepts a `Config` subclass (whose methods may differ), and `Match`/`GameFormat` numbers (`best_of`, `sequence`, `points_to`, `win_by`, `cap`) keep their `isinstance` checks (issue #116 tracks `game_format`, `played_at`, `sequence`, `best_of`). Search issues before filing anything for the `Config` subclass case.

## Proof

- Criterion 1, 3: the repro and lying-comparison tests. 2: parametrized storage tests. 4, 5: seed tests per entry point. 6: snapshot tests. 7, 8: fingerprint tests plus the unchanged existing suite. 9: `pytest.raises` with the specific class. Live drive (High): rerun the scratch repro script on the branch and show equal deltas, rejected seeds, and the unchanged fingerprint; also drive a default replay and a snapshot round trip and compare ratings to main's output.
