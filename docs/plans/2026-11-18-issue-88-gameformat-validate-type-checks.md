# Plan: GameFormat.validate does not type-check scoring, points_to, win_by, or cap (issue #88)

Risk tier: Medium. It changes existing validation behavior (a breaking change to which matches validate, and to the error class a bad game format raises through `Match.validate`), but every path is a unit-testable pure function with no state, concurrency, or schema change.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-allauth, django-celery, python-fastapi, React and Expo skills: not applicable because this is a standard-library Python package with no web, UI, or framework code.

Decisions from the maintainer (they count as plan approval):

- A `scoring` that is not a `ScoringType` is rejected, including a str equal to a member's value.
- `points_to` and `win_by`, and `cap` when not `None`, must be ints and not bools. An int subclass is accepted (as #65 did for `sequence` and `best_of`).
- The error class follows the entry point. A bad `GameFormat` reached through `Match.validate` raises `InvalidMatchError`. Reached any other way (`GameFormat.validate()` called directly, the win probability functions, `RatingEngine.predict_win_probability`) it raises `ConfigError`, as today. One set of rules, with the error class chosen by the caller.
- Update the `points_to` pair in `tests/test_engine.py::LOOSE_PAIRS`.
- `Config().fingerprint()` and every default stay unchanged. Breaking: changelog entry.

Finding from Phase 1: `Config` holds no `GameFormat`. `allowed_game_formats` is a set of `(ScoringType, int)` pairs, already type-checked in `Config.__post_init__` and `Config.from_dict`. So the "GameFormat inside Config" case of the decision has no code path today; the `ConfigError` path is every caller other than `Match.validate`.

Reading of "a bad GameFormat": the existing range rules (`points_to` and `win_by` positive, `cap` greater than `points_to`) belong to the same rule set, so through `Match.validate` they also raise `InvalidMatchError` now instead of `ConfigError`. Called any other way they still raise `ConfigError` with the same messages.

## Acceptance criteria

1. Through `Match.validate`, each of these raises `InvalidMatchError` (an `OpenEngineError`) with the exact message, and `RatingEngine.record` leaves the engine unchanged (no player, no processed or reserved `match_id`, same snapshot):
   - `scoring="rally"`, `scoring="side_out"`, `scoring=None`: `scoring must be a ScoringType, not <type>`
   - `points_to="11"`, `points_to=11.0`, `points_to=True`, `points_to=None`: `points_to must be an int, not <type>`
   - `win_by="2"`, `win_by=2.0`, `win_by=True`: `win_by must be an int, not <type>`
   - `cap="15"`, `cap=15.0`, `cap=True`: `cap must be an int or None, not <type>`
   - The range rules (`points_to=0`, `win_by=0`, `cap=5`): the same messages as before, now as `InvalidMatchError`.
2. `replay` raises the same `InvalidMatchError` for such a row wherever it sits, including after `as_of`; `prequential_predictions` raises it too.
3. `GameFormat.validate()` called directly, `game_win_probability`, `match_win_probability`, and `RatingEngine.predict_win_probability` raise `ConfigError` (not `InvalidMatchError`) with the same messages for every bad value in criterion 1.
4. An int subclass for `points_to`, `win_by`, or `cap` validates and records like the int. `cap=None` still validates.
5. `tests/test_engine.py::LOOSE_PAIRS`: the `points_to=11.0` pair moves to `INVALID_LOOSE_PAIRS` with message `points_to must be an int, not float`. `LOOSE_PAIRS` keeps a `points_to` pair using an int subclass (equal under `==`, valid, different type), so the duplicate-conflict tests still run.
6. `Config().fingerprint()` and every default are unchanged. `GameFormat.validate`'s public signature is unchanged.
7. Docs: the `Match` class docstring, `Match.validate` docstring, `GameFormat.validate` docstring, the module docstring's Errors note, the README (Errors list `ConfigError` line and the `Match.validate` type paragraph), and a `**Breaking:**` `CHANGELOG.md` entry with a Migration note under `### Changed` in `[Unreleased]`.

## Files that change

- `src/open_engine/engine.py`: `GameFormat` gains a private `_check(self, error: type[OpenEngineError]) -> None` holding every rule (types first, then ranges). `GameFormat.validate()` calls `self._check(ConfigError)`. `Match.validate` calls `self.game_format._check(InvalidMatchError)` where it calls `self.game_format.validate()` today. Docstrings above.
- `tests/test_game_format_checks.py` (new): criteria 1 to 4.
- `tests/test_engine.py`: `LOOSE_PAIRS` and `INVALID_LOOSE_PAIRS` (criterion 5).
- `README.md`, `CHANGELOG.md`: criterion 7.

`winprob.py` keeps calling `game_format.validate()`, so it keeps `ConfigError`. No change there.

## Order of work

1. Red tests, committed alone: `tests/test_game_format_checks.py` and the `tests/test_engine.py` pair change. They fail on main (TypeError, accepted, or ConfigError where InvalidMatchError is now expected).
2. Implement `_check` and route `Match.validate` through it.
3. Docs and changelog.
4. Gate: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.

## Risks

- Changing the range errors through `Match.validate` from `ConfigError` to `InvalidMatchError` could break a caller catching `ConfigError` on `record`. Both are `OpenEngineError` and `ValueError`. Called out in the changelog Migration note.
- Type checks must run before any comparison, or a str still raises `TypeError`.
- `type[OpenEngineError]` must accept both subclasses under mypy strict.

## Proof

- Criteria 1, 2, 4: `tests/test_game_format_checks.py` through `Match.validate`, `record` (with snapshot equality before and after), `replay` (row before and after `as_of`), and `prequential_predictions`.
- Criterion 3: the same file, direct `GameFormat.validate` and the three win probability entry points, asserting `ConfigError` and that it is not an `InvalidMatchError`.
- Criterion 5: `tests/test_engine.py` loose-pair tests.
- Criterion 6: existing fingerprint tests stay green.
- Live drive: not applicable beyond the gate; the change is a library validation path fully covered by unit tests. A repro script run before and after shows the change.

Measured on main (574b044), `record` of the issue's base singles match with `game_format` replaced:

| `GameFormat(...)` | Result on main |
| --- | --- |
| `points_to="11"` | `TypeError` |
| `cap="15"` | `TypeError` |
| `points_to=11.0` | accepted, counted |
| `scoring="rally"` | accepted, counted |
| `win_by=True` | accepted, counted |
| `cap=True` | `ConfigError: cap must be greater than points_to` |
| `points_to=0` | `ConfigError: points_to must be positive` |
