# Plan: Match dates near date.min raise OverflowError from window cutoffs (issue #92)

Risk tier: Medium. It changes existing behavior (inputs that raised now work) on code paths that affect ratings, connectivity, and scheduled evaluation, but each change is small, in one module, and fully unit-testable.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-allauth, django-celery, python-fastapi, React and Expo skills: not applicable because this is a standard-library Python package with no web, UI, or framework code.

Decision from the maintainer (counts as plan approval): clamp. Each backward window cutoff (`date - timedelta(days=window)`) is clamped at `date.min` instead of overflowing. For a forward site, results stay identical for every input that works today; if clamping would change a result, raise the right `OpenEngineError` subclass instead.

## Findings from Phase 1

Every date arithmetic site in `src/open_engine` (`grep timedelta`, `.days`, `toordinal`):

1. `_network_connectivity`: `reference - timedelta(days=cfg.diversity_window_days)`. Used by `player_summary` (connectivity). Contacts are kept when `on >= cutoff`.
2. `_rolling_unique_count`: `on - timedelta(days=window_days)`. Used for partner and opponent diversity in `record` and `player_summary`. Kept when `last_seen >= cutoff`.
3. `RatingEngine._record`, self-reported repeat window: `match.played_on - timedelta(days=cfg.self_reported_repeat_window_days)`. Counted when `cutoff <= d`.
4. `prequential_predictions(mode="scheduled")`: `anchor + timedelta(days=k * interval)`, the largest grid point strictly before `played_on`. It is always before `played_on`, so it never passes `date.max`. It falls before `date.min` when `k` is negative enough: a match within `interval` days of `date.min`, an `anchor` after early matches, or a huge `replay_interval_days`. At most one window can lie before `date.min` (the next grid point after it is at most `played_on`), and it is the first window, so no earlier match falls in it.
5. Date differences (`(on - state.rd_as_of).days`, `(reference - state.initial_source_date).days`, `(match.played_on - anchor).days`) never overflow: a difference of two dates always fits in a `timedelta`.

There is no other `as_of` or `replay_interval_days` arithmetic.

Sites 1 to 3 compare inclusively against the cutoff. A cutoff below `date.min` includes every date, and so does a cutoff at `date.min`, so clamping changes no result.

Site 4: clamping the window's date key at `date.min` and keeping the filter `played_on <= replay_date` would put a match dated `date.min` into the replay that predicts it. So the window is keyed by `k` instead, and the replay before a window takes the matches of earlier windows (`k_m < k`, which is the same set as `played_on <= anchor + k * interval` for every grid point that exists). Only the replay's `as_of` is clamped at `date.min`. With no matches before it, that `as_of` only sets `calculated_as_of` and `ReplayInfo` on a scratch engine that is thrown away, and the seed date check is off in this path. So no result changes and no error is needed.

## Acceptance criteria

1. Recording or replaying a match dated within a window of `date.min` (self-reported and verified, singles and doubles) never raises `OverflowError`.
2. `player_summary` with an early `as_of` never raises `OverflowError`.
3. The large-window repros in the issue comment do not raise `OverflowError`: `Config(diversity_window_days=800_000)` with `player_summary`, `Config(self_reported_repeat_window_days=3_652_058)` with `record` of a self-reported match, and `Config(replay_interval_days=3_652_058)` with `prequential_predictions(mode="scheduled")`.
4. `prequential_predictions(mode="scheduled")` with matches near `date.min` (default config) returns predictions and does not leak a match into the replay that predicts it.
5. No rating, summary, or prediction changes for any input that works today: tests compare results for normal dates against pinned or equivalent computations, and a property test checks the window helper against plain `timedelta` arithmetic wherever that does not overflow.
6. The `_MAX_DAYS` comment, the `Config` docstring, the README "Config values" section, and the Unreleased changelog entry no longer say such a window raises `OverflowError`. A `### Fixed` changelog entry names issue #92.

## Files that change

- `src/open_engine/engine.py`: a private helper `_days_before(day, days)` that returns `day` minus `days` days, clamped to `date.min` and `date.max` (the upper bound added after review, so a future forward caller cannot raise), computed with ordinals so it never raises; used at sites 1 to 3 and for the scheduled replay date. The scheduled window loop keys windows by `k`. Comment and docstring updates.
- `tests/test_date_min_windows.py` (new): criteria 1 to 5.
- `README.md`, `CHANGELOG.md`.

## Order of work

1. Red tests, committed alone.
2. The helper and the four sites.
3. Docs and changelog.
4. Gate: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.

## Risks

- Changing the scheduled window grouping could change which matches each replay sees. The `k` equivalence above and the existing scheduled-mode tests (which pin output) guard it.
- `date.fromordinal` raises for an ordinal outside its range. The helper clamps at both ends; the scheduled site, the only forward caller, is always before `played_on`.

## Proof

- Criteria 1 to 4: each repro on `origin/main` raised `OverflowError: date value out of range` (record early, replay early, summary early, summary with an 800,000-day window, record with a 3,652,058-day repeat window, scheduled with a 3,652,058-day interval, scheduled with a match on 0001-01-05). Measured with a scratch script run against the worktree before any change.
- Criterion 5: existing pinned output tests (`tests/test_cross_version_pins.py` and the scheduled-mode tests) stay green, plus the helper property test.
