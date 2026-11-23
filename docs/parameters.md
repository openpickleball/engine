# Maintenance and parameter versioning

Status: reference implementation. The engine's output is not an OPEN rating, and no OPEN rating is live. Ratings are provisional: they come from one club's closed pool, pools that share no players are not comparable, and parameters are retuned on the [schedule](#schedule) below. This page sets the rules a published parameter set follows. Following them does not make engine output an OPEN rating.

A parameter set is a named group of `Config` values, such as `v1`. The sets live in `src/open_engine/params/` and ship in the package, so an installed wheel loads them without a repository checkout. [PARAMETERS.md](../PARAMETERS.md) lists each one with its values, data, evidence, and fingerprints.

## Identity

A config has two labels and two fingerprints.

- `Config.version` is the package version. It changes in every release.
- `Config.params_version` is the parameter set label, `v<N>`, or `None`. `None` means the config is not a published set. Any config can carry any label, so a label alone proves nothing.
- `Config.parameter_fingerprint()` hashes every field but those two labels. It names the parameter values. The same values under another label, or under another release that does not change the `Config` payload, give the same fingerprint.
- `Config.fingerprint()` hashes every field, both labels included. It names the exact config.

A label is trusted only when the parameter fingerprint equals the one published for that set.

## What a shown rating carries

Every `PlayerSummary` carries `config_version`, `params_version`, and `parameter_fingerprint`, taken from the engine's config. Every replay's `ReplayInfo` carries the config version, both fingerprints, and `params_version`. So a shown rating names the parameter set for people, the values hash that checks the label, and the release.

Take the engine version from the installed package (`open_engine.__version__`), not from `config_version`. A caller can set `Config.version` to any str without changing the parameter fingerprint.

## The file format

Each set is one file, `src/open_engine/params/v<N>.json`, named after its label. It is JSON with sorted keys and a two-space indent, ending in a newline. The loader reads three keys:

- `params_version`: the label, such as `"v1"`.
- `parameter_fingerprint`: the recorded `parameter_fingerprint()` of the values.
- `parameters`: the config's `to_dict()` output without `version` and `params_version`.

The other keys are provenance for people, and the loader does not read them: `frozen_on` (the data freeze date), `data` (match counts, dates, and the data version), `fitted_on_engine` (the engine release, commit, and fingerprints the evaluation ran with), and `evidence` (where the evaluation record lives). A file never holds match data.

## The loader

`Config.published(label)` loads a set this release publishes from the installed package:

```python
from open_engine import Config

Config.published_sets()  # ("v1",)
cfg = Config.published("v1")
cfg.parameter_fingerprint()  # "f9149f3c38a01efc"
```

`published` takes only a label in `published_sets()`. Any other label raises `ConfigError`, the error `from_parameter_set` raises for a label this release does not publish. It reads the packaged file and passes its parsed JSON to `Config.from_parameter_set(data)`, so the packaged file gets every check below.

`Config.from_parameter_set(data)` takes a parsed file (`json.loads` output) and reads no file itself. It checks the file's label and recorded fingerprint against the sets this release publishes, a table in the engine. The label must name a published set, and the recorded fingerprint must be the one published for it. Then it builds the config with `version` set to the running package version and `params_version` set to the file's label, and checks that the built config's `parameter_fingerprint()` equals the recorded one. So a file cannot pass other values off as a published set or relabel one, which a label set by hand cannot promise. Any failure raises `ConfigError`. A label inside `parameters` is rejected, so the values cannot override the file's label. [Config validation](behavior.md#config-validation) lists every check in order.

Publishing a new set needs an engine release that adds its file under `src/open_engine/params/`, its entry in PARAMETERS.md, and its entry in the engine's table, together. CI builds the wheel, installs it alone in a clean environment, and loads every published set from it.

## Why `Config()` is not v1

A new set replaces the `Config()` defaults only when it passes the default-change rule. The rule is fixed before the evaluation runs. It compares `Config()` against the new set on held-out matches, with a bootstrap interval on the difference in Brier score and in log loss. The gain bar needs the lower bound of the improvement above 0 on Brier score or on log loss. The non-inferiority bar needs the Brier lower bound above -0.005.

v1 did not clear the gain bar. It passed the non-inferiority bar, a Brier lower bound above -0.005, so it is no worse on Brier within that margin, but it is not shown to be better. So `Config()` keeps its defaults, and v1 ships as a named set that a caller loads on purpose. PARAMETERS.md has the numbers.

## Existing ratings when a set changes

When a new parameter set goes live, authoritative ratings are rebuilt by a full replay of the whole ledger under it. There is no mode that applies a new set to future matches only.

- Authoritative ratings already come from full scheduled replays.
- A rating must be reproducible from one ledger and one config. Mixing sets would make a rating depend on when a match was played relative to a parameter change.
- `RatingEngine.from_snapshot` refuses a snapshot whose `replay_info` has a `params_version` or `parameter_fingerprint` that differs from the given config. So a resume cannot carry state from the old set forward.

Before a re-rate goes live, the shift by percentile is published: the old ratings against the new ones on the same ledger. For v1 no shift report is needed, because no OPEN rating has been published, so there is nothing to shift.

## Evidence each new set needs

A new set needs two things, both produced by the evaluation outside this repository, since it reads real match data:

- A gate on held-out data, fixed before the run. For a set that would replace the defaults, that is the default-change rule above. Tuning and scoring on the same matches does not count.
- A shift report: how ratings move by percentile under the new set, on the same ledger.

The register entry cites the evaluation record by repository and path.

## Change caps

The planned rule gives each parameter a stated limit on how far it may move in one retune, and a helper checks a candidate config against the current one. Neither the limits nor the helper is written yet. They will be added in a later change for issue #48.

## Schedule

Early on, parameters are retuned at data milestones, such as 5,000, 10,000, and 25,000 matches. Once they settle, they are retuned every six months. A retune ships only when its gate passes.

## A release that changes the `Config` payload

Adding, removing, or renaming a `Config` field changes every parameter fingerprint, the published ones included. Changing how `to_dict()` writes a value changes every fingerprint whose values it touches. A published file whose fingerprint changes then fails to load with `ConfigError` (which check fails depends on what changed), and the tests that pin each published set fail. Updating the file alone does not help: the engine's table of published sets (`_PUBLISHED_PARAMETER_SETS`) still holds the old fingerprint. That is intended: a fingerprint must name exactly the values behind a rating.

No such change has been made since v1 was added. How a published set carries across one (rewritten under the same label, or replaced by a new label) is not decided yet. It will be settled with the rest of issue #48, before any release that changes the payload.
