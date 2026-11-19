# Plan: Calibration bins count a prediction at 0.3 twice (issue #130)

Risk tier: Medium. It changes published evaluation output (`EvaluationReport.calibration`) for a prediction that lands exactly on 0.3.

Domain skills: superpowers:test-driven-development, invoke. Framework skills: not applicable, a standard-library Python package.

## Acceptance criteria

1. In `_report_from_predictions` (`src/open_engine/_evaluation.py`), bin `index` (0 to 9) takes the pairs with `index / 10 <= expected < (index + 1) / 10`, and bin 9 also takes `expected == 1.0`. Each edge is computed as `k / 10` for an integer k, so bin k's upper edge is the same float as bin k + 1's lower edge, and every pair lands in exactly one bin.
2. For every scored pair set, the bin counts add up to `matches_scored`.
3. Nothing else in the report changes: `mse`, `mae`, `bias`, `matches_scored`, each bin's lower bound, the 4-place rounding of the bin means, and the rule that only bins with at least one pair are listed. Predictions, ratings, and fits do not change: the fits read the unrounded MSE, not the bins.
4. `docs/model.md` (evaluation section) describes the bins as a partition and drops the note about double counting at 0.3. `CHANGELOG.md`, `## [Unreleased]`, `### Fixed`.

## Files that change

- `src/open_engine/_evaluation.py`: the bin loop in `_report_from_predictions` only.
- A new test file, `tests/test_calibration_bins.py`.
- `docs/model.md`, `CHANGELOG.md`.

## Order of work

1. Red test, committed alone. Build `PrequentialPrediction` lists directly (or patch `_evaluation._prequential`, as `tests/test_calibration.py` does) with `expected` at every edge `k / 10` for k = 0 to 10, at values just below and above each edge (`math.nextafter`), and at 0.3 alone. Assert each pair lands in the bin `k` with `k / 10 <= expected < (k + 1) / 10` (1.0 in bin 9), and that counts sum to `matches_scored`. On `main`, `expected == 0.3` gives two bins.
2. Change the loop to compute `lower = index / 10` and `upper = (index + 1) / 10`, and test `lower <= e < upper or (index == 9 and e == 1.0)`.
3. Docs and changelog. Full gate.

## Risks

- A pair whose bin changes besides 0.3. Mitigation: the edge test covers every k / 10 and its float neighbors, and a scratch check runs both versions over 100,000 random `expected` values in [0, 1] and reports every pair whose bin set changes (only exact edges where `index / 10 + 0.1 != (index + 1) / 10` may differ; record them in the PR).

## Proof

- Measured on `main`: one prediction with `expected = 0.3` gives `calibration == ((0.2, ...,1), (0.3, ..., 1))`.
- The red test and the random comparison above.

## As built

Criterion 3 said the fits do not change. The fitted scale and the `fit_win_calibration` result do not, but `ScaleFit.fitted` and `ScaleFit.at_default` hold whole `EvaluationReport`s, so their calibration tables change the same way as `evaluate`'s. The changelog says so. The bin edges on `main` differed from `k / 10` at two indices: index 2 (`0.30000000000000004`, which put 0.3 in two bins) and index 7 (`0.7999999999999999`, which only a raw value of exactly that float could fall through; a scored `expected` is rounded to 5 places and never is).
