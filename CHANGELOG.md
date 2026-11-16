# Changelog

All notable changes to the OPEN rating engine are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Until 1.0.0, a minor version may include breaking changes.

The engine is a demonstration. Its outputs are not OPEN ratings.

## [Unreleased]

### Added

- A "Public API" section in the README that lists every public name, with the note that the API may change in any 0.x release. Names not listed there, including everything reached only through `open_engine.engine` or `open_engine.winprob`, are internal. A test checks that the list equals `open_engine.__all__`.
- An exception hierarchy. `OpenEngineError` is the base, and every class is a `ValueError`, so `except ValueError` still works: `ConfigError` (configuration and game formats), `InvalidMatchError` (matches, teams, ids, game scores, `played_at`), `InvalidSeedError` (external and replay seeds), `PlayerError` (player ids, account and external identity links), `EvaluationError` (evaluation, calibration, and win probability arguments), and `LedgerConflictError`, now under `OpenEngineError`. Seeding can also raise `PlayerError` when the player id or the external identity is not valid.
- `RejectionReason`, a string enum with one member per reason a match is not counted.
- `PlayerSummary`, the frozen dataclass `player_summary` returns, and `ReliabilityBasis` (`open` or `imported`).
- Docstrings on every public name and on the public methods of public classes. A test checks them.

### Changed

- **Breaking:** `RatingEngine.predict` is now `predict_point_share` and `RatingEngine.win_probability` is now `predict_win_probability`, with the same arguments and results. The old names are gone.
- **Breaking:** `clone_player`, `inflated_rd`, `player_k`, `information_multiplier`, and `recency_multiplier` are no longer exported from `open_engine`. They stay in `open_engine.engine` as internal helpers.
- **Breaking:** `player_summary` returns a `PlayerSummary` instead of a dict. Fields keep their names and values (`initial_source_reliability` now comes before `initial_source_date`, which changes the key order of `dataclasses.asdict`), but `rating_format` is a `RatingFormat`, `reliability_basis` is a `ReliabilityBasis`, `reliability` is always a float, and `last_played`, `initial_source_date`, and `calculated_as_of` are `date` objects or `None` instead of ISO strings. `dataclasses.asdict` gives a dict, but its dates need converting before JSON.
- **Breaking:** `MatchResult.reason` is a `RejectionReason`, or `None` when the match counted, instead of free text. `MatchResult.rating_format` is a `RatingFormat` on every result, including rejections before any rating work, instead of a string that could be empty. `rating_format` is now required and comes second (`counted`, `rating_format`, `reason`), and `reason` defaults to `None` instead of `""`, so code that builds a `MatchResult` must pass `rating_format` and, if it uses positions, follow the new order.
- **Breaking:** errors that were plain `ValueError`s are now the classes above. A `GameFormat` used for win probability is validated first, so an invalid format raises `ConfigError`. That includes `win_by=0`, which used to raise the `ValueError` that is now `EvaluationError`.
- **Breaking:** `Match.validate` rejects a `match_id` or player id that is not a non-empty, non-whitespace string, and a game that is not a pair of scores, with `InvalidMatchError`. `predict_point_share` and `predict_win_probability` apply the same id check. Blank match ids already raised. What is new: a ledger with a blank, whitespace-only, or non-string player id, or a non-string id such as bytes, now raises in `replay` and `prequential_predictions` (and so in `evaluate`, `fit_scale`, and `fit_win_calibration`), even when the row is after `as_of` or would have been rejected. Before, such ledgers could replay. Every ledger that still replays gives the same ratings.
- `RatingFormat`, `MatchType`, and `ScoringType` are `StrEnum`s, so `str()` and f-strings give the value (`"doubles"`, not `"RatingFormat.DOUBLES"`). Comparison with the value, `repr`, pickling, and `Config.fingerprint()` are unchanged.

### Fixed

- `record` with a blank, whitespace-only, or non-string player id no longer changes any rating. It used to update the players before the bad id and then raise before marking the match processed, so every retry moved those ratings again. It now raises `InvalidMatchError` before anything changes.
- `LedgerConflictError` survives pickling and copying with its message and `match_ids` intact. The message used to come back garbled.
- `game_win_probability`, `match_win_probability`, and `predict_win_probability` validate the `GameFormat` and raise `ConfigError` for an invalid one. Before, a `cap` at or below `points_to` silently returned a wrong probability, and `points_to=0` raised a bare `ValueError` from inside the math.
- A malformed game score (not a pair of scores) raises `InvalidMatchError` instead of a bare `ValueError` or `TypeError` from unpacking.

## [0.3.0.dev0] - 2026-11-15

The first dev release, published to TestPyPI.

### Added

- `scripts/check_wheel.py`, which fails unless a wheel holds only the `open_engine` modules, `py.typed`, and one `openpickleball_engine-<version>.dist-info` directory, which must contain `METADATA`, `WHEEL`, and `RECORD`. Every module and `py.typed` must be present, and repeated names and other directory entries fail. CI's `package` job and the release build run it after `check_sdist.py`.
- Tests that the README quick start runs and that the README install line pins the package version.
- `twine` in the `dev` extra, so contributors can run `twine check --strict` as CI does.
- `LedgerConflictError`, a `ValueError` raised by `replay` and `prequential_predictions` (and so by `evaluate` and `fit_scale`) when ledger rows share a `match_id` but differ. Its `match_ids` attribute is the sorted tuple of every conflicting id.
- CI builds the package and runs `scripts/check_sdist.py`, which fails when the source distribution has an unexpected top-level entry.
- CI reports branch coverage of `src/open_engine` on every supported Python version. The `dev` extra now includes `pytest-cov`; run `pytest --cov` for the same report locally.
- A release workflow (`.github/workflows/release.yml`). Pushing a `v*` tag reruns the test workflow (`test.yml`) on the tagged commit, checks that the commit is on `main`, builds the sdist and wheel once, and publishes them to TestPyPI through Trusted Publishing, with no stored upload token. `docs/releasing.md` lists the one-time setup and the release steps.
- `scripts/check_release.py`, which fails unless a release tag is exactly `v` plus the version of the built sdist and wheel, and `dist/` holds nothing else.
- A test that `Config.version` equals `__version__`.

### Changed

- **Breaking:** Python 3.11 or later is required. Python 3.10 is end of life. CI tests 3.11, 3.12, and 3.13.
- The version is set in one place, `src/open_engine/_version.py`. `open_engine.__version__`, the `Config.version` default, and the build read it from there.
- `Config.version` and `__version__` are `0.3.0.dev0`. This changes every `Config.fingerprint()`.
- The source distribution now ships only `src/`, `tests/`, `scripts/`, `examples/`, `README.md`, `LICENSE`, `CHANGELOG.md`, and `pyproject.toml` (plus the `PKG-INFO` and `.gitignore` that hatchling adds). Untracked top-level files and directories (such as local tool output) and repo-only paths such as `CLAUDE.md`, `docs/`, and `.github/` are no longer bundled. Untracked files that are not gitignored inside the shipped directories still ship, and those under `src/open_engine/` also ship in the wheel. The wheel is otherwise unchanged.
- fit_scale compares unrounded MSE; ScaleFit.profile reports unrounded values.
- CI's `package` job installs `build`, `hatchling`, and `twine` from a hash-pinned lock (`.github/requirements/release.txt`), builds with `--no-isolation`, and runs `twine check --strict`, the same install and build steps the release workflow uses.
- The engine now type-checks under `mypy --strict`, and CI runs `mypy src` on every supported Python version. `ledger_order` was annotated as returning `tuple[date, time, int, str]` (its return type changes again in the breaking entry below), and `RatingEngine.player_summary` as returning `dict[str, Any]`. Runtime behavior is unchanged.
- **Breaking:** `Match.played_at` must be timezone-aware and convertible to UTC. `Match.validate()` and `ledger_order` raise `ValueError` for a naive value or one outside the range UTC can represent.
- **Breaking:** matches still sort by `played_on` first. Within one day, `ledger_order` now orders timed matches by the UTC instant of `played_at` instead of its local clock time, places untimed matches before timed ones (a timed match at local midnight used to tie with them), and returns `tuple[date, int, datetime, int, str]`. This changes the replay order, and so the ratings, for some ledgers that mix time zones on one day.
- **Breaking:** `replay` and `prequential_predictions` (and so `evaluate` and `fit_scale`) validate every ledger row before replaying, including rows after `as_of`. An invalid row now raises wherever it sits in the input.

### Fixed

- `replay` and `prequential_predictions` no longer depend on input order when the ledger repeats a `match_id`. Rows with the same `match_id` collapse to one, so `ReplayInfo.matches_considered` counts them once, only when every field matches in type and value, with `played_at` compared by its UTC instant and UTC offset. Any other repeat raises `LedgerConflictError`, including values equal under `==` but of different types (such as `best_of=3` and `best_of=3.0`) and two readings of a repeated wall time in a daylight saving fold.

## 0.2.0 - 2026-11-15

0.2.0 was never tagged or published.

### Added

- `game_win_probability`, `match_win_probability` and `calibrate_win_probability` in `open_engine.winprob`, and `RatingEngine.win_probability` for a calibrated match win probability.
- `Config.win_calibration_by_scoring`, a per-scoring-type calibration exponent that defaults to 1.0.
- `PrequentialPrediction` and `prequential_predictions`, which predict each match from ratings that have not seen it.
- `ScaleFit.profile` (the error at each scale searched) and `ScaleFit.at_bound` (whether the fit landed on a search edge), so an unidentified fit is visible.
- `WinCalibrationFit` and `fit_win_calibration`, which fit the win-probability calibration per scoring type.

### Changed

- `evaluate` and `fit_scale` accept `mode` (`"live"` or `"scheduled"`), `score_from` and `anchor`. The defaults keep the previous behavior.
- `match_win_probability` raises `ValueError` unless `best_of` is an odd integer of at least 1.
- `Config.version` and `__version__` are now `0.2.0`.

## [0.1.0] - 2026-11-12

### Added

- First demonstration release of the `openpickleball-engine` package (`open_engine`), with no runtime dependencies, for Python 3.10 and later.
- A basic match example in `examples/basic_match.py`.

[Unreleased]: https://github.com/openpickleball/engine/compare/v0.3.0.dev0...HEAD
[0.3.0.dev0]: https://github.com/openpickleball/engine/compare/v0.1.0...v0.3.0.dev0
[0.1.0]: https://github.com/openpickleball/engine/releases/tag/v0.1.0
