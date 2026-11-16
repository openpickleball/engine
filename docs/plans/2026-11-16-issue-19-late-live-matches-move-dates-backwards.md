# Plan: Late live matches move rd_as_of, last_played, and partner dates backwards (issue #19)

Risk tier: High. The fix changes existing live-mode behavior and adds a field to the public `MatchResult` (an API contract change). It also touches inactivity RD, which the project counts as rating integrity.

Domain skills: none apply. This is a standard-library Python package, so django, django-ninja, allauth, celery, fastapi, and the frontend and Expo skills are not applicable.

## Problem

`RatingEngine._compute` sets `rd_as_of`, `last_played`, and every partner and opponent date to `match.played_on`. In live mode a match can arrive after a later-dated match was already recorded. Then those dates move backwards. Measured on `origin/main` (see Proof): after a 2026-10-01 match and then a late 2026-01-01 match, the player's `last_played` and `rd_as_of` both read 2026-01-01.

The effects:

- Inactivity RD growth (`inflated_rd`) runs again from the older date, so it is counted twice.
- `last_played` is wrong, and with it `player_summary`'s default reference date.
- Rolling diversity can drop a partner or opponent seen recently, because their date moves back out of the window.

Live processing applies results in arrival order, not play order. That stays true. Only a replay gives play order. The backend needs to know when a live match arrived late so it can schedule a replay.

## Decisions taken with the maintainer

- **Late means a date before a player's last match in that rating format.** A result is late when `match.played_on` is earlier than the `last_played` of any of its players in the result's rating format, read before the update. Every player counts, frozen ones included: a frozen player's rating feeds the expectation, and a later match has already moved it.
- **Widened after review: late also means a date before what the rating already reflects.** Code review found a gap. A category rating copied from overall doubles (`_new_state`) keeps the doubles `rd_as_of` but has `last_played=None`. So a mixed match dated before a doubles match was not flagged, and when its parent was rejected (for example by the partner gap) nothing was flagged at all. The maintainer approved this rule: a result is also late when, for any of its players (frozen ones included), `rd_as_of is not None and rd_as_of != initial_source_date and match.played_on < rd_as_of`. The `initial_source_date` check leaves out a plain imported baseline (its `rd_as_of` is the capture date) and a category state copied from an unplayed imported parent.
- **Dates only.** The engine stores dates, not times. Two matches on the same date that arrive out of `played_at` order are not flagged. The docs say so.
- **Counted results only.** `late` is `False` for a match that did not count, as `imbalance` is 0.0. A rejection changes no state, so no date moves.
- **Each format reports its own.** A category match's `parent` result carries its own `late`, measured against the overall doubles dates. The backend checks `result.late` and, for a category match, `result.parent.late`.
- **Replay never reports late.** Replay records in ledger order, so `played_on` never decreases. The fix is a no-op there, and authoritative ratings do not change.
- **Rating math does not change.** Only the stored dates change, so later inactivity growth and diversity windows are correct. The late match's own update is unchanged: it already skipped inflation, since `inflated_rd` returns the stored RD when `on <= rd_as_of`.

## Acceptance criteria

1. After a late live match, no player's `last_played`, `rd_as_of`, or partner or opponent date is earlier than it was before. A late match adds a new partner or opponent with the late date when that player was not in the map.
2. After a late live match, RD inflation runs from the newest date: `rd_as_of` stays at the newest match date, and `player_summary(..., as_of=X).rd` equals the inflation of the post-match RD from that newest date to X.
3. A partner or opponent seen on the newest date still counts toward rolling diversity after a late match with them on an older date.
4. `MatchResult.late` is a `bool` field, default `False`, placed after `imbalance` and before `parent`. It is `True` for a counted match dated before any of its players' `last_played` in that format, or before a player's `rd_as_of` in that format when `rd_as_of` is not that state's `initial_source_date` (widened after review, approved by the maintainer). So a category match dated before the overall doubles match its copied rating reflects is late, even when its parent is rejected. It is `False` for an in-order match, a same-date match, a match before a plain imported baseline or before an unplayed imported parent, and any match that did not count. A category match's `parent` result reports its own value.
5. Recording a ledger live in ledger order gives `late=False` for every result and the same states as before the fix (the existing `tests/test_replay_live_parity.py` stays green unchanged).
6. Documented: the module docstring (the provisional versus authoritative note: live is arrival order, only replay gives play order, dates never move back, `late` flags out-of-order arrivals), the `MatchResult` docstring, the `record` docstring, the `player_summary` docstring (remove "A backdated live match moves last_played back, and so the default date with it."), the README (the `MatchResult` Public API line or the quick start text, whichever describes live recording), and a CHANGELOG entry under `[Unreleased]`: `### Added` for `MatchResult.late` and `### Fixed` for the dates (issue #19). Added after review: the docs tell the backend to check `result.late` and, for a category match, `result.parent.late`, and describe the widened rule. They also state a backend production requirement: reject a match whose `played_on` is after the date it was submitted (module docstring production notes, README next to the late-match text, CHANGELOG `Fixed` entry).

## Files that change

- `src/open_engine/engine.py`
  - `MatchResult`: add `late: bool = False  # dated before a player's last match in this format` after `imbalance`. Extend the docstring: what late means, that it compares dates only, that it is `False` for a match that did not count, that `parent` has its own, and that the backend should schedule a replay when it sees one.
  - `_compute`: before any update, compute `late = any(work[pid].last_played is not None and match.played_on < work[pid].last_played for pid in ids)`. In the update loop, replace the four assignments:
    - `state.rd_as_of = match.played_on if state.rd_as_of is None else max(state.rd_as_of, match.played_on)`
    - `state.last_played` the same way.
    - partners and opponents: for each other id, set the date to `max(existing, match.played_on)` when present, else `match.played_on`. Write a small helper `_seen(interactions: dict[str, date], ids: Iterable[str], on: date) -> None` and use it for both maps. Keep iteration over the match's id tuples, which are ordered.
    - Pass `late=late` to the counted `MatchResult`. Rejections from `_compute` keep the default `False`.
  - `record` docstring: one sentence that live results come in arrival order and `late` flags a match dated before a player's last match.
  - `player_summary` docstring: drop the backdated sentence. Say instead that a late live match never moves `last_played` back.
  - Module docstring: extend the provisional versus authoritative note as in criterion 6.
- `tests/test_late_live_match.py` (new): the tests in Proof.
- `README.md`: one sentence where live recording or `MatchResult` is described.
- `CHANGELOG.md`: the two entries in criterion 6.

The snapshot schema does not change: `MatchResult` is not engine state, and `RatingState` gets no field. `__all__` does not change. `examples/basic_match.py` does not change.

## Order of work

1. Commit this plan alone as `docs: plan for issue 19`.
2. Worktree setup is done: `.venv` in the worktree (Python 3.12), editable install, `open_engine` imports from this worktree's `src`. Use `.venv/bin/` for every command.
3. TDD: write `tests/test_late_live_match.py` and watch it fail (dates move back, `late` missing). Freeze it and commit the red tests.
4. Implement in `engine.py`. Run the tests until green. Commit.
5. Docs: module docstring, docstrings, README, CHANGELOG. Commit.
6. The full gate: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`.
7. Live drive (High tier: the changed path plus failure and edge paths): a script that records an in-order match, a late match, a same-date match, a late category match with its parent, and a late match that is rejected (duplicate id and a partner-gap rejection), and prints `late`, `last_played`, `rd_as_of`, partner dates, and `player_summary` RD and reliability at a later date. Also run `python examples/basic_match.py`.

## Risks

- **Changing authoritative ratings by accident.** Riskiest step: the date assignments. `max` must be a no-op whenever dates arrive in order. The parity test and a live-in-ledger-order test cover it.
- **Snapshot validation.** `from_snapshot` checks dates against `calculated_as_of` in a resumable snapshot. Live engines are not resumable, and the fix only keeps newer dates, so no new invalid state appears. A test round-trips a snapshot after a late match.
- **Same-date out-of-order.** Not flagged, by design (dates only). Documented, so the backend does not read `late=False` as play order.
- **Self-reported repeat log.** Arrival order can also change which self-reported repeat is rejected. This fix does not touch the log; `late` still flags the match when it counts. Not in scope.
- **Future-dated live matches (found in review).** With dates kept at the newest value, a future-dated live match (for example `played_on` 2062-03-01, by typo or on purpose) holds `last_played`, `rd_as_of`, and partner and opponent dates for every player in it until the next replay. RD stops growing, reliability is held up, and `player_summary`'s default date jumps ahead. The engine never reads the clock, so there is no code fix. The maintainer approved documenting a backend production requirement instead: reject a match whose `played_on` is after the date it was submitted. `tests/test_late_live_category.py::test_honest_match_after_a_far_future_match_is_flagged_late` shows the signal the backend gets: a later honest match with those players is `late=True`.
- **Review change to a frozen test.** The maintainer approved rewriting one assertion in the frozen `tests/test_late_live_match.py`: `test_category_parent_reports_its_own_late` line 190, `assert mixed.late is False` became `assert mixed.late is True`, because the widened rule flags that mixed match. Nothing else in that file changed. The rewritten test still fails on `origin/main` (see Proof).
- **Not chosen:** reordering or re-applying earlier updates in live mode (that is what replay is for), and a `RejectionReason` for late matches (a late match is valid and still counts).

## Proof

| Criterion | Test in `tests/test_late_live_match.py` |
| --- | --- |
| 1 | parametrized over `last_played`, `rd_as_of`, partner date, opponent date: Oct 1 match then late Jan 1 match with the same partner and opponent; each stays Oct 1. A new opponent in the late match gets Jan 1. Review found no test for a new partner: `tests/test_late_live_category.py::test_late_match_adds_a_new_partner_with_the_late_date` adds it. |
| 2 | after the late match, `rd_as_of` is Oct 1 and `player_summary(as_of=Dec 1).rd` equals `min(rd_max, sqrt(rd**2 + g**2 * 61))` rounded to 4, from the stored post-match RD. |
| 3 | `reliability` at a date inside the window for Oct 1 but outside it for Jan 1 counts the partner seen Oct 1. |
| 4 | `late` True for the late match; False for in-order, same-date, duplicate id, partner-gap rejection, and before-baseline rejection; category match: `parent.late` from the overall doubles dates; a frozen player's later `last_played` makes the result late. `dataclasses.fields(MatchResult)` order puts `late` after `imbalance` and before `parent`. |
| 4, widened (review) | in `tests/test_late_live_category.py`: a mixed match on Jan 1 after a doubles match on Oct 1 is late at the top level (default config); with `Config(max_doubles_partner_gap=0.5)` and `syn-w` seeded at doubles 5.0 and mixed 3.5, the mixed result is late while the rejected parent is not; a match before a plain imported baseline, and a mixed match before an unplayed imported parent, are not late. `test_category_parent_reports_its_own_late` now asserts `mixed.late is True`. |
| 5 | record a synthetic multi-format ledger live in `ledger_order`; every result's `late` is False (and the parent's). `tests/test_replay_live_parity.py` unchanged and green. |
| 6 | review, plus `tests/test_readme.py` if it checks the touched text. |
| 5, with seeds (review) | `tests/test_late_live_category.py::test_live_in_ledger_order_with_seeds_is_never_late`: a multi-format ledger with doubles and mixed imported seeds, some captured mid-ledger so some matches are frozen, recorded live in `ledger_order`, gives `late=False` on every result and parent. |
| snapshot | `to_snapshot` then `from_snapshot` after a late match round-trips. |

Live drive must show: the late match flagged with dates unchanged, the in-order and same-date matches not flagged, rejections not flagged, and RD at a later date grown from Oct 1.

Measured on `origin/main` (d789e7c), default config, a short scratch script: record doubles `m1` on 2026-10-01 (`syn-a`, `syn-b` vs `syn-c`, `syn-d`), then `m2` on 2026-01-01 (`syn-a`, `syn-e` vs `syn-f`, `syn-g`). Output: `last_played 2026-01-01 rd_as_of 2026-01-01 partners {'syn-b': 2026-10-01, 'syn-e': 2026-01-01}`. The dates move back, as the issue says.

The rewritten frozen test still catches the defect on `origin/main`. A scratch worktree at `git merge-base origin/main HEAD` (d789e7c) with only the rewritten `tests/test_late_live_match.py` copied in:

```
PYTHONPATH=<scratch>/src .venv/bin/python -m pytest -p no:cacheprovider -q <scratch>/tests/test_late_live_match.py::test_category_parent_reports_its_own_late
>       assert mixed.late is True
E       AttributeError: 'MatchResult' object has no attribute 'late'
<scratch>/tests/test_late_live_match.py:190: AttributeError
1 failed in 0.04s
```

`open_engine` was imported from `<scratch>/src`. It fails because `late` is missing on main, as expected.
