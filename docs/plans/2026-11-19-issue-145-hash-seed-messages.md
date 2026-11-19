# Plan: Make Config error messages independent of the hash seed (issue #145)

Risk tier: Medium. It changes which message some invalid inputs get (the message becomes fixed instead of hash-dependent), on input validation paths.

Domain skills: superpowers:test-driven-development, invoke. Framework skills: not applicable, a standard-library Python package.

## Acceptance criteria

1. For any input, every error the engine raises has the same class and message under any `PYTHONHASHSEED`. Every valid input gives the same result as on `main`, `Config.fingerprint()` and digests unchanged.
2. Each check that walks a set or frozenset (or anything built from one) and raises on the first failing member walks it in a fixed order:
   - members that are already plain, checked values (for example the stored `allowed_game_formats` in `_check_mapping_values`) are walked in sorted order;
   - members that are a caller's unchecked objects (for example a caller's `allowed_game_formats` in `_store_game_formats`, or the set given to the Config rebuild) are never sorted or compared before their types pass, since that would run caller code (CLAUDE.md, Exact input types). For those, the message must not depend on which member is found first: either it names no member (as today for the type messages), or every member is type-checked first and the reported one is chosen in a fixed order among the plain ones.
3. A message that changes is one that varied with the hash seed on `main`; record each, old and new, in the plan and the changelog. A message that was already fixed does not change.
4. Tests: one per changed check, running the input in subprocesses under at least five hash seeds and asserting one message. Committed red first.
5. `CHANGELOG.md`, `## [Unreleased]`, `### Fixed`.

## Files that change

- `src/open_engine/_config.py` (and any other module the survey finds).
- New `tests/test_hash_seed_messages.py`.
- `CHANGELOG.md`.

## Order of work

1. Survey, from `main`: every loop, `any`, `all`, `next`, `min`, `max`, or comprehension over a set, frozenset, or set-derived iterable on a path that can raise; and run a corpus of invalid inputs to every public entry point (reuse the approach from #128: Config single and paired field breaks, `from_dict`, `from_snapshot` mutations, seeds, matches) under five hash seeds in subprocesses, and list every input whose message differs across seeds. Record the list in the plan.
2. Red tests for each varying case, committed alone.
3. Fix each per criterion 2.
4. Rerun the corpus under the five seeds: no differences. Changelog. Full gate.

## Risks

- Sorting caller objects runs caller code. Mitigation: criterion 2; sort only checked plain values.
- Changing a message that tests or the README name. Mitigation: only messages that varied change, and their text stays the same apart from which member is named.

## Proof

- The corpus run under five hash seeds, before (list of varying inputs) and after (none).
- The new tests, red on `main`.
- Measured on `main`: `Config(game_length_weights={11: 1.0})` names 15 or 21 depending on the seed.

## Survey result (step 1, on `main` at c447915)

Static: three checks walk a set and raise on the first failing member with a message that depends on which member comes first. Every other set walk on a raising path either raises one fixed message, checks a whole set at once (`<=`, `!=`), or sorts before naming ids (`LedgerConflictError`, the resume and snapshot checks).

- `Config._check_mapping_values`: walks the stored `allowed_game_formats` and names the first game length with no `game_length_weights` entry.
- `Config._store_game_formats`: walks a caller's `allowed_game_formats`; a set with a non-member `ScoringType` and another bad entry gives "has a ScoringType that is not a member" or "entries must be (scoring type, whole number) pairs" by order.
- `_config._rebuilt_formats` (the Config rebuild every entry point taking `cfg` runs): walks a tampered `allowed_game_formats` and names the first bad entry's type ("entries must be pairs, not str", "must hold whole numbers, not bool", "has a key or entry of type NoneType", "has an unknown scoring type").

Measured: the #128 corpus (112,724 cases: Config single, pair, and multi field breaks, `from_dict`, `record`, seeds, `add_player`, `from_snapshot` and resume mutations, `reliability`, `player_summary`) under `PYTHONHASHSEED` 0 to 4, plus a set-focused corpus (7,765 cases: `Config(allowed_game_formats=...)` with two to six mixed entries, a tampered `allowed_game_formats` through `reliability`, `RatingEngine`, `replay`, `evaluate`, `prequential_predictions`, `fit_scale`, and `fit_win_calibration`, `Match.confirmed_by`, ledger conflicts, and the win probability functions) under seeds 0 to 6. The error class never varied (always `ConfigError`). The message varied for:

- #128 corpus, 4 inputs: `Config(game_length_weights={})` and the same through `from_dict` (three bases): "No game_length_weight for games to 11" or "... 15" (all three default lengths are missing). `Config(game_length_weights={11: 1.0})` gives "... 15" or "... 21".
- Set corpus, 2,598 inputs: 872 `Config(allowed_game_formats=...)` (the missing game length named, or the pair versus non-member message), 774 tampered through `reliability` and 952 tampered through the other six entry points (the entry type named).

The only valid-input variation is a `Match` repr, whose `confirmed_by` frozenset prints in set order; that is not an error and is not in scope.

## Fix and departures (step 3)

Old and new messages:

- `_check_mapping_values` walks the distinct stored game lengths in sorted order and names the shortest one with no weight. `Config(game_length_weights={})`: "No game_length_weight for games to 11" or "... 15", now always "... 11". `Config(game_length_weights={11: 1.0})`: "... 15" or "... 21", now "... 15".
- `_store_game_formats` checks in three passes over all entries: every entry is a two-item tuple, then no first item is a non-member `ScoringType`, then each scoring type and whole number. Old: "allowed_game_formats entries must be (scoring type, whole number) pairs" or "allowed_game_formats has a ScoringType that is not a member" by set order. New: the first when an entry is not a two-item tuple, else the second when a non-member is present. No caller code runs before the passes that need none, and the third pass runs the same code per entry as before.
- `_rebuilt_formats` checks every entry (by real types only, so no caller code runs) and raises the smallest message in sorted order. Old: the type of whichever bad entry came first. New: for example "cfg.allowed_game_formats entries must be pairs, not float" for `{"ab", 1.5, (SO, True)}`.

Departure from criterion 3: a tampered `allowed_game_formats` (set after construction with `object.__setattr__`) whose entries all hash without the seed (ints, floats, `None`, tuples or frozensets of those, for example `{5, (1.5, 11)}`) already iterated in a fixed order on `main`, so its message was fixed and the sorted rule can change it. Measured: 32 corpus inputs (6 distinct tampered sets, through the seven entry points) stayed the same over 39 hash seeds on `main` and change on the branch. Five of the sets hold only such entries. The sixth, `{(SO, 11.5), (5, 11), (SO, 2.5)}`, holds `ScoringType` members, so its order can change with the seed, but did not over 39 seeds. Keeping them would need a rule that picks set order or sorted order by each entry's hash type, and `hash(None)` is not fixed on Python 3.11 anyway. The same holds on the `Config(...)` path and the rebuild path for a set whose entries all hash without the seed together with a non-member `ScoringType` built on the empty string (`f = str.__new__(ScoringType, ''); f._value_ = ''; f._name_ = 'X'`), whose hash is always 0: `Config(allowed_game_formats=frozenset({(f, 11), (5, 11)}))` said "allowed_game_formats entries must be (scoring type, whole number) pairs" under every seed on `main` and now says "allowed_game_formats has a ScoringType that is not a member", since the non-member pass runs before the scoring and points pass. The maintainer accepted this departure.

## Folded issues (maintainer, after review)

1. Caller code in the third pass of `_store_game_formats`. `_plain_number`'s `isinstance` ran a `__class__` property and `entry_scoring in _SCORING_VALUES` ran a caller's `__hash__` and `__eq__`, so a caller's exception could escape instead of `ConfigError`, depending on set order. Now every entry is judged by its real type alone (`_format_scoring`: a member by identity, or exactly a `str` naming one; `_format_points`: a real `int`, not a `bool`, converted with `int.__int__`, finite as a float) before any membership test, and the message is the fixed "pairs" one. Rejected now and accepted on `main`: a `str` subclass or other lookalike scoring type (Breaking; the existing test `test_a_lookalike_scoring_type_in_a_format_is_stored_as_the_enum` became `..._is_rejected` with the maintainer's approval). An object whose `__class__` property raises now gives `ConfigError` instead of its own exception.
2. Game lengths of 0 or less in `allowed_game_formats` are rejected while storing, after the pairs pass, with "allowed_game_formats points must be at least 1" (the `game_length_weights` keys say "keys must be at least 1"). `from_dict` and the rebuild go through the same check. Old message: "No game_length_weight for games to 0" (or the negative length). The check now precedes the enum mapping checks.
3. README and `docs/behavior.md` state the scoring type and points rule.

Measured after the folded fixes (corpus under seeds 0 to 4, set corpus under 0 to 6, against `main` over 5 and 39 seeds): nothing varies on the branch. Inputs stable on `main` that change: 7 in the #128 corpus, all a game length of 0 or less ("No game_length_weight for games to 0" to "points must be at least 1"); 102 in the set corpus: 45 game lengths of 0 or less, 25 a `str` subclass scoring type ("No game_length_weight for games to 17" to the pairs message), and 32 the tampered sets above. Every other outcome, valid results and fingerprints included, is the same as on `main`.
