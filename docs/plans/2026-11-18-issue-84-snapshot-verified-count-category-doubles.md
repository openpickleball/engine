# Plan: Snapshot verified-count check allows relabeling category-fed doubles updates (issue #84)

Risk tier: High. CLAUDE.md hard rule 7 makes this security-sensitive (a stored snapshot sets every reliability label), and it tightens snapshot validation, so a snapshot that loads today can fail.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-allauth, django-celery, python-fastapi, React and Expo skills: not applicable because this is a standard-library Python package with no web, UI, or framework code.

Decisions from the maintainer (they count as plan approval): validation only, no schema change, no `snapshot_version` bump (stays 7). Build on main after #76 (PR #95), #74 (PR #98), and #79 (PR #100). Stop and report if correctness would need a new snapshot field.

## Acceptance criteria

1. With `max_doubles_partner_gap` unset and `category_feeds_parent` on, an overall doubles state with no `initial_source_date` must have `matches_played - verified_matches_played` equal to the self-reported doubles log dates naming the player plus the self-reported category log dates naming the player. Otherwise `from_snapshot` (and `to_snapshot`, which checks its own output) raises `SnapshotError`.
2. The issue's repro (synthetic `syn-a`, one confirmed self-reported doubles match and one confirmed self-reported mixed doubles match, default `Config`, doubles `verified_matches_played` set from 0 to 1) raises `SnapshotError`, for both `resumable` true and false.
3. The same exact rule applies to an overall doubles state of a resumable snapshot that has an `initial_source_date`, counting only log dates after it (the case the existing code already treats as exact for its own format). This is the only other case tightened.
4. Not tightened, and still a range: `max_doubles_partner_gap` set (the snapshot cannot tell which category matches reached doubles), and an imported doubles state in a snapshot that is not resumable (left as the existing upper bound; see Risks).
5. Every snapshot the engine produces still loads: random replays, resumes, and live engines, with category matches, filters (`max_doubles_partner_gap`, `category_feeds_parent=False`, `reject_content_duplicates=True`, `self_reported_requires_both_teams`), rejections, seeds, and live reseed attempts (accepted and refused).
6. Each new raise is `SnapshotError` (an `OpenEngineError`), and a test asserts the type and message.
7. README snapshot schema section, the `from_snapshot` and `_check_verified_against_log` docstrings, and `CHANGELOG.md` (a `**Breaking:**` Security entry with a Migration note) describe the new rule and its remaining limits.

## Why the rule is sound (no new state needed)

`record` appends one log date, under the match's own format, for every counted self-reported match, and never prunes the log. For a counted category match under `category_feeds_parent`, `_compute` runs again for overall doubles. That parent update is rejected only when every player is frozen in doubles (`BEFORE_IMPORTED_BASELINE`) or by `max_doubles_partner_gap` (`DOUBLES_PARTNER_GAP`); team sizes match, so `_validate_teams` cannot fail there. A doubles state with no `initial_source_date` is never frozen, so with the gap unset the player's doubles update always counts and the player moves in it. A doubles-format logged match moves such a player too. Such a state is never reseeded once logged (`seed_external_rating` refuses after a counted doubles or category match naming the player, PR #100), and no method replaces it with an unplayed state. So the count is exact.

In a resumable snapshot every seed precedes every row, so a doubles state's `initial_source_date` held for the whole replay. A logged doubles or category match dated after it did not freeze the player in doubles, so the same argument holds counting only dates after `initial_source_date`.

## Files that change

- `src/open_engine/engine.py`: `_check_verified_against_log` and its docstring; the `from_snapshot` docstring paragraph on the self-reported log.
- `tests/test_snapshot_verified_log.py`: new tests; strengthen the random "engines the code produces load" test.
- `README.md`: the snapshot schema paragraph (line about `self_reported_log` limits).
- `CHANGELOG.md`: a `**Breaking:**` entry under `### Security` in `[Unreleased]`.

## Order of work

1. Red tests, committed alone (fail on main): the repro for `resumable` true and false; the resumable imported doubles case (seed dated before every row, a self-reported doubles match and a self-reported mixed match after it, plus a mixed match on or before the seed date in which the player is frozen in doubles but the match is logged because another player moved; relabel one update). Each asserts `SnapshotError` with a message match. `pytest.raises(SnapshotError, ...)` asserts the type.
2. Implement in `_check_verified_against_log`: for `fmt is DOUBLES`, `feeds` on, `exact`, and `cfg.max_doubles_partner_gap is None`, require `unverified == own + categories`, else raise `SnapshotError(f"{prefix} must equal the self-reported doubles and category matches logged for this player ({n})")`. Keep every other branch as it is.
3. Tests that pin what is not tightened: with `max_doubles_partner_gap` set, the relabeled snapshot still loads (a doubles state below `own + categories` but at least `own`), and a live imported doubles state below the bound still loads. And tests that an engine-produced snapshot with a gap-rejected parent loads.
4. Strengthen `test_engines_the_code_produces_load`: more seeds, live engines that interleave `seed_external_rating` calls (with `replace_unplayed_seed=True`, catching `InvalidSeedError`), live recording after resuming from a snapshot, and a config with `self_reported_requires_both_teams=False`. Every produced snapshot must load. Plus a direct check that exactness holds: for every doubles state the new rule covers in those engines, the count equals the bound (so the test fails if the rule were ever looser than the engine).
5. Docs: docstrings, README, CHANGELOG.
6. Gate: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.

## Risks

- Riskiest step: rejecting a snapshot the engine itself produced. A load failure on the backend's own snapshot is an availability break. Mitigated by the argument above and the fuzz in step 4, which covers live reseeds, rejections, late matches, filters, and resumes.
- Imported doubles states in non-resumable snapshots: after PR #100 they also look exact (counting dates after `initial_source_date`), but that relies on the history of seeding, which the snapshot does not record, and on the completeness of the #100 refusal, which has open follow-ups (#99, #101). Not tightened here; noted in the PR body.
- `to_snapshot` runs the same check on its own output, so an engine whose public attributes a caller edited directly can now raise there. No engine method produces such a state.
- Out of scope: #88, #90, #92, #93, #96, #97, #99, #101.

## Proof

- Criterion 1, 2: repro test, parametrized on `resumable`, red on main, green after.
- Criterion 3: resumable imported doubles test, red on main, green after.
- Criterion 4: the gap-set and live-import tests load (green before and after).
- Criterion 5: the strengthened random test and the existing suite, green after.
- Criterion 6: every new raise test uses `pytest.raises(SnapshotError, match=...)`.
- Live drive: the issue's repro script run against the branch raises `SnapshotError` for both `resumable` values; on main it loads.

Measured on main (04e50e3), the repro with `PYTHONPATH=src`: doubles state `matches_played` 2, `verified_matches_played` 0; after setting `verified_matches_played` to 1, `from_snapshot` loads for `resumable` true and false.
