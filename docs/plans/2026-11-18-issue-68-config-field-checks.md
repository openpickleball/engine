# Plan: Config field type, finiteness, and range checks (issues #68 and #70)

Risk tier: Medium. The change only adds validation to `Config.__post_init__`, but it rejects
configs that used to build (a changed API contract), touches config provenance (a
security-sensitive area in CLAUDE.md), and spans `engine.py`, tests, README, and CHANGELOG.
Every rule is unit-testable.

Domain skills: superpowers:test-driven-development, invoke. Django, Celery, FastAPI, React,
Expo, and the other framework skills: not applicable because this is a standard-library
Python package with no framework, UI, API, data store, auth, payments, or email.

Issues #68 and #70 ship together in one branch and one PR, as the maintainer asked. The
maintainer's notes on the issues count as plan approval.

## Acceptance criteria

From #68:

1. Every `Config` field given a value of the wrong type raises `ConfigError`. A `bool` is
   not accepted where a number is expected. Ints are required where an int is expected (a
   float such as `7.0` is rejected), bools for flags, and `str` for `version`.
2. Out-of-range `match_weights` and `game_length_weights` values (and keys) raise
   `ConfigError`, as do out-of-range `game_count_bonus` and gap filter values.
3. No existing default changes, and `Config().fingerprint()` stays `17f2981186edf96c`.
4. A scalar value JSON cannot hold (a `Decimal`), a non-finite float, or an int too large
   for a float (such as `10**5000`), in a scalar field, a mapping value, or a
   `game_length_weights` key, raises `ConfigError` at construction, so `fingerprint()`,
   `==`, and `hash()` never raise `TypeError` or `ValueError` on a built config.

From #70:

5. `Config` raises `ConfigError` for a non-finite `min_rating`, `max_rating`, or
   `default_rating` (NaN, inf, -inf).
6. `Config` raises `ConfigError` for a rating range large enough that a team rating sum or
   the expectation can overflow (the #70 repro, `max_rating=1e308`, raises at
   construction).

Both:

7. No rating, prediction, or evaluation output changes for any config that still
   validates. The change is validation only.
8. `Config.from_dict(cfg.to_dict())` round-trips every valid config, also through
   `json.dumps` and `json.loads`.
9. Every new error is a `ConfigError`, and tests assert the type and message.
10. The rules are documented in the `Config` docstring and in the README, and the
    CHANGELOG records the breaking change with a migration note.

## Rules (one set per field)

Definitions used below:

- A **number** is an `int` or a `float`, not a `bool`, that is finite as a float: not NaN,
  not inf, and not an int too large to convert to a float. One helper,
  `_is_config_number`, decides this (it catches the `OverflowError` that
  `math.isfinite` raises on a huge int). The existing `_is_finite_number` is not changed:
  seed and snapshot code use it.
- A **whole number** is an `int`, not a `bool`, that is also a number (so at most about
  1.8e308 in magnitude, which keeps it well under Python's 4300-digit int-to-str limit).
- `_RATING_LIMIT = 2.0 ** 511` (about 1.3e154). Squaring a value this size is finite.
- `_MAX_DAYS = (date.max - date.min).days` (3,652,058). A window this long already covers
  every pair of dates, so a longer one changes nothing, and `timedelta` holds it. It does
  not make every window safe: one that reaches back before `date.min` from a match or
  reference date raises `OverflowError` (issue #92).

| Field | Type | Range |
| --- | --- | --- |
| `version` | `str` | none |
| `min_rating`, `max_rating`, `default_rating` | number | each at most `_RATING_LIMIT` in magnitude; `min_rating < max_rating`; `min_rating <= default_rating <= max_rating` (existing) |
| `scale_by_scoring` | mapping, a value for every `ScoringType` (existing) | values numbers > 0 (existing message) |
| `win_calibration_by_scoring` | same | values numbers > 0 (existing) |
| `k_min`, `k_max` | number | `0 <= k_min <= k_max` (existing), and `(k_min + (k_max - k_min) * 1.0) * (max(match_weights) * max_information_multiplier)` finite in floats |
| `rd_min`, `rd_max` | number | `0 < rd_min < rd_max <= _RATING_LIMIT` (`<` is new, was `<=`), and `rd_max - rd_min > 0` as computed |
| `new_player_rd`, `external_seed_rd`, `category_seed_rd` | number | within `rd_min..rd_max` (existing) |
| `rd_shrink_per_match` | number | `>= 0` (new) |
| `rd_growth_per_day` | number | `0 <= value <= _RATING_LIMIT` (new); an int at most `2**500` |
| `win_weight` | number | `0..1` (existing) |
| `match_weights` | mapping, a value for every `MatchType` (existing) | values numbers > 0 (new) |
| `allowed_game_formats` | unchanged | unchanged |
| `game_length_weights` | mapping, int keys (existing) | keys whole numbers >= 1 (new), values numbers > 0 (new); every allowed format's points has a key (existing) |
| `game_count_bonus` | number | `>= 0` (new); an int at most `2**511` |
| `max_information_multiplier` | number | `>= 1` (existing) |
| `replay_interval_days`, `diversity_window_days`, `self_reported_repeat_window_days` | whole number | `1.._MAX_DAYS` (upper bound new) |
| `import_reliability_max_age_days` | whole number | `0.._MAX_DAYS` (upper bound new) |
| `diversity_target`, `self_reported_max_repeats` | whole number | `>= 1` (existing) |
| `import_reliability_carry_matches` | whole number | `>= 0` (existing) |
| `self_reported_repeat_factor` | number | `(0, 1]` (existing) |
| `established_reliability_threshold` | number | `0..100` (existing) |
| `established_min_verified_matches` | whole number | `>= 0` (existing) |
| `connectivity_reliability_cap` | `None` or number | `0..100` (existing) |
| `connectivity_min_*` (three) | whole number | `>= 1` (existing) |
| `self_reported_requires_both_teams`, `reject_content_duplicates`, `category_feeds_parent` | `bool` | none |
| `max_singles_rating_gap`, `max_doubles_partner_gap` | `None` or number | `>= 0` (new) |

Why each new range is the narrowest the math needs:

- `match_weights` values > 0: the effective weight multiplies the surprise. Zero means the
  match never moves a rating, and a negative weight moves ratings against the result.
- `game_length_weights` values > 0: same reason, through the information multiplier.
  Keys >= 1: `GameFormat.points_to` must be at least 1, so a smaller key can never be read.
- `game_count_bonus >= 0`: the multiplier's count term is `1 + bonus * (games - 1)`, and
  `best_of` has no upper limit, so any negative bonus makes it zero or negative for a long
  enough match.
- Gap filters `>= 0`: they compare against an absolute rating difference, so a negative
  gap rejects every match it applies to.
- Rating bounds at most `2**511` in magnitude (#70): a team rating sum, the team gap
  behind the expectation, the up to four rating changes a match adds for `imbalance`
  (each at most `max_rating - min_rating`), and `net_rating_created` across any
  realistic number of matches all stay finite. `float_max / 2` would cover only the team
  sum and gap, and leaves `imbalance` able to raise `OverflowError`.
- `rd_max` and `rd_growth_per_day` at most `2**511`: `inflated_rd` squares both with `**`,
  which raises `OverflowError` above about 1.3e154 (verified on main with
  `rd_max=1e200`).
- `rd_min < rd_max`: `player_k` and `reliability` divide by `rd_max - rd_min`, so equal
  bounds raise `ZeroDivisionError` on the first match (verified on main).
- `rd_shrink_per_match >= 0`: a negative value grows RD past `rd_max`, and
  `from_snapshot` then rejects the engine's own snapshot (verified on main with `-0.5`).
- `rd_growth_per_day >= 0`: it is squared, so a negative value acts as its absolute value
  under a different fingerprint.
- The `k_max` product: when `k * effective_weight` overflows to inf and a match's surprise
  is exactly 0, the change is NaN, and the clamp turns NaN into `max_rating` (verified on
  main: all four players jumped to 8.0 on a 0.0 surprise).
- Day counts at most `_MAX_DAYS`: `timedelta(days=...)` raises `OverflowError` above
  999,999,999, and no window longer than the whole date range changes a result.

Review round (added after the first green commit):

- The `k_max` product is computed in floats, as the engine computes it. An all-int product
  is exact, and `math.isfinite` raises `OverflowError` on one too large for a float.
- Two bounds apply to ints only, because exact int arithmetic raises `OverflowError` where
  float arithmetic goes to inf. An int `rd_growth_per_day` is at most `2**500`, so
  `rd_max**2 + growth**2 * _MAX_DAYS` stays below the float maximum with `rd_max <= 2**511`.
  An int `game_count_bonus` is at most `2**511`; games minus 1 is below `2**63`, so the int
  product stays below `2**574`. A float keeps the `2**511` growth bound and has no bonus
  bound beyond being finite: it goes to inf, and the `min` with
  `max_information_multiplier` or `rd_max` catches it.
- `rd_max - rd_min > 0` as computed: an int and a float equal once converted give 0.0 and
  divide by zero in `player_k`.
- `allowed_game_formats` points use the whole-number rule (finite as a float), so a huge
  int raises the entry message instead of the int-to-str `ValueError`. `from_dict` shows
  a value too large to print as "a value too large to show".
- Keys and format entries are stored as their own types: enum members for the enum-keyed
  mappings and for format scoring types, plain ints for game lengths and points. A config
  that builds on main keeps its fingerprint (tested against values measured on main), except
  a key or points value whose `str()` differs from its value (a str or int subclass with its
  own `__str__`). Ratings do not change in those cases.

Second review round:

- The K product uses the largest K `player_k` returns, `k_min + (k_max - k_min) * 1.0`,
  which can round one ulp above `k_max`. With `k_max` alone, a config at the edge still
  overflowed the step and sent every player to `max_rating` on a 0.0 surprise.
- Ints are converted with `int.__int__`, which gives the int's own value. `int()` follows an
  overridden `__int__`, which changed the stored value, the fingerprint, and which matches
  count versus main.
- Two `game_length_weights` keys that convert to the same int raise "game_length_weights
  repeats the game length N", as `from_dict` does. The enum-keyed mappings cannot repeat a
  key after the set-equality check. Their `except ValueError` ("has an unknown key") stays:
  it is reachable with a str subclass that equals the enum member but not its plain str
  value, and a test covers it.
- Ints are judged and converted by their own value (`int.__float__`, `float.__float__`),
  never a subclass's `__float__`, in the number check and the K product.
- `_MAX_DAYS` is what `timedelta` holds. A window that reaches back before `date.min` from a
  match or reference date still raises `OverflowError`; issue #92 tracks it. The stress test
  uses 365-day windows for that reason.

Message rules: keep every existing message. A type or finiteness failure on a scalar says
`"<name> must be a finite number"`, `"<name> must be a whole number"` (int fields),
`"<name> must be a bool"`, `"<name> must be a str"`, or
`"<name> must be None or a finite number"`. Mapping values keep
`"<name> values must be finite numbers"`. New range failures name the field and the bound.

Departure found while implementing: existing tests match a combined message for a type or
finiteness failure in six fields, so those fields keep it for type failures too:
`established_reliability_threshold` (NaN gives "must be between 0 and 100"),
`established_min_verified_matches` ("must be a nonnegative int"),
`connectivity_reliability_cap` ("must be None or a number from 0 to 100"), and the three
connectivity counts ("must be a positive int"). Every other field uses the messages above.

## Files that change

- `src/open_engine/engine.py`: `Config.__post_init__` rewritten as one pass: type and
  finiteness checks for every scalar field first (driven by a small table of field name
  to kind), then the mapping checks, then range and cross-field checks. New module
  helpers `_is_config_number`, `_RATING_LIMIT`, `_MAX_DAYS`. `Config` docstring: replace
  the "other scalar fields are not yet" paragraph with the rules. No change to
  `fingerprint`, `_payload`, `to_dict`, `from_dict`, or any rating code.
- `tests/test_config_checks.py` (new): the tests below.
- `tests/test_config_checks_review.py`, `tests/test_config_checks_review2.py`, and `tests/test_config_float_override.py` (new, from the review rounds): the frozen tests for each review finding.
- `tests/test_config_immutable.py`: the `("k_min", "low")` row of
  `test_from_dict_rejects_bad_data` matched `"Config"` (the wrapped `TypeError`); it now
  matches `"k_min must be a finite number"`.
- Any other existing test that builds a config the new rules reject: fix the test's
  config only if it was incidental, and say so in the commit.
- `README.md`: a short "Config values" section listing the rules.
- `CHANGELOG.md`: a `**Breaking:**` entry under `### Changed` with a Migration note; edit
  the `Config` hashable entry under `### Added` that says "issue #68 adds type checks".

## Order of work

1. Red: write `tests/test_config_checks.py`, run it, confirm each new test fails on the
   current code for the expected reason, commit it alone.
2. Green: rewrite `__post_init__` and add the helpers. Run the new tests, then the full
   suite. Fix incidental test configs.
3. Docs: `Config` docstring, README, CHANGELOG.
4. Gate.

## Risks

- An existing test or example builds a config the new rules reject (for example equal RD
  bounds or a float day count). Run the full suite and the examples.
- Check order changes which message a config with several problems gets. Only the type
  must be checked before any comparison that uses the field, so no `TypeError` escapes.
- `from_dict` with a JSON round trip: a float field stored as an int stays an int, which
  is still a number. A whole-number field stored as `7.0` is now rejected, which is
  correct: `to_dict` never writes it that way for a valid config.

## Proof

- Criterion 1: parametrized test over every scalar field with wrong-type values (`"x"`,
  `None` where not allowed, `True` for numeric fields, `Decimal("1")`, `1.0` for int
  fields, `1` for bool fields, `1` for `version`); each raises `ConfigError` with the field
  name in the message. A test asserts `issubclass` and that it is not a bare `TypeError`.
- Criterion 2: parametrized tests for `match_weights` values 0 and -1,
  `game_length_weights` values 0 and -1 and keys 0 and -11, `game_count_bonus=-0.01`,
  each gap filter at -0.1, and the accepted boundaries (gap 0.0, bonus 0.0).
- Criterion 3: `Config().fingerprint() == "17f2981186edf96c"`, and `Config()` field
  values equal a literal of the documented defaults.
- Criterion 4: `Decimal`, NaN, inf, and `10**5000` in scalars, mapping values, and a
  `game_length_weights` key raise `ConfigError`.
- Criteria 5 and 6: NaN, inf, -inf in each rating bound; `max_rating=1e308,
  default_rating=1e308` raises; `2.0**511` as `max_rating` is accepted and a doubles match
  at that rating records with a finite `expected_a` and `imbalance`.
- Criterion 7: the full existing suite stays green (property tests and replay parity
  included); the RD, K, and NaN bugs each get a test of the new `ConfigError`.
- Criterion 8: round trip of `Config()` and several valid non-default configs (ints in
  float fields, gap filters set, bonus 0.0, the largest rating bound) through
  `to_dict`, `json`, and `from_dict`, with equal fingerprints.
- Criterion 9: every new test uses `pytest.raises(ConfigError, match=...)`.

Measured on main (3845bec), with `scratchpad/repro.py`:
`Config(k_min="x")` raises `TypeError`; `match_weights` of -1.0, `max_singles_rating_gap="wide"`,
`max_information_multiplier=inf`, `max_rating=inf`, `min_rating=-inf`, `k_max=True`,
`diversity_window_days=365.0`, and `rd_min == rd_max` are accepted;
`Config(game_count_bonus=Decimal("0.12")).fingerprint()` raises `TypeError`;
`game_length_weights={11: 10**5000, ...}` then `fingerprint()` raises `ValueError`;
recording a doubles match under `max_rating=1e308, default_rating=1e308` raises
`OverflowError: intermediate overflow in fsum`.
