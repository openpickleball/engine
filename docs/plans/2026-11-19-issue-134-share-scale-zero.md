# Plan: expected_point_share raises ZeroDivisionError for a scale of 0 (issue #134)

Risk tier: Medium. A small change, but it alters existing behavior: a negative scale passed straight to the public `expected_point_share` is accepted today and will be rejected.

Domain skills: superpowers:test-driven-development, invoke. Framework skills (Django, FastAPI, React, and the rest): not applicable, a standard-library Python package.

## Acceptance criteria

1. `expected_point_share(a, b, scale)` raises `EvaluationError` with the message `scale must be greater than 0` when `scale` is 0, -0.0, or negative, after the existing type and finiteness checks and before any arithmetic. It never raises `ZeroDivisionError`.
2. Every other input behaves as on `main`: the same results for a positive scale, and the same errors, in the same order, for a wrong type, a non-finite value, or an overflowing rating difference.
3. The rule matches `Config`, which already rejects a scale of 0 or less ("scales must be positive"), so no path through `RatingEngine`, `replay`, or the evaluation functions changes.
4. The `Raises:` section of `expected_point_share` names the new condition, after the type check. `docs/model.md` (expected point share) says the scale must be greater than 0.
5. `CHANGELOG.md` under `## [Unreleased]`, `### Fixed`: the error, and that a negative scale is now rejected (a breaking change for a direct caller who passed one; minor versions may break before 1.0.0).

## Files that change

- `src/open_engine/_math.py`: `expected_point_share` only.
- A new test file, `tests/test_expected_share_scale.py`.
- `docs/model.md`, `CHANGELOG.md`.

## Order of work

1. Red test, committed alone: `scale` in (0, 0.0, -0.0, -1, -2.9, -1e-300) raises `EvaluationError` matching `scale must be greater than 0`; a wrong-type scale (`"1"`, `True`) still raises the type error first; a non-finite scale (`nan`, `inf`) still raises the finiteness error first; an overflowing rating difference with a positive scale (int ratings of 10**308 and -(10**308), scale 1) still raises the overflow error, since a tiny positive float scale gives an infinite exponent that the clamp absorbs rather than an error; a tiny positive scale (1e-300) keeps its result; a positive scale returns the same value as before (compare with a few fixed expected values computed on `main`).
2. Add the check in `expected_point_share` after the type loop: `if scale <= 0: raise EvaluationError("scale must be greater than 0")`.
3. Docs and changelog. Full gate.

## Risks

- Ordering: the new check must run after the type checks, so a wrong-type scale keeps its message. The tests pin the order with a two-failure input (a negative rating of the wrong type plus a scale of 0).

## Proof

- The red test fails on `main` with `ZeroDivisionError` for 0 and with no error for a negative scale, and passes after.
- Measured on `main`: `expected_point_share(4.0, 3.5, 0.0)` raises `ZeroDivisionError: division by zero`; `expected_point_share(4.0, 3.5, -2.9)` returns a share below 0.5.
