# Parameter sets

This register lists every published parameter set in `params/`. Each set is a named group of `Config` values, loaded on purpose by passing the parsed JSON of its file to `Config.from_parameter_set`, which checks it against the sets this engine release publishes. `Config()` is not a published set. [docs/parameters.md](docs/parameters.md) explains the file format, the loader, and the rules for a new set.

Status: demonstration only. The engine's output is not an OPEN rating, and no OPEN rating is live. A published parameter set does not change that.

## v1

File: `params/v1.json`. Parameter fingerprint: `f9149f3c38a01efc`.

### Reason

v1 is the first set fitted on match data. The `Config()` defaults are uncalibrated placeholders. v1 sets the side-out scale to a declared unit of 1.0, rescales K to match, and fits the side-out win calibration, which the defaults leave at 1.0. It is published as a named set so that a rating made with it names its parameters, while the defaults wait for more data (see "Default-change rule and result").

### Values

Four values differ from `Config()`. Every other field is at its `Config()` default.

| Field | v1 | `Config()` | How it was set |
| --- | --- | --- | --- |
| `scale_by_scoring` | side-out 1.0, rally 1.0 | side-out 2.9, rally 2.9 | Declared: the unit scale. |
| `win_calibration_by_scoring` | side-out 0.476, rally 1.0 | side-out 1.0, rally 1.0 | Side-out fitted, not at its search bound, on 667 predictions. Rally declared, since the data had no rally matches. |
| `k_min` | 0.1079136690647482 | 0.12 | The default times 1.0 / s_fit, with s_fit = 1.112 fitted, not at its search bound, on 667 scored matches. |
| `k_max` | 0.539568345323741 | 0.6 | As `k_min`. |

The file holds the K values exactly as the evaluation recorded them. Recomputing them, for example as `0.12 / 1.112`, gives a different last digit and a different fingerprint.

### Data

- 1,601 matches from 2025-08-10 to 2026-11-20, on 52 match dates, with 189 players.
- All doubles and all side-out scoring: 1,283 league and 318 tournament matches, 0 rally matches.
- Data frozen on 2026-11-20. The fit window starts on 2026-04-12. Data version `28bcab9de3af`.
- No match touched the rating floor or ceiling.

The match data is not in this repository and never will be. The file holds no match data.

### Default-change rule and result

The rule was fixed before the run. It compared `Config()` against v1 on 1,018 pooled matches over 6 folds, with a two-way bootstrap and 95% intervals. A difference is `Config()` minus v1, so a positive value favors v1.

- Brier score: +0.004680, interval [-0.004359, +0.013895].
- Log loss: +0.009338, interval [-0.010563, +0.029472].

The gain bar needed a lower bound above 0 on Brier score or on log loss. It failed: neither lower bound is above 0. The non-inferiority bar, a Brier lower bound above -0.005, passed. It is set on Brier only: the log loss lower bound is below -0.005, so v1 is not shown to be no worse on log loss. So `Config()` keeps its defaults, and v1 ships as a named set that a caller loads on purpose. The defaults will be checked again with the same rule on new data, after the future-play test the evaluation registered in advance.

### Shift summary

None: no OPEN rating has been published, so there is nothing to shift.

### Fingerprints

- Parameter fingerprint on this engine: `f9149f3c38a01efc`. `Config().parameter_fingerprint()` is `1058623faf5d9367`.
- The evaluation recorded `22b53a37587d6881` for v1 and `17f2981186edf96c` for `Config()`. It ran engine 0.3.0.dev0 at commit `e62468c508126ed46b5a26a82f175ba17dd78371`.

The two pairs differ because the evaluation used the scheme before `parameter_fingerprint()`. That scheme hashed the package version, and its payload had no `params_version` key. `parameter_fingerprint()` leaves out both labels. The values are the same: hashing the file's `parameters` with `version` set to `"0.3.0.dev0"` gives `22b53a37587d6881` again, and a test checks it. The rating math has not changed between that commit and the release that published v1.

### Evidence

GeoBradDev/open-engine-evaluation, reports/v1-fit-2026-11/v1-fit.md, evaluation commit `995de996541608632b585c1d59d1089b2b6bc2c4`. That repository is private, because it reads real match data.
