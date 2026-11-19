# Plan: Break up the remaining long engine functions (issue #143)

Risk tier: High. Same reasons as #128: it restructures snapshot validation (`_check_resumable`, `_snap_seed_sensitive_rejections`), the rating core (`_compute`, `_consider`), match validation, and evaluation, where a reordered or dropped check is a silent defect.

Domain skills: superpowers:test-driven-development, invoke. Framework skills: not applicable.

## Principle

Extract, do not rewrite, exactly as #128: each helper holds a contiguous block of the original body moved as written; every check keeps its order, error class, message, and the point at which state changes.

## Scope

| Function | Module | Body lines |
| --- | --- | --- |
| `_check_resumable` | `_snapshot.py` | 78 |
| `_snap_seed_sensitive_rejections` | `_snapshot.py` | 77 |
| `_prequential` | `_evaluation.py` | 70 |
| `RatingEngine.player_summary` | `_engine.py` | 66 |
| `RatingEngine._consider` | `_engine.py` | 65 |
| `RatingEngine._compute` | `_engine.py` | 65 |
| `fit_scale` | `_evaluation.py` | 64 |
| `Match.validate` | `_model.py` | 63 |
| `_network_connectivity` | `_connectivity.py` | 62 |
| `Match._validate_field_types` | `_model.py` | 61 |

## Acceptance criteria

1. No function in `src/open_engine/` has a body over 60 lines (excluding the docstring). Each new helper has a docstring saying what it checks or computes and in what order.
2. No behavior change: every public entry point returns equal results, raises the same error class and message, and leaves the same state after a failed call as on `main`. Fingerprints, digests, snapshots, ratings, predictions, and evaluation output unchanged.
3. No existing test changes. A new parity test pins outcomes from `main` in a form that does not depend on the version or the hash seed (no pinned fingerprints; derive version-dependent values at run time; avoid inputs whose message depends on set order unless #145 has merged).
4. Any doc that names a split function (`docs/model.md`, `docs/behavior.md`, `CLAUDE.md`, docstrings) is still true. `CHANGELOG.md`, `### Changed`.

## Order of work

1. Differential corpus from `main` (the #128 approach; reuse its generators if they are in the scratch directory: `/tmp/claude-1000/-home-bradstricherz-PycharmProjects-OPEN-engine/07ecd0b4-f645-40bd-b09d-f0f0915e5d0f/scratchpad/i128/scripts/`), extended to `prequential_predictions`, `evaluate`, `fit_scale`, `player_summary`, `Match.validate`, `ledger_order`, and connectivity (summaries with the connectivity cap set). Two-failure inputs for each pair of adjacent checks in each function. Record outcomes under a fixed hash seed.
2. Commit the parity test, passing on `main`.
3. Split one function at a time; rerun the corpus and the gate after each; commit.
4. Docs and changelog. Full gate.

## Risks

- As in #128: reordered checks, early returns, state changed early, a helper re-reading a caller value. Mitigation: two-failure inputs, post-call snapshots, the corpus after each function.
- `_prequential` and `fit_scale` hold loops whose order matters for determinism (`math.fsum`, ledger order). Mitigation: corpus covers both modes and fits, compared byte for byte.

## Proof

- The corpus, identical to `main` (counts per entry point).
- The parity test, passing on `main`, the branch, and with the version changed.
- The gate; `examples/basic_match.py` identical.
