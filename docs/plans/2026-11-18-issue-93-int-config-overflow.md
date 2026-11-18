# Plan: Int Config values can overflow exact int arithmetic in rating code (issue #93)

Risk tier: Medium. It changes existing behavior (some configs that build today will raise `ConfigError`) and is a breaking change to `Config`, but it touches one validation block in one module, changes no rating code, and is fully unit-testable.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-allauth, django-celery, python-fastapi, React and Expo skills: not applicable because this is a standard-library Python package with no web, UI, or framework code.

Decision from the maintainer (counts as plan approval): keep the fix in `Config.__post_init__`, so no rating changes for any config that still builds. Do not change rating code arithmetic. Choose between bounding the int magnitudes and having the largest-K check compute the products the way the engine does (exact ints when all factors are ints) and rejecting on overflow, whichever covers every exact-int product the engine can form with the smallest rule. Raise `ConfigError`.

## Findings from Phase 1

Every place rating, RD, prediction, or evaluation code multiplies or exponentiates Config values that can be ints (`grep` for `cfg.` arithmetic in `src/open_engine`):

1. `RatingEngine._record`: `repeat_factor = 1.0` for every match, and `cfg.self_reported_repeat_factor ** prior` on a self-reported match. The factor is in (0, 1], so the only int factor is `1`, and `1 ** prior` is the int `1`. A float factor gives a float at most `1.0`.
2. `information_multiplier`: `count = 1.0 + game_count_bonus * (n - 1)` is a float (an int bonus is already bounded at `2**511`, issue #70); `length * count` is a float; `min(max_information_multiplier, length * count)` returns `max_information_multiplier` itself, an int when it is one, when it is the smaller value or equal.
3. `_compute`: `effective_weight = match_weights[match_type] * repeat_factor * information_multiplier`. It is an exact int only when the weight, the repeat factor, and the multiplier are all ints: a self-reported match with `self_reported_repeat_factor == 1` (int), an int `match_weights[SELF_REPORTED]`, and an int `max_information_multiplier` that `information_multiplier` returns. On every other match the repeat factor is `1.0`, so the product is `float(w) * float(m)` or smaller, which the current check covers.
4. `_compute`: `raw_delta = sign * player_k(state, cfg) * effective_weight * surprise`. `player_k` always returns a float. With an int `effective_weight`, Python converts it to float: `OverflowError` when it is too large (the issue's repro), or `float(w * m)`, which can be one ulp above `float(w) * float(m)`. Then `K * float(w * m)` can be `inf` where the current check saw a finite step, and a surprise of 0.0 gives NaN, which the clamp turns into `max_rating` with `clamped=()`.
5. `_compute`: `state.rd * (1.0 - cfg.rd_shrink_per_match * effective_weight)`. With an int `rd_shrink_per_match` and an int `effective_weight`, the product is an exact int; `1.0 - product` raises `OverflowError` when it is too large for a float. A float `rd_shrink_per_match` gives at worst `inf`, then `-inf`, and the `max(rd_min, ...)` holds RD at `rd_min`: no error and no NaN (existing behavior for float configs, unchanged).
6. `inflated_rd` (`rd_growth_per_day ** 2 * days`) and `information_multiplier` (`game_count_bonus * (n - 1)`): already bounded as ints by issues #68 and #70.
7. `player_k`, `reliability`, `expected_point_share`, `calibrate_win_probability`, `_actual_point_share`: int true division and int-times-float only, with config values that are already finite floats. No exact int product forms. `round(effective_weight, 5)` of an int stays an int and never converts.

So the only new exact int products are `w * m` (the self-reported weight times the multiplier) and `rd_shrink_per_match * w * m`.

Option A (bound the int magnitudes of `match_weights`, `max_information_multiplier`, and `rd_shrink_per_match`) needs three new bounds and still leaves item 4's one-ulp case: two ints at most `2**511` can still give `float(w * m) > float(w) * float(m)`, so the K check would also have to change. Option B (compute the product the way the engine does and reject on overflow) is one change to the existing largest-K check plus one RD line. Option B is chosen.

## Acceptance criteria

1. No config that builds can make `record` or `replay` raise `OverflowError` from int-to-float conversion.
2. No config that builds can produce a NaN rating step.
3. No rating changes for any config that still builds. No line of rating, RD, prediction, or evaluation code changes.
4. The rejection raises `ConfigError` (an `OpenEngineError`), never a bare `OverflowError`.
5. Defaults and `Config().fingerprint()` (`17f2981186edf96c`) are unchanged.
6. The `Config` docstring, the code comment, the README "Config values" section, and the Unreleased #68/#70 changelog entry no longer say this case is not bounded. A new `**Breaking:**` changelog entry names issue #93, with a "Migration:" note.

## Files that change

- `src/open_engine/engine.py`, `Config.__post_init__` only, the largest-K block: for every `(match_type, weight)` in `match_weights`, form the step weight as `_compute` does at its largest: `weight * repeat * max_information_multiplier`, where `repeat` is `self_reported_repeat_factor ** 0` for `MatchType.SELF_REPORTED` (the int `1` for the int factor `1`, else `1.0`; `factor ** prior` is never larger) and `1.0` otherwise. Check both that value and the float form `_as_float(weight) * _as_float(max_information_multiplier)` (which a float `information_multiplier` just below an int multiplier can reach). Convert with `_as_float` inside `try`/`except OverflowError`; an overflow, or a non-finite `largest_k * value`, raises `ConfigError` with the existing message "k_max times the largest match weight and max_information_multiplier must be finite". When `rd_shrink_per_match` is an int and the step weight is an int, also convert `rd_shrink_per_match * step_weight`; on `OverflowError` raise `ConfigError("rd_shrink_per_match times the largest match weight and max_information_multiplier must be finite")`. Compute on the plain values (`int.__int__` / `_as_float`), not through a subclass's operators. Update the comment above the block and the `Config` docstring bullet. No other code changes.
- `tests/test_int_config_overflow.py` (new).
- `README.md` ("Config values" bullet on the largest K), `CHANGELOG.md` (edit the #68/#70 sentence "With int match weights and multipliers near `2**511` ... (issue #93)", add a `**Breaking:**` entry under `### Changed` with a Migration note).

## Order of work

1. Commit this plan alone.
2. Red test commit: `tests/test_int_config_overflow.py` with the cases below that fail on main (they build today, so `pytest.raises(ConfigError)` fails).
3. Fix in `Config.__post_init__`, docstring, comment. Green.
4. README and CHANGELOG.
5. Full gate.

## Tests (tests/test_int_config_overflow.py)

Rejected (each asserts `pytest.raises(ConfigError, match=...)` and that the error is an `OpenEngineError` and not an `OverflowError` escaping):

- The issue's repro: `w = m = math.isqrt(6 * 2**1024 // 10)`, `k_min=0, k_max=1`, every match weight `w`, `max_information_multiplier=m`, `self_reported_repeat_factor=1`, `rd_shrink_per_match=2`, `game_length_weights={11: int(sys.float_info.max), 15: 1.1, 21: 1.2}`. `w * m` does not convert.
- The ulp case: `w = m = 2**511 + 2**457` (`float(w * w) > float(w) ** 2`), `k_min = k_max = K` where `K` is the largest float with `K * (float(w) * float(m))` finite, factor `1`, `rd_shrink_per_match=0`. On main a self-reported match with equal points (`win_weight=0`, games `(11, 9), (0, 11), (11, 2)`, `best_of=3`) moves all four players to `max_rating` with `clamped=()`.
- The RD case: `w = m = 2**511`, `k_min=0, k_max=1`, factor `1`, `rd_shrink_per_match=4` (int). `w * m = 2**1022` converts; `4 * 2**1022` does not. On main `record` of a self-reported match raises `OverflowError`.
- Only the self-reported weight needs to be int: the same configs with only `match_weights[SELF_REPORTED]` set to the int and the other weights `1.0` are rejected too.

Accepted (still build, and `record` plus `replay` of a self-reported match and a tournament match give finite ratings and RDs with no exception):

- At the bound: `w = m = 2**511`, `k_min=0, k_max=1`, factor `1`, `rd_shrink_per_match=1` (`1 * 2**1022` converts).
- A float `self_reported_repeat_factor` (`0.5`) with the issue's `w`, `m`: the engine never forms the exact int product, so the config builds as before.
- The issue's `w`, `m` as int weights with factor `1` but every non-self-reported weight int and `match_weights[SELF_REPORTED] = 1.0` (float): builds, since no exact int product forms.
- An int `rd_shrink_per_match` with float weights: builds.
- `Config()` builds and its fingerprint is `17f2981186edf96c`.

No rating change for a valid config: a parametrized test over a small grid of int and float configs that build (int and float weights, multiplier, factor, shrink) records a fixed synthetic ledger and compares each `MatchResult` and final state with values pinned from main (computed before the fix, recorded in the test). Since no rating code changes, the comparison is a guard, and the Proof section records a one-off script run against `origin/main`.

## Risks

- Rejecting more than the engine needs: limited by checking only the self-reported weight for the exact int path and only an int shrink for the RD path.
- Message order: a config that fails both this check and the later `self_reported_repeat_factor` range check now gets this message first only if the factor is an int other than `1` (`0 ** 0` and `(-3) ** 0` are `1`, so the product is the same exact int); existing tests show which message they expect.
- A subclass of int or float with its own operators can still make the engine compute something else; that is outside this issue (Config does not store plain values for scalar fields today).

## Proof

Measured on `origin/main` (c263831):

- The issue repro raises `OverflowError: int too large to convert to float` from `engine.record`.
- With `w = m = 2**511 + 2**460*...` (seeded search), `k_min = k_max = 3.9999999999999996`, factor `1`, `rd_shrink_per_match=0`, `win_weight=0`, a self-reported match with equal points returns `surprise=0.0`, every delta `4.5`, every rating `8.0` (`max_rating`), `clamped=()`, `imbalance=9.0`.
