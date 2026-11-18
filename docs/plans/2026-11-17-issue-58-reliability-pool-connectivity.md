# Plan: Reliability does not see whether a player's pool connects to the wider network (issue #58)

Risk tier: High. The change adds a public dataclass, four `Config` fields, and a `PlayerSummary` field, changes the default `PlayerSummary.reliability` and `established` for players in closed pools, and touches a security-sensitive label (how credible a rating looks). It must not change any rating, RD, K, prediction, `evaluate`, or fit output.

Domain skills: none apply. This is a standard-library Python package with no framework, UI, API, data store, authentication, payments, or email. superpowers:test-driven-development applies.

## Problem

`reliability()` combines RD with rolling partner and opponent variety. Neither sees whether those players ever play anyone outside their group. Reproduced on main (a0d7674): a closed club of 14 players (4 league doubles matches a week among themselves for 26 weeks) next to a separate wider network of 120 players. No match links the two. Every club member is established (reliability 97.2 to 100), and nothing in the output says the club sits on its own scale.

## Scope decision

Reliability and `established` are labels. `reliability` is read only by `player_summary`; no rating, RD, K, prediction, `evaluate`, `prequential_predictions`, `fit_scale`, or `fit_win_calibration` path reads it. So the change is a label change, not a change to how ratings move, and the prediction-accuracy rule does not apply (the issue says so too). The measure is always reported. The cap is off by default (`connectivity_reliability_cap=None`); see the scope revision below. Default ratings must stay byte-identical, proved under Proof.

No snapshot change. The term is derived at summary time from state the engine already stores and snapshots: each rating state's `partners` and `opponents` (player id to last date). So resume equals a full replay for it whenever the states are equal, which existing tests already prove, and `snapshot_version` stays 6.

## Design

All measured per rating format, at the summary's reference date, over the rolling window `Config.diversity_window_days` (the same window as variety).

1. Contact graph: players with a stored state in the format. Two players are in contact when either one's state lists the other as a partner or opponent with a last date on or after `reference - diversity_window_days`. The contact date is the later of the two states' dates. (A frozen player does not record partners, so both sides are read.)
2. Groups: connected components of the contact graph. A group is a main network when it has at least half as many players as the largest group. (Revised after review: the first draft took only the unique largest group, so two equal networks had none and a few new accounts could flip every label in another group.)
3. Strong tie: two players in contact are tied when they share at least `connectivity_min_shared_contacts` (default 4) contacts and each of them is in contact with those shared contacts on at least `connectivity_min_contact_days` (default 3) distinct dates. A doubles match between groups with no earlier contact gives each cross pair at most 2 shared contacts, so it ties no one across them, and a one-day event never ties a visitor, since all the visitor's dates with the shared contacts are one date. (Revised during implementation: the first draft pooled both players' dates, which let a visitor who played several club members on one day tie into the club through the club members' own varied dates.)
4. Pool: connected components of strong ties. A player with no strong tie is a pool of one.
5. Fringe and outside players: the pool's boundary is every player outside it in contact with it. Its fringe is the boundary players whose own contacts are all in the pool or the boundary (drop-ins). Its outside players are the rest of the boundary, and `outside_days` counts distinct contact dates between the pool and them. (Revised after review: drop-ins and accounts that only played the club used to count as outside contact.)
6. Connected: the player has a contact in the window, the group is a main network, and either the pool with its fringe holds more than half the group, or the pool has at least `connectivity_min_outside_players` (default 8) outside players on at least `connectivity_min_contact_days` outside days. (Revised after review: the first draft treated a pool of one as connected, which let small closed groups, same-day groups, and stragglers, who never tie, lose the cap after one bridge match.)
7. Cap: when not connected and `connectivity_reliability_cap` (default 50.0) is not None, `PlayerSummary.reliability` is `min(earned, cap)`. `established` and the imported reliability carry compare against that displayed value, as today.
8. Report: new frozen public dataclass `NetworkConnectivity` with `pool_size`, `fringe_players`, `outside_players`, `outside_days`, `group_size`, `in_main_network`, and `connected`. New `PlayerSummary.connectivity` as the last field. A player with no stored state in the format is measured as if the unplayed state a seed would store were stored (consistent with `player_summary`'s rule for a missing format): a group of one with no contacts, never connected, in a main network only when the largest group has at most 2 players.
9. Determinism: every traversal iterates sorted ids, union-find roots are the smallest id, and no set or dict is iterated in an order that reaches the output. The result depends only on the stored states, the config, and the reference date.
10. Speed: building the graph is O(players + contacts); strong ties use C-level set intersection per contact pair. The result for one (format, reference date) is cached on the engine in a private `_connectivity` dict, cleared by `_changed()` (every state change already goes through it). The cache is derived data, not state: it never changes a rating or a later replay, and a fresh engine rebuilds it, so it is not in the snapshot. `test_snapshot_covers_every_engine_attribute` lists it with that reason.

Config validation in `__post_init__`: `connectivity_reliability_cap` is None or a number from 0 to 100 (`ConfigError("connectivity_reliability_cap must be None or a number from 0 to 100")`); the three counts are ints (not bools) of at least 1 (`ConfigError("<name> must be a positive int")`). All four are scalars or None, which `norm()` already turns into stable JSON, so the fingerprint, `to_dict`, and `from_dict` cover them. The cap is shown rounded down to 1 place, so displayed reliability never exceeds it. The cache keeps at most 16 (format, date) entries.

Limits to document (claims must hold): a pool that is the whole ledger, or more than half the main network, is the network's own scale and is never capped; the term cannot place it. Pools that play each other but not the rest of the main network can count each other's players as outside contacts. Contacts keep only the last date per pair, so `outside_days` can undercount repeated contact with the same player. `as_of` earlier than the stored state does not rebuild past contacts (as for variety).

## Acceptance criteria

1. With the cap set to 50.0, a synthetic closed pool (14 players, separate from a wider network) has every member's reliability capped at or below the cap (below `established_reliability_threshold`) and not established, and a matched pool of the same size and schedule whose players also play the wider network reaches established.
2. One cross-pool match (or one day of matches) between the pool and the wider network does not lift the pool's cap.
3. Replay and resume give the same `NetworkConnectivity` and summaries for the same ledger, config, and date, in any input order (shuffled ledger, and resume from a checkpoint, also after a JSON round trip).
4. The default config (`connectivity_reliability_cap=None`) gives the same `reliability` and `established` as main for every player.
5. Config validation table with `pytest.raises(ConfigError, match=...)`, defaults, fingerprint, and `to_dict`/`from_dict` round trip.
6. Ratings are unchanged: a synthetic ledger replays to byte-identical snapshots (every rating state field) on main and on this branch, and `tests/test_properties.py` passes.
7. Module docstring, README (reliability text and Public API), `Config`, `PlayerSummary`, and `CHANGELOG.md` describe the term and its parameters; `NetworkConnectivity` is in `__all__` and the README Public API with a docstring.
8. The full gate passes: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.

## Files that change

- `src/open_engine/engine.py`: `Config` fields and validation; `NetworkConnectivity`; `_network_connectivity` helper; `RatingEngine._connectivity` cache and `_changed`; `player_summary`; docstrings (module notes, `Config`, `PlayerSummary`, `player_summary`).
- `src/open_engine/__init__.py`: export `NetworkConnectivity`.
- `README.md`: reliability paragraph, Public API.
- `CHANGELOG.md`: Added and Changed entries.
- Tests: new `tests/test_network_connectivity.py`; `tests/test_snapshot.py` attribute list; any test whose assertions change under the default cap (explained in the PR).

## Order of work

1. Red tests (new file): closed pool capped, connected pool established, one-match bridge, determinism and resume, cap None equals main, config table. Commit red.
2. Implement Config fields, `NetworkConnectivity`, the helper, the cache, and `player_summary`. Gate.
3. Byte-identical rating proof against main on a synthetic ledger.
4. Docs and changelog.

## Risks

- Existing tests with disconnected groups may now see capped reliability. Each is checked; ratings never change.
- Single linkage can still chain a pool into a dense group it met across several days. That is repeated contact, which is what the issue asks for.
- Cost on large ledgers: the cache keeps it to one build per format and reference date.

## Proof

- Criteria 1 and 2: `tests/test_network_connectivity.py` closed pool, connected pool, and bridge tests.
- Criterion 3: shuffled and resumed replays compared field by field.
- Criterion 4: cap None compared with a summary computed on the same states by the unchanged `reliability()` rule.
- Criterion 6: a script in the scratch directory writes `json.dumps(replay(...).to_snapshot(), sort_keys=True)` on main and on the branch for the same synthetic ledger and compares the bytes; recorded in the PR body.
- Measured on main: `repro.py` (closed club of 14 plus a wider network of 120): club00 97.2 established, club01 100.0 established.

## Scope revision after review round 2

Two review rounds found that every size-based rule this measure can build from the stored contacts (one last date per contact pair, any match type) can be gamed near its threshold: drop-in accounts or one match between two closed groups can make a closed group a main network or the majority of its group, and a straggler next to a densely playing club can count its members as outside players. The same rules can wrongly cap real players: one match between two real networks can make the smaller one no longer a main network. Since the cap is a label in a security-sensitive area (hard rule 7) and its accuracy cannot be checked on real ledgers in this repository, the cap is off by default (`connectivity_reliability_cap=None`), the measure is always reported, and the acceptance tests run with the cap set to 50.0. A follow-up issue covers a robust measure (per-contact history: match counts, dates, and match types, so repeated contact and self-reported contacts can be told apart) and the out-of-sample review needed before the cap is on by default. The cache now drops only its oldest entry when full.
