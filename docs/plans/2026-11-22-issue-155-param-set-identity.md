# Plan: Parameter set identity independent of the package version (issue #155)

Risk tier: High. The change adds a public `Config` field and method, changes the public `ReplayInfo` record and the snapshot schema (a `snapshot_version` bump), and touches config provenance, which CLAUDE.md names a security-sensitive area. Plan approval was delegated by the repo owner for this run, so the tier is recorded and work proceeds.

Domain skills: django, django-ninja-api, django-allauth, django-celery, python-fastapi: not applicable because this is a standard-library-only Python package with no web framework. Frontend, React, Expo, design, and data visualization skills: not applicable because there is no UI. superpowers:test-driven-development: invoke (the implementer runs TDD). No payments, auth, or email code is touched.

## Problem

`Config.fingerprint()` hashes `version`, and `Config.version` is the package version, which moves with every release. So the same parameter values get a new fingerprint at each patch release. A published rating needs a parameter identity that survives a release that changes no rating. Issue #48 (published parameter sets `params/v<N>.json` and `PARAMETERS.md`) builds on this and is out of scope here.

## The identity scheme

- **`Config.params_version: str | None = None`**, a new field placed right after `version`. `None` means the config is not a published parameter set (today's defaults are placeholders, so the default is `None`). Otherwise it must be exactly a `str` (not a subclass) matching `v<N>`, where `N` is a positive whole number written in ASCII digits with no leading zero (`re.fullmatch(r"v[1-9][0-9]*", value)`, checked only after the exact-str type check). So `"v1"`, `"v12"`; not `"v0"`, `"v01"`, `"1"`, `"V1"`, `"v1 "`, `"v1\n"`. The form maps one to one to a file name such as `params/v1.json`, which #48 will add. It is a label: it changes no rating.
- **`Config.parameter_fingerprint() -> str`**: the first 16 hex characters of the SHA-256 of `json.dumps(payload, sort_keys=True)`, where `payload` is `_payload()` (the same walk over every field of `Config` itself that `fingerprint()` uses) minus the two label keys `version` and `params_version`. So it identifies the parameter values alone: the same values under another package version or another `params_version` give the same parameter fingerprint, and a change to any other field changes it. It has the same format as `fingerprint()` (16 lowercase hex characters).
- **`Config.fingerprint()` is unchanged in method**: it still hashes every field, `version` included, and now `params_version` too (a new field is in the payload automatically). So no provenance is lost. Its value changes for every config, because the payload gains a key.
- **Where it is exposed**: `Config.params_version`, `Config.parameter_fingerprint()`, and two new `ReplayInfo` fields, `params_version: str | None` and `parameter_fingerprint: str`, placed right after `config_fingerprint`, set by `replay` from the engine's config copy. They go into the snapshot's `replay_info` object.

A published rating is then identified by the engine version (the code, which holds the model constants that are not `Config` fields), `params_version` (the human label), and `parameter_fingerprint` (the values). The full `config_fingerprint` stays for exact provenance.

## Acceptance criteria

1. `Config().params_version is None`; `Config(params_version="v1").params_version == "v1"`.
2. `Config(params_version=x)` raises `ConfigError` for a non-str (int, bytes), a str subclass, and each malformed str listed above, with a message naming `params_version`. The type check runs in `_check_scalar_fields` right after the `version` check, and that helper's docstring lists it in order.
3. `parameter_fingerprint()` returns 16 lowercase hex characters, is deterministic, and is equal for two configs that differ only in `version` and/or `params_version`.
4. `parameter_fingerprint()` differs between two configs that differ in any one other field. A test walks every field of `Config` other than the two labels (taken from `dataclasses.fields(Config)`, so a new field is covered automatically) and changes it to another valid value.
5. `fingerprint()` still differs when only `version` differs, and now also when only `params_version` differs.
6. `to_dict()` includes `params_version`; `from_dict(to_dict())` round-trips a config with `params_version` `None` and `"v3"`, also through `json`. `from_dict` still requires exactly the `to_dict()` keys, so a stored config dict without `params_version` raises `ConfigError` (a breaking change, recorded in the changelog).
7. `replay(...)` gives a `ReplayInfo` whose `params_version` and `parameter_fingerprint` equal the config's.
8. `to_snapshot()` writes `snapshot_version` 9 and both new keys in `replay_info`. `from_snapshot` round-trips them, and rejects a `replay_info` whose `params_version` or `parameter_fingerprint` does not equal the given config's, a missing or extra key, and a version 8 snapshot. A resume from a checkpoint still equals a full replay.
9. The public API checks still pass: `ReplayInfo`'s docstring and the README's `ReplayInfo` and `Config` lines describe the new fields and method; the new method has a docstring.
10. Docs: `docs/behavior.md` (Config validation rule for `params_version`; snapshot schema version 9 with the new keys and a line saying what version 9 adds; the from_snapshot check list; backend requirements line on storing `ReplayInfo`), `docs/model.md` (Parameters section: the two fingerprints and `params_version`, and the parameter table or the note under it), `README.md`, and `CHANGELOG.md` under `## [Unreleased]` (added field and method, ReplayInfo fields and their position, snapshot version 9 and that version 8 snapshots no longer load, every config fingerprint changes, stored config dicts need the new key) are true of the code.
11. The full gate passes: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest -n auto`.

## Files that change

- `src/open_engine/_config.py`: the field, its check (type then format) in `_check_scalar_fields`, its comment, the `Config` docstring line about `fingerprint()`, `_payload` reuse, the new method. Add `params_version` to any error-message table (`_CONFIG_TYPE_MESSAGES`) if the existing pattern needs it. Check `from_dict` handles a `None` value for a str-or-None field (look at how other optional fields convert).
- `src/open_engine/_model.py`: the two `ReplayInfo` fields and its docstring.
- `src/open_engine/_replay.py`: set the new fields.
- `src/open_engine/_snapshot.py`: `SNAPSHOT_VERSION = 9`, `_REPLAY_INFO_KEYS`, `_snap_replay_info` validation and construction (keyword arguments, pass the config's values in from `_snap_header`, and update `_snap_header`'s docstring order if it changes).
- `src/open_engine/_engine.py`: only if `to_snapshot` or `_replay_info_value` needs a change (`None` passes through `_iso`; confirm), and any `ReplayInfo` construction there.
- Tests: a new `tests/test_parameter_identity.py` for criteria 1 to 8; existing tests that pin `snapshot_version` 8, the `replay_info` keys, positional `ReplayInfo(...)` construction, the Config field list, or STR_FIELDS in `tests/test_config_checks.py` are updated to the new schema (this is the intended schema change, not a test edited to pass). The `test_snapshot.py` field-coverage tests must pass without loosening them.
- Docs: `README.md`, `CHANGELOG.md`, `docs/behavior.md`, `docs/model.md`.

## Order of work

1. Red tests for criteria 1 to 6 (Config), then implement in `_config.py`.
2. Red tests for criterion 7, then `_model.py` and `_replay.py`.
3. Red tests for criterion 8, then `_snapshot.py` (and `_engine.py` if needed). Update existing tests that pin the old schema.
4. Docs and changelog.
5. Full gate.

## Risks

- Field order: inserting `params_version` after `version` shifts positional `Config(...)` arguments, and inserting the `ReplayInfo` fields after `config_fingerprint` shifts positional `ReplayInfo(...)` arguments. Both are pre-1.0 breaking changes; call them out in the changelog. Internal code must use keyword arguments.
- A label key left in the parameter payload, or a rating field dropped from it: criterion 3 and the every-field walk in criterion 4 catch both.
- Snapshot validation must compare against the config the caller gives, not trust the snapshot's own values (provenance).
- Riskiest step: the snapshot change, since many tests pin the schema. Do not loosen the coverage tests.
- Not done here: `PlayerSummary` does not gain the new fields (the issue asks for `ReplayInfo`; #48 decides what a shown rating carries), no `params/` directory, no `PARAMETERS.md`, no top-level snapshot key (the top-level `config_fingerprint` already covers `params_version`, and `replay_info` carries both values).

## Proof

- Criteria 1 to 6: `tests/test_parameter_identity.py` Config tests.
- Criterion 7: replay test in the same file.
- Criterion 8: snapshot round trip, mismatch, key, and version tests in the same file, plus the existing resume tests.
- Criteria 9 to 11: the existing public API tests and the full gate output.
- Live drive: run a short script that builds two configs differing only in `version`, prints both fingerprints and both parameter fingerprints, replays `examples/`-style synthetic matches, prints `ReplayInfo`, and round-trips a snapshot through `json`.
- No "behave alike" test compares against existing default-branch behavior: `parameter_fingerprint` and `params_version` do not exist on `origin/main`, so nothing there to measure.
