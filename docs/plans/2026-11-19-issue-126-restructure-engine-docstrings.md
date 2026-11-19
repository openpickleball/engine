# Plan: Restructure the engine docstrings and move the behavior notes to docs (issue #126)

Risk tier: Medium. Documentation only, with no change to behavior, but it moves about 1,600 lines of published claims across many places (`engine.py`, a new `docs/behavior.md`, `docs/model.md`, `CLAUDE.md`, `README.md`). The main risk is a claim lost or changed in the move.

Domain skills: superpowers:test-driven-development, invoke (for the new docstring-layout and label tests). Django, FastAPI, React, and the other listed framework skills: not applicable because this is a standard-library Python package with no web, UI, data store, auth, payments, or email code.

## Principle

Move, do not rewrite. A long edge-case paragraph moves verbatim (reflowed only) into `docs/behavior.md` under a heading, and the docstring points to that heading. A sentence is reworded only to remove a duplicate of a rule already stated in the same document, or to fit the Args, Returns, and Raises layout. Every reworded claim keeps its exact conditions (CLAUDE.md, Writing style). This keeps the review to "is everything still there" rather than "is every new sentence true".

## Acceptance criteria

1. `docs/behavior.md` exists. It holds every published behavior note and every production note from the `engine.py` module docstring, one `##` heading per topic, plus the edge-case text moved out of the public docstrings (criterion 3). It starts with the demonstration label.
2. The `engine.py` module docstring is at most 40 lines: what the module holds, the demonstration label, and pointers to `docs/model.md` and `docs/behavior.md`.
3. Every public docstring (each name in `open_engine.__all__` defined in `engine.py`, and each public method of an exported class) follows one layout: a one-line summary; a short description; then `Args:`, `Returns:`, and `Raises:` sections where they apply (Google style). No public docstring is over 30 lines. Edge-case lists move to `docs/behavior.md`, and the docstring names the section.
4. The exact input type rules (exact types, the carve-outs from issue #107, the Config rebuild from issue #122, `played_at` time zones, snapshot container types) are stated once, in `docs/behavior.md` under "Input types". Docstrings refer to it instead of repeating it.
5. No docstring or behavior note cites an issue number to record history. An issue number stays only where it points a reader to an open question (for example the reasons a default waits for evaluation).
6. Every claim survives. For each sentence of the old module docstring and the old public docstrings, the same sentence (whitespace normalized) appears in `docs/behavior.md`, `docs/model.md`, or a new docstring, or the PR lists it with where its content now lives and why it was reworded or dropped as a duplicate.
7. References are updated: `docs/model.md` links to `docs/behavior.md` sections instead of "the module docstring"; `CLAUDE.md` says the behavior notes and production requirements live in `docs/behavior.md` (its file list and the "Own K and imbalance" rule); the README links `docs/behavior.md`.
8. No behavior changes: no file under `src/` changes except docstrings and comments, `git diff --stat origin/main -- tests` shows only new tests, and the gate passes.
9. New tests: one asserts the module docstring keeps the demonstration label and stays within the line limit; one asserts every public function and method docstring with parameters has an `Args:` section and every one that can raise an `OpenEngineError` has a `Raises:` section (or, if detecting "can raise" is not reliable, every public docstring has a summary line under 100 characters followed by a blank line and stays within 30 lines).

## Files that change

- `docs/behavior.md` (new).
- `src/open_engine/engine.py`: the module docstring and public docstrings. Private docstrings and comments only where they say "see the module docstring" or "module notes".
- `src/open_engine/winprob.py`, `src/open_engine/_errors.py`: only if a docstring points at the module docstring's notes, or for the shared layout of their public functions.
- `docs/model.md`: links.
- `CLAUDE.md`: the two references above.
- `README.md`: one link to `docs/behavior.md`. The README's own long sections (Config values, Public API errors) are out of scope here; see Not done.
- `CHANGELOG.md`: an entry under `## [Unreleased]`, `### Changed`: documentation moved, no behavior changed.
- `tests/test_docstrings.py` (new): the tests in criterion 9.

## Order of work

1. Save the old text: write the old module docstring and every old public docstring to the scratch directory (not the repo) for the claim check in Proof.
2. Write the failing tests (criterion 9). Commit them red, alone.
3. Create `docs/behavior.md` from the module docstring, one heading per note, in this order: Status; Input types; Errors; Ledger order; Match status; Match ids, rejections, and resubmission; Likely duplicates; Late live matches; K and imbalance; Match performance (link to `docs/model.md` for the math); Provisional and authoritative ratings; Imported ratings and reliability display; Reference dates; Established; Network connectivity; Snapshots and checkpoints; Backend requirements (the production notes). Reflow and add headings only.
4. Replace the module docstring (criterion 2).
5. Rewrite the public docstrings to the layout, largest first: `RatingEngine.from_snapshot`, `Config`, `RatingEngine.add_player`, `RatingEngine.seed_external_rating`, `replay`, `RatingEngine.to_snapshot`, `Match`, `MatchResult`, `RatingEngine.record`, `RatingEngine.player_summary`, `PlayerSummary`, `prequential_predictions`, `NetworkConnectivity`, `ReplayInfo`, `RatingEngine.seed_provisional_rating`, then the rest. Move each edge-case block into `docs/behavior.md`, under the topic heading it belongs to or a new heading for that entry point (for example "Seed rules", "Config validation", "Snapshot schema and checks").
6. Update `docs/model.md`, `CLAUDE.md`, `README.md`, `CHANGELOG.md`.
7. Run the claim check (Proof), resolve every missing sentence, and run the gate.

## Risks

- A claim lost or changed in the move. Mitigation: the move-not-rewrite rule and the sentence-level claim check.
- A dataclass field docstring or `#:` comment that tests read. Mitigation: `tests/test_api.py` only checks that a docstring exists; keep every public docstring non-empty.
- Ruff line length (100) and docstring formatting in reflowed text. Mitigation: the gate.
- The README already repeats the Config validation and error rules almost verbatim. This change leaves that as is; a third copy is not added (docstrings point to `docs/behavior.md`).

Departures made during the work:

- The README needed two more reference fixes than the one link: its `InvalidSeedError` entry pointed to "the seed rules in `add_player`'s docstring" twice, and those rules now live in `docs/behavior.md`, "Seed rules". Both now point there. The README's long sections are otherwise unchanged.
- `docs/behavior.md` has more entry-point sections than the examples named: "Config validation", "Match fields", "Recording a match", "Match results", "Seed rules", "External seeds", "Provisional states", "Player summaries" (with `NetworkConnectivity` and `PlayerSummary` field notes), "Replay and resume", "Snapshot schema and checks", "Prequential predictions", and "Calibration fits".
- Criterion 4 holds for the docstrings, which refer to `docs/behavior.md` instead of repeating the type rules. Inside `docs/behavior.md`, the type paragraphs moved with their entry point's text (for example the Config rebuild under "Config validation", `played_at` time zones under "Match fields", snapshot container types under "Snapshot schema and checks"), so some general rules from "Input types" appear again there. Removing those repeats means rewriting moved text, which the move-not-rewrite rule leaves to a follow-up.
- Public docstrings that already fit the layout and the 30-line limit and take no parameters (`ReplayInfo`, `RejectionReason`, `MatchStatus` apart from its pointer, `Config.to_dict`, `GameFormat.validate`, and the short ones) are unchanged.
- Review found that `RatingEngine.__init__` had a public docstring outside the layout. `tests/test_docstrings.py` now also checks every exported class's own written `__init__` docstring (summary line, 30-line limit, `Args:` when it takes parameters); dataclass-generated `__init__` methods have no docstring and are skipped.
- `docs/` is not in the sdist or the wheel, so an installed copy's "See docs/behavior.md" has nothing to resolve. The module docstring gives the full GitHub URLs of `docs/model.md` and `docs/behavior.md` once and says each docstring's "docs/behavior.md" means that page, and the README's links to both pages are absolute URLs so they resolve on a package index. `pyproject.toml` and the sdist checks are unchanged.
- Review also found Raises sections that dropped errors or their order (`fit_scale`, `fit_win_calibration`, `evaluate`, `reliability`, `seed_external_rating`, `replay`, `player_summary`); those were restored from the old text. Pointers in `docs/behavior.md` that led to a short docstring ("see add_player", "see Config", "see to_snapshot" and the like) now link the section that holds the content, and the bullet labels left under matching headings were dropped.
- Issue numbers in private docstrings and comments are unchanged: the plan limits private changes to pointers at the module docstring.

Not done here: shortening the README's long sections (a follow-up issue can make them link to `docs/behavior.md`), aligning the `Config` docstring's "uncalibrated placeholders" wording with `docs/model.md` beyond what the move requires (do align it if the `Config` docstring is rewritten anyway: some defaults are declared choices or labels), splitting the module (#127), breaking up functions (#128).

## Proof

- Criteria 2, 3, 9: `tests/test_docstrings.py`, red before the rewrite and green after.
- Criterion 9 uses the fallback. Telling whether a public function "can raise" an `OpenEngineError` needs a call graph through `self` methods and module helpers, and an over-approximation would demand a `Raises:` section on calls that never raise (`Config.fingerprint`). So `tests/test_docstrings.py` checks that every public docstring has a summary line under 100 characters followed by a blank line and stays within 30 lines, and keeps the reliable half of the first variant: every public function or method with parameters has an `Args:` section. `Raises:` sections are still written where they apply, but no test checks them.
- Criterion 6: a scratch script (not committed) splits the saved old docstrings into sentences, normalizes whitespace, and searches for each in the new `docs/behavior.md`, `docs/model.md`, and new `engine.py` docstrings. Its list of unmatched sentences goes in the PR body with where each one's content now lives.
- Criterion 8: the gate (`ruff check .`, `ruff format --check .`, `mypy src`, `pytest`); `git diff origin/main -- src` reviewed to contain docstring and comment lines only (an AST comparison of `origin/main` and the branch with docstrings stripped must be equal).
- Criterion 1, 4, 5, 7: review and verify phases.
