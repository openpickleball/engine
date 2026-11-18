# Plan: Match checks trust Match and tuple subclass reads (issue #109)

Risk tier: High. Forced: match validation guards the ledger's determinism and duplicate protection (CLAUDE.md hard rule 7), and the change breaks the public API by rejecting inputs that were accepted.

Domain skills: none applicable (pure Python package, no framework, UI, API, data store, auth, payments, or email).

Decision (recorded on #109): exact types.

## Acceptance criteria

1. The issue's reproduction (a `Match` subclass whose `match_id` property changes between reads) raises `InvalidMatchError` before any state change in `record`, `replay`, and `prequential_predictions` (both modes, and so `evaluate`, `fit_scale`, `fit_win_calibration`).
2. In `replay` and `prequential_predictions`, such a row raises wherever it sits in the ledger (first, middle, last, after `as_of`).
3. `team_a` and `team_b` must be exactly `tuple`; a `tuple` subclass raises `InvalidMatchError`.
4. `confirmed_by` must be exactly `set` or `frozenset`; a subclass raises `InvalidMatchError`.
5. Each `confirmed_by` element follows the team player id rule (exact non-blank `str`); anything else raises `InvalidMatchError`.
6. `games` must be exactly `tuple` or `list`, each game exactly `tuple` or `list`, and each score exactly `int` (a list subclass can override `__iter__` and give validation one sequence and rating another).
7. `Match.validate` itself raises `InvalidMatchError` for a subclass instance that does not override it.
8. Plain `Match` objects behave exactly as before (full suite unchanged).

## Files that change

- `src/open_engine/engine.py`: `Match.validate`, `Match._validate_field_types`, a new `_validate_match(match)` helper (type check, then `validate`), used in `RatingEngine._record`, `_ledger`, and `prequential_predictions`'s `step`. `Match` docstring.
- `tests/test_match_exact_types.py`: new.
- `tests/test_match_digest_strict.py`: its "list member int vs int subclass" case is now an invalid match, so it becomes "list member list vs tuple", a case that still validates and still differs inside a list (revision during implementation).
- `CHANGELOG.md`: a `**Breaking:**` entry under Changed, with Migration.
- `README.md`: the `InvalidMatchError` entry and the Match type rules.

## Order of work

1. Commit this plan.
2. Commit the red tests.
3. Implement, run the gate.

## Risks

- Internal code or tests that build a `Match` subclass or pass a list subclass: the full suite shows it.
- A subclass that overrides `validate` itself: the entry points check `type(match) is Match` before calling `validate`, so the override never runs.
- Not done here (follow-up): subclasses of `GameFormat`, `date`, `datetime`, and int `sequence`/`best_of` fields. They need their own rules (`GameFormat` is a dataclass with nested fields), so they are out of scope.

## Proof

`tests/test_match_exact_types.py` covers each criterion: the repro in each entry point with a state check (criterion 1), a parametrized ledger position (2), field subclasses (3 to 6), direct `validate` (7). The full suite covers 8.
Measured on main: the repro prints `record counted True`, then `SnapshotError snapshot.processed_match_ids[0] must be a non-blank str`.
