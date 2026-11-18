# Plan: Stored derived category state goes stale after a doubles reseed (issue #101)

Risk tier: High. CLAUDE.md hard rule 7 names replay determinism and the handling of imported external ratings as security-sensitive, and this changes what `seed_external_rating` stores (existing behavior). The change itself is small: one method, one module.

Domain skills: superpowers:test-driven-development: invoke. django, django-ninja-api, django-allauth, django-celery, python-fastapi, React and Expo skills: not applicable because this is a standard-library Python package with no web, UI, or framework code.

Decision from the maintainer (recorded on #101, counts as plan approval): option 1, re-derive. A reseed of a parent format (overall doubles) under `category_feeds_parent` also refreshes every stored, unplayed category state derived from it (`initial_source` `open_parent:<parent>`), so it equals what `_new_state` would build from the new parent seed. Played derived states are not touched. The refresh happens in the same call, after all validation, so a refused reseed changes nothing, and it clears the resumable mark as other state changes do.

## Reproduction on main (6a794f8)

Script (synthetic ids): seed syn-b, syn-c, syn-d in mixed at 4.0 (2026-01-01); seed syn-a in doubles at 4.0; `seed_provisional_rating("syn-a", MIXED_DOUBLES)` stores `open_parent:doubles` at 4.0; reseed syn-a doubles at 2.5 with `replace_unplayed_seed=True`. The stored mixed state stays at 4.0. Then record mixed match syn-a, syn-b vs syn-c, syn-d, 11-5, on 2026-02-01: live syn-a mixed 4.055875; replay with the stored derived state as a seed 4.055875; replay without it 2.598973. Live and the two replays disagree with each other.

## Acceptance criteria

1. After `seed_external_rating(p, DOUBLES, new, replace_unplayed_seed=True)` under `category_feeds_parent`, every stored category state of p whose format's parent is doubles, with `initial_source == "open_parent:doubles"` and `matches_played == 0`, equals `RatingState` as `_new_state(p, fmt)` builds it from the new doubles state (rating, RD `max(parent rd, category_seed_rd)`, `rd_as_of`, `initial_rating`, `initial_source`, `initial_source_date`, `inherited_live_updates`, every other field at its default).
2. The issue repro: after the reseed, a later mixed match gives the same syn-a (and every player's) mixed state live, in a replay seeded with the stored derived state, and in a replay seeded without it.
3. A refused doubles reseed (no `replace_unplayed_seed`, and a refusal after the existing-state checks, for example an external id already linked to another player) leaves the stored derived state and the whole snapshot unchanged.
4. Category states not derived from doubles (an import, `open_provisional`) and a first doubles seed with no stored derived state are untouched. A reseed of a format with no category children (singles, or a category format itself) changes no other format.
5. After a refresh, `to_snapshot()` succeeds and `RatingEngine.from_snapshot` loads it to an equal snapshot, and the derived state still meets the #97 shape rules: dated exactly when the parent is, RD at least `category_seed_rd` while unplayed. The refreshed date equals the parent's.
6. The refresh clears the resumable mark (it uses `_put_state`).
7. The `seed_external_rating` docstring, the README `InvalidSeedError`/seeding text where it describes reseeds, and the `_reserved_shape_broken` docstring and `add_player` docstring note "(the dates may differ, since a parent can be reseeded after the derivation)" stay true. The engine no longer produces differing dates for an unplayed derived state, so that note is reworded to say differing dates come from a snapshot or seed made before this fix (or a caller-built seed). The validation rules themselves do not change.
8. `CHANGELOG.md` gets an entry under `### Fixed` (or `### Security`) in `[Unreleased]`.

## Files that change

- `src/open_engine/engine.py`: `seed_external_rating` refreshes derived children after `_put_state` of the new seed; its docstring says so; reword the two "dates may differ" notes.
- `tests/test_reseed_refreshes_derived.py` (new): the tests below.
- `README.md`: the `seed_external_rating` description under `InvalidSeedError` mentions the refresh, and the "dates may differ" parenthetical is reworded the same way.
- `CHANGELOG.md`: Fixed entry.

## Order of work

1. Red test commit: the new test file, covering criteria 1, 2, 3, 4, 5. Criteria 1, 2 and 5 (date equality) fail on main; 3 and 4 pass on main and guard the fix.
2. Fix in `seed_external_rating`: after `self._put_state(player, rating_format, state)`, when `cfg.category_feeds_parent`, for each `child in RatingFormat` (enum order, deterministic) with `child.parent is rating_format`, if `child in player.ratings`, its state has `matches_played == 0` and `initial_source == f"open_parent:{rating_format.value}"`, call `self._put_state(player, child, self._new_state(player_id, child))`. No new validation is needed: every check runs before the first state change.
3. Docstrings, README, changelog.
4. Gate.

## Risks

- A played derived child cannot reach the refresh: a counted category match makes the earlier `_in_counted_match` check refuse the doubles reseed. The `matches_played == 0` guard is kept anyway as the decision requires.
- A frozen derived child (matches_played 0 but in a counted match) is likewise refused earlier by `_in_counted_match` for the child format.
- `_new_state` reads the parent state that was just stored, so the order matters: refresh after storing the new parent.
- Snapshot validation: a refreshed state has the parent's date, RD `max(external_seed_rd, category_seed_rd)`, `inherited_live_updates` 0 (a new seed has no live updates), so it meets every existing rule.
- Not done: tightening seed or snapshot validation to require equal dates. Older snapshots and seeds can hold differing dates; rejecting them would be a breaking change outside this decision.

## Proof

- Criterion 1, 5: unit tests compare the stored state with the expected `RatingState` and round-trip `to_snapshot`/`from_snapshot`.
- Criterion 2: live versus two replays (with and without the derived seed), comparing every `RatingState` field of every player in mixed and doubles.
- Criterion 3: parametrized refusals compare `to_snapshot()` before and after.
- Criterion 4: tests on an imported mixed state, a first doubles seed, and a singles reseed.
- Criterion 6: snapshot `resumable` is false after the reseed (already the case because of the parent `_put_state`; the refresh uses the same helper).
- Measured on main: the repro script above, output `live 4.055874981120198 replay with derived seed 4.055874981120198 replay without 2.598973282253521`.
