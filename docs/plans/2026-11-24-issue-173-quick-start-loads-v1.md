# Plan: Make the quick start and example load parameter set v1 (issue #173)

Risk tier: Low. Documentation and example only: no engine code, `Config` default, or public API changes, and the existing tests run both the quick start and the example.

Domain skills: none available apply (pure Python package; no web framework, UI, API server, data store, authentication, payments, or email).

## Acceptance criteria

1. The README quick start builds the engine with `RatingEngine(Config.published("v1"))` and imports `Config`.
2. `examples/basic_match.py` builds its config with `Config.published("v1")`. No runnable example in the README quick start or `examples/` builds an engine from `RatingEngine()` with no config or from `Config()`.
3. One sentence right under the quick start code block says that `RatingEngine()` with no config uses the `Config()` placeholders, which carry no declared gap meaning, and that `v1` is the published set. It links the README's "What a rating means" section and `PARAMETERS.md`.
4. Output text in the example stays true under `v1`. The win probability line no longer says "before calibration data", since `v1` sets `win_calibration_by_scoring` (side_out 0.476). The last line also prints `cfg.params_version`.
5. `tests/test_readme.py` and `tests/test_examples.py` pass, and new tests fail on `origin/main` and pass after the change:
   - the quick start's engine, read from the exec namespace, has `cfg == Config.published("v1")`;
   - every script in `examples/` calls `Config.published("v1")` and contains no zero-argument `Config()` or `RatingEngine()` call (checked with `ast`, not string search).
6. `Config()` defaults are unchanged (`src/` is not touched).
7. A `CHANGELOG.md` entry under `## [Unreleased]` records the documentation change.

## Files that change

- `README.md`: quick start block (import `Config`, build the engine with `Config.published("v1")`); one new sentence after the block; the `replay(...)` pointer in the paragraph under the block passes `cfg=Config.published("v1")`.
- `examples/basic_match.py`: `cfg = Config.published("v1")`; the win probability label; the final print adds `params_version`.
- `tests/test_readme.py`: `run_block` returns its namespace; new test that the quick start engine's config is `v1`; new test that every `replay(` call in the quick start section passes `cfg=`.
- `tests/test_examples.py`: new parametrized AST test over `examples/*.py`.
- `CHANGELOG.md`: one bullet under `## [Unreleased]` (a `### Changed` heading).

## Order of work

1. Red: add both new tests. Run them and record the failures (README quick start uses `RatingEngine()`; the example uses `Config()`). Freeze them.
2. Green: change the README quick start and the example. Run the new and existing tests.
3. Add the README sentence and the changelog entry.
4. Full gate: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest -n auto`. Run `python examples/basic_match.py` and show its output.

## Risks

- `test_broken_quick_start_raises` replaces the first `engine.record(` in the README. The new sentence must not put `engine.record(` ahead of the code block. It sits after the block, so the first match stays inside it.
- The new sentence must not overclaim. "Carry no declared gap meaning" matches the README's "What a rating means" text ("do not carry this meaning"). Do not call v1 validated or say it predicts better: the Known limitations section says it is not shown to.
- The README anchor `#what-a-rating-means` is an in-page link, the same form as `#known-limitations` on line 13. The `PARAMETERS.md` link uses the absolute GitHub URL the README already uses.
- Not done: no change to `Config()` defaults (default-change rule, PARAMETERS.md), and no other doc code blocks changed (none build `RatingEngine()`).

## Proof

- Criterion 1, 2: `test_quick_start_uses_v1` and `test_example_loads_v1` (new). Replay pointer (added in review): `replay(` in the quick start section defaulted `cfg` to `Config()`, so the authoritative replay would run on the placeholders while the live engine ran on v1. `test_quick_start_replay_passes_cfg` (new, red first) checks that every `replay(` call in the section, code and prose, passes `cfg=`. Criterion 5: those plus `test_quick_start_runs` and `test_example_runs`.
- Criterion 4: the example output in the gate run shows the new label and `v1`.
- Criterion 6: `git diff --stat origin/main` lists no file under `src/`.

Measured on `origin/main` (be2a0ff) for the "phrase absent" tests:

- `grep -n -E 'RatingEngine\(\)|Config\(\)' README.md examples/*.py` found `README.md:81: engine = RatingEngine()` (the quick start) and `examples/basic_match.py:18: cfg = Config()`. Both new tests must fail there.
- With `cfg = Config.published("v1")` swapped into the example by hand (then reverted), it ran and printed `Team A win probability (before calibration data): 0.638` and `Config 1.0.0 fbee4568a6b0a21c`. So the example runs under v1, and its label needs the change in criterion 4.
