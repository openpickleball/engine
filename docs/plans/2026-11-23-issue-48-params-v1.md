# Plan: Version tuned parameters and document the retuning policy, V1 part (issue #48)

Risk tier: High. The change adds a public `Config` method and two public `PlayerSummary` fields (the API surface file changes), and it adds a check on config provenance, which CLAUDE.md names a security-sensitive area. Plan approval was delegated by the repo owner for this run, so the tier is recorded and work proceeds.

Domain skills: django, django-ninja-api, django-allauth, django-celery, python-fastapi: not applicable because this is a standard-library-only Python package with no web framework. Frontend, React, Expo, design, and data visualization skills: not applicable because there is no UI. superpowers:test-driven-development: invoke (the implementer runs TDD). No payments, auth, or email code is touched.

## Scope

This pull request is the V1 part of #48. It does not close #48. Left for later: the change-cap helper and its tests (the docs page states the planned rule and says the helper is not written yet). Not touched: `Config()` defaults (the default-change rule kept them), release wording (#49), and #149.

## Inputs (the V1 numbers)

From the merged evaluation record in the private repository GeoBradDev/open-engine-evaluation, `reports/v1-fit-2026-11/v1-fit.md` and `run.json` (evaluation commit `995de996541608632b585c1d59d1089b2b6bc2c4`). Cite it by repository and path, never by URL: the repository is private and this repo does not link it.

- `scale_by_scoring`: side_out 1.0, rally 1.0 (declared unit).
- `win_calibration_by_scoring`: side_out 0.476 (fitted, not at its search bound, 667 predictions), rally 1.0 (declared).
- `k_min` 0.1079136690647482, `k_max` 0.539568345323741 (default K times 1.0 / s_fit, s_fit = 1.112, not at bound, 667 matches scored). Use these exact floats, not a recomputation (`0.12 / 1.112` gives a different last digit).
- Every other field at its `Config()` default.
- Data: 1,601 matches from 2025-08-10 to 2026-11-20, all doubles and side-out (1,283 league, 318 tournament, 0 rally), 52 match dates, 189 players. Freeze date 2026-11-20. Fit window from 2026-04-12. Data version `28bcab9de3af`. 0 matches touch the rating floor or ceiling.
- The evaluation ran engine 0.3.0.dev0 at commit `e62468c508126ed46b5a26a82f175ba17dd78371`. Its fingerprints use the scheme before #155: V1 `22b53a37587d6881`, `Config()` `17f2981186edf96c`. Measured here: hashing the run's `v1_config` dict (`version` "0.3.0.dev0", no `params_version` key) with `json.dumps(sort_keys=True)` and SHA-256 gives `22b53a37587d6881`, and `Config(version="0.3.0.dev0").to_dict()` without `params_version` gives `17f2981186edf96c`. On this engine, the V1 `parameter_fingerprint()` is `f9149f3c38a01efc`, and `Config().parameter_fingerprint()` is `1058623faf5d9367`. They differ from the old ones because the old `fingerprint()` hashed the package version and the payload had no `params_version` key; `parameter_fingerprint()` leaves out both labels. The rating math is unchanged between `e62468c` and this branch (only #155's label fields and tests and docs landed since), so the V1 values mean the same here.
- Default-change rule, fixed before running: `Config()` minus V1 on 1,018 pooled matches over 6 folds, two-way bootstrap, 95% intervals. Brier +0.004680 [-0.004359, +0.013895]; log loss +0.009338 [-0.010563, +0.029472]. The gain bar (a lower bound above 0) failed; the -0.005 non-inferiority bar passed. Decision: `Config()` defaults are not replaced; V1 ships as the named set `v1`, loaded explicitly; revisit the defaults with the same rule on new data after the pre-registered future-play test (evaluation issue #26).

## Decisions

1. **Loader API: `Config.from_parameter_set(data: Mapping[str, object]) -> Config`**, a classmethod next to `from_dict`. It takes the parsed JSON of a `params/v<N>.json` file (`json.loads` output), so the engine does no file I/O and the wheel need not ship the files. It trusts the label only after the values hash to the recorded fingerprint. Checks, in this order, each raising `ConfigError`:
   1. `data` is exactly a `dict`.
   2. Every key is exactly a `str` (checked before any set operation or lookup, as `from_dict` does).
   3. The keys `params_version`, `parameter_fingerprint`, and `parameters` are present (message names the missing ones, sorted). Other keys (provenance, data volume, dates) are allowed and not read.
   4. `params_version` and then `parameter_fingerprint` are each exactly a `str`. (Added in implementation: `Config` accepts `params_version=None`, so without this check a file with a `null` label and the right values would load as an unlabelled config instead of raising, and criterion 4 requires `None` to raise. The form `v<N>` is checked in step 7, before `from_dict`.)
   5. `parameters` is exactly a `dict` with exactly-`str` keys, and holds neither `version` nor `params_version` (the labels come from the file's own `params_version` and the running package, never from the values).
   6. `parameter_fingerprint` is exactly 16 lowercase hex characters (`[0-9a-f]{16}`, full match). The message does not echo the value.
   7. `params_version` has the form `v<N>` (the message does not echo the value).
   8. `params_version` is a key of `_PUBLISHED_PARAMETER_SETS` in `_config.py`, the release's table of (label, parameter fingerprint) pairs, else `ConfigError` "is not a parameter set this release publishes" naming the published labels.
   9. `parameter_fingerprint` equals the table's fingerprint for that label, else `ConfigError` naming the label, the recorded and the published fingerprint.
   10. The config is built with `Config.from_dict({**parameters, "version": __version__, "params_version": data["params_version"]})`, so `from_dict`'s key and value checks apply unchanged.
   11. `cfg.parameter_fingerprint()` equals `data["parameter_fingerprint"]`, else `ConfigError` naming both the recorded and the computed fingerprint, and the label.
   Returns the config, with `version` the running package version and `params_version` the file's label.
2. **`params/v1.json`** at the repository root, JSON with two-space indent and sorted keys, ending in a newline:
   - `params_version`: `"v1"`.
   - `parameter_fingerprint`: `"f9149f3c38a01efc"`.
   - `frozen_on`: `"2026-11-20"` (the data freeze date).
   - `data`: `matches` 1601, `first_match_date` "2025-08-10", `last_match_date` "2026-11-20", `match_dates` 52, `players` 189, `doubles` 1601, `side_out` 1601, `rally` 0, `league` 1283, `tournament` 318, `fit_window_start` "2026-04-12", `data_version` "28bcab9de3af".
   - `fitted_on_engine`: `version` "0.3.0.dev0", `commit` "e62468c508126ed46b5a26a82f175ba17dd78371", `fingerprint` "22b53a37587d6881", `default_fingerprint` "17f2981186edf96c", `note`: one sentence saying those two use the scheme before `parameter_fingerprint()`, which hashed the package version and had no `params_version` key.
   - `evidence`: `"GeoBradDev/open-engine-evaluation, reports/v1-fit-2026-11/v1-fit.md, evaluation commit 995de996541608632b585c1d59d1089b2b6bc2c4"`.
   - `parameters`: the V1 config's `to_dict()` without `version` and `params_version`.
3. **Existing ratings rule: re-rate all history.** When a new parameter set goes live, authoritative ratings are rebuilt by a full replay of the whole ledger under it; there is no "future matches only" mode. Reasons: authoritative ratings already come from full scheduled replays, and a rating must be reproducible from one ledger and one config; mixing sets would make a rating depend on when a match was played relative to a parameter change; `from_snapshot` already refuses a checkpoint whose `params_version` or `parameter_fingerprint` differs from the given config, so a resume cannot carry old-set state forward. Before a re-rate goes live, the shift by percentile (old against new on the same ledger) is published. For v1 no shift report is needed: no OPEN rating has been published, so there is nothing to shift. This is stated in the docs page and in `PARAMETERS.md`.
4. **`PlayerSummary` carries `params_version` and `parameter_fingerprint`**, appended at the end (after `connectivity`), which docs/behavior.md "Stability" lists as an allowed addition. Values from the engine's config: `cfg.params_version` and `cfg.parameter_fingerprint()`. `config_version` stays. So every shown rating names the label (for people), the values hash (to check the label), and the engine release. No snapshot change: `PlayerSummary` is not stored.
5. **No length bound on N** in `params_version`: the regex is linear and the label names a file. Recorded in the #48 comment, not changed.
6. **sdist ships `params/`**: the sdist ships `tests/`, and the new tests read `params/`, so add `"params"` to `[tool.hatch.build.targets.sdist] only-include` in `pyproject.toml` and to `ALLOWED_TOP_LEVEL` in `scripts/check_sdist.py` (update its comment, and `tests/test_check_sdist.py` if it pins the set). The wheel does not change.

## Acceptance criteria

1. `params/v1.json` exists with the fields in decision 2. `Config.from_parameter_set(json.loads(...))` returns a config with `params_version == "v1"`, `parameter_fingerprint() == "f9149f3c38a01efc"`, `version == __version__`, the four V1 values exactly as listed, and every other field equal to `Config()`'s.
2. The file's `parameters` equal the loaded config's `to_dict()` without the two labels, key for key.
3. Hashing the file's `parameters` plus `"version": "0.3.0.dev0"` with `json.dumps(sort_keys=True)` and SHA-256 (first 16 hex) gives `22b53a37587d6881`, the fingerprint the evaluation recorded.
4. `from_parameter_set` raises `ConfigError` for: a non-dict (and a dict subclass); a non-str top-level key; each of the three required keys missing; a non-str `parameter_fingerprint`; a non-dict `parameters`; `parameters` with a non-str key; `parameters` holding `version` or `params_version`; a `parameters` missing a field or with an unknown field; a malformed or non-str `params_version` (`"v01"`, `"1"`, `1`, `None`); a changed value (for example `k_min` + 1e-12) with the original fingerprint; the right values with a wrong fingerprint. A wrong fingerprint's message names the label, the recorded and the published fingerprint; a changed value's message names the recorded and computed fingerprints. Extra top-level keys are accepted. Review round 1 adds: a self-consistent forged `v1` (other values with their own true fingerprint), the v1 values relabelled `v2`, a self-consistent unknown label, a fingerprint that is not 16 lowercase hex characters (message bounded, value not echoed), a test that `_PUBLISHED_PARAMETER_SETS` and the `params/` files match exactly, and one test per adjacent pair of checks (`tests/test_parameter_set_binding.py` and `tests/test_parameter_set_check_order.py`, both frozen). A relabelled set no longer loads.
5. Every `params/*.json` file loads, its name is `<params_version>.json`, its text is canonical (`json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"`), and `PARAMETERS.md` has the whole line `## <label>` with the exact line ``File: `params/<label>.json`. Parameter fingerprint: `<fp>`.`` in that section (a test checks each). The sdist ships `PARAMETERS.md` too, since the test reads it.
6. `PlayerSummary` ends with `params_version` and `parameter_fingerprint`; a default engine's summary gives `None` and `"1058623faf5d9367"`, and an engine on the loaded v1 config gives `"v1"` and `"f9149f3c38a01efc"`.
7. `Config()` defaults are unchanged (`Config().parameter_fingerprint() == "1058623faf5d9367"` still passes; no default edited).
8. `tests/api_surface.txt` is regenerated by `python tests/test_api_surface.py` and differs only by `from_parameter_set` and the two `PlayerSummary` fields.
9. Docs: a new `docs/parameters.md` ("Maintenance and parameter versioning") covers identity and what a shown rating carries, the file format and the loader, the default-change rule and why `Config()` is not v1, the existing-ratings rule, the evidence each retune needs (a gate on held-out data and a shift report, both produced by the evaluation), change caps (the planned rule, and that the helper is not written yet), the schedule (data milestones such as 5k, 10k, 25k matches early, then every six months once parameters settle, and only when the gate passes), and what a release that changes the `Config` payload does to published files. `PARAMETERS.md` at the root has the v1 entry: values (the four that differ, and "every other field at its default"), data volume and dates, the default-change rule and its result, the shift summary ("none: no ratings published"), the fingerprints and the reason. `README.md` (Config and PlayerSummary lines, and a pointer to the new page), `docs/behavior.md` ("Player summaries" fields: `connectivity` is no longer the last field; the new fields; a short loader note in "Config validation" listing its checks in order; backend requirements: show `params_version` with a published rating), `docs/model.md` (the Parameters section mentions the published set and the loader), `CLAUDE.md` (file list and docs list: `params/`, `PARAMETERS.md`, `docs/parameters.md`; the Config provenance rule: a change to the `Config` payload breaks every published file's fingerprint, see the docs page), and `CHANGELOG.md` under `## [Unreleased]` are true of the code. Docs keep the demonstration label and name no competing rating system. No em dashes, no emojis.
10. The full gate passes: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest -n auto`.

## Files that change

- `src/open_engine/_config.py`: `from_parameter_set` classmethod after `from_dict`, with a docstring in the repo's style (Args, Returns, Raises listing the checks in order).
- `src/open_engine/_model.py`: two `PlayerSummary` fields at the end and its docstring.
- `src/open_engine/_engine.py`: `player_summary` fills them.
- `params/v1.json` (new), `PARAMETERS.md` (new), `docs/parameters.md` (new).
- `tests/test_parameter_sets.py` (new): criteria 1 to 7. `tests/api_surface.txt` regenerated. Any test that constructs `PlayerSummary` positionally or pins its field list is updated for the appended fields (intended surface change).
- `pyproject.toml`, `scripts/check_sdist.py` (and its test if it pins the set).
- Docs: `README.md`, `CHANGELOG.md`, `CLAUDE.md`, `docs/behavior.md`, `docs/model.md`.

## Order of work

1. Red tests for the loader (criteria 1 to 5, with `params/v1.json` written first, generated from the V1 values with a throwaway script in the scratch directory, not committed). Then implement `from_parameter_set`.
2. Red test for criterion 6, then `_model.py` and `_engine.py`. Regenerate `tests/api_surface.txt`.
3. sdist changes.
4. `PARAMETERS.md`, `docs/parameters.md`, other docs and the changelog.
5. Full gate.

## Risks

- Trusting the label before the check: the loader must compute the fingerprint from the values it built, never return early or accept a label alone. Criterion 4's tamper tests catch this.
- A label smuggled in through `parameters` (`version`, `params_version`) overriding the file's label: rejected explicitly.
- Float drift: the file must hold the exact floats from the evaluation. Criterion 3 ties the file to the evaluation's recorded fingerprint, so a changed digit fails.
- A future `Config` payload change makes every file written for the old payload fail to load (missing key and a new fingerprint). That is intended. Decision from review round 2: a label keeps naming the same rating behavior, so that release rewrites each file under its label in the same change (the new field at the value that keeps ratings unchanged, the new fingerprint, the matching table entry, and a register note with both fingerprints), or retires the set and publishes a new label when no such value exists. Every file in `params/` loads under the release that ships it. Not done here: rewriting files.
- Riskiest step: docs claims about re-rating and identity; keep them true of the code (`from_snapshot`'s checks exist in `_snapshot.py`).
- Not done: the change-cap helper, a `to_parameter_set` writer, a length bound on N, any `Config()` default change.

## Review round 1

- The loader compared the values only with a fingerprint from the same file, so any self-consistent file loaded under any label. It now checks the label and the recorded fingerprint against `_PUBLISHED_PARAMETER_SETS` (decision 1, checks 6 to 9). A new set needs an engine release that adds its file, its register entry, and its table entry together.
- The documented order put the label form after `from_dict`, but `Config` checks it before any value. With the table check the label is validated before `from_dict`; the docstring and docs state the real order, and a test covers each adjacent pair.
- A malformed fingerprint was echoed into the message without bound. Messages now echo a fingerprint or label only after it passed its form check.
- Docs: the non-inferiority bar is on Brier only; `docs/model.md` names the two labels again; the changelog marks the two new `PlayerSummary` fields as breaking (no defaults); the loader is described as taking parsed JSON and checking the published table.
- Tests: the register test requires the exact heading and fingerprint line, and every `params/*.json` file is checked for canonical text. `PARAMETERS.md` ships in the sdist.

## Proof

- Criteria 1 to 7: `tests/test_parameter_sets.py`, plus the existing pinned default fingerprint test.
- Criterion 8: `tests/test_api_surface.py` and `git diff tests/api_surface.txt`.
- Criteria 9 and 10: doc review and the gate output.
- Live drive: a short script that loads `params/v1.json` through `from_parameter_set`, prints the label and both fingerprints, replays a few synthetic matches under it and under `Config()`, prints a `PlayerSummary` from each showing the new fields, and shows the `ConfigError` for a tampered `k_min`.
- Measured on the default branch: the old-scheme fingerprints (`22b53a37587d6881`, `17f2981186edf96c`) and the new parameter fingerprints (`f9149f3c38a01efc`, `1058623faf5d9367`), computed with this branch's base (`8e5aca4`) from the evaluation's `run.json` `v1_config` as described under Inputs. No "behave alike" test compares against default-branch behavior.
