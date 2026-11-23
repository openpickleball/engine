# Plan: Settle the last API decisions and add known limitations before 1.0.0 (issue #167)

Risk tier: Medium. It changes a public API contract frozen at 1.0.0 (the declared type and stored value of three output fields), which raises Low one tier. The rest is documentation. Recorded without a human gate: the owner delegated plan approval for this run.

Domain skills: none applicable. This is a standard library Python package with no framework, UI, API server, data store, auth, payments, or email.

## Acceptance criteria

1. `EvaluationReport.scoring` is annotated `ScoringType | None`, and `ScaleFit.scoring` and `WinCalibrationFit.scoring` are annotated `ScoringType`. `evaluate`, `fit_scale`, and `fit_win_calibration` store the `ScoringType` member itself (`is` the member passed in), and `None` when `evaluate` gets no scoring.
2. The stored value still compares equal to its str value (`fit.scoring == "side_out"`), formats as the value in an f-string and `str()`, and `json.dumps` gives the str value. Existing tests that compare with the str keep passing.
3. `tests/api_surface.txt` records the new annotations, regenerated with `python tests/test_api_surface.py`, and `CHANGELOG.md` has a Changed entry marked breaking.
4. The README "Public API" section, `docs/behavior.md` "Stability", and the `RatingEngine.player()` and `state()` docstrings say these return the engine's own objects, not copies (for `state()`, the stored state until a later update replaces it with a new object; a starting state for a format with no stored state is a new copy), and that changing a `Player` or `RatingState` in place is unsupported and outside the stability promise. `MatchResult` is new output for each call.
5. `docs/behavior.md` "Stability" and `docs/parameters.md` say a published parameter set's values never change, retuning publishes a new label, and a change to a `Config()` default comes in a minor release.
6. The README has a "Known limitations" section in plain language with the evaluation's simulation results: fixed partners share one rating (weaker partner about 0.8 too high when the true gap is 1.6), sandbagging is not detected and persists after 60 honest matches in most runs, colluding accounts can inflate one player (four accounts, about +3.5 under `v1`) with no cap, closed groups reach "established", and `v1` makes the sandbagging and collusion effects 1.9 to 2.6 times larger than `Config()`. It also says `v1` showed no measurable accuracy gain over the defaults on held-out matches and that the test on future play is still pending. It names no external rating service and no player, and cites the evaluation only in general terms. `docs/model.md` links it.
7. No parameter value, the v1 fingerprint, `snapshot_version`, or the package version changes. No snapshot or `to_dict` path serializes these three types, so serialization does not change.

## Files that change

- `src/open_engine/_evaluation.py`: the three annotations; `_report_from_predictions` stores `scoring` (not `.value`); `fit_scale` and `fit_win_calibration` store `scoring` (not `.value`).
- `src/open_engine/_engine.py`: `player()` and `state()` docstrings.
- `tests/test_calibration.py` (or a new focused test module): tests for criteria 1 and 2 on all three types.
- `tests/api_surface.txt`: regenerated.
- `CHANGELOG.md`, `README.md`, `docs/behavior.md`, `docs/parameters.md`, `docs/model.md`.

## Order of work

1. Red test: the three `scoring` fields are the `ScoringType` member (`is`), with `==` str, `str()`, f-string, and `json.dumps` checks.
2. Change `_evaluation.py`; regenerate `tests/api_surface.txt`.
3. Docstrings for `player()` and `state()`.
4. Docs: Stability, Public API, parameters, Known limitations, model link, CHANGELOG.
5. Full gate.

## Risks

- A caller that checks `type(report.scoring) is str` breaks. That is the intended break, called out in the changelog.
- The limitations numbers are in `Config()` units (the simulations' truth scale) except where marked. Under `v1` a rating gap is 2.9 times smaller for the same expected share, so the text says which scale each number is on, or states it relative to the true gap.
- The issue names "about 285 held-out matches" for v1 against the defaults. In the evaluation, 285 is the main test set for an earlier tuned config; the default-change comparison of v1 against `Config()` used 1,018 pooled held-out matches (PARAMETERS.md). The README uses the PARAMETERS.md comparison, which is about v1 itself.

## Proof

- Criteria 1 and 2: new test, red before the change.
- Criterion 3: `tests/test_api_surface.py`.
- Criteria 4 to 7: doc review; `tests/test_api.py` and `tests/test_docstrings.py` keep passing; v1 fingerprint and snapshot tests unchanged.
- Measured on origin/main: `fit_scale(...).scoring` is the plain str `"side_out"` (`type(...) is str`), so the new `is` test is red there.
