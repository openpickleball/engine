# Plan: Exact input types at every public entry point (issues #116, #118, #119)

Risk tier: High. It touches replay determinism, provenance, and calibration honesty (CLAUDE.md hard rule 7), changes existing behavior at most public entry points (a breaking change), and touches the snapshot loader.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-allauth, django-celery, python-fastapi, React, and Expo skills: not applicable because this is a standard-library Python package with no web, UI, or framework code.

Decision from the maintainer (recorded on #116, #118, and #119; counts as plan approval): one rule at every public entry point. Inputs must be exact types (`type(x) is int`, `float`, `date`, `datetime`, `GameFormat`, `Config`, the enum classes, and so on; a bool is rejected where a number is expected). Where an earlier issue chose conversion to a plain value (#107: `Config` numbers and mappings, seed numbers, `ExternalRatingSnapshot` numbers, snapshot numbers), that rule stays. A subclass raises the entry point's existing `OpenEngineError` subclass before any state change.

## Reproduction on main (c23ab5a)

- #116: the issue's `FlipFormat` raises `KeyError: 'bogus'` from `record`. A `tzinfo` subclass whose `utcoffset` runs `object.__setattr__(match, "match_id", "")` lets `record` count the match with `processed_match_ids == {""}`.
- #118: the issue's `Evil(Config)` gives deltas 4.5 / -1.5 instead of 0.1125 / -0.1125.
- #119: confirmed by reading: `_is_plain_date` is `isinstance(date) and not datetime`, `low`/`high`/`tolerance`/`burn_in_matches` have no type check, `fit_scale` builds trials with `dataclasses.replace(cfg, ...)`, which reads the instance `__dataclass_fields__` and `cfg.__class__`.

## Design decisions made from the code

1. **played_at tzinfo.** A `tzinfo` subclass runs caller code during validation (`utcoffset` in `_utc_instant`, `_canonical`, `_match_digest`, `ledger_order`), so it can rewrite the `Match` after a check passed (#109 route). Rule: `played_at` must be exactly `datetime` and its `tzinfo` exactly `datetime.timezone` (which cannot be subclassed and runs no caller code) or, since the user's decision "Accept exact ZoneInfo" (relayed during review, after the first pass rejected it), exactly `zoneinfo.ZoneInfo` with no instance `__dict__`. An exact ZoneInfo is standard library C code with no instance dict and an immutable type on CPython 3.11 to 3.13, so no caller code runs when the engine reads its offset, and its offsets come from its tz data (the tz database, or the file given to `ZoneInfo.from_file`), as on main. On a Python build that uses the pure-Python `zoneinfo` fallback, ZoneInfo instances have a `__dict__` and are rejected. An offset of a day or more, which a `from_file` zone can hold, raises `InvalidMatchError`. Its offset must be exactly a `timedelta`. Every other tzinfo, a subclass of either included, raises `InvalidMatchError`. No conversion: the engine reads only the UTC instant and the UTC offset, so a caller converts with `played_at.astimezone(timezone(timedelta(seconds=played_at.utcoffset().total_seconds())))` (plain values even when the offset is a `timedelta` subclass) and gets the same instant, offset, `_canonical` key, and ledger digest (existing checkpoints still resume).
2. **Error class per input.** The class the entry point already raises for that input. Where it raises none today: `ConfigError` for a config, `ReferenceDateError` for an `as_of`, `EvaluationError` for a number in prediction, evaluation, or win probability math, `PlayerError` for `rating_format` in the read lookups (`state`, `player_summary` already raise `PlayerError`), `InvalidMatchError` for teams, `rating_format`, and `scoring` in `predict_point_share` (it already raises `InvalidMatchError` for teams).
3. **Caller iterables are read once, before any validation.** `replay`, `prequential_predictions`, `evaluate`, `fit_scale`, and `fit_win_calibration` copy `matches` and `seed_players` into lists first, so a generator cannot rewrite a row between its validation and its use.
4. **Exact Config also means no extra instance attributes.** As for `Match` (#109), an exact `Config` must hold nothing in its instance `__dict__` but its own fields (an extra entry could hide `fingerprint`, `_payload`, or `__dataclass_fields__`). Rewriting a field of an exact, already built `Config` with `object.__setattr__` is not a subclass route; it is reported as a follow-up, not fixed here.
5. **GameFormat.** Exactly `GameFormat`, with an instance `__dict__` holding only its four fields, `scoring` exactly a `ScoringType`, `points_to` and `win_by` exactly `int`, `cap` `None` or exactly `int`. The engine calls `GameFormat.validate(fmt)` and `GameFormat.validate_game(fmt, a, b)` through the class, never through the instance.

## Audit: every public input, its check on main, and the new rule

| Entry point | Input | Check on main | New rule (error) |
|---|---|---|---|
| `RatingEngine(cfg)` | cfg | none: `cfg or Config()` | `None` or exact `Config` with only field attributes (`ConfigError`) |
| `RatingEngine.from_snapshot` | cfg | none | as above (`ConfigError`) |
| `from_snapshot` | snapshot and every nested object | `isinstance(Mapping)`, `isinstance(list, tuple)` | exact `dict`; exact `list` or `tuple` (`SnapshotError`) |
| `from_snapshot` | `resumable` | `isinstance(bool)` | exact `bool` (`SnapshotError`) |
| `from_snapshot` | strs, dates, numbers, `net_rating_created`, version | already exact str / parsed / `_plain_number` / exact | unchanged |
| `Config(...)` | numbers, mapping values, length keys, format points | `_plain_number` (#107) | unchanged (conversion kept) |
| `Config(...)` | mapping containers, `allowed_game_formats` | `isinstance`, read once into copies | unchanged (read once, converted) |
| `Config(...)` | bool fields | `isinstance(bool)` (a `__class__` spoof passes) | exact `bool` (`ConfigError`) |
| `Config.from_dict` | data | `isinstance(Mapping)` | exact `dict` (`ConfigError`); nested values unchanged (they go through `Config`) |
| `player`, `link_account`, `attach_external_id` | ids | exact str (#96) | unchanged |
| `add_player`, seeds in `replay` / `prequential_predictions` | Player, ratings, states, ids, provenance strs | exact (#96) | unchanged |
| same | ratings keys | `isinstance(RatingFormat)` | exact `RatingFormat` (`InvalidSeedError`) |
| same | seed numbers | `_plain_number` (#107) | unchanged |
| same | `initial_source_date`, `rd_as_of` | `_is_plain_date` (subclass passes) | exact `date` (`InvalidSeedError`) |
| `seed_external_rating` | rating_format | `isinstance` | exact `RatingFormat` (`InvalidSeedError`) |
| same | `snapshot.captured_on` | `_is_plain_date` | exact `date` (`InvalidSeedError`) |
| same | `snapshot.rating`, `.reliability` | `_plain_number` (#107) | unchanged |
| same | `replace_unplayed_seed` | none (`if not x`) | exact `bool` (`InvalidSeedError`) |
| `seed_provisional_rating` | rating_format | `isinstance` | exact `RatingFormat` (`InvalidSeedError`) |
| `state` | rating_format | none | exact `RatingFormat` (`PlayerError`) |
| `player_summary` | rating_format | none | exact `RatingFormat` (`PlayerError`) |
| `player_summary` | as_of | `_is_plain_date` | exact `date` or None (`ReferenceDateError`) |
| `predict_point_share` | team_a, team_b | any Sequence | exact `tuple` or `list` (`InvalidMatchError`) |
| same | rating_format | none | exact `RatingFormat` (`InvalidMatchError`) |
| same | scoring | none | exact `ScoringType` (`InvalidMatchError`) |
| `predict_win_probability` | game_format | duck-typed `.validate()` | exact `GameFormat` (`ConfigError`) |
| same | best_of | isinstance int, not bool | exact `int` (`EvaluationError`) |
| `record` | match | exact `Match` (#109) | unchanged |
| `record` | as_of | `_is_plain_date` | exact `date` or None (`InvalidMatchError`) |
| `Match.validate`, `record`, `replay`, `prequential_predictions`, `ledger_order` | `status`, `rating_format`, `match_type` | `isinstance` enum (a `__class__` spoof passes) | exact enum (`InvalidMatchError`) |
| same | `game_format` | `isinstance(GameFormat)` | exact `GameFormat`, decision 5 (`InvalidMatchError`) |
| same | `played_on` | `_is_plain_date` | exact `date` (`InvalidMatchError`) |
| same | `played_at` | `isinstance(datetime)`; tzinfo unchecked | `None`, or exact `datetime` with tzinfo exactly `timezone` or exactly `ZoneInfo` (no instance `__dict__`), offset exactly `timedelta` (`InvalidMatchError`) |
| same | `sequence`, `best_of` | isinstance int, not bool | exact `int` (`InvalidMatchError`) |
| same | ids, teams, `confirmed_by`, `games`, scores | exact (#96, #109) | unchanged |
| `ledger_order` | match fields it reads | only the `Match` type | every field type checked as in `Match.validate` (`InvalidMatchError`); value rules not applied |
| `GameFormat.validate` | self and fields | `isinstance` | exact `GameFormat`, decision 5 (`ConfigError`) |
| `GameFormat.validate_game` | self, score_a, score_b | none | exact `GameFormat` (`ConfigError`), exact `int` scores (`InvalidMatchError`) |
| `replay` | cfg | none | as `RatingEngine` (`ConfigError`) |
| `replay` | as_of | `_is_plain_date` | exact `date` (`ReferenceDateError`) |
| `replay` | whole_ledger | `isinstance(bool)` | exact `bool` (`SnapshotError`) |
| `replay`, evaluation functions | matches, seed_players | iterated lazily | read into lists first, decision 3 |
| `prequential_predictions`, `evaluate`, fits | cfg | none | as `RatingEngine` (`ConfigError`) |
| same | score_from, anchor | `_is_plain_date` | exact `date` or None (`EvaluationError`) |
| same | scoring | none | exact `ScoringType` (None allowed where it is today) (`EvaluationError`) |
| same | burn_in_matches | none | exact `int` (`EvaluationError`) |
| same | mode | `not in (...)` | exact `str` (`EvaluationError`) |
| `fit_scale`, `fit_win_calibration` | low, high, tolerance | none (range only) | exact `int` or `float` (`EvaluationError`) |
| `fit_scale` | trial configs | `dataclasses.replace(cfg)` | built from `fields(Config)` read off the exact `Config` |
| `reliability` | state | none | exact `RatingState`; `rd` exact int or float, `rd_as_of` None or exact date, `partners`/`opponents` exact dict of exact date values (`EvaluationError`) |
| same | cfg | none | exact `Config` (`ConfigError`) |
| same | as_of | none | exact `date` (`ReferenceDateError`) |
| `expected_point_share` | team ratings, scale | none | exact `int` or `float` (`EvaluationError`) |
| `game_win_probability` | p | none | exact `int` or `float` (`EvaluationError`) |
| same | game_format | duck-typed | exact `GameFormat` (`ConfigError`) |
| `match_win_probability` | p, game_format | as above | as above |
| same | best_of | isinstance int, not bool | exact `int` (`EvaluationError`) |
| `calibrate_win_probability` | p, a | none | exact `int` or `float` (`EvaluationError`) |
| `to_snapshot`, `fingerprint`, `to_dict` | none | n/a | n/a |

Output dataclasses (`MatchResult`, `PlayerSummary`, `ReplayInfo`, `EvaluationReport`, `PrequentialPrediction`, `ScaleFit`, `WinCalibrationFit`, `NetworkConnectivity`) are never read back from a caller, so they take no new check.

## Acceptance criteria

1. #116: a subclass, or an object whose `__class__` claims the type, in `game_format` (or in its `scoring`, `points_to`, `win_by`, `cap`), `played_on`, `played_at`, `played_at.tzinfo` (any tzinfo other than exactly `timezone`), `sequence`, `best_of`, `status`, `rating_format`, or `match_type` raises `InvalidMatchError` from `Match.validate`, `record`, `replay`, and `prequential_predictions` (both modes), with engine state (`to_snapshot()`) unchanged. The issue's `FlipFormat` reproduction raises `InvalidMatchError`.
2. #109 route closed: a test whose `tzinfo` subclass `utcoffset`, and one whose `GameFormat` subclass `points_to` property, would rewrite another field of the owning `Match` (`match_id`, `games[0]`, or `confirmed_by`) raises `InvalidMatchError`, the callback never runs (a counter stays 0), the `Match` is unchanged, and the engine records nothing. A generator ledger that rewrites an already-yielded row on its next step is read whole before validation, so the rewrite is seen by validation (the row that is rated is the row that was validated).
3. A `GameFormat` holding an extra instance attribute (for example `validate`) raises `InvalidMatchError` in a match and `ConfigError` from `GameFormat.validate` and the win probability functions.
4. #118: `RatingEngine`, `RatingEngine.from_snapshot`, `replay`, `prequential_predictions`, `evaluate`, `fit_scale`, `fit_win_calibration`, and `reliability` raise `ConfigError` for a `Config` subclass, a non-`Config` object (a falsy one included), and an exact `Config` with an extra instance attribute (`__dataclass_fields__` included), before any state change. The issue's `Evil` reproduction raises `ConfigError`.
5. #119: each argument in the audit table that takes a number, date, enum, bool, or str raises the listed error for a subclass (and for a `__class__` spoof, and a bool where a number is expected), before any prediction work or state change. Seed dates, `as_of`, `score_from`, and `anchor` reject a `date` subclass. `fit_scale` trial configs no longer use `dataclasses.replace`.
6. Plain inputs unchanged: `Config().fingerprint()` equals `"17f2981186edf96c"`, and a synthetic ledger (singles, doubles, a category format, timed rows with two `timezone` offsets, untimed rows with sequences, best of 3, a seed from `seed_external_rating`) replayed on this branch gives a `to_snapshot()` whose sha256 of `json.dumps(..., sort_keys=True)` equals the value measured on c23ab5a and pinned in the test; `evaluate` and `fit_scale` on it give the same report and fit as on c23ab5a (pinned).
7. Every new raise is an `OpenEngineError` subclass; each new test names the subclass and uses `match=`.
8. Docs: entry point docstrings say "exactly" where they state these types (and drop "an int subclass is" accepted wording); the module docstring, README (Match and played_at text, Public API notes if they state types), and `ReferenceDateError`/`ConfigError` docstrings if they name inputs. `CHANGELOG.md`: a `**Breaking:**` entry under `### Security` naming #116, #118, #119 with a "Migration:" note (pass plain values: `int(x)`, `float(x)`, `date(d.year, d.month, d.day)`, an exact `Config` built with `Config(...)` or `Config.from_dict`, `GameFormat(...)`; convert `played_at` with `played_at.astimezone(timezone(timedelta(seconds=played_at.utcoffset().total_seconds())))`; teams as a tuple or list).
9. No snapshot schema change, no `Config` field change, no `RatingState` field change; `SNAPSHOT_VERSION` stays 8.

## Files that change

- `src/open_engine/engine.py`:
  - `_is_plain_date` becomes `type(value) is date` (covers played_on, seed dates, captured_on, every as_of, score_from, anchor).
  - New helpers: `_is_exact_int(value)` (`type is int`), `_is_exact_number(value)` (`type in (int, float)`), `_require_config(cfg, error=ConfigError) -> Config` (None gives `Config()`; else exact type plus `vars(cfg)` an exact dict whose keys are exactly the field names), `_require_game_format(fmt, error)` (decision 5).
  - `GameFormat.validate` (exact self, exact field types, `type(...) is` checks), `validate_game` (exact scores; call sites use the class).
  - `Match.validate` / `_validate_field_types`: all field type checks first and exact (enums, game_format via the helper, played_on, played_at and tzinfo `timezone`, sequence, best_of); `GameFormat.validate(...)` and `GameFormat.validate_game(...)` through the class; `_utc_instant` keeps its awareness check.
  - `ledger_order`: run `Match._validate_field_types` after `_require_plain_match`.
  - `RatingEngine.__init__`, `from_snapshot`, `replay`, `prequential_predictions`, `evaluate`, `fit_scale`, `fit_win_calibration`: `_require_config`; materialize iterables first; argument checks listed in the table.
  - `fit_scale`: trial `Config(**{f.name: getattr(cfg, f.name) for f in fields(Config)} | {"scale_by_scoring": ...})`.
  - `_check_search_bounds`: exact number checks first.
  - `seed_external_rating`, `seed_provisional_rating`, `_validate_seed`, `state`, `player_summary`, `predict_point_share`, `predict_win_probability`, `_validate_teams`: exact checks per table.
  - `reliability`, `expected_point_share`: checks per table.
  - `Config.__post_init__` bool fields `type is bool`; `Config.from_dict` exact dict.
  - Snapshot loader: `_snap_object`, `_snap_list`, `_snap_date_map`, `_snap_player`, team and dates checks in `_snap_log_entry`, `_snap_content_keys`, `_snap_possible_duplicates`, `_snap_seed_sensitive_rejections`, `_read_snapshot` (top level, `resumable`, `ledger_digests`): exact dict / list / tuple / bool.
  - Docstrings.
- `src/open_engine/winprob.py`: `game_win_probability`, `match_win_probability`, `calibrate_win_probability` checks; `GameFormat.validate(game_format)` via a runtime import inside the function (the module imports `GameFormat` only for type checking today).
- `src/open_engine/_errors.py`: docstrings only if they list inputs that change.
- Tests (new): `tests/test_exact_input_types.py` (criteria 1, 3, 4, 5, 7, parametrized per entry point and input), `tests/test_exact_input_callbacks.py` (criterion 2), `tests/test_exact_input_plain_unchanged.py` (criterion 6).
- Tests that change because their behavior is intentionally reversed (each listed in the PR body): `tests/test_match_type_checks.py` (`_Int` sequence/best_of now rejected), `tests/test_engine.py` (`_Int` points_to in `LOOSE_PAIRS` now `InvalidMatchError`; ZoneInfo fold tests become: ZoneInfo rejected, and the same fold rows converted to `timezone` offsets conflict and collapse as before (since the user's ZoneInfo decision, main's fold tests are restored unchanged and the converted versions stay as extra cases; the rejection test checks exact ZoneInfo accepted and a subclass rejected); `NoOffset` message), `tests/test_game_format_checks.py` and `tests/test_game_format_coverage.py` (int subclass now rejected), `tests/test_game_format_override.py` (subclass now rejected before its `validate` runs), `tests/test_evaluation_date_types.py` and `tests/test_replay_as_of_type.py` (`_Day` now rejected), `tests/test_properties.py` (ZONES to `timezone` offsets if it uses ZoneInfo). Tests that build `Config` subclasses only to check the fingerprint (`test_plain_numbers*.py`) stay: `Config` itself still allows subclassing, the entry points reject them.
- `README.md`, `CHANGELOG.md`, module docstring.

## Order of work

1. Red tests for criteria 1 to 6 (pin criterion 6 values on the unchanged source first), committed alone red.
2. Helpers and `_is_plain_date`.
3. `GameFormat` and `Match` (criteria 1 to 3); update the reversed tests in the same commit.
4. Config entry points (criterion 4).
5. Remaining arguments (criterion 5), snapshot loader containers.
6. Docs and changelog.
7. Full gate.

## Risks

- Custom tzinfo callers break (an exact ZoneInfo is accepted since the user's decision): called out as breaking with a one-line migration that keeps instant and offset, so ledger digests and resume are unaffected.
- `_is_plain_date` is shared; a stored date the engine writes itself is always an exact `date` (dates come from inputs or `date.fromisoformat`), so tightening it rejects nothing the engine produces. Snapshot round trip tests cover this.
- Snapshot loader: `to_snapshot` emits exact dicts and lists, and `json.loads` gives them, so exact containers reject nothing the engine produces. A caller passing a `MappingProxyType` breaks: changelog.
- Order of checks: existing tests pin which error comes first for some entry points (`replay` as_of first; `fit_*` bounds first). Keep those orders; add the new checks after the existing first check.
- Riskiest step: the Match validation reorder, since every rating path goes through it. Criterion 6 pins ratings.

## Not done

- A `Config` or `GameFormat` built exactly and then rewritten with `object.__setattr__`: not a subclass route; reported as a follow-up issue.
- `expected_point_share(scale=0)` raising `ZeroDivisionError`: value check, not a type; noted in the PR.

## Departures found during implementation

- Messages: existing messages are kept where existing tests pin them (`game_format must be a GameFormat, not X`, `Config data must be a mapping, exactly a dict, not X`); new checks name the argument (`cfg must be exactly a Config`, `low must be an int or float`).
- The game format helpers return a message instead of raising a passed-in error class, since `tests/test_api.py` requires every `raise` to name an `OpenEngineError` subclass.
- `fit_scale` and `fit_win_calibration` reject `scoring=None` with `EvaluationError`. On c23ab5a it raised `ConfigError` (`fit_scale`, from the trial config) or `AttributeError` (`fit_win_calibration`), so no working call changes.
- `#118`'s reproduction no longer gives equal fingerprints on c23ab5a (#120 made the fingerprint see the `_Big` value), so its test checks only the `ConfigError`.
- `tests/test_engine.py`: `LOOSE_PAIRS` keeps a valid loosely equal pair (`confirmed_by` as a `frozenset` and a `set`), and the `_Int` `points_to` pair moves to `INVALID_LOOSE_PAIRS`.
- `ledger_order` checks the types `Match._validate_field_types` checks: every field but `games` and the ids inside the teams and `confirmed_by`.
- Review findings, fixed after the first pass (red tests in `tests/test_exact_input_review.py`):
  - `played_at`'s offset must be exactly a `timedelta`: an exact `timezone` can hold a `timedelta` subclass.
  - Enum checks accept only a member by identity, since an object of the enum's own type can be built without being a member. `Config` rejects such mapping keys and `allowed_game_formats` entries; plain str keys still convert.
  - `GameFormat.validate_game` checks the format's field types, not only its shape.
  - Snapshot objects, `Config.from_dict` data, and seed ratings keys are checked for exact str or member keys before any hashing.
  - Number checks also need the value to be finite as a float (an int too large for a float, NaN, or infinity raises `EvaluationError`), except the fits' bounds, where an infinite float keeps its existing "must be finite" message; a `best_of` too large to compute raises `EvaluationError`.
  - `predict_win_probability` checks `best_of` next to `game_format`, before any prediction work.
  - `reliability` needs exact str keys in `partners` and `opponents`.
  - Rejection messages name a wrong-typed value's type instead of its repr. `tests/test_exact_identity_types_review.py::test_from_snapshot_rejects_a_format_key_subclass` pinned a str subclass key's repr and now expects the type name.
  - `fit_scale` checks `mode`, `scoring`, `burn_in_matches`, `score_from`, and `anchor` before it builds a trial config, so `fit_scale(low=0, mode="bad")` now raises `EvaluationError`, not `ConfigError`.
- Second review (red tests in `tests/test_exact_input_review2.py`):
  - `Config` enum mapping keys are converted once, from a member or an exact str. A str subclass raises `ConfigError` ("has an unknown key"), and any other key counts as missing ("needs a value for every ..."). This reverses issue #107's conversion of a lookalike key; `tests/test_config_checks_review.py::test_a_lookalike_scoring_key_is_stored_as_the_enum` became `test_a_lookalike_scoring_key_is_rejected`. `allowed_game_formats` entries in `Config(...)` keep their existing conversion (not in the review's scope).
  - Type names in messages are read through `type.__dict__["__name__"]` (`winprob._type_name`), so a metaclass property never runs.
  - `game_win_probability`, `expected_point_share`, and `reliability` turn an `OverflowError` from their arithmetic into `EvaluationError`; no result changes for inputs that computed before.
  - `Config.from_dict` reads nested data only as exact dicts, lists, tuples, strs, and ints, and its messages name a wrong-typed value's type. A from_dict game length key that is an int subclass is now rejected.

## Proof

Frozen row rewritten with the user's approval: Accept exact ZoneInfo. In `tests/test_exact_input_types.py` `BAD_MATCH_FIELDS`, the "played_at ZoneInfo" row (an exact ZoneInfo expected to raise) became "played_at ZoneInfo subclass" (`_SubZone("America/New_York")`, still expecting `InvalidMatchError` matching "played_at tzinfo must be"); commit 03a327c, alone. Proof that the row still catches the defect: in a scratch worktree at the merge base with origin/main (c23ab5a), with only the rewritten test file copied in, `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_exact_input_types.py -k ZoneInfo` gave `6 failed, 392 deselected`, each `Failed: DID NOT RAISE InvalidMatchError` (validate, record, replay, prequential live and scheduled, ledger_order): main accepts the subclass. The worktree was removed after.

The ZoneInfo tests and every `tests/test_exact_input_*.py` file pass on Python 3.11 and 3.13 (`env -u VIRTUAL_ENV uv run --no-project -p 3.11 --with-editable . --with pytest pytest tests/test_engine.py tests/test_exact_input_*.py -q`: 694 passed on each). On both, an exact ZoneInfo has no `__dict__`, and setting `ZoneInfo.utcoffset` raises `TypeError` (immutable type).

Red run on the unchanged source (5578d59, tests only): `.venv/bin/pytest -q tests/test_exact_input_types.py tests/test_exact_input_callbacks.py tests/test_exact_input_plain_unchanged.py` gave `410 failed, 25 passed`. The passing tests are the criterion 6 pins and the plain-input checks.

Criterion 6 values, measured on c23ab5a before any source edit with `.venv/bin/python -c` importing the helpers of `tests/test_exact_input_plain_unchanged.py`:

- `Config().fingerprint()`: `17f2981186edf96c`.
- sha256 of `json.dumps(replay(_ledger(), _seeds(), as_of=date(2026, 6, 1)).to_snapshot(), sort_keys=True)`: `6020a5917012308bbb014760ce197ce4e18fd3ff004a85f3d5631047928ca801`.
- `evaluate(_ledger(), _seeds(), burn_in_matches=2)`: `matches_scored=42, mse=0.006789, mae=0.071175, bias=0.018455`, bins `(0.4, 0.4777, 0.5268, 9), (0.5, 0.5377, 0.5478, 33)`.
- `fit_scale(_ledger(), _seeds(), burn_in_matches=2, tolerance=0.05)`: `scale=3.58`, `default_scale=2.9`, fitted `mse=0.007213`, at default `mse=0.007232`, 25 profile points, `at_bound=False`. The full report and profile are pinned in the test.

Gate (after the change): see the PR description.
