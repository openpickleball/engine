# Plan: Config dicts can be changed after creation, which changes the fingerprint (issue #25)

Risk tier: High. `Config.fingerprint()` is the provenance record stored with every replay and checked by `from_snapshot`, and replay provenance is a security-sensitive area. The change alters existing behavior (mapping fields become read-only, equality and hashing change) and adds public API (`Config.to_dict`, `Config.from_dict`). It is fully unit-testable.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-celery, django-allauth, python-fastapi, React and Expo skills: not applicable because this is a standard-library-only Python package with no framework, API layer, or UI.

## Acceptance criteria

1. Mutating a mapping field of a `Config` raises (`TypeError`) for `scale_by_scoring`, `win_calibration_by_scoring`, `match_weights`, and `game_length_weights`: item assignment, deletion, `update`, `clear`, `pop`. The fingerprint is unchanged after the attempt.
2. Mutating the dict a caller passed in after construction does not change the `Config` or its fingerprint (the config holds its own copy).
3. A mutable `set` passed as `allowed_game_formats` is stored as a `frozenset`, so it cannot change either. Any other type raises `ConfigError`.
4. A mapping field given a value that is not a mapping raises `ConfigError`.
5. `Config` is hashable. Two configs are equal exactly when their fingerprint payloads are equal, so equal configs have equal fingerprints and equal hashes. A config is never equal to a non-`Config`.
6. The fingerprint of `Config()` stays `c5bb49f7c37a013a`, and no existing config's fingerprint changes.
7. `Config.to_dict()` returns plain JSON-safe data (str keys, lists, numbers, bools, strings, `None`). `Config.from_dict(cfg.to_dict())` equals `cfg` with the same fingerprint, also after a `json.dumps`/`json.loads` round trip, for the default config and for configs with non-default mappings, formats, and optional gaps.
8. `Config.from_dict` raises `ConfigError` for a non-mapping, missing keys, unknown keys, an unknown enum value, a bad game length key, a malformed `allowed_game_formats` entry, and any value `Config` rejects.
9. `copy.copy`, `copy.deepcopy`, `pickle`, and `dataclasses.replace` still work on a `Config` and give an equal config. `fit_scale`'s `replace(cfg, scale_by_scoring={**cfg.scale_by_scoring, ...})` still works.
10. Docs: README "Public API" `Config` line, the `Config` docstring, the new methods' docstrings, and `CHANGELOG.md` under `[Unreleased]` (Added: `to_dict`, `from_dict`, hashing; Changed: read-only mappings and the new equality rule, marked breaking; Fixed: issue #25).

## Files that change

- `src/open_engine/engine.py`:
  - A private `_FrozenMapping(Mapping[K, V])`: `__slots__ = ("_data",)`, holds a dict copy, read-only, `__hash__` over its items, a `__repr__`, picklable and deep-copyable.
  - `Config.__post_init__`: before the existing checks, require each of the four mapping fields to be a `Mapping` (else `ConfigError`) and replace it with a `_FrozenMapping` copy (`object.__setattr__`); require `allowed_game_formats` to be a `set` or `frozenset` and store a `frozenset`.
  - `Config._payload()` (private): today's `norm` payload. `fingerprint()` hashes `json.dumps(payload, sort_keys=True)` exactly as now. `__eq__` compares that canonical JSON string for two `Config`s (`NotImplemented` otherwise); `__hash__` hashes it. Decorator `@dataclass(frozen=True, eq=False)` so the dataclass does not generate either.
  - `Config.to_dict()` and `Config.from_dict()` (classmethod).
- `tests/test_config_immutable.py`: new tests for criteria 1 to 9.
- `tests/test_api.py`: only if the docstring test needs the new methods registered.
- `README.md`, `CHANGELOG.md`.

## Order of work

1. Write the failing tests (criteria 1 to 9), run them red on this branch before any engine change.
2. Implement `_FrozenMapping` and the `__post_init__` freeze, then equality and hashing, then `to_dict`/`from_dict`.
3. README, changelog, docstrings.
4. Gate: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.

## Risks

- Fingerprint drift: the payload and JSON call must stay byte for byte as now. A test pins the default fingerprint.
- Equality change: `Config(k_min=0) == Config(k_min=0.0)` was `True` and becomes `False`, since their fingerprints already differ. This is intended (hash must agree with equality, and equality with the fingerprint) and goes in the changelog.
- `MappingProxyType` would break `pickle` and `deepcopy`, which is why a small private mapping class is used instead.
- Code that called `dict` methods (`update`, item assignment) on a config mapping breaks. That is the fix.

Not done here: type and range checks for every `Config` field (for example `match_weights` values, or `k_min="x"` raising `TypeError` instead of `ConfigError`). Filed as a separate issue.

## Proof

- Criteria 1 to 5, 7 to 9: `tests/test_config_immutable.py`.
- Criterion 6: a test pins `Config().fingerprint() == "c5bb49f7c37a013a"`.
- Measured on `origin/main` (946a143): `Config().fingerprint()` is `c5bb49f7c37a013a`; `cfg.match_weights[MatchType.LEAGUE] = 9` succeeds and the fingerprint becomes `82da7590cd297e04`; `hash(Config())` raises `TypeError: unhashable type: 'dict'`; `copy.deepcopy` and `pickle` of `Config()` work; `Config(k_min=0) == Config(k_min=0.0)` is `True`.
