# Plan: An exact Config rewritten with object.__setattr__ is still trusted (issue #122)

Risk tier: High. It touches replay provenance and integrity (CLAUDE.md hard rule 7) and changes what every public entry point that takes a `Config` stores.

Domain skills: superpowers:test-driven-development, invoke. Django, FastAPI, React, Expo, and the other listed skills: not applicable because this is a standard-library Python package with no framework, UI, API, data store, auth, payments, or email.

Decision (the user's, recorded on #122): rebuild at entry. Every public entry point that takes a `Config` builds a fresh exact `Config` from the given one's field values and uses only that copy from then on.

## Acceptance criteria

1. Every public entry point that takes a `Config` (`RatingEngine(cfg)`, `RatingEngine.from_snapshot(snapshot, cfg)`, `replay(cfg=...)` with and without `from_snapshot`, `prequential_predictions`, `evaluate`, `fit_scale`, `fit_win_calibration`, `reliability`) builds a fresh exact `Config` from the given one's stored field values, read once through `fields(Config)`, so every `__post_init__` check and the #107 plain-value conversion run again. From then on it uses only the copy, including for `RatingEngine.cfg`, the fingerprint in `ReplayInfo` and `to_snapshot()`, and the fingerprint check in `from_snapshot`.
2. The rebuild reads field values without calling caller code. It reads them from the instance `__dict__` that `_require_config` already checked is an exact dict holding only the field names. A mapping field holding exactly a `_FrozenMapping` is read through the `_data` slot descriptor (`_FrozenMapping._data.__get__`), never through its methods; if that inner value is not exactly a `dict`, it raises `ConfigError`. A mapping field holding exactly a `dict` is copied. Any other value in a mapping field (a `_FrozenMapping` subclass, a `dict` subclass, any other `Mapping`, any other object) raises `ConfigError` without calling its methods. `allowed_game_formats` must be exactly a `frozenset` or `set`, else `ConfigError`. Scalar fields go to `Config(...)` as they are, where `__post_init__` checks and converts them as for a constructor argument.
3. The issue reproduction (a `match_weights` replaced by a plain dict holding an int subclass of 1 that overrides `__mul__`) rates like the plain config: the rebuild converts the value to the plain int 1 (#107), exactly as `Config(match_weights={..., TOURNAMENT: Big(1)})` does. A rewritten value `Config` would reject (for example `k_max` set to `-1.0`, a `match_weights` value of `-5.0` placed inside the `_FrozenMapping`'s dict, a str where a number belongs) raises `ConfigError` at every entry point, before any state changes.
4. No change for an untouched `Config`: the rebuilt copy has the same fingerprint and field values, and ratings, snapshots, fingerprints, evaluation reports, and fits are byte-identical to `aa3a2d0` (origin/main). Pinned by a new test with a non-default config, with values measured on `aa3a2d0`, plus the existing `tests/test_exact_input_plain_unchanged.py` pins.
5. Cost: the rebuild runs once per public call, never per match. Internal paths that build engines or call other public functions (`_replay`, the prequential scratch, live, and per-window engines, `_resume_start`, and `evaluate`, `fit_scale`, and `fit_win_calibration` calling `prequential_predictions`) use the already rebuilt copy and do not rebuild again. `fit_scale` still builds one trial `Config` per scale, as today; a trial is not rebuilt again.
6. Tampering with the caller's `Config` after an entry point returned does not change the engine (it holds its own copy). Tampering with the engine's own stored copy (`engine.cfg`) after the call is outside the threat model, and the README and the `Config` docstring say so.
7. Every new rejection raises `ConfigError` (an `OpenEngineError`), never a bare `ValueError`, `TypeError`, `KeyError`, or `AttributeError`; tests assert the type with `pytest.raises(ConfigError, match=...)`.
8. README, `Config` docstring, the docstrings of the entry points where they describe the `cfg` check, and `CHANGELOG.md` (Security, with a `**Breaking:**` entry and a Migration note) describe the behavior.

## Files that change

- `src/open_engine/engine.py`:
  - New private helper `_rebuilt_config(cfg: Config) -> Config` next to `_require_config`. It reads `vars(cfg)` (already checked), one value per `fields(Config)` name, unwraps mapping fields as in criterion 2, checks `allowed_game_formats`, and returns `Config(**values)`. `Config(...)` raising `ConfigError` propagates; a `TypeError` from `Config(...)` should not be possible since every field name is passed, but if one can arise, wrap it in `ConfigError`.
  - `_require_config` returns `_rebuilt_config(cfg)` after its existing checks. `_config_or_default` keeps `Config()` for `None`.
  - `reliability` uses the returned copy: `cfg = _require_config(cfg)`.
  - `RatingEngine.__init__` body moves to a private method (for example `_start(self, cfg: Config)`) that sets every attribute; `__init__` calls it with `_config_or_default(cfg)`. A private module function `_engine_under(cfg: Config) -> RatingEngine` makes an engine from an already rebuilt config without rebuilding (`RatingEngine.__new__(RatingEngine)` then `_start`). The set of attributes on `vars(RatingEngine())` must not change (tests/test_snapshot.py checks it).
  - `from_snapshot` keeps `engine = cls(cfg)` (one rebuild) and moves the rest of its body into a private helper that loads a checked snapshot into a given engine (for example `_load_snapshot(engine, snapshot)`). `_resume_start` takes a checked `Config` and uses `_engine_under(cfg)` plus that helper instead of calling `RatingEngine.from_snapshot`.
  - `_replay` takes `cfg: Config` (not `None`) and uses `_engine_under(cfg)`.
  - `prequential_predictions` keeps its argument checks and moves the rest into a private `_prequential(...)` taking the checked config; it uses `_engine_under(cfg)` for the scratch and live engines. `evaluate`, `fit_scale` (for each trial), and `fit_win_calibration` call `_prequential` with the checked config or trial instead of the public function.
  - Docstrings: `Config` (rebuild at entry, the engine trusts its own stored copy, tampering with that copy after the call is outside the threat model; `fingerprint()`, `to_dict()`, and `==` called on a tampered object read its tampered values), and the `cfg` sentences of the entry points listed in criterion 1 where they describe the check.
- `tests/test_config_rebuilt_at_entry.py` (new): the tests in Proof.
- `tests/test_snapshot.py`: `test_from_snapshot_uses_the_config_it_is_given` asserts `restored.cfg is cfg`; that is now false by design. Change it to assert `restored.cfg == cfg`, equal fingerprints, and `restored.cfg is not cfg`. This is a test of the old contract, not a frozen defect test.
- `README.md`: the "Exact input types" paragraph (and the Config section if it describes entry-point checks): rebuild at entry, the stored copy, the threat model limit.
- `CLAUDE.md`: no change in this PR. Edits to CLAUDE.md need the user's own approval, so the proposed sentences go in the final report instead.
- `CHANGELOG.md`: a Security entry under Unreleased.

## Order of work

1. Measure pins on `aa3a2d0` (the worktree before any change): the new pin test's expected values (see Proof), recorded as constants.
2. Red tests (commit alone, red, and freeze in `.claude/fix-lock`): tamper tests for every entry point, the inner `_data` test, the "no caller code" tests, the copy and later-tamper tests, the once-per-call count test. The pin test is green on main and stays green; commit it with the red tests.
3. Implement `_rebuilt_config`, route `_require_config` through it, then the internal no-rebuild paths.
4. Update `tests/test_snapshot.py` identity assertion.
5. Docs and changelog.
6. Full gate.

## Risks

- Behavior drift for an untouched config, if `__post_init__` is not idempotent on its own stored output (for example an int `game_length_weights` value, a set vs frozenset, enum keys). Covered by the pin test with a non-default config that uses int mapping values and a non-default `allowed_game_formats`.
- Refactoring `__init__`, `from_snapshot`, and `prequential_predictions` into private helpers could drop an attribute or an argument check. Covered by the existing suite (attribute list test in tests/test_snapshot.py, the exact input tests).
- `engine.cfg is cfg` no longer holds: a caller comparing by identity breaks. Breaking, documented with a migration (compare with `==` or `fingerprint()`).
- A config tampered into a value `Config` rejects used to rate and now raises `ConfigError`. Breaking, documented.
- Not done: no check of a tampered `Config`'s own `fingerprint()`, `to_dict()`, `==` (methods on the object itself, not entry points); no protection of the engine's stored copy against tampering after the call (outside the threat model by decision).

## Proof

Tests in `tests/test_config_rebuilt_at_entry.py`, parametrized over every entry point in criterion 1 (each run with a ledger, seeds, or state small enough to be fast):

- Criterion 3, rates like plain: the issue repro config gives the same deltas, the same replay snapshot (sha256 of sorted JSON) and `ReplayInfo.config_fingerprint` as `Config()`, and the same `evaluate` report, `fit_win_calibration` fit, `prequential_predictions`, `reliability`, and `fit_scale` (short search) as `Config()`. Red on main (main gives the Big deltas).
- Criterion 3, raises: `k_max` set to `-1.0`, `match_weights` set to a plain dict with `-5.0`, a `_FrozenMapping` whose `_data` holds `-5.0`, `k_max` set to `"x"`: `ConfigError` at every entry point, and `RatingEngine` state untouched where an engine exists (from_snapshot and resume). Red on main.
- Criterion 2: `_data` replaced by a list, by a dict subclass, and by an object whose every method sets a flag: `ConfigError`, flag not set. A mapping field replaced by a `Mapping` subclass and by a dict subclass whose methods set a flag: `ConfigError`, flag not set. `allowed_game_formats` replaced by a set subclass whose `__iter__` sets a flag: `ConfigError`, flag not set. Red on main.
- Criterion 1 and 6: `RatingEngine(cfg).cfg is not cfg`, `== cfg`, same fingerprint; tampering the caller's `cfg` after `RatingEngine(cfg)` or `replay(..., cfg=cfg)` does not change later deltas or the stored fingerprint. Red on main.
- Criterion 5: monkeypatch `open_engine.engine._rebuilt_config` with a counter; each public call (`RatingEngine(cfg)`, `from_snapshot`, `replay` with and without `from_snapshot`, `prequential_predictions` in both modes, `evaluate`, `fit_scale`, `fit_win_calibration`, `reliability`) calls it exactly once with a non-None cfg, and zero times with `cfg=None`. Red on main (no such helper: the test fails at monkeypatch or count).
- Criterion 4: pin test with a non-default config, `Config(k_max=0.5, match_weights={TOURNAMENT: 2, LEAGUE: 1.5, RECREATIONAL: 1, SELF_REPORTED: 1}` style plain values using the real member names, a `game_length_weights` dict with an int value, and an `allowed_game_formats` given as a plain `set`), through `replay`, resume from its snapshot, `evaluate`, `fit_win_calibration`, and `reliability`; expected values (fingerprint, snapshot sha256, report dicts) measured on `aa3a2d0` before any change. Green on main and after.
- Criterion 7: every rejection above uses `pytest.raises(ConfigError, match=...)`.

Measured on aa3a2d0 (repro script in the scratch directory):

```
plain    {'syn-a': 0.1125, 'syn-b': 0.1125, 'syn-c': -0.1125, 'syn-d': -0.1125}
tampered {'syn-a': 4.5, 'syn-b': 4.5, 'syn-c': -1.5, 'syn-d': -1.5} 9fcbe80ff07bd549
inner    {'syn-a': -0.5625, 'syn-b': -0.5625, 'syn-c': 0.5625, 'syn-d': 0.5625}
Config() us 28.8
```

The issue repro rates with the tampered weight on main, and a `-5.0` weight placed inside the `_FrozenMapping` flips the winner's sign. `Config()` takes about 29 microseconds, so one rebuild per public call is negligible.

## Departures during the work

- Criterion 3's comparison: the issue repro rebuilds to `match_weights[TOURNAMENT] = 1` (a plain int), which rates exactly like `Config()` (same deltas) but has a different fingerprint from `Config()` (an int and a float differ to the fingerprint). So the parametrized test compares every entry point against the constructor-built `Config(match_weights={..., TOURNAMENT: Big(1)})`, and a separate test checks the repro's deltas against `Config()`. `reliability` reads no match weight, so the tampered config also sets `diversity_target` to `Big(12)` (which overrides `__rtruediv__`), and the reference passes the same value to the constructor.
- (Superseded by review finding A below: the rebuild now builds a new dict of plain keys and values.) Criterion 2: a mapping field holding exactly a `dict` is passed to `Config(...)` as it is, not copied first. `Config(...)` reads its items once and stores its own `_FrozenMapping`, so the result shares nothing with the caller, and skipping the copy avoids rehashing caller keys before `__post_init__` checks them. A `_FrozenMapping` whose `_data` slot is unset also raises `ConfigError` (a test covers it).
- `tests/test_calibration.py` monkeypatched `open_engine.engine.prequential_predictions` to fake the predictions `fit_scale` and `fit_win_calibration` see. Those now call `_prequential`, so the patches target `_prequential`. An old-contract test of an internal path, not a frozen one.
- The `fit_scale` cases of the new rejection and rates-like-plain tests already pass on main: `fit_scale` builds each trial with `Config(...)` from the field values, so it already rebuilt. They stay as regression coverage. The once-per-call count test is red for `fit_scale` on main.
- "RatingEngine state untouched where an engine exists": `from_snapshot` and resume build a new engine, so no caller engine exists to check; each rejection raises before an engine is returned.
- Review finding A (caller code inside the rebuild): checking only the containers was not enough. `Config(...)` hashes and compares an `allowed_game_formats` scoring entry and reads `__class__` through `isinstance`, so a hostile leaf could run code inside the rebuild, rewrite the copy being built (a frame-walking `__hash__`), or escape as a bare `ValueError`. `_rebuilt_config` now turns every leaf into a plain value by its real type before `Config(...)`, through three small private helpers next to it (`_leaf_value`, `_leaf_member`, `_leaf_whole`) and two for the containers (`_rebuilt_mapping`, `_rebuilt_formats`). A scalar is kept when it is an exact bool, an exact str, or None. A real int or float subclass becomes its plain value, and anything else raises `ConfigError`. Enum keys must be members by identity or exact strs (an exact str naming a member becomes it), game length keys and format points real ints that are not bools, and format entries real tuples read through `tuple.__len__` and `tuple.__getitem__`. Two keys that become one raise. `Config(...)` then sees only plain values, and every check and the #107 conversion still run. Tests: `tests/test_config_rebuilt_leaves.py`.
- Review finding C (criterion 5): `RatingEngine.player_summary` called the public `reliability`, which rebuilt `self.cfg` on every call. A private `_reliability(state, cfg, as_of)` now takes a checked Config and keeps the state and `as_of` checks. Public `reliability` checks the state, rebuilds cfg, and calls it, and `player_summary` calls it with `self.cfg`.
- Review finding G1 (copy built before the iterables drain): `replay`, `prequential_predictions`, `evaluate`, `fit_scale`, and `fit_win_calibration` built the copy before `list(matches)` and `list(seed_players)`, so a generator could walk the frame stack and rewrite the copy after its checks. The exact-type and instance `__dict__` checks still run where they did (`_config_shape`, so a wrong-typed cfg raises before anything is drained), and the copy is now built with `_config_or_default` after the iterables drain, the last point where caller code runs. `_check_evaluation_arguments` returns the checked, unrebuilt cfg; `fit_scale` reads `base` and `scale_by_scoring` only from the copy built after draining. Still one rebuild per public call. A caller's own `Config` rewritten while the iterables drain is rebuilt from its rewritten values, and so checked and converted: a value `Config` rejects raises `ConfigError`, and an int subclass becomes its plain value. The request asked that such a tamper "must not reach the result"; with the copy built after draining it reaches the result only as checked plain values, which is what the threat model sentence states. Tests: `tests/test_config_rebuilt_after_iterables.py`.

## Review findings left to the maintainer

- On Python 3.13 and later (PEP 667), a caller's `seed_players` generator can rebind a running entry point's local variables through frame introspection (for example `rows` in `replay`) and so run code after the copy is built. This run did not fix it and did not extend the documented threat model, since the decision on #122 names only rewriting the engine's stored copy after the call. So the G1 note above, which calls the iterable reads "the last point where caller code runs", holds only when no local is rebound; the CHANGELOG and the engine docstrings no longer make that claim. A caller's `matches` generator can do the same.
