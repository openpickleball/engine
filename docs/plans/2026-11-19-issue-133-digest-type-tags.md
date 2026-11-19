# Plan: Stop the match digest depending on class module names (issue #133)

Risk tier: High. The match digest is how resume tells a checkpointed row was edited, which is replay provenance (CLAUDE.md hard rule 7).

Domain skills: superpowers:test-driven-development, invoke. Framework skills: not applicable, a standard-library Python package.

## Decision (differs from the issue's suggested fix)

The issue suggested a new tag scheme plus a `snapshot_version` bump. Instead, `_json_form` takes its type tag from a fixed table that maps each type a validated match can hold to the exact string it produces today (`builtins.int`, `open_engine.engine.GameFormat`, and so on). Every digest, snapshot, and checkpoint stays byte-identical, no `snapshot_version` bump is needed, and the `__module__` pins from #127 can come off.

## Acceptance criteria

1. `_json_form` (`src/open_engine/_ledger.py`) reads its tag from a module-level table, `_DIGEST_TAGS: dict[type, str]`, keyed by exact type (looked up with `type(value)`), holding today's string for every type a validated `Match` can contain: `str`, `int`, `bool`, `NoneType`, `float`, `tuple`, `list`, `frozenset`, `set`, `date`, and the engine's `RatingFormat`, `MatchType`, `ScoringType`, `MatchStatus`, `GameFormat` (with the tag string `open_engine.engine.<Name>`). The implementer lists every type from the `Match` and `GameFormat` field annotations and their validation rules, and adds any the list above misses.
2. For a type not in the table, `_json_form` keeps today's behavior (`module.qualname`). A validated match never reaches it; a comment says so.
3. Every `_match_digest` output is unchanged. `SNAPSHOT_VERSION` is unchanged, and a snapshot or checkpoint taken on `main` resumes on the branch and equals a full replay, and the reverse.
4. The `__module__ = "open_engine.engine"` pins added in #127 are removed (public classes and `_FrozenMapping`), so each class reports the module that defines it, and `inspect.getsource` works on every public class. `engine.py` keeps re-exporting every public class and `_FrozenMapping`, so a pickle that names `open_engine.engine.<Class>` (every pickle made before this change) still loads. The imports in `engine.py` that exist only for annotations of pinned classes are removed if `typing.get_type_hints` works without them.
5. Tests: `tests/test_public_class_identity.py` changes from "classes report `open_engine.engine`" to: every public class reports its defining module and `inspect.getsource` works; a pickle stream that names `open_engine.engine.<Class>` loads for every public class and `_FrozenMapping`; `get_type_hints` works on every public class. A new test shows the digest does not read `__module__`: a match digest is unchanged when a class's `__module__` is temporarily set to another string (with `monkeypatch.setattr`). The pinned snapshot hashes in `tests/test_exact_input_plain_unchanged.py` and `tests/test_config_rebuilt_at_entry.py` stay unchanged and pass.
6. Distinct types still give distinct digests: `3` vs `3.0`, `True` vs `1`, a `tuple` vs a `list` of the same items, `frozenset` vs `set`. Existing tests for this pass unchanged.
7. Docs: `CLAUDE.md` drops the note that a new public class must pin `__module__` and says instead that a type a `Match` can hold needs an entry in `_DIGEST_TAGS`; a test fails when a `Match` or `GameFormat` field's type has no entry. `CHANGELOG.md`, `## [Unreleased]`, `### Changed`: classes report their own module again; pickles made before still load; digests and snapshots do not change. Remove the plan note in #127's plan only if it states something now false (plans are records; do not edit #127's plan).

## Files that change

- `src/open_engine/_ledger.py`: `_json_form` and the new table.
- `src/open_engine/_types.py`, `_model.py`, `_config.py`, `_evaluation.py`, `_engine.py`: remove the pin lines.
- `src/open_engine/engine.py`: imports.
- `tests/test_public_class_identity.py`, a new `tests/test_digest_tags.py`.
- `CLAUDE.md`, `CHANGELOG.md`.

## Order of work

1. Save digests, snapshot JSON, and checkpoints from `main` for a varied synthetic ledger (every format, match type, status, game format with and without a cap, `played_at` with `timezone` and `ZoneInfo`, `confirmed_by` as a set and a frozenset, sequences), in the scratch directory.
2. Red tests, committed alone: the digest-ignores-`__module__` test and the table-coverage test fail on `main`.
3. Add the table and use it. Confirm digests equal the saved ones.
4. Remove the pins. Rewrite `tests/test_public_class_identity.py` per criterion 5 (an assertion change: the pin is gone by design).
5. Docs and changelog. Full gate.

## Risks

- A type the table misses gives a new tag and changes some digests, which would make resume reject unchanged rows. Mitigation: the coverage test, plus the saved-digest comparison over a ledger that exercises every field type, including containers given as `list` and as `tuple`.
- Removing the pins changes pickles made on the branch (they name the new modules), so they will not load on versions before this change. Pickles made before still load here. This is the same forward-compatibility trade #127 avoided; the PR says so.

## Proof

- Digests, snapshot JSON, and resume across `main` and the branch, both directions, byte-identical.
- The new tests, red on `main`.
- Pickles: a `Config`, a replayed `RatingEngine`, and every public class pickled on `main` load on the branch.

## As built

- `_json_form` reads `_DIGEST_TAGS` through `_digest_tag`, which walks the table and compares `type(value) is key`, instead of `_DIGEST_TAGS.get(type(value))` as criterion 1 says. A dict lookup hashes the type and can run a metaclass `__hash__` or `__eq__`; an identity walk runs no caller code, matching the exact-type rule in CLAUDE.md. The table and its strings are the same, so no digest changes (checked against the digests, snapshot, and checkpoints saved from `main`). A test shows the lookup runs no metaclass `__hash__` or `__eq__`, and another pins the whole table, `bool` and `float` included.
