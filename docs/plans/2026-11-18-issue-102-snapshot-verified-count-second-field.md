# Plan: Snapshot verified-count check passes when a second field is edited too (issue #102)

Risk tier: High. It changes snapshot validation, which guards the verified labels CLAUDE.md hard rule 7 treats as security-sensitive (relabeling self-reported updates as verified), and it is breaking for edited snapshots.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-allauth, django-celery, python-fastapi, React and Expo skills: not applicable because this is a standard-library Python package with no web, UI, or framework code.

Decision from the maintainer (recorded on #102, counts as plan approval): validation only, no new snapshot field, no `snapshot_version` bump. Case 2 and case 3 get validation rules, case 1 is a documented limit.

## Reproduction on main (f1d47aa)

Base: `replay` to 2026-01-10 of two confirmed self-reported matches for `syn-a` under the default `Config`: doubles `syn-m1` (`syn-a`, `syn-b` v `syn-c`, `syn-d`, 2026-01-05) and mixed doubles `syn-m2` (`syn-a`, `syn-e` v `syn-f`, `syn-g`, 2026-01-06), plus a league singles match `syn-m3` (`syn-z` v `syn-y`, 2026-01-07). `syn-a` doubles has `matches_played` 2, `verified_matches_played` 0; mixed 1 and 0. Snapshot v8, `resumable` true. Adapted to v8: since #97 a derived `open_parent:doubles` state must be dated exactly when its doubles state is, so the mixed state is edited too.

- Case 1: doubles `initial_source` `ext`, `initial_source_date` `2025-01-01`, `verified_matches_played` 2; mixed `initial_source` `ext`, `initial_source_date` `2025-01-01`, `verified_matches_played` 1; `resumable` false. Loads. With `resumable` true it already raises (the exact rule counts dates after 2025-01-01). With `initial_source` left `open_provisional` it already raises (#97).
- Case 2: the same edits with date `2026-01-06` and `resumable` true. Loads (also with the mixed state left `open_parent:doubles`, dated `2026-01-06`).
- Case 3: doubles and mixed `verified_matches_played` raised by 1, and `syn-a` replaced by `syn-z` (singles only) in the mixed log entry. Loads.

## What the engine keeps

Case 2. `_compute` freezes a player exactly when `match.played_on <= initial_source_date` of the state the update starts from (`_peek_state`), and only a moved player's state is written; a move sets `last_played = max(last_played, played_on)`. So every move of a dated state is strictly after its date, and `last_played` never moves back. A state's `initial_source_date` changes only through a seed, and a seed needs an unplayed state (`seed_external_rating` refuses a played one; `add_player` and replay seeds must be unplayed). A category state derived under `category_feeds_parent` takes the parent's date when the category match is considered, and is stored only when moved, so the same holds for it. For overall doubles fed by a category match, the doubles update freezes on the doubles state's own date, so again a move is strictly after it. Rule: a played state with an `initial_source_date` has `last_played` strictly after it. The decision scopes it to resumable snapshots; a fuzz of the existing harness (5 configs x 40 seeds, about 19,700 played dated states, replays, resumes, live in random order with reseeds) found no engine-made state that breaks it in live snapshots either. Widening it to every snapshot is left to the maintainer (noted in the PR), since the decision names resumable snapshots.

Case 3. `record` appends a log date under the match's own format only when that format's update counts. Each named player then either moved in that format (a stored state with `matches_played > 0` and `last_played >= played_on`) or was frozen in it: the state the update started from had `initial_source_date >= played_on`. That state is the stored state in that format, or, with none stored, under `category_feeds_parent` in a category format, the state `_new_state` derives from a stored overall doubles state that has a date or a played count; frozen needs a date, so the doubles state is dated (this is `_has_dated_baseline`). Afterwards: a played state stays played; a dated stored state cannot be reseeded (`_in_counted_match` finds the counted match through the moved players' partners and opponents); the overall doubles state cannot be reseeded once a counted category match named the player; a later stored category state is derived with the doubles date, or played. The doubles update of a category match is not logged under doubles, so no doubles rule follows from a category entry. Rule, for every player a log entry names, with `last` the entry's latest date: the player's stored state in that format is played with `last_played >= last`, or the dated baseline (`_has_dated_baseline`: the stored state's `initial_source_date`, or with none stored, under `category_feeds_parent` in a category format, the overall doubles state's) is on or after `last`. Per date this is "moved on or after it, or frozen through a baseline dated on or after it"; `last_played` and the baseline date are fixed or only grow, so checking the latest date covers every date. The probe above also checked this rule: no engine-made snapshot breaks it, in any snapshot (resumable or not). It applies to every snapshot.

Review-round additions (round 1), within the decision's validation-only scope:

- R1. In a resumable snapshot, every `partners` and `opponents` date of a played state with an `initial_source_date` is after that date. Those maps are written only on a move, and a move of a dated state is strictly after its date. Without it, case 2 with the date moved to the first match (2026-01-05), `last_played` kept, still loads. Error at `{path}.partners[<id>]` or `{path}.opponents[<id>]`.
- R2. The case 3 rule is replaced by a stricter one. For an entry's latest date L, the match on L froze exactly the named players whose baseline (`_baseline_date`) is on or after L and moved every other named player, at least one. Each mover's stored state in the format is played and records every teammate in `partners` and every player of the other team in `opponents`, each dated on or after L (`_seen` keeps the latest date). So every named player has a baseline on or after L or is a mover with those links, and some named player is not frozen-capable. Without it, a log name can be swapped to a player imported, or playing the format, after L, and the swapped snapshot loads; a later reseed of that player then succeeds and `to_snapshot()` raises. Errors, checked per entry in log order: first each named player's state in sorted order (the case 3 message), then each mover's links in sorted order ("whose <format> state does not record <id> as a partner/an opponent on or after <date>"), then "names no player who moved in the match on <date>".
- Remaining limits, documented as examples, not a complete list: case 1 below; the case 2 shapes in a snapshot that is not resumable; a whole log entry deleted with the named players' verified counts raised (the snapshot does not tie a match id to its match type); edits that also rewrite partner and opponent names or dates to match.
- Frozen test correction: `tests/test_snapshot_log_links.py::test_swap_in_a_doubles_entry_to_an_unplayed_later_import_does_not_load` expected the date 2026-01-06 in its message, but the doubles entry it edits is `syn-m1`, dated 2026-01-05, so the right date is 2026-01-05. Only that string changed; the test still pins the same rejection. Approved by the launching agent (orchestrator) on behalf of the run; not confirmed as the user's own approval.

Case 1 stays a documented limit: a non-resumable snapshot does not record when a seed was set relative to the logged matches, so an `initial_source_date` added before every logged date, with a non-reserved `initial_source`, cannot be told apart from a live import followed by matches. Remaining variants: `resumable` false and a dated state whose `initial_source` is an external name (any non-`open_` text), or an `open_parent:doubles` category state dated with its doubles state. A dated `open_provisional` is rejected since #97, and with `resumable` true the exact rule already rejects it.

## Acceptance criteria

1. Case 2's repro (both the `ext` and the `open_parent:doubles` mixed variants) raises `SnapshotError` naming `ratings.doubles.last_played` (or the first state that breaks the rule, in sorted player and format order) with a message that `last_played` must be after `initial_source_date` in a resumable snapshot. A state with `last_played` equal to `initial_source_date` is rejected (boundary).
2. Case 3's repro raises `SnapshotError` naming the log entry and the player: `snapshot.self_reported_log[<i>].teams names 'syn-z', who has no <format> state that a match on <date> could have moved or frozen` (wording may differ; the test pins it with `match=`). Also rejected: a named player whose stored state in that format is unplayed and undated; a named player whose only state is a dated baseline before the latest logged date; a named player whose played state's `last_played` and date are both before the latest logged date. Accepted: a category entry naming a player with no stored category state whose overall doubles state is dated on or after the latest date, under `category_feeds_parent`.
3. Case 1's repro still loads, and is documented as a limit in the README schema section, the `from_snapshot` docstring, the `_check_verified_against_log` docstring, and the changelog, which also say #97 rejects a dated `open_provisional`.
4. Every snapshot the engine produces still loads: the existing fuzz in `tests/test_snapshot_verified_log.py` (`test_engines_the_code_produces_load`: replays, checkpoints, resumes, live after resume, live in random order, with category matches, filters, rejections, seeds, and reseeds, under five configs) passes, and a new fuzz assertion shows the new rules are exercised: across the runs, some log entry names a player with no stored state in a category format (frozen through overall doubles), and some resumable snapshot has a played dated state.
5. All new errors are `SnapshotError` (an `OpenEngineError`), asserted by type with `match=`.
6. No snapshot field, `snapshot_version`, `Config`, `RatingState`, or `Player` change. `CHANGELOG.md` gets a `**Breaking:**` entry under Unreleased with a Migration note (an edited or hand-built snapshot that breaks the rules no longer loads; run a full replay to make a new one).
7. The gate passes: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.

## Files that change

- `src/open_engine/engine.py`:
  - `_baseline_date(player, fmt, cfg)`: the baseline lookup, returning the date; `_has_dated_baseline` now wraps it.
  - New `_check_log_players(players, log, cfg)` (no `paths`: errors name the log entry, not a player path): the case 3 rule, extended by R2, called in `_read_snapshot` right after the log is read (after the existing "names a player not in players" check) and before `_check_verified_against_log`. Iterate entries in log order (index matches `snapshot.self_reported_log[i]`) and named players sorted, so the first error is deterministic.
  - `_check_resumable`: the case 2 rule, in the per-state loop: `matches_played > 0`, `initial_source_date` set, `last_played <= initial_source_date` raises `SnapshotError` at `{path}.last_played`; R1 adds the same check for each `partners` and `opponents` date.
  - Docstrings: `_check_verified_against_log` (the limit and what now closes the other cases), `from_snapshot`, `to_snapshot` if it lists the checks.
- `tests/test_snapshot_invariants.py`: the "future log date" row now expects the log entry rule's message, which fires before the log date check in `_check_resumable` (that check can no longer be reached).
- `tests/test_snapshot_log_links.py` (new, review round 1): R1 and R2 repros.
- `tests/test_snapshot_log_players.py` (new): cases 2 and 3 repros, boundaries, accept rows, case 1 still loads (documented limit), and the fuzz coverage assertion (or that assertion added to `tests/test_snapshot_verified_log.py`, reusing `_drive`).
- `README.md`: schema section: the two rules, and the case 1 limit replacing the sentence that says a snapshot edited in other fields can still load (narrow it to what remains).
- `CHANGELOG.md`: Unreleased `**Breaking:**` entry with Migration note.

## Order of work

1. Red tests for cases 2 and 3 (and their boundaries), committed alone (`test: failing tests for issue 102`), frozen.
2. The two checks in `engine.py`.
3. Fuzz coverage assertion, case 1 load test.
4. Docstrings, README, changelog.
5. Gate.

## Risks

- Rejecting a snapshot the engine can produce. Mitigation: the reasoning above, the probe already run, and the fuzz in the gate. Riskiest step: the case 3 rule for category formats; it must use the same baseline lookup as `_has_dated_baseline` under the snapshot's config.
- Error ordering: existing tests that pin messages for other checks on edited snapshots could now hit the new check first. Run the full suite after step 2.
- Not chosen: widening case 2 to live snapshots (outside the decision), a new provenance field for case 1 (the decision rules it out).

## Proof

- Criterion 1 and 2: red tests from step 1, failing on main because the snapshot loads.
- Criterion 3: a test that the case 1 snapshot loads, plus the docs.
- Criterion 4: `test_engines_the_code_produces_load` and the new coverage assertion.
- Measured on main (f1d47aa), script in the run's scratch directory: case 1 (`ext`, resumable false) LOADS; case 2 (`ext`, resumable true) LOADS; case 2 with mixed left `open_parent:doubles` LOADS; case 3 LOADS. Probe of both rules over `_drive` for 5 configs x 40 seeds: 19,670 played dated states, 0 with `last_played <= initial_source_date` (resumable or not); 0 log names breaking the case 3 rule; 982 log names with no stored state in the format (frozen through overall doubles).
