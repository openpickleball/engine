# Plan: Replay depends on input order for duplicate match IDs and ignores time zones (issue #9)

Risk tier: High. Replay determinism and provenance is a security-sensitive area (CLAUDE.md hard rule 7), the change alters the published ledger order, and it breaks the public API (a naive `played_at` is now rejected and `ledger_order` returns a new tuple shape).

Domain skills: none apply. This is a standard-library math package with no web, API, data store, authentication, payments, or email layer. The implementer uses superpowers:test-driven-development.

Decisions taken with the maintainer before planning:

- A naive `played_at` is rejected (breaking change), not assumed to be UTC.
- Within one `played_on` date, untimed matches come first (by `sequence`, then `match_id`), then timed matches (by UTC instant, then `sequence`, then `match_id`). No warning is emitted.
- Duplicate handling lives in one helper used by both `replay()` and `prequential_predictions()` (which also feeds `evaluate` and `fit_scale`), and it checks every row passed in, including rows after `as_of`.

## Acceptance criteria

1. `replay(ledger)` equals `replay(shuffled(ledger))` for any permutation, including ledgers with duplicate IDs (property test). "Equals" means the same rating state for every player and format (rating, rd, matches played) and the same `ReplayInfo`.
2. Conflicting duplicate IDs raise a clear, typed exception: `LedgerConflictError`, a subclass of `ValueError`, exported from `open_engine`. Its message names the conflicting `match_id` values, and its `match_ids` attribute is the sorted tuple of every conflicting ID, so the error is the same whatever the input order.
3. Rows with the same `match_id` that compare equal (`Match.__eq__`) are collapsed to one row. `ReplayInfo.matches_considered` counts the collapsed row once.
4. Ordering across time zones follows the UTC instant: of two matches on the same `played_on`, one at 21:00 UTC-5 (02:00Z the next day) sorts after one at 22:00 UTC.
5. A naive `played_at` (no tzinfo, or a tzinfo whose `utcoffset()` is None) is rejected with `ValueError` by `Match.validate()` and by `ledger_order()`. `ledger_order` must never call `astimezone()` on a naive value, since that reads the host's local time zone.
6. Within one `played_on`, untimed matches sort before timed ones; untimed by `sequence` then `match_id`; timed by UTC instant, then `sequence`, then `match_id`.
7. `prequential_predictions()` applies the same duplicate collapse and conflict error, and its output is the same for any permutation of its input.
8. The ordering rules are documented in the README and in the `engine.py` module docstring.

## Files that change

- `src/open_engine/engine.py`
  - Add `class LedgerConflictError(ValueError)` with a `match_ids: tuple[str, ...]` attribute, near `ledger_order`.
  - Add a private helper `_require_aware(played_at: datetime) -> None` raising `ValueError("played_at must be timezone-aware")` when `played_at.tzinfo is None or played_at.utcoffset() is None`.
  - `Match.validate()`: call `_require_aware` before the existing `played_at.date() != played_on` check. That check stays: `played_on` is the local calendar date at the venue, and `played_at.date()` of an aware datetime is its local date.
  - `ledger_order()`: return `tuple[date, int, datetime, int, str]`: `(played_on, 0, _UTC_MIN, sequence, match_id)` for an untimed match, `(played_on, 1, played_at.astimezone(timezone.utc), sequence, match_id)` for a timed one, where `_UTC_MIN = datetime.min.replace(tzinfo=timezone.utc)`. Call `_require_aware` first. Update the docstring to state the rule. Drop the `time` import if it becomes unused.
  - Add a private helper `_ledger(matches: Iterable[Match]) -> list[Match]`: group rows by `match_id` in a dict, keep one row per id when all rows for it compare equal, collect every id whose rows differ, raise `LedgerConflictError` with the sorted ids if any, otherwise return the unique rows sorted by `ledger_order`.
  - `replay()`: iterate `_ledger(matches)` instead of `sorted(matches, key=ledger_order)`. Update its docstring: identical duplicate rows are collapsed, conflicting ones raise `LedgerConflictError`.
  - `prequential_predictions()`: `ordered = _ledger(matches)`. The scheduled mode's inner `replay(...)` call receives already unique rows, so it does no extra harm.
  - Module docstring: add a "Ledger order" item to the published behavior notes stating the order rule, the time zone requirement, and the duplicate rule.
- `src/open_engine/__init__.py`: import and export `LedgerConflictError` in `__all__` (alphabetical position).
- `tests/test_engine.py`: new tests (see Proof). Update `test_same_day_order_uses_time_not_id` to use aware datetimes.
- `examples/basic_match.py`: make its `played_at` aware (for example `tzinfo=timezone.utc` or a fixed offset). Run it to confirm.
- `README.md`: add a short "Ledger order" section after "Quick start" with the rules in plain sentences.
- `CHANGELOG.md`, under `## [Unreleased]`:
  - Added: `LedgerConflictError`.
  - Changed (breaking): `Match.played_at` must be timezone-aware; `ledger_order` orders timed matches by UTC instant, places untimed matches first within a day, and returns `tuple[date, int, datetime, int, str]`.
  - Fixed: `replay` and `prequential_predictions` no longer depend on input order when the ledger repeats a `match_id`; identical rows are collapsed (so `matches_considered` counts them once) and conflicting rows raise `LedgerConflictError`.

## Order of work

1. Red: add the tests below to `tests/test_engine.py` and run them; confirm each new test fails on the current code for the expected reason. Commit them alone (`git commit -s`) and add them to `.claude/fix-lock`.
2. Green, engine: `LedgerConflictError`, `_require_aware`, `Match.validate`, `ledger_order`, `_ledger`, `replay`, `prequential_predictions`. Export from `__init__.py`. Update the existing same-day test and the example to aware datetimes.
3. Docs: module docstring, README section, CHANGELOG entries.
4. Full gate: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`, then `python examples/basic_match.py`.

## Risks

- Riskiest step: the new `ledger_order` key. A wrong tuple position silently changes rating order. The tests pin the issue's exact example, the untimed-first rule, and the `sequence` and `match_id` tie breaks.
- Comparing an aware datetime with a naive one raises `TypeError`. `_require_aware` runs inside `ledger_order`, so a naive row raises a clear `ValueError` before any comparison.
- `Match.__eq__` compares `played_at` by instant. Two rows with the same id, the same instant, and different offsets compare equal only if every other field (including `played_on`) is equal, and then they order identically, so collapsing them is safe.
- `matches_considered` drops for ledgers that repeat a row. That is a visible provenance change; it is recorded in the changelog.
- Mypy strict: the new return type and the `datetime.min` sentinel must type-check. `__eq__` comparisons between `Match` values are fine for frozen dataclasses.
- Not done: a warning for days that mix timed and untimed matches, `hypothesis` as a dev dependency (seeded `random.Random` shuffles are enough and keep the dev set unchanged), and duplicate handling in the live `RatingEngine.record` path (it already rejects a repeated id with "duplicate match_id", which is correct for live ingestion).

## Proof

Tests in `tests/test_engine.py`, all on synthetic players:

| Test | Criterion |
| --- | --- |
| `test_replay_is_permutation_invariant`: a generated ledger of about 40 doubles and singles matches over several days, mixing untimed rows, timed rows in at least three UTC offsets, repeated `sequence` values, and identical duplicate rows; compare the snapshot of `replay(ledger, as_of=...)` (every player, format: rating, rd, matches_played; plus `replay_info`) against 50 `random.Random(seed).shuffle` permutations | 1, 3 |
| `test_conflicting_duplicate_ids_raise`: the issue's `dup` rows (11-0 and 0-11) in both input orders raise `LedgerConflictError` with `match=` on the id; `isinstance(err, ValueError)`; with two conflicting ids, `match_ids` is the same sorted tuple for both input orders | 2 |
| `test_conflicting_duplicate_after_as_of_raises` | 2 |
| `test_identical_duplicates_collapse`: `replay([m, m])` equals `replay([m])` and `matches_considered == 1` | 3 |
| `test_order_follows_utc_instant`: the issue's 21:00 UTC-5 versus 22:00 UTC example | 4 |
| `test_naive_played_at_rejected`: parametrized over `Match.validate` and `ledger_order`, `pytest.raises(ValueError, match="timezone-aware")` | 5 |
| `test_untimed_before_timed_same_day` and a tie-break test for equal instants by `sequence` then `match_id` | 6 |
| `test_prequential_is_permutation_invariant` and `test_prequential_conflict_raises` | 7 |

Criterion 8 is checked by reading the README and module docstring in review. Live drive (library): run `python examples/basic_match.py` and a scratch script that replays the issue's ledgers in both orders and prints the result.

Measured on `origin/main` (34fa1f9) with a scratch script, `python proof.py`:

```
dup order [((11, 0),), ((0, 11),)] 3.725
dup order [((0, 11),), ((11, 0),)] 3.275
tz order ['m1', 'm2'] (m2 earlier in UTC)
mixed order ['u', 't']
naive allowed: True
```

So on main a duplicate id depends on input order, the UTC-5 match sorts before the earlier UTC match, an untimed match already sorts before a timed one on the same day (criterion 6 keeps that), and a naive `played_at` validates.
