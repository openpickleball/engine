# Plan: Add tests for GameFormat.validate_game edge cases (issue #4)
Risk tier: Low. Tests only, in one file; no change to engine code or behavior, and pytest fully exercises the change.
Domain skills: test-driven-development: invoke (adapted: the code under test already exists, so "red" is shown by mutating each branch, see Proof). All others not applicable because the change has no framework, UI, API, data, auth, payments, or email surface.

## Acceptance criteria
1. `tests/test_engine.py` has parametrized pytest cases calling `GameFormat.validate_game` directly, using made-up scores only, covering at least:
   a. a game ending at the target with exactly the win-by margin (valid, e.g. 11-9), and one that misses the margin (11-10 invalid);
   b. extended games ending at exactly the margin (13-11 valid) and overshooting it (14-11 invalid);
   c. capped games: a valid score at the cap (15-14 with `cap=15`), a score above the cap (16-14 with `cap=15`), and an unreachable capped score (15-12 with `cap=15`);
   d. scores below the target (e.g. 10-8);
   e. every case in both orders (`score_a` higher and `score_b` higher).
2. Each of the five `ValueError` branches in `validate_game` is exercised by at least one test that asserts its specific message with `pytest.raises(ValueError, match=...)`:
   "A game must reach its target score", "Game score exceeds the cap", "Game was not won by the required margin", "Capped game score is not reachable", "Extended game must end at exactly the win-by margin".
3. The new tests pass; the full existing suite still passes; `src/` is unchanged.
4. `ruff check .`, `ruff format --check .`, and `mypy src` pass.

## Files that change
- `tests/test_engine.py`: one new numbered section, "GameFormat.validate_game", appended at the end of the file (follow the file's existing `# N. Title ----` section comment style; use the next number).
- `CHANGELOG.md`: Phase 8 decides (probably no entry; tests only).

## Order of work
1. Append the section to `tests/test_engine.py`. Shape:
   - `import re` at the top if `re.escape` is used (keep ruff's import order).
   - Module-level formats: `STANDARD = GameFormat(points_to=11)` (win_by 2, no cap), `CAPPED = GameFormat(points_to=11, cap=15)`, optionally `WIN_BY_3 = GameFormat(points_to=11, win_by=3)`.
   - `VALID_GAMES`: list of `pytest.param(fmt, winner, loser, id=...)`. At least: STANDARD 11-9, 11-0, 12-10, 13-11; CAPPED 14-12 (extended, below cap), 15-13, 15-14; WIN_BY_3 12-9.
   - `INVALID_GAMES`: list of `pytest.param(fmt, winner, loser, message, id=...)`. At least:
     - STANDARD 10-8, "A game must reach its target score"
     - STANDARD 11-10, "Game was not won by the required margin"
     - STANDARD 14-11, "Extended game must end at exactly the win-by margin"
     - STANDARD 12-11, "Extended game must end at exactly the win-by margin"
     - CAPPED 16-14, "Game score exceeds the cap"
     - CAPPED 15-12, "Capped game score is not reachable"
     - CAPPED 15-11, "Capped game score is not reachable"
     - WIN_BY_3 11-9, "Game was not won by the required margin"
   - A `swap` parametrize (`[False, True]`, ids `"a_wins"`/`"b_wins"`) stacked on both tests, so every case runs in both orders. Inside, `score_a, score_b = (loser, winner) if swap else (winner, loser)`.
   - `test_validate_game_accepts_possible_scores(fmt, winner, loser, swap) -> None`: calls `fmt.validate_game(score_a, score_b)` and expects no exception.
   - `test_validate_game_rejects_impossible_scores(fmt, winner, loser, message, swap) -> None`: `with pytest.raises(ValueError, match=re.escape(message)):`.
   - Type-hint the test functions (`fmt: GameFormat`, `winner: int`, ...) per user style; existing tests are untyped and stay as they are.
2. Run the full gate (below).
3. Mutation proof (see Proof), then confirm `git diff --stat src/` is empty.

## Risks
- Freezing a wrong expectation: every expectation above was measured on origin/main (Proof). Riskiest is the capped-unreachable set, which depends on the `high - win_by <= low <= high - 1` rule.
- Not doing: fixing or testing `win_by=1` extended scores. `GameFormat(points_to=11, win_by=1).validate_game(12, 11)` is accepted although 11-10 would already have ended that game. That is a possible engine bug outside this issue; it is reported to the user and no test asserts either behavior for it. Also not testing negative or tied scores: `Match.validate()` (called by `RatingEngine.record`) rejects those before it calls `validate_game`.

## Proof
- Criterion 1, 2: inspect the diff; `pytest tests/test_engine.py -k validate_game -v` lists every case in both orders.
- Criterion 2 (branch teeth): for each of the five `raise` lines in `validate_game`, temporarily make that branch not raise (e.g. replace `raise ValueError(...)` with `return`), run `pytest tests/test_engine.py -k validate_game -q`, record that at least one test fails, restore. Afterwards `git diff --stat src/` must be empty.
- Criterion 3: full `pytest`, 65 passed on main before the change plus the new cases.
- Criterion 4: the gate.
- Gate, in CI order: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.
- Live drive: skipped; the change is tests only and the gate is the whole proof.
- Measured on origin/main (68ac6a4, Python 3.12.3), `GameFormat(points_to, win_by, cap).validate_game(a, b)` for each planned case in both orders:
  - points_to=11 win_by=2: 11-9 ok, 11-0 ok, 12-10 ok, 13-11 ok; 11-10 "not won by the required margin"; 14-11 and 12-11 "Extended game must end at exactly the win-by margin"; 10-8 and 0-1 "must reach its target score". Each reversed order gives the same result.
  - points_to=11 win_by=2 cap=15: 15-14, 15-13, 14-12, 13-15 ok; 16-14 "exceeds the cap"; 15-12, 15-11, 15-15 "Capped game score is not reachable". Reversed orders same.
  - points_to=11 win_by=3: 12-9 ok; 11-9 "not won by the required margin"; 13-11 "Extended game must end at exactly the win-by margin".

### Mutation results (no-commit mode)
Each `raise ValueError(...)` in `validate_game` was replaced with `return`, then `pytest tests/test_engine.py -k validate_game -q` ran, then the file was restored with `git checkout`. Baseline: 32 passed.
- "A game must reach its target score": 2 failed (standard-10-8, both orders).
- "Game score exceeds the cap": 2 failed (capped-16-14, both orders).
- "Game was not won by the required margin": 4 failed (standard-11-10, win_by_3-11-9, both orders).
- "Capped game score is not reachable": 4 failed (capped-15-12, capped-15-11, both orders).
- "Extended game must end at exactly the win-by margin": 4 failed (standard-14-11, standard-12-11, both orders).
After all five, `git diff --stat src/` was empty.
