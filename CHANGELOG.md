# Changelog

All notable changes to the OPEN rating engine are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Until 1.0.0, a minor version may include breaking changes.

The engine is a demonstration. Its outputs are not OPEN ratings.

## [Unreleased]

### Added

- CI builds the package and runs `scripts/check_sdist.py`, which fails when the source distribution has an unexpected top-level entry.
- CI reports branch coverage of `src/open_engine` on every supported Python version. The `dev` extra now includes `pytest-cov`; run `pytest --cov` for the same report locally.

### Changed

- The source distribution now ships only `src/`, `tests/`, `scripts/`, `examples/`, `README.md`, `LICENSE`, `CHANGELOG.md`, and `pyproject.toml` (plus the `PKG-INFO` and `.gitignore` that hatchling adds). Untracked top-level files and directories (such as local tool output) and repo-only paths such as `CLAUDE.md`, `docs/`, and `.github/` are no longer bundled. Untracked files that are not gitignored inside the shipped directories still ship, and those under `src/` also ship in the wheel. The wheel is otherwise unchanged.
- fit_scale compares unrounded MSE; ScaleFit.profile reports unrounded values.
- The engine now type-checks under `mypy --strict`, and CI runs `mypy src` on every supported Python version. `ledger_order` is annotated as returning `tuple[date, time, int, str]`, and `RatingEngine.player_summary` as returning `dict[str, Any]`. Runtime behavior is unchanged.

## [0.2.0] - 2026-11-15

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

[Unreleased]: https://github.com/openpickleball/engine/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/openpickleball/engine/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/openpickleball/engine/releases/tag/v0.1.0
