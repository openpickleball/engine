# Plan: Trim the README to a front page that links docs/behavior.md (issue #144)

Risk tier: Low. Documentation only, but the README is the project's front page and several tests read it; moving text risks losing a rule, so the #126 claim check applies.

Domain skills: none apply beyond the repo's own rules (standard-library Python package; documentation change).

## Principle

Move, do not rewrite, as in #126. A rule leaves the README only after the same rule (same conditions) is confirmed in `docs/behavior.md`; if `docs/behavior.md` lacks it, move it there first.

## Acceptance criteria

1. The README keeps: the title and status (demonstration label), Technology, Install (the pinned TestPyPI line a test checks), Setup and tests, Quick start (the code block a test runs), Public API (every exported name listed, which a test checks, each with a one-line description), Test data, Contributing, License.
2. "Config values", "Ledger order", "Match performance", "Match status", and "Snapshots and checkpoints", the prose after the Quick start code block, and the long entries under Public API (the exact input types paragraph and the error entries), become short summaries (a few sentences each) that say what the topic is and link to the `docs/behavior.md` section that holds the rules.
3. Every sentence of the old README sections that are shortened appears in `docs/behavior.md`, `docs/model.md`, or the new README, or the PR lists it with where its content lives (a scratch claim-check script, as in #126).
4. The README is under about 20 KB. Links are absolute GitHub URLs to `docs/behavior.md` and `docs/model.md`, so they work on a package index page.
5. `tests/test_readme.py` and `tests/test_api.py` pass unchanged. `CHANGELOG.md` gets an entry under `### Changed` (documentation only).

## Files that change

- `README.md`, `docs/behavior.md` (only rules it lacks), `CHANGELOG.md`.
- `src/open_engine/_engine.py`: the `to_snapshot` docstring said "The README lists the schema in full"; it now names the new `docs/behavior.md` "Snapshot schema" section. Docstring only.

Departures recorded during the work: the Quick start prose and the long Public API entries were added to the shortened sections, since the README cannot get under 20 KB without them. The schema and the per-class error list moved into `docs/behavior.md` as whole blocks ("Snapshot schema", and a list under "Errors"), with internal links adjusted to point inside that file. Where the README and `docs/behavior.md` disagreed (the order of the `player_summary` player id checks), the code decided, and the true text was kept.

After review: `docs/behavior.md` passages that stated one rule twice (input types, the `played_at` time zone rule, ledger order, match fields, match status, errors, resume, and the snapshot schema and checks) were merged into one statement that keeps every condition, with links from the other places. `CLAUDE.md` (two pointers to README sections that moved) and the `player_summary` docstring (the order of its id checks) were also corrected, and the README's relative links became absolute. A second review pass shortened the `InvalidSeedError` entry under "Errors" to a summary that links "Seed rules", "External seeds", and "Provisional states" (which now hold the few conditions only that entry had), and left "Recording a match" with the `record`-only `as_of` rules, linking "Match ids, rejections, and resubmission" for the rest.

## Order of work

1. Save the old README to the scratch directory; split the shortened sections into sentences.
2. For each sentence, find it in `docs/behavior.md` or `docs/model.md`; move any missing rule into the matching `docs/behavior.md` section, verbatim.
3. Rewrite the README sections as summaries with links.
4. Claim check; link check (every anchor exists); full gate.

## Risks

- A rule only the README held is lost. Mitigation: step 2 before step 3, and the claim check.
- `docs/behavior.md` and the README disagree today in some detail. Mitigation: where they differ, keep both in `docs/behavior.md` only after checking which is true of the code, and record it in the PR.

## Proof

- The claim check output (sentences matched, sentences moved, sentences reworded with where they live).
- The gate, including the README tests.
