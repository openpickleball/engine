# Plan: Define and document the public API (issue #12)

Risk tier: High. The change alters the public API contract (removed exports, renamed
methods, new result types, new exception classes), changes existing behavior rather than
adding to it, and touches most of the package, its tests, the example, and the README.

Domain skills: none applicable. The listed skills cover Django, FastAPI, React, Expo, and
browser work; this is a standard-library Python package.

## Decisions (made by the maintainer before planning)

1. Keep `ledger_order` and `expected_point_share` public (the private evaluation repo
   imports both). Remove `clone_player`, `inflated_rd`, `player_k`,
   `information_multiplier`, and `recency_multiplier` from `open_engine` entirely: they
   stay importable from `open_engine.engine`, which is documented as internal.
2. Rename `RatingEngine.predict` to `predict_point_share` and
   `RatingEngine.win_probability` to `predict_win_probability`. No aliases.
3. Exception hierarchy, every class a `ValueError` subclass so existing
   `except ValueError` keeps working:
   - `OpenEngineError(ValueError)`: base.
   - `ConfigError`: `Config.__post_init__`, `GameFormat.validate`. `game_win_probability`
     calls `GameFormat.validate` first, so an invalid format (`points_to=0`, a `cap` not
     above `points_to`, `win_by=0`) raises `ConfigError` from it, `match_win_probability`,
     and `predict_win_probability` (`tests/test_winprob_format.py`). `win_by=0` moves from
     `EvaluationError` to `ConfigError`; a valid `win_by` other than 1 or 2 still raises
     `EvaluationError`.
   - `InvalidMatchError`: `Match.validate`, `GameFormat.validate_game`, played_at checks
     (`_utc_instant`, so `ledger_order` too), team checks (`_validate_teams`, so
     `predict_point_share` and `predict_win_probability` too), `record`'s
     "as_of cannot be earlier than the match date", `_actual_point_share`. Also a
     `match_id` or team player id that is not a non-empty, non-whitespace `str`
     (criterion 11), and a game that is not a tuple or list of two scores (criterion 5).
   - `InvalidSeedError`: `seed_external_rating`, and `replay`'s "Seed for ... already
     contains OPEN matches".
   - `PlayerError`: `RatingEngine.player`, `add_player`, `link_account`,
     `attach_external_id`.
   - `EvaluationError`: `prequential_predictions` (bad `mode`), evaluation with no
     established matches, `fit_win_calibration` with nothing to fit, and the argument
     checks in `open_engine.winprob` (`game_win_probability`, `match_win_probability`).
   - `LedgerConflictError` keeps its name and `match_ids`, and is re-parented to
     `OpenEngineError` (still a `ValueError`). Its `__reduce__` rebuilds it from
     `match_ids`, so pickle, `copy.copy`, and `copy.deepcopy` keep the message and ids
     (`tests/test_ledger_conflict_copy.py`). No `DuplicateMatchError`: nothing raises on
     a duplicate (`record` returns a not-counted result).
4. `player_summary` returns a frozen `PlayerSummary` dataclass with precise types: dates
   are `date | None`, `rating_format` is `RatingFormat`.

## Acceptance criteria

1. The README has a "Public API" section that lists every public name, grouped, one line
   each, with the note "The API may change in any 0.x release." It says that names not
   listed, including everything reached only through `open_engine.engine` or
   `open_engine.winprob`, are internal.
2. `open_engine.__all__` equals the set of names listed in that README section, enforced
   by a test that parses the section. `__all__` is exactly:
   `Config`, `ConfigError`, `EvaluationError`, `EvaluationReport`,
   `ExternalRatingSnapshot`, `GameFormat`, `InvalidMatchError`, `InvalidSeedError`,
   `LedgerConflictError`, `Match`, `MatchResult`, `MatchType`, `OpenEngineError`,
   `Player`, `PlayerError`, `PlayerSummary`, `PrequentialPrediction`, `RatingEngine`,
   `RatingFormat`, `RatingState`, `RejectionReason`, `ReliabilityBasis`, `ReplayInfo`,
   `ScaleFit`, `ScoringType`, `WinCalibrationFit`, `calibrate_win_probability`,
   `evaluate`, `expected_point_share`, `fit_scale`, `fit_win_calibration`,
   `game_win_probability`, `ledger_order`, `match_win_probability`,
   `prequential_predictions`, `reliability`, `replay`. The five hidden helpers are not
   attributes of `open_engine` (`hasattr(open_engine, "clone_player")` is false).
3. `RatingEngine.player_summary` returns `PlayerSummary`, a frozen dataclass with these
   fields, values computed exactly as the dict values are today except where the type
   changes: `player_id: str`, `rating_format: RatingFormat`, `rating: float`,
   `reliability: float`, `reliability_basis: ReliabilityBasis`, `open_reliability: float`,
   `rd: float`, `matches_played: int`, `last_played: date | None`,
   `initial_rating`, `initial_source`, `initial_source_reliability` (types as on
   `RatingState`), `initial_source_date: date | None`, `calculated_as_of: date | None`,
   `live_updates_since_recalculation: int`, `config_version: str`,
   `account_linked: bool`. `ReliabilityBasis` is a `str` enum with `OPEN = "open"` and
   `IMPORTED = "imported"`. Rounding stays as today.
4. `MatchResult.reason` is `RejectionReason | None`: `None` when the match counted, a
   member otherwise. `RejectionReason` is a `str` enum with one member per rejection the
   engine produces today, with stable snake_case values:
   `DUPLICATE_MATCH_ID`, `GAME_FORMAT_NOT_ACCEPTED`, `SELF_REPORTED_UNCONFIRMED`,
   `SELF_REPORTED_REPEAT_LIMIT`, `BEFORE_IMPORTED_BASELINE`, `SINGLES_RATING_GAP`,
   `DOUBLES_PARTNER_GAP`. `MatchResult.rating_format` is a `RatingFormat` on every
   result, early rejections included (they report the match's own format), so it is
   never empty. The rejection and acceptance rules themselves do not change.
5. Every exception the engine raises on purpose is an instance of one of the classes in
   decision 3. A test scans the source of `engine.py` and `winprob.py` (with `ast`) and
   fails on any `raise` of a class that is not an `OpenEngineError` subclass. A
   parametrized test checks one representative input per class raises that class, and
   each class is a `ValueError` subclass. Malformed input reaches these classes too: a
   game that is not a tuple or list of exactly two items raises `InvalidMatchError`
   from `Match.validate`, not a bare `ValueError` or `TypeError` from unpacking
   (`tests/test_game_shape.py`).
6. Every name in `__all__` has its own docstring (a dataclass's generated signature does
   not count), and so does every public method and property defined on a public class.
   A test enforces both.
7. `RatingEngine.predict_point_share` and `RatingEngine.predict_win_probability` replace
   `predict` and `win_probability` with the same signatures and results; the old names
   no longer exist. Internal callers, tests, `examples/basic_match.py`, and the README
   use the new names.
8. Ratings do not change: every existing rating, replay, and calibration test passes
   with only the mechanical edits this plan names (renamed methods, `reason` and
   summary access, import paths for hidden helpers). No `Config` default and no rating
   math changes, so `Config.version` and the fingerprint are untouched.
9. Docs: CHANGELOG entries under `[Unreleased]`, each API break marked **Breaking**; the
   `engine.py` module docstring no longer says plain `ValueError` where a specific class
   now applies. (CHANGELOG and CLAUDE.md are done by the coordinator after review; the
   README section and the module docstring are part of the implementation.)
10. The five public `str` enums (`RatingFormat`, `MatchType`, `ScoringType`,
    `RejectionReason`, `ReliabilityBasis`) share a private `_StrEnum` base, so `str(m)`
    and `f"{m}"` give `m.value` on every supported Python. repr, pickle, `json.dumps`,
    `== "value"`, and `Config().fingerprint()` do not change (`tests/test_enum_str.py`).
11. `Match.validate` raises `InvalidMatchError` when `match_id` or any team player id is
    not a non-empty, non-whitespace `str`, and `RatingEngine._validate_teams` applies the
    same id check, so `predict_point_share` and `predict_win_probability` raise too.
    `record` with such an id raises before anything is committed: no rating moves, no
    player is created, and a retry changes nothing (`tests/test_match_ids.py`). Before
    this fix, `record` committed the earlier ids and then raised before marking the
    match processed, so each retry moved ratings again.

## Files that change

- `src/open_engine/engine.py`: exception classes; every `raise ValueError` re-pointed;
  `RejectionReason`, `ReliabilityBasis`, `PlayerSummary`; `MatchResult` fields;
  `player_summary`; method renames and internal callers (`_predict_match`); docstrings;
  module docstring wording about exceptions.
  Review fixes: the `_StrEnum` base (criterion 10); the id check (criterion 11).
- `src/open_engine/winprob.py`: raise `EvaluationError` (import it from `.engine`; check
  for an import cycle: `engine.py` already imports from `.winprob`, so define the
  exception classes in a new private module `src/open_engine/_errors.py` that both
  import, and re-export them from `engine.py` with explicit `X as X` imports, so
  `from open_engine.engine import LedgerConflictError` still passes `mypy --strict`).
- `src/open_engine/_errors.py`: new, the six exception classes plus nothing else.
  `LedgerConflictError` moves here too.
- `src/open_engine/__init__.py`: new `__all__` and imports; helpers removed.
- `tests/test_engine.py`, `tests/test_calibration.py`, `tests/test_winprob.py`: the
  mechanical edits in criterion 8.
- `tests/test_api.py`: new. Criteria 2, 5, 6, plus `PlayerSummary` and `RejectionReason`
  behavior tests (criteria 3 and 4) may live here or beside the existing engine tests.
- `examples/basic_match.py`: renamed method; print the summary sensibly.
- `README.md`: the "Public API" section; update the quick start's `player_summary` and
  `win_probability` mentions.
- `CHANGELOG.md`, `CLAUDE.md`: coordinator, Phase 8.

`_errors.py` is under `src/`, which already ships in the sdist and wheel; no packaging
change.

## Order of work

Use superpowers:subagent-driven-development (more than about 5 tasks). Commit after each
task with the gate green; each behavior change starts with a failing test committed alone.

1. **Exceptions.** Red: `tests/test_api.py` with the hierarchy tests (each class subclasses
   `OpenEngineError` and `ValueError`; one representative raise per class, parametrized;
   the `ast` scan). Green: `_errors.py`, re-point every raise in `engine.py` and
   `winprob.py` per decision 3. Existing `pytest.raises(ValueError, match=...)` tests stay
   valid; do not change them.
2. **RejectionReason and MatchResult.** Red: tests that each of the seven rejections
   yields its enum member and the match's `rating_format`, and a counted match has
   `reason is None`. Green: the enum, field types, every `MatchResult(...)` call site.
   `tests/test_engine.py:184` (`"both teams" in r.reason`) becomes an enum comparison.
3. **PlayerSummary.** Red: tests for the type, frozenness, field types (dates are `date`,
   basis is `ReliabilityBasis`), and that values equal what the dict gave (rating rounded
   to 3, rd to 4, imported reliability rounded to 1). Green: the dataclass and method.
   Existing summary tests switch from `s["x"]` to `s.x` and compare dates as `date`.
4. **Renames.** Rename the two methods and every caller. Existing tests that call them
   are updated mechanically; add one test that the old names are gone.
5. **Hide helpers.** Update `__init__.py`; tests that import `inflated_rd` (or any other
   hidden helper) import from `open_engine.engine`.
6. **Docstrings.** Red: the docstring test (criterion 6). Green: docstrings, short and
   plain (one line where enough), for every name the measurement below lists plus the new
   classes. Keep the demonstration label in module docstrings.
7. **README Public API section.** Red: the test that parses it and compares with
   `__all__` (use HTML comment markers such as `<!-- public-api:start -->` and
   `<!-- public-api:end -->`, and take each backticked name at the start of a list item).
   Green: the section. Update the quick start for the renames and the summary.
8. Module docstring of `engine.py`: replace "raises ValueError" wording where a specific
   class now applies.
9. Full gate and live drive.

## Risks

- **Silent behavior change in ratings.** Re-pointing raises or editing `record` could
  change control flow. Mitigation: criterion 8; no edits to math; the full suite must
  pass with only the named mechanical edits. Any other test edit is a red flag to report.
- **Import cycle** between `engine.py` and `winprob.py` if the exceptions live in
  `engine.py`. Mitigation: `_errors.py`.
- **Stricter ids (criterion 11).** A ledger that replays today with a blank,
  whitespace, or non-string id (after `as_of`, or in a row rejected before commit) now
  raises `InvalidMatchError`. No rating changes for any ledger that replays successfully
  today.
- **`__all__` drift from the README.** Mitigation: the parsing test.
- **Downstream breakage.** The evaluation repo calls `engine.predict` three times and reads
  `.reason` once; the backend does not import the engine yet. All breaks are allowed in
  0.x and recorded as **Breaking** in CHANGELOG.
- **README tests ship.** The sdist includes `README.md` and `tests/`, so the parsing test
  works from an sdist too. `docs/` is not shipped, which is why the list lives in the
  README rather than `docs/api.md`.
- Riskiest step: task 1, because it touches every validation branch. Mitigation: the `ast`
  scan plus the existing `match=` tests.

Chosen not to do: removing `player_summary`'s `date.today()` fallback (it does not affect
ratings); docstrings on internal helpers; a `to_dict()` on `PlayerSummary`
(`dataclasses.asdict` covers it); a `DuplicateMatchError`; deprecated aliases.

## Proof

- Criterion 1, 2: the README parsing test; `hasattr` checks for the five hidden helpers.
- Criterion 3: `PlayerSummary` tests.
- Criterion 4: rejection tests, one per member.
- Criterion 5: the hierarchy tests and the `ast` scan.
- Criterion 6: the docstring test.
- Criterion 7: renamed-method tests and the old-names-gone test.
- Criterion 8: the existing suite passes; the diff of `tests/` contains only the
  mechanical edits listed.
- Live drive (High: changed path plus failure and edge paths): run
  `examples/basic_match.py` and show its output; in a scratch script, record a counted
  match, a duplicate, a self-reported unconfirmed match, and a game-format rejection and
  print each `reason` and `rating_format`; print a `PlayerSummary` for a seeded player and
  for one with an imported baseline; trigger one error of each class and print
  `type(e).__mro__`; confirm `from open_engine import clone_player` fails with
  `ImportError`.

Measured on `origin/main` at 1a624ab, missing docstrings among exported names and the
public methods of exported classes (a scratch script checking each class's own
`__doc__`, treating a dataclass's generated `Name(...)` signature as missing): 30 items.
Classes with only the generated signature: `Config`, `EvaluationReport`,
`ExternalRatingSnapshot`, `Match`, `MatchResult`, `Player`, `PrequentialPrediction`,
`RatingState`, `ReplayInfo`, `ScaleFit`, `WinCalibrationFit`. No docstring: `MatchType`,
`RatingEngine`, `RatingFormat`, `ScoringType`, `RatingFormat.team_size`,
`GameFormat.validate`, `GameFormat.validate_game`, `Match.validate`,
`RatingEngine.player`, `.add_player`, `.attach_external_id`, `.seed_provisional_rating`,
`.state`, `.predict`, `.player_summary`, and the four helpers being hidden. Baseline
suite: 181 passed.
