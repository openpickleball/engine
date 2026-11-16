# Plan: Reliability ignores inactivity unless as_of is passed, and uses the wall clock (issue #17)

Risk tier: High. It changes existing behavior and the signature of two public exports
(`reliability`, `RatingEngine.player_summary`), adds a public error class, and touches
replay determinism, which CLAUDE.md marks security-sensitive. The maintainer confirmed High.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api,
django-celery, django-allauth, python-fastapi: not applicable because this repository is a
standard-library Python package with no web framework, task queue, or auth. Frontend, React,
Expo, browser skills: not applicable because there is no UI.

## Decision

The maintainer chose: `player_summary` defaults `as_of` to the engine's `calculated_as_of`
(the date of the last replay) and raises when both are unset. The free function
`reliability(state, cfg, as_of)` takes `as_of` as a required argument, since it has no
engine. No code path in the package reads the wall clock.

## Acceptance criteria

1. No `date.today()`, `datetime.now()`, `datetime.utcnow()`, `datetime.today()`, or
   `time.time()` call anywhere in `src/open_engine`. A test scans every module for them.
2. Reliability reflects inactivity by default: `player_summary` without `as_of` measures
   at `engine.calculated_as_of`, never at the player's own `rd_as_of` and never at today.
3. Test: an inactive player's reliability drops over time without passing `as_of` to
   `player_summary`. The same ledger replayed at a later `as_of` gives a lower default
   reliability (and a wider default `rd`) for a player with no matches since.
4. `player_summary` with no `as_of` on an engine that was never replayed
   (`calculated_as_of is None`) raises `ReferenceDateError` before any state changes
   (it must not create a category rating state first). The message says to pass `as_of`
   or replay first.
5. An explicit `as_of` still wins over `calculated_as_of`, including a date before it.
6. `reliability(state, cfg)` without `as_of` raises `TypeError` (required argument).
7. `ReferenceDateError` is an `OpenEngineError` subclass, exported in `open_engine.__all__`,
   listed in the README "Public API" section, and has a docstring.
8. Imported reliability carry uses the same reference date, so a seed captured after the
   last replay date is not shown by default (it is not yet fresh at the reference date).
9. Docs (module docstring, `PlayerSummary` and `player_summary` docstrings, README quick
   start, CHANGELOG) describe the new default and no longer mention today or rd_as_of as
   a fallback. `examples/basic_match.py` runs.

## Files that change

- `src/open_engine/_errors.py`: add `class ReferenceDateError(OpenEngineError)` with the
  docstring "No reference date to measure inactivity and reliability at." Place it after
  `SnapshotError`, before `LedgerConflictError` or at the end, whichever keeps the module
  readable; update the module docstring if it lists classes.
- `src/open_engine/engine.py`:
  - import and re-export `ReferenceDateError` the same way the other errors are.
  - `reliability(state, cfg, as_of: date) -> float`: drop the default and the
    `date.today()` fallback. Docstring: as_of is the date inactivity and variety are
    measured at.
  - `player_summary(player_id, rating_format, as_of: date | None = None)`: compute
    `reference = as_of if as_of is not None else self.calculated_as_of` first, and raise
    `ReferenceDateError("player_summary needs as_of when the engine has not been replayed")`
    (wording may vary, test matches on "as_of") when it is None, before `self.player(...)`
    and `self.state(...)` so no state is created. Use `is not None`, not `or`.
    Docstring: as_of defaults to `calculated_as_of`; pass it for a live engine or to
    measure at another date; raises ReferenceDateError when both are None.
  - `PlayerSummary` docstring: reference date is "player_summary's as_of, else the
    engine's calculated_as_of".
  - Module docstring bullet on `PlayerSummary.reliability` and imports: replace the
    sentences about "with no as_of, player_summary measures from the state's own rd_as_of
    ... unless the caller passes as_of (for example today)" with the new rule. Add a
    behavior note that `player_summary` measures inactivity at the last replay date by
    default and that the engine never reads the wall clock.
- `src/open_engine/__init__.py`: import and add `"ReferenceDateError"` to `__all__`
  (keep the existing ordering convention).
- `README.md`: add `ReferenceDateError` to the "Public API" list (the test compares it to
  `__all__`). The quick start's live `player_summary` call passes `as_of=date(2026, 10, 29)`.
  (README prose is reconciled again in Phase 8.)
- `examples/basic_match.py`: pass `as_of=` the match date to `player_summary`.
- `CHANGELOG.md` under `## [Unreleased]`: Added `ReferenceDateError`; Changed
  (**Breaking**) `reliability` requires `as_of`; `player_summary` defaults `as_of` to
  `calculated_as_of` and raises `ReferenceDateError` when the engine was never replayed,
  so a stale player's reliability and RD now reflect inactivity up to the last replay;
  the engine no longer reads the wall clock (issue #17). The earlier Unreleased Fixed entry
  for issue #15 said the reference defaults to `rd_as_of` and told the backend to pass
  today; reword that clause to point at the new entry.
- Tests:
  - New `tests/test_reference_date.py` with the criteria 1 to 6 and 8 tests below.
  - Update existing call sites that relied on the old default (expected, since the
    behavior changes on purpose): `tests/test_engine.py` (`test_import_reliability_expires_after_carry_matches`,
    `test_live_updates_labeled_and_replay_resets` live part), `tests/test_api.py`
    (`test_player_summary_is_a_frozen_dataclass`, `test_player_summary_for_a_provisional_player_after_replay`,
    which asserted the rd_as_of default and now asserts the calculated_as_of default),
    `tests/test_imported_reliability.py` (`test_import_expires_after_carry_matches`), and any
    other test the suite shows failing for the same reason. Pass an explicit `as_of` on
    live engines; keep each test's intent. Do not weaken assertions.
  - `tests/test_api.py` or wherever the public-API checks live: they cover the new export
    automatically through `__all__`; confirm.

## Order of work

1. Red: write `tests/test_reference_date.py` (all new tests), run it, see each fail for
   the right reason (the wall-clock scan finds two calls; default summary equals 95.3
   at both replay dates; no ReferenceDateError exists). Commit the red tests.
2. Add `ReferenceDateError` and export it (errors, engine re-export, `__init__`, README
   Public API list).
3. Change `reliability` and `player_summary`, and the docstrings.
4. Fix the existing tests and the example that relied on the old default.
5. CHANGELOG entry.
6. Full gate: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`, then
   `python examples/basic_match.py`.

## Risks

- Backend callers that call `player_summary` on a live engine without `as_of` now get an
  error. That is the intended breaking change, called out in the changelog.
- Live matches after the last replay: a player whose `rd_as_of` is after
  `calculated_as_of` is measured at the earlier replay date. `inflated_rd` returns the
  stored RD for a date on or before `rd_as_of`, and the variety window counts partners
  seen after the date, so reliability is not inflated or shrunk by this. A seed captured
  after the replay date is not carried until the next replay or an explicit `as_of`. Both
  documented.
- `player_summary` creates a missing category state; raising after creation would change
  state on a failed call (and clear the snapshot `resumable` mark). The check must come
  first; a test asserts no state is created and the snapshot stays resumable.
- `or` versus `is not None`: dates are always truthy, but use `is not None` for clarity.
- Riskiest step: the module docstring rewrite, since several tests check docs; keep the
  wording plain and consistent with CHANGELOG.

Not done: no engine-wide clock that advances with live matches (that would add snapshot
state), and no change to `record` or `replay`.

## Proof

Tests in `tests/test_reference_date.py`:

- `test_package_never_reads_the_wall_clock` (criterion 1): parse every `.py` file under
  `src/open_engine` with `ast` and fail on any call whose function is an attribute named
  `today`, `now`, `utcnow`, or `time` on a name (`date.today()`, `datetime.now()`,
  `time.time()`), naming the file and line. Measured on origin/main:
  `grep -rnE "today\(|\.now\(|utcnow|time\.time" src/` gives
  `engine.py:881 as_of = state.rd_as_of or date.today()` and
  `engine.py:2185 reference = as_of or state.rd_as_of or date.today()`, so it fails there.
- `test_inactive_player_reliability_drops_by_default` (criteria 2, 3): synthetic ledger,
  player `syn-a` with 30 matches every 2 days from 2024-01-01, unique partners and
  opponents. `replay(..., as_of=date(2024, 3, 1))` and `replay(..., as_of=date(2026, 11, 1))`;
  `player_summary("syn-a", D)` with no `as_of` on each. Late reliability < early, late rd >
  early, and late equals `player_summary(..., as_of=date(2026, 11, 1))`. Measured on
  origin/main with the scratch script: early default 95.3, late default 95.3 (bug),
  late with as_of 34.5.
- `test_resumed_checkpoint_measures_at_its_new_as_of` (criterion 3): resume
  `replay([], as_of=later, from_snapshot=early.to_snapshot())` and check the default
  reliability equals the full later replay's.
- `test_summary_without_reference_date_raises` (criterion 4): live engine, record a match,
  `pytest.raises(ReferenceDateError, match="as_of")`; also for a format with no state
  (e.g. a category format) and assert the player gained no new rating state.
- `test_summary_raise_creates_no_state` (criterion 4): the no-new-state check, split out of
  the test above. It covers a format with no state and a player id the engine has never
  seen.
- `test_summary_raise_keeps_snapshot_resumable` was not written: a never-replayed engine's
  snapshot is never resumable, so the no-new-state check covers it.
- `test_explicit_as_of_overrides_calculated_as_of` (criterion 5): on the late replay,
  `as_of=date(2024, 3, 1)` equals the early replay's default.
- `test_reliability_requires_as_of` (criterion 6): `pytest.raises(TypeError)` on
  `reliability(state, cfg)`.
- `test_reference_date_error_is_public` (criterion 7): subclass of `OpenEngineError` and
  `ValueError`, in `open_engine.__all__`. The existing API tests check README and
  docstrings.
- `test_import_captured_after_replay_is_not_carried_by_default` (criterion 8): replay at
  D0, seed an import captured after D0 via `seed_external_rating`, default summary has
  `imported_reliability is None`; with `as_of` = capture date it is carried.

Live drive (High tier): run `python examples/basic_match.py` (changed path), run the
scratch repro script after the change (expect early default > late default, late default
equals late with as_of), and call `player_summary` without `as_of` on a live engine to see
the `ReferenceDateError` message (failure path).
