# Plan: Type-check the engine with mypy in CI (issue #3)
Risk tier: Low. Tooling and annotation-only fixes; no runtime behavior changes, and the existing gate (pytest plus the new mypy step) covers everything.
Domain skills: none applicable (no framework, UI, API, data, auth, payments, or email surface; plain Python package).

## Acceptance criteria
1. `pyproject.toml` lists `mypy` in the `dev` extra and has a `[tool.mypy]` section with `strict = true`.
2. `mypy src` (run from the repo root, so it picks up `[tool.mypy]`) exits 0 with "Success" on Python 3.10, 3.11, 3.12, and 3.13.
3. No runtime behavior changes: the full existing test suite passes unchanged, and no test file is edited.
4. `.github/workflows/test.yml` runs `mypy src` as its own step after "Lint" and "Check formatting" and before "Test".
5. The "Setup and tests" section of `README.md` lists `mypy src` with the other check commands.
6. `ruff check .` and `ruff format --check .` pass.

## Files that change
- `pyproject.toml`: add `"mypy>=1.13"` to `dev`; add `[tool.mypy]` with `strict = true`.
- `src/open_engine/engine.py`: fix the 14 strict errors (list below).
- `.github/workflows/test.yml`: add a "Type check" step running `mypy src`.
- `README.md`: add `mypy src` to the commands block in "Setup and tests".
- `CHANGELOG.md`: an entry under `[Unreleased]` (Phase 8 decides).

## Order of work
1. pyproject changes; reinstall the dev extra; confirm `mypy src` reports the 14 errors below.
2. Fix each error in `engine.py` without changing behavior:
   - L290 `Config.fingerprint.norm`: annotate `def norm(value: object) -> object:` (or `Any`); this also clears the four `no-untyped-call` errors at L294-301.
   - L431 `ledger_order -> tuple`: give the precise type `tuple[date, time, int, str]` (`Match.sequence` is `int`). It is exported, so keep it accurate.
   - L555 `_matchup_key -> tuple` and L572 `self_reported_log: dict[tuple, ...]`: define one alias (for example `MatchupKey = tuple[RatingFormat, frozenset[frozenset[str]]]`) and use it in both places.
   - L470 `10.0**exponent` and L497 `0.5 ** (...)`: mypy types float**float as Any. Wrap in `float(...)`, which is an identity on a float and keeps numerics bit-identical. Do not switch to `math.pow`.
   - L821 `primary_updates` is `dict | None`: narrow it so the call type-checks without changing control flow (for example `if not primary.counted or primary_updates is None: return primary`; `_compute` only returns None together with an uncounted result, so this is equivalent; confirm by reading `_compute`).
   - L823 `parent_format` is `RatingFormat | None`: change the guard to `if parent_updates and parent_format is not None:` (parent_updates is only set when parent_format is not None, so equivalent).
   - L852 `match.played_on <= work[pid].initial_source_date` in a comprehension: mypy cannot narrow across the `and`. Bind the attribute once (walrus `(src := work[pid].initial_source_date) is not None and match.played_on <= src`) or a small loop; same semantics.
   - L962 `player_summary -> dict`: use `dict[str, Any]` (or a tighter value type if every value shares one), importing `Any` from `typing`.
3. Run `mypy src` on 3.10 and 3.13 interpreters (uv venvs) plus the 3.12 dev venv; all must succeed.
4. Workflow step and README line.
5. Full gate.

## Risks
- A type fix that silently changes behavior (riskiest: the L821 narrowing and the float wrapping). Mitigation: each fix is equivalence-preserving by construction, and the full suite must pass with no test edits.
- mypy versions differ in what they report; a future mypy may flag new errors. Accepted: CI installs the latest matching version, same as ruff.
- Tightening the exported `ledger_order` annotation could surface errors in downstream type-checked users only if their code was already wrong.
- Not doing: type-checking `tests/` or `examples/` (issue scopes `mypy src`), pinning an exact mypy version, adding `python_version` to `[tool.mypy]` (CI matrix covers each version).

## Proof
- Criterion 1, 4, 5: inspect the diff.
- Criterion 2: `mypy src` output "Success: no issues found in 3 source files" under Python 3.10, 3.12, and 3.13 venvs locally (3.11 if available), and the CI matrix on the PR.
- Criterion 3: `pytest` full suite, same pass count as main (65 passed), `git diff --stat tests/` empty.
- Criterion 6: `ruff check .` and `ruff format --check .`.
- Live drive: not needed; the change is a type-check gate whose run is itself the evidence, plus `python examples/basic_match.py` runs as a smoke check.
- Measured on origin/main: `mypy --strict src` (mypy 2.4.1) reports 14 errors in engine.py, same set with `--python-version 3.10` and `3.13`. `pytest`: 65 passed.

### Red: `mypy src` after the pyproject change, before any engine.py fix (mypy 2.4.1, Python 3.12)
```
src/open_engine/engine.py:290: error: Function is missing a type annotation  [no-untyped-def]
src/open_engine/engine.py:294: error: Call to untyped function "norm" in typed context  [no-untyped-call]
src/open_engine/engine.py:296: error: Call to untyped function "norm" in typed context  [no-untyped-call]
src/open_engine/engine.py:298: error: Call to untyped function "norm" in typed context  [no-untyped-call]
src/open_engine/engine.py:301: error: Call to untyped function "norm" in typed context  [no-untyped-call]
src/open_engine/engine.py:431: error: Missing type arguments for generic type "tuple"  [type-arg]
src/open_engine/engine.py:470: error: Returning Any from function declared to return "float"  [no-any-return]
src/open_engine/engine.py:497: error: Returning Any from function declared to return "float"  [no-any-return]
src/open_engine/engine.py:555: error: Missing type arguments for generic type "tuple"  [type-arg]
src/open_engine/engine.py:572: error: Missing type arguments for generic type "tuple"  [type-arg]
src/open_engine/engine.py:821: error: Argument 2 to "_commit" of "RatingEngine" has incompatible type "dict[str, RatingState] | None"; expected "dict[str, RatingState]"  [arg-type]
src/open_engine/engine.py:823: error: Argument 1 to "_commit" of "RatingEngine" has incompatible type "RatingFormat | None"; expected "RatingFormat"  [arg-type]
src/open_engine/engine.py:852: error: Unsupported operand types for <= ("date" and "None")  [operator]
src/open_engine/engine.py:852: note: Right operand is of type "date | None"
src/open_engine/engine.py:962: error: Missing type arguments for generic type "dict"  [type-arg]
Found 14 errors in 1 file (checked 3 source files)
```

### Green: `mypy src` after the fixes (mypy 2.4.1)
- Python 3.10.12: `Success: no issues found in 3 source files`
- Python 3.11.14: `Success: no issues found in 3 source files`
- Python 3.12 (.venv): `Success: no issues found in 3 source files`
- Python 3.13.12: `Success: no issues found in 3 source files`

`_compute` was read to confirm the L821 narrowing: every `None` return pairs with an uncounted `MatchResult`, and the only counted return supplies a dict.
