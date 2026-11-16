# Plan: No low-reliability (under 60%) provisional status (issue #18)

Risk tier: Medium. The change adds behavior and changes no rating math, but it changes the public API (a new `PlayerSummary` field) and the default config fingerprint (a new `Config` field).

Domain skills: none apply. This is a standard-library Python package, so django, django-ninja, allauth, celery, fastapi, and the frontend and Expo skills are not applicable.

## Decisions taken with the maintainer

- **Name: "established", not "provisional".** "Provisional" already means two other things here: a live rating between replays (CLAUDE.md, "Provisional versus authoritative") and the `open_provisional` initial source. The new names are `Config.established_reliability_threshold` and `PlayerSummary.established`. The docs say that a rating that is not established is what external rating systems usually call provisional.
- **`evaluate()` does not change.** The threshold is a label for display and eligibility only. It does not change ratings, predictions, `evaluate`, `prequential_predictions`, `fit_scale`, or `fit_win_calibration`. `evaluate` already leaves out each player's early matches through `burn_in_matches`. The docs say so.

## Acceptance criteria

1. `Config.established_reliability_threshold` exists, defaults to `60.0`, and is part of `Config.fingerprint()`: two configs that differ only in this field have different fingerprints.
2. `Config.__post_init__` raises `ConfigError` with the message "established_reliability_threshold must be between 0 and 100" for a value below 0, above 100, or NaN. It accepts 0 and 100.
3. `PlayerSummary.established` is a `bool`. It is `True` exactly when `PlayerSummary.reliability` (the OPEN-earned value, rounded to 1 place, at the summary's reference date) is at least the threshold.
4. Imported reliability never counts toward `established`. An unplayed imported seed with an imported reliability of 95 is not established under the default config.
5. `established` uses the same reference date as `reliability`, so an established player who stops playing loses it when measured late enough.
6. The threshold changes no rating or evaluation output. A replay and an `evaluate` under thresholds 0 and 100 give the same ratings and the same report.
7. Documented: the module docstring (a published behavior note), the `Config` comment, the `PlayerSummary` docstring, the README (quick start text and the Public API line for `PlayerSummary`), and a CHANGELOG entry under `[Unreleased]`. Together they give the expected match count to become established under the defaults, the meaning of "established" next to the two meanings of "provisional", that the threshold does not affect `evaluate`, and that the default config fingerprint changes.

## Files that change

- `src/open_engine/engine.py`
  - `Config`: add `established_reliability_threshold: float = 60.0` next to the imported reliability fields, under a comment `# Reliability label.` that explains it. In `__post_init__`, add `if not 0.0 <= self.established_reliability_threshold <= 100.0: raise ConfigError("established_reliability_threshold must be between 0 and 100")`. NaN fails the chained comparison, so it raises. `norm()` needs no new case for a float.
  - `PlayerSummary`: add `established: bool` directly after `imported_reliability`, with an inline comment. Extend the docstring: established is True when `reliability` (as displayed, rounded to 1 place) is at least `Config.established_reliability_threshold`; imported reliability never counts; it is measured at the same reference date; it is a label only and changes no rating; a rating that is not established is what external rating systems usually call provisional, which is different from OPEN's live-versus-authoritative "provisional" and from the `open_provisional` source.
  - `player_summary`: pass `established=earned >= cfg.established_reliability_threshold`.
  - Module docstring: add a published behavior note after the `player_summary` reference date note. It covers the threshold, the comparison with the displayed earned reliability, that imported reliability does not count, the vocabulary point above, that `evaluate` and the fits ignore it (burn-in is `burn_in_matches`), and the match counts in "Expected match count" below.
- `tests/test_established.py` (new): the tests in Proof.
- `tests/test_api.py`: add `established` after `imported_reliability` in `SUMMARY_FIELDS` (the test pins the field order) and assert `established is False` for the imported baseline. Added during implementation: the existing field list test failed on the new field.
- `README.md`: in the paragraph after the quick start, one or two sentences on `established` with the default threshold and the expected match count. On the `PlayerSummary` Public API line, mention the established flag if that line lists fields. Do not add a field list it does not already have.
- `CHANGELOG.md`, under `[Unreleased]` `### Added`: one entry for `Config.established_reliability_threshold` and `PlayerSummary.established` (issue #18). It names the comparison, the 0-100 validation, that imported reliability does not count, that ratings and `evaluate` do not change, the expected match count, and that the default `Config().fingerprint()` changes, so snapshots and replay records from earlier configs carry a different fingerprint.

`examples/basic_match.py` stays as it is. `__init__.py` and `__all__` do not change, since there is no new export. The snapshot schema does not change: the config is covered by its version and fingerprint, and `PlayerSummary` is not stored.

## Expected match count (measured on origin/main, default config)

The scenario: a new player with no seed, one doubles match a week, single games to 11 won 11-9, with a new partner and two new opponents each week. Reliability measured at each match date:

| Matches | 8 | 9 | 10 | 11 |
| --- | --- | --- | --- | --- |
| League reliability | 50.8 | 55.2 | 59.3 | 63.1 |

Matches needed to reach 60, by match type, for the same weekly schedule:

| Match type | Diverse, single game | Diverse, best of 3 | Same four players, single game |
| --- | --- | --- | --- |
| Tournament | 10 | 8 | 18 |
| League | 11 | 9 | 20 |
| Recreational verified | 13 | 10 | 24 |
| Self-reported | 25 | 20 | never (repeat matches are discounted, then rejected) |

The docs state the headline: "about 10 matches with varied partners and opponents (11 league matches, 8 to 13 depending on match type and games), and about twice as many against the same players". They do not paste the whole table.

After the 11th league match, measured at a later date with no new matches: 60.6 at 90 days, 59.7 at 120 days, 58.1 at 180 days.

## Order of work

1. Commit this plan alone as `docs: plan for issue 18`.
2. Worktree setup: the repo-root `.venv` imports the main checkout's `src`, not this worktree's. Create a worktree venv with `uv venv && uv pip install -e ".[dev]"`, run the gate with it, and confirm that `python -c "import open_engine; print(open_engine.__file__)"` points into this worktree.
3. TDD: write `tests/test_established.py` and watch it fail (`AttributeError` or `TypeError` on the missing field and parameter). Commit the red tests.
4. Implement `Config` and `PlayerSummary` and `player_summary`. Run the tests until green, then commit.
5. Docs: the module docstring, the README, and the CHANGELOG. Commit.
6. The full gate: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.
7. Live drive: run `python examples/basic_match.py` and a short script that prints `established` for a player crossing the threshold, an imported seed, and a stale player.

## Risks

- **Fingerprint change.** The default `Config().fingerprint()` changes. No test pins a literal fingerprint value (checked: tests compare fingerprints to each other). Existing stored snapshots would fail to load with a fingerprint mismatch, but there is no release in between and the version does not change here. The CHANGELOG says so.
- **Vocabulary drift.** Someone may later call this "provisional". The docstrings state the distinction once, in the module docstring and in `PlayerSummary`.
- **Rounding at the boundary.** The comparison uses the displayed (rounded) reliability, as the imported reliability comparison does, so a reader who sees 60.0 sees established. A test pins the boundary.
- **Threshold as int.** `Config(established_reliability_threshold=60)` fingerprints differently from the default `60.0`, as every float field already does. This is out of scope and is not changed.
- **Riskiest step:** none touches the rating math. The main risk is docs that contradict each other, so Phase 8 checks the module docstring, the README, and the CHANGELOG together.

Not done: no `evaluate` or `prequential_predictions` filter (by decision), no `RatingState` or snapshot field, no change to `reliability()`.

## Proof

All tests are in `tests/test_established.py`. Use synthetic ids (`p`, `q0`, `r0`, ...) and a helper that records N weekly league doubles matches from 2026-01-05, as measured above.

- Criterion 1: `Config().established_reliability_threshold == 60.0`. Parametrize over a few values (0.0, 59.9, 100.0): `Config(established_reliability_threshold=v).fingerprint() != Config().fingerprint()`.
- Criterion 2: parametrize over -0.1, 100.1, and `float("nan")`, using `pytest.raises(ConfigError, match="established_reliability_threshold must be between 0 and 100")`. 0.0 and 100.0 construct without error.
- Criterion 3:
  - (a) A new player with no matches (`player_summary("z", DOUBLES, as_of=...)`): reliability 0.0, established False under the default config.
  - (b) After 10 league matches, reliability 59.3 and established False. After 11, reliability 63.1 and established True. This test also pins the documented count, so a change to the math fails it until the docs are updated.
  - (c) Boundary: after 11 matches, read `r = summary.reliability` under the default config. Then replay or record the same matches under `Config(established_reliability_threshold=r)`: established True. Under `Config(established_reliability_threshold=r + 0.1)`: established False.
  - (d) Threshold 0.0: the new player with 0.0 reliability is established. Threshold 100.0: the 11-match player is not.
  - Assert `type(summary.established) is bool`.
- Criterion 4: `seed_external_rating` with an `ExternalRatingSnapshot` with reliability 95.0, then `player_summary` at the capture date: `imported_reliability == 95.0`, `reliability < 60`, `established is False`.
- Criterion 5: after 11 matches, `as_of` = last match + 90 days gives established True (60.6), and + 120 days gives established False (59.7).
- Criterion 6: build a ledger of the 11 matches. Under `Config(established_reliability_threshold=0.0)` and `Config(established_reliability_threshold=100.0)`: every player's `rating`, `rd`, `reliability`, and `matches_played` from `replay(...)` are equal, and `evaluate(..., burn_in_matches=0)` gives equal reports (compare with `==`; `EvaluationReport` is a dataclass). The burn-in value and the ledger must give at least one scored match; adjust burn-in if `evaluate` raises `EvaluationError`.
- Criterion 7: the doc finder in Phase 8 checks the text. The existing public API tests keep passing (no new export).

The "behave alike" assertion (criterion 6) cannot be measured on origin/main, since the field does not exist there. It is safe by construction: `player_summary` is the only reader of the new field. The match counts above were measured on origin/main (5d7bd98) with a scratch script that records weekly matches and prints `player_summary(...).reliability` at each match date.

Live drive: the output of `python examples/basic_match.py`, and a script that prints `reliability` and `established` for the 10-match and 11-match player, the imported seed with reliability 95, and the 11-match player at +120 days.
