# Maintenance and parameter versioning

Status: demonstration only. The engine's output is not an OPEN rating, and no OPEN rating is live. This page sets the rules a published parameter set follows. Following them does not make engine output an OPEN rating.

A parameter set is a named group of `Config` values, such as `v1`. The sets live in `params/` at the repository root. [PARAMETERS.md](../PARAMETERS.md) lists each one with its values, data, evidence, and fingerprints.

## Identity

A config has two labels and two fingerprints.

- `Config.version` is the package version. It changes in every release.
- `Config.params_version` is the parameter set label, `v<N>`, or `None` when the config is not a published set. Any config can carry any label, so a label alone proves nothing.
- `Config.parameter_fingerprint()` hashes every field but those two labels. It names the parameter values. The same values under another release or another label give the same fingerprint.
- `Config.fingerprint()` hashes every field, both labels included. It names the exact config.

A label is trusted only when the parameter fingerprint equals the one published for that set.

## What a shown rating carries

Every `PlayerSummary` carries `config_version`, `params_version`, and `parameter_fingerprint`, taken from the engine's config. Every replay's `ReplayInfo` carries the config version, both fingerprints, and `params_version`. So a shown rating names the parameter set for people, the values hash that checks the label, and the release.

Take the engine version from the installed package (`open_engine.__version__`), not from `config_version`. A caller can set `Config.version` to any str without changing the parameter fingerprint.

## The file format

Each set is one file, `params/v<N>.json`, named after its label. It is JSON with sorted keys and a two-space indent, ending in a newline. The loader reads three keys:

- `params_version`: the label, such as `"v1"`.
- `parameter_fingerprint`: the recorded `parameter_fingerprint()` of the values.
- `parameters`: the config's `to_dict()` output without `version` and `params_version`.

The other keys are provenance for people, and the loader does not read them: `frozen_on` (the data freeze date), `data` (match counts, dates, and the data version), `fitted_on_engine` (the engine release, commit, and fingerprints the evaluation ran with), and `evidence` (where the evaluation record lives). A file holds parameter values and counts only, never match data.

## The loader

`Config.from_parameter_set(data)` takes the parsed file (`json.loads` output). The engine reads no file, and the wheel does not ship `params/`. A caller reads the file and passes the result:

```python
import json
from pathlib import Path

from open_engine import Config

cfg = Config.from_parameter_set(json.loads(Path("params/v1.json").read_text()))
```

It builds the config with `version` set to the running package version and `params_version` set to the file's label. Then it checks that the built config's `parameter_fingerprint()` equals the recorded one. Any failure raises `ConfigError`. A label inside `parameters` is rejected, so the values cannot override the file's label. [Config validation](behavior.md#config-validation) lists every check in order.

## Why `Config()` is not v1

A new set replaces the `Config()` defaults only when it passes the default-change rule. The rule is fixed before the evaluation runs. It compares `Config()` against the new set on held-out matches, with a bootstrap interval on the difference in Brier score and in log loss. The gain bar needs the lower bound of the improvement above 0.

v1 did not clear the gain bar. It passed the non-inferiority bar, so it is no worse within the stated margin, but it is not shown to be better. So `Config()` keeps its defaults, and v1 ships as a named set that a caller loads on purpose. PARAMETERS.md has the numbers.

## Existing ratings when a set changes

When a new parameter set goes live, authoritative ratings are rebuilt by a full replay of the whole ledger under it. There is no mode that applies a new set to future matches only.

- Authoritative ratings already come from full scheduled replays.
- A rating must be reproducible from one ledger and one config. Mixing sets would make a rating depend on when a match was played relative to a parameter change.
- `RatingEngine.from_snapshot` refuses a snapshot whose `replay_info` has a `params_version` or `parameter_fingerprint` that differs from the given config. So a resume cannot carry state from the old set forward.

Before a re-rate goes live, the shift by percentile is published: the old ratings against the new ones on the same ledger. For v1 no shift report is needed, because no OPEN rating has been published, so there is nothing to shift.

## Evidence each new set needs

A new set needs two things, both produced by the evaluation outside this repository, since it reads real match data:

- A gate on held-out data, fixed before the run. For a set that would replace the defaults, that is the default-change rule above. A set that does not replace them still needs held-out evidence that it is no worse than `Config()`, as v1's non-inferiority result gives. Tuning and scoring on the same matches does not count.
- A shift report: how ratings move by percentile under the new set, on the same ledger.

The register entry cites the evaluation record by repository and path.

## Change caps

The planned rule gives each parameter a stated limit on how far it may move in one retune, and a helper checks a candidate config against the current one. Neither the limits nor the helper is written yet. They will be added in a later change for issue #48.

## Schedule

Early on, parameters are retuned at data milestones, such as 5,000, 10,000, and 25,000 matches. Once they settle, they are retuned every six months. A retune ships only when its gate passes.

## A release that changes the `Config` payload

Adding, removing, or renaming a `Config` field, or changing how `to_dict()` writes a value, changes every parameter fingerprint. Every published file then fails to load: the loader reports a missing or unknown key, or a fingerprint that does not match. That is intended, since a fingerprint must name exactly the values behind a rating. Such a release must publish new files for the sets it keeps, each under a new label, with a register entry that says what changed and gives the new fingerprint.
