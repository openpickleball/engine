# Plan: Add property tests and cover singles, late matches, duplicates, and reliability (issue #26)

Risk tier: Low. Test-only change: new test files under `tests/`, no change to engine code or
`Config`, and every property runs fully in pytest. If a property finds a real bug, the tier is
reassessed for that fix.

Domain skills: superpowers:test-driven-development, invoke (each property is written and run
before it is trusted). Django, Django Ninja, Celery, allauth, FastAPI, React, Expo: not
applicable because this is a standard-library Python package with no web, UI, or task layer.

## Acceptance criteria

1. `tests/test_properties.py` holds seeded property tests, each over many synthetic cases
   generated with `random.Random(<fixed seed>)` (no new dependency), with plain asserts:
   - Conservation: for every counted match, `imbalance` equals the mean team A rating change
     plus the mean team B rating change (from the players' states before and after, within
     the 5-place rounding), and it is 0.0 whenever both teams have the same mean K and
     nothing is clamped or frozen, including teams whose players have different K.
   - Symmetry: swapping team A and team B and flipping every game score gives every player
     the same rating change, and the same `imbalance`, `clamped`, `frozen`, `late`, and
     `counted`, with `expected_a`, `actual_a`, and `surprise` mirrored.
   - Margin: for one engine state, changing only the match's game scores to raise a team's
     point share, with the same number of games and the same result (same game winners),
     never gives that team a worse rating change, for every player on it. Covers singles
     and doubles, several game formats (scoring type, points to), best of 1 and 3, several
     `win_weight` values, and states near the rating floor and ceiling.
   - Replay order independence: for many generated ledgers (singles, doubles, a category,
     timed and untimed rows, self-reported rows, identical repeated rows, a not completed
     row, content duplicates under other ids, imported seeds), every permutation tried of
     the rows and of the seeds gives an identical `to_snapshot()`, under the default config
     and under `reject_content_duplicates=True`.
2. Singles: properties above run on singles as well as doubles, plus singles tests that the
   existing suite lacks (checked against `tests/` before writing).
3. The issue's listed gaps that `main` does not yet cover get tests:
   - Self-reported repeat window expiry: a repeat inside the window is discounted or
     rejected, and once the earlier matches fall outside
     `self_reported_repeat_window_days` the factor resets and the limit no longer blocks,
     with the window edge tested on both sides.
   - Reliability growth, inactivity decay, and RD shrink: RD never rises across a counted
     match (versus its inflated pre-match value) and never drops below `rd_min`; with no
     new matches, inflated RD never falls and reliability never rises as the date moves
     forward, and RD caps at `rd_max`; reliability does not fall across a weekly schedule of
     matches with varied partners and opponents under the default config. Verified schedules
     mix tournament, league, and recreational verified matches, game formats, and best of 1
     and 3. A self-reported schedule keeps one weight (one game to 11): after a run of
     heavier matches has brought RD near `rd_min`, one lighter match can shrink RD by less
     than a week of inactivity grew it, so reliability can then fall by design.
   - Duplicate `match_id` (live): re-recording any processed match returns
     `DUPLICATE_MATCH_ID` and leaves `to_snapshot()` unchanged, over generated ledgers.
   - Late live matches: recording a generated ledger live in a shuffled order flags `late`
     exactly when the match is dated before a date its players' state in that format
     already reflects (checked against a model kept in the test), for singles and doubles.
   - `evaluate()` output, duplicate match_id conflicts in replay, and late-match rules are
     already covered on `main` (`tests/test_calibration.py`, `tests/test_engine.py`,
     `tests/test_late_live_match.py`, `tests/test_reserved_match_ids.py`); no duplicate
     tests are added for them.
4. `examples/basic_match.py` runs in CI: a pytest test runs every `examples/*.py` with
   `sys.executable` and asserts a zero exit and non-empty output. CI runs pytest, so the
   example runs on every supported Python.
5. Added runtime is a few seconds at most (target under 3 s on a developer machine).
6. No real player data: invented ids only.

## Files that change

- `tests/test_properties.py` (new): generators and the property tests in criteria 1 and 3.
- `tests/test_singles.py` (new, only if criterion 2 needs tests beyond the properties).
- `tests/test_examples.py` (new): criterion 4.
- `docs/plans/2026-11-17-issue-26-property-tests.md`: this plan.
- No `CHANGELOG.md` entry: test-only change, no behavior, API, or Config change. If a bug fix
  lands, it gets a `### Fixed` entry under `## [Unreleased]`.

## Order of work

1. Generators: a valid game score generator per `GameFormat` (target reached, win by 2,
   deuce scores), a match generator that keeps a given set of game winners, an engine
   state builder. A state built by setting `RatingState` fields directly is fine for
   `record`, but `to_snapshot` rejects such states, so any test that compares snapshots
   builds its engine by recording or replaying a generated warm-up ledger, and a test that
   compares two engines rebuilds both from the same seed.
2. Conservation, symmetry, margin.
3. Replay order independence, live duplicate match_id, late live matches.
4. Reliability, RD, repeat window.
5. Singles gaps, examples test.
6. Gate, and measure the added runtime (`pytest --durations` on the new files).

## Risks

- Floating point: symmetry was measured exact on main, but compare rating changes with an
  absolute tolerance of 1e-12 and rounded fields with 1e-5 plus 1e-12, so a different Python
  cannot flake it. Two values rounded to 5 places on their own can differ by one unit in the
  last place (a mirrored `surprise` of exactly half a unit rounds to 0.25937 one way and
  0.25938 the other), and the float difference of such a pair is slightly above 1e-5. Conservation compares within the rounding of `imbalance` (5e-6 plus 1e-12).
- A property that looks broken may be the test's model, not the engine. Before calling it a
  bug, reduce it to one match and check it against the module docstring. A real bug: small
  and in scope, fix it with a test that fails first; otherwise file an issue with a
  synthetic repro and mark the case `pytest.mark.xfail(strict=True, reason="#N")`. Never
  weaken a property to make it pass.
- Margin across game formats or game counts can legitimately cost a winner rating (module
  docstring), so the property holds the format and game count fixed.
- Runtime: keep case counts modest (hundreds of single-match cases, tens of ledgers of about
  40 rows, a few shuffles each).

## Proof

- Criterion 1: the four property tests in `tests/test_properties.py`. The conservation test
  also checks `clamped`: a player is clamped exactly when its unclamped own-K step would
  leave the rating range, and every other player that is not frozen moves by that step.
  Category cases seed varied overall doubles states, so the parent checks see nonzero
  imbalance.
- Criterion 3: the late test seeds players mid-ledger and requires cases that are late only
  because of a frozen player; the weekly verified schedules drive RD to `rd_min` and assert
  it stays there.
- Each strengthened check was shown to fail against an engine mutation (the RD floor
  dropped, `late` ignoring frozen players, `clamped` never set, the parent update computed
  from category states), reverted before commit.
- Criterion 2: the same tests parametrized over singles, plus `tests/test_singles.py` if added.
- Criterion 3: the repeat window, reliability, duplicate, and late tests.
- Criterion 4: `tests/test_examples.py`.
- Criterion 5: `pytest --durations=0` on the new files.
- No live drive: the gate fully covers a test-only change.

Measured on main (commit 9033934), symmetry: a probe over 2000 seeded cases (singles and
doubles, ratings 2.0 to 8.0, RD 0.1 to 0.8, one game) recorded each match and its mirror on
two engines built from the same seed. Every player's rating matched to within 1e-12 (in fact
exactly), every rounded delta matched, and `imbalance` matched. Result: `bad 0`.
