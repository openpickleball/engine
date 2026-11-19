# Plan: Split engine.py into focused private modules (issue #127)

Risk tier: Medium. The change moves code only, with no behavior change, but it touches every line of `engine.py` (6,345 lines) and adds about eleven modules. The tests that monkeypatch private functions through `open_engine.engine` must follow those functions to their new modules, and a patch that misses its target can let a test pass while checking nothing.

Domain skills: superpowers:test-driven-development, invoke (for the strengthened source-scanning test and the module-layout test). Django, FastAPI, React, and the other listed framework skills: not applicable because this is a standard-library Python package with no web, UI, data store, auth, payments, or email code.

## Principle

Move code, do not change it. Every function, class, and constant keeps its name, signature, body, and docstring. The only edits inside moved code are the import lines each new module needs. Behavior, error messages, the public API, `Config.fingerprint()`, and the snapshot format stay the same.

## Module layout

Modules in import order. Each imports only from modules above it, so there are no import cycles. Line ranges are from `engine.py` on `main` at the time of planning; the implementer moves whole definitions, with the comments above them.

| Module | Holds | From `engine.py` |
| --- | --- | --- |
| `_validation.py` | The type and number predicates and helpers: `_type_name`, `_type_is` (moved from `winprob.py`), `_is_plain_str`, `_is_id`, `_is_plain_date`, `_is_exact_int`, `_is_exact_number`, `_is_whole_number`, `_is_finite_number`, `_number_broken`, `_is_config_number`, `_is_config_whole`, `_as_float`, `_SpoofedNumber`, `_SPOOFED_NUMBER`, `_plain_number`, `_days_before` and the day constants it uses | scattered |
| `_types.py` | `_StrEnum`, `RatingFormat`, `MatchType`, `_VERIFIED_MATCH_TYPES`, `ScoringType`, `MatchStatus`, `GameFormat`, `_GAME_FORMAT_FIELD_NAMES`, `_is_member`, `_enum_shown`, `_safe_shown`, `_game_format_shape_broken`, `_game_format_broken`, `ALL_GAME_FORMATS` | 67 to 306 |
| `_config.py` | `_FrozenMapping`, the `_CONFIG_*` tables and limits, `Config`, and its rebuild and check helpers (`_require_config`, `_config_shape`, `_rebuilt_config`, `_config_or_default`, `_enum_keyed`, `_int_keyed`, `_game_formats`, `_shown`, and the rest of that section) | 307 to 1361 |
| `_model.py` | `ExternalRatingSnapshot`, `RatingState`, `Player`, `Match` and its validation helpers, `_UTC_MIN`, `_utc_instant`, `RejectionReason`, `_SEED_SENSITIVE_REASONS`, `MatchResult`, `NetworkConnectivity`, `PlayerSummary`, `ReplayInfo`, `_zero_by_format` | 1362 to 1480, 1880 to 2211 |
| `_ledger.py` | `ledger_order`, `_ledger`, `_canonical`, `_strict`, `_match_digest`, `_json_text`, `_json_form`, `MatchupKey`, `_matchup_key`, `_content_score`, `_content_key` | 1700 to 1880, 2476 to 2528 |
| `_math.py` | The rating math and reliability: the named model constants, `expected_point_share`, `inflated_rd`, `player_k`, `information_multiplier`, `_actual_point_share`, `_rolling_unique_count`, `_latest`, `_is_late`, `_seen`, `_check_reliability_state`, `reliability`, `_reliability`, `_clone_state`, `clone_player` | 2212 to 2475 |
| `_connectivity.py` | `_UnionFind`, `_contact_graph`, `_network_connectivity`, the cache size constant | 1981 to 2113 |
| `_seeds.py` | Seed validation: `_seed_type_broken`, `_RESERVED_SOURCE_PREFIX`, `_reserved_source_broken`, `_seed_rule_broken`, `_provenance_linked`, `_check_seed_date`, `_HeldIdentities`, `_check_seed_identity`, `_plain_seed`, `_plain_state`, `_validate_seed` | 2583 to 2885 |
| `_snapshot.py` | `SNAPSHOT_VERSION`, the key sets, `_Snapshot`, the snapshot writers (`_state_form` and the rest) and readers (`_snap_*`, `_check_*`, `_read_snapshot`) | 2886 to 4227 |
| `_engine.py` | `_check_lookup_format`, `RatingEngine`, `_load_snapshot` | 4228 to 5458 |
| `_replay.py` | `_engine_under`, `replay`, `_resume_start`, `_replay`, `_check_resume_rows` | 5459 to 5710 |
| `_evaluation.py` | `PredictionMode`, `EvaluationReport`, `PrequentialPrediction`, `_prequential` and its helpers, `evaluate`, `_scored_pairs`, `_report_from_predictions`, `_SEARCH_STEPS`, the fit checks, `ScaleFit`, `fit_scale`, `WinCalibrationFit`, `fit_win_calibration` | 5711 to end |

Where a definition's natural home differs from the table (for example a helper that only one later module uses), the implementer may move it there and records the choice in the plan.

Departures recorded during the work (the table above already shows them):

- `_utc_instant` and `_UTC_MIN` live in `_model.py`, not `_ledger.py`: `Match.__post_init__` calls `_utc_instant`, and `_model.py` imports before `_ledger.py`.
- `_math.py` imports before `_connectivity.py`: `_contact_graph` calls `_math._latest`.
- `_load_snapshot` lives in `_engine.py`, not `_replay.py`: `RatingEngine.from_snapshot` calls it, and `_engine.py` imports before `_replay.py`.
- `winprob.py` takes its place in the import order after `_types.py` (the criterion 5 test lists it there). It keeps its one import inside a function, in `_check_format`, now from `._types`; that is the only import inside a function, as on `main`. It imports only `_type_name` from `_validation`, since `_type_is` is no longer used there.
- `__init__.py` keeps importing public names from `engine`.
- Public classes keep `open_engine.engine` as their `__module__`. `_json_form` tags each value in a match digest with `type(value).__module__` and `__qualname__`, and snapshots store those digests (`ledger_digests`). With the new module names every digest changed: the pinned snapshot hashes in `test_exact_input_plain_unchanged.py` and `test_config_rebuilt_at_entry.py` failed, and resuming from a snapshot taken on `main` raised `LedgerConflictError` for every row. So each module that defines a public class sets `Cls.__module__ = "open_engine.engine"` after its definitions, with a comment giving the reason. No definition changes. The maintainer approved the pin; issue #133 will remove the digest's dependence on module names. `repr` of a class and digests stay as on `main`. `inspect.getsource` on a public class now looks in `engine.py` and fails, as it would for any re-exported class with a pinned module.

`engine.py` keeps its module docstring, updated to say the package's code lives in the private modules listed, and becomes a re-export module (plus the type hint imports and the `_FrozenMapping` re-export the departures above describe): every name without a leading underscore that `engine.py` defines on `main` (the public API plus `clone_player`, `player_k`, `inflated_rd`, `information_multiplier`, `PredictionMode`, `MatchupKey`, `SNAPSHOT_VERSION`, `ALL_GAME_FORMATS`, and any other), each as `from ._x import Name as Name` so `mypy --strict` accepts `from open_engine.engine import Name`. It also keeps re-exporting the error classes. Private names are not re-exported.

`winprob.py` imports `_type_name` (and `_number_broken`, criterion 7) from `_validation` and `GameFormat` from `_types` (no longer from `engine`). `__init__.py` imports public names from their new modules or from `engine`; `__all__` does not change.

Each new module starts with `from __future__ import annotations` and a short docstring that says what it holds and carries the demonstration label (CLAUDE.md: keep the label in the module docstrings).

## Acceptance criteria

1. `engine.py` holds no definitions: only its docstring, imports, and re-exports. Every new module is under about 1,500 lines and has a docstring that says what it holds and carries the demonstration label.
2. The modules import in the order of the table, with no import cycle and no import inside a function except where `main` already has one.
3. No behavior change: with each definition compared by name, the AST of every function, class, and module-level assignment on the branch equals the one on `main`, ignoring import statements and the module docstrings. `open_engine.__all__` and every name `open_engine.engine` exports without a leading underscore on `main` are still importable from `open_engine.engine`.
4. Tests change only where they reach a private name or read a source file:
   - an import of a private name changes to its new module;
   - a `monkeypatch.setattr(engine_module, "_name", ...)` changes to the module (or modules) whose code calls `_name`, and each changed patch is shown to take effect (see Proof);
   - a test that reads `engine.py`'s source (`test_engine_has_no_assert_statements`) reads every module in `src/open_engine/` instead, so its coverage does not shrink;
   - `tests/test_check_wheel.py`'s package file list adds the new modules.
   No assertion changes.
5. A new test asserts the import order: each private module imports from the package only modules listed above it in the table.
6. `scripts/check_wheel.py` `PACKAGE_FILES` lists every new module. The sdist and wheel checks pass on a fresh build.
7. The overlapping predicates live together in `_validation.py` under a short comment that names each one, what it accepts, and when to use it, including that `_is_finite_number` and `_is_whole_number` use `isinstance` and are safe only on values `_plain_number` has already converted. Exact duplicates are removed: `winprob._check_number` raises the same messages `_number_broken` returns, so it becomes a call to `_number_broken`, with the same error class and messages. No predicate changes what it accepts.
8. Docs: `CLAUDE.md`'s file list, its references to `engine._is_member`, `winprob._type_is`, and `winprob._type_name`, and its "named model constant in `engine.py`" rule name the new modules. `docs/model.md` and `docs/behavior.md` name the new module wherever they name `engine.py` as the home of code. `CHANGELOG.md` gets an entry under `## [Unreleased]`, `### Changed`: internal layout only, no behavior or public API change; public classes still report `open_engine.engine` as their module, so match digests and stored snapshots do not change.

## Files that change

- `src/open_engine/engine.py`, `winprob.py`, `__init__.py`; new `_validation.py`, `_types.py`, `_config.py`, `_model.py`, `_ledger.py`, `_connectivity.py`, `_math.py`, `_seeds.py`, `_snapshot.py`, `_engine.py`, `_replay.py`, `_evaluation.py`.
- `scripts/check_wheel.py`.
- Tests named in criterion 4, plus one new test file for criterion 5.
- `CLAUDE.md`, `docs/model.md`, `docs/behavior.md`, `CHANGELOG.md`.

## Order of work

1. Save `main`'s `engine.py` and `winprob.py` to the scratch directory for the definition-by-definition AST comparison (Proof). Write the comparison script there.
2. Write the new tests first, red: the import-order test (criterion 5) and the widened no-assert test. Commit them alone.
3. Create the modules bottom-up in table order, moving definitions with a script where practical (cut by line range, then fix imports), so the text moves byte for byte. Run `python -c "import open_engine"`, `mypy src`, and the comparison after each module.
4. Turn `engine.py` into the re-export module. Point `winprob.py` and `__init__.py` at the new modules.
5. Update the tests in criterion 4, and prove each changed monkeypatch takes effect.
6. Update `scripts/check_wheel.py` and `tests/test_check_wheel.py`. Build and run both package checks.
7. Docs and changelog (criterion 8).
8. Full gate.

## Risks

- A monkeypatch that misses its target: the test passes but checks nothing. Mitigation: Proof shows each changed patch takes effect.
- A name a module uses but does not import fails only at call time, on a path the tests might not reach. Mitigation: `ruff check` (F821, undefined names) and `mypy --strict` both catch this statically.
- An import cycle, for example through `winprob.py`. Mitigation: the table's order and the criterion 5 test.
- Determinism and provenance are security-sensitive (CLAUDE.md hard rule 7). Moving code cannot change them if the bodies are byte-identical, which the AST comparison checks.
- The pin has two consequences, found in review, that `engine.py` handles so they match `main`:
  - `typing.get_type_hints(cls)` resolves a class's string annotations in `sys.modules[cls.__module__]`, now `engine.py`. Without help it raised `NameError` for `Config` and `ReplayInfo` (`Mapping`) and for `Match`, `ExternalRatingSnapshot`, `RatingState`, `PlayerSummary`, and `PrequentialPrediction` (`date`; `Match` also uses `datetime`). `engine.py` imports `Mapping`, `date`, and `datetime` for those annotations, with a comment and `# noqa: F401`. They are not re-exports and not in `__all__`.
  - Pickles of `Config`, and so of `RatingEngine` and `ReplayInfo`, hold a `_FrozenMapping`. On `main` a pickle names it `open_engine.engine._FrozenMapping`. `_config.py` pins `_FrozenMapping.__module__ = "open_engine.engine"`, and `engine.py` re-exports `_FrozenMapping`, its one private re-export. Pickles made on `main` load on the branch, and pickles made on the branch load on `main`.
  - `tests/test_public_class_identity.py` checks both, and checks that every public class reports `open_engine.engine` (the error classes `open_engine._errors`, as on `main`), so a new public class cannot miss the pin.
- Match digests in snapshots tag each value with its type's module. Mitigation: public classes keep `open_engine.engine` as their `__module__` (see the departures above), so digests, snapshots, and pickles stay as on `main`.

Not done here: renaming or rewriting any function (#128 breaks up long functions), merging predicates that differ in what they accept, or moving `_errors.py` or `_version.py`.

## Proof

- Criterion 3: a scratch script parses `main`'s `engine.py` and `winprob.py` and every module on the branch, collects each top-level definition by name (functions, classes, assignments), drops docstrings from module level only, and compares `ast.dump` per name. Every name on `main` must appear once on the branch with an equal dump, except `winprob._check_number` (criterion 7), whose change is shown separately. It also imports every public name from `open_engine.engine`.
- Criterion 4: for each changed `monkeypatch.setattr`, run the test once with the patched function replaced by one that raises a marker error, and show the marker in the failure; then restore. Record the commands and output in the PR.
  - One exception, asked for in review: `test_prequential_predictions_compute_no_digests` patches `_match_digest` in `_ledger` as well as `_replay`, so a later caller that reads it from `_ledger` cannot slip past. No code calls it through `_ledger` today, so that patch shows no marker; the `_replay` patch does (through `replay`).
- Criteria 5 and 6: the new test; `python -m build` into a clean `dist/`, `scripts/check_sdist.py`, `scripts/check_wheel.py`, `twine check --strict`.
- The full gate: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`, and `python examples/basic_match.py` output identical to `main`'s.
