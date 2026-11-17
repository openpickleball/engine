# Plan: Replay results differ in the last bit across Python versions (issue #40)

Risk tier: Medium. Determinism is a security-sensitive area, and the change alters existing float results in the last bit, but it is mechanical (swap `sum()` for `math.fsum()` on float sums) and fully unit-testable.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-celery, django-allauth, python-fastapi, React and Expo skills: not applicable because this is a standard-library-only Python package with no framework, API layer, or UI.

## What main does today

Measured on `origin/main` (2f472d4) with a synthetic ledger (400 doubles matches among 40 invented players, `random.Random(7)`, one game each), run with `uv run --python 3.11|3.12|3.13`:

- Replay: the snapshot JSON hash and every rating and RD (`float.hex`) are identical on 3.11, 3.12, and 3.13. Every float sum on the rating path adds at most two terms (a team has one or two players), and Neumaier summation of two floats gives the same result as plain addition. The rating differences the issue saw at 1371165 do not reproduce on current main.
- `fit_scale`: `ScaleFit.profile` differs between 3.11 and 3.12/3.13 in the last digits of 26 of 27 entries (for example `0.08937702183383686` on 3.11, `0.08937702183383685` on 3.13). The fitted scale, the rounded reports, and `at_bound` are identical.
- `fit_win_calibration`: `brier` differs (`0.2508432845023298` on 3.11, `0.25084328450232984` on 3.13).
- 3.12 and 3.13 agree with each other.

So the cause holds: the built-in `sum()` of floats changed in 3.12 to compensated summation.

## Acceptance criteria

1. Every `sum()` over floats in `src/open_engine/engine.py` and `src/open_engine/winprob.py` uses `math.fsum()`. Integer sums (points, games, counts) stay as they are.
2. The same synthetic ledger, config, and `as_of` give byte-identical ratings, RD values, and `ReplayInfo` on Python 3.11, 3.12, and 3.13.
3. `evaluate`, `fit_scale` (including the unrounded `profile`), `fit_win_calibration`, and `predict_win_probability` give identical results on those versions.
4. A test pins exact replay, evaluation, fit, and win probability output (`float.hex`) for a small synthetic ledger, and runs on every CI Python version. It fails on 3.11 before the fix.
5. `CHANGELOG.md` records the change: ratings and RDs do not change (an `fsum` of two floats equals their plain sum), while win probabilities, evaluation errors, `ScaleFit.profile`, and Brier scores can move in the last bit on any version.
6. CLAUDE.md's Determinism rule names the summation rule.

## Files that change

- `src/open_engine/engine.py`: team averages in `predict_point_share` and `_compute`, the `imbalance` sum, calibration bins, `mse`, `mae`, `bias` in `_report_from_predictions`, and the Brier score in `fit_win_calibration` use `math.fsum`. `math` is already imported.
- `src/open_engine/winprob.py`: `_first_to`, the direct term in `game_win_probability`, and `match_win_probability` use `math.fsum`. The `_capped` DP is an explicit ordered loop and stays.
- `tests/test_float_summation.py` (new): the pinned cross-version test. Criterion 1 is checked by review and grep, not by a test.
- `CHANGELOG.md`: a `### Fixed` entry under `## [Unreleased]`.
- `CLAUDE.md`: one sentence in the Determinism rule.

## Order of work

1. Commit this plan.
2. Write the pinned test with values computed on the fixed code; confirm it fails on 3.11 against main's code.
3. Switch the sums; run the test on 3.11, 3.12, and 3.13; run the gate.
4. Changelog and CLAUDE.md.

## Risks

- `fsum` can differ from the old result in the last bit on 3.12+ too (Neumaier is not always correctly rounded), so stored evaluation output can change. Ratings cannot, since every rating-path sum has at most two terms.
- `fsum` is slower than `sum`. The sums are short (at most 11 terms in win probability, one pass over predictions in evaluation), so the cost is small.
- The pinned values depend on `math.exp`, `math.log`, and `**`, which come from the platform C library. CI runs on Linux x86_64 like the local runs; a different libm could differ. That is outside this issue.

## Proof

- Criterion 1: review of the diff, plus `grep -n "sum(" src/open_engine` showing only integer sums left.
- Criteria 2 to 4: `tests/test_float_summation.py`, run locally with `uv run --python 3.11`, `3.12`, `3.13`, and in CI on all three.
- Measured on the default branch: the run described under "What main does today".
