# OPEN rating engine

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/openpickleball/engine/badge)](https://scorecard.dev/viewer/?uri=github.com/openpickleball/engine)

The reference rating engine for [OPEN](https://github.com/openpickleball), the Open Pickleball Evaluation Network.

> **Project status:** This engine is a demonstration. Its methods have not been validated on real match data, its outputs are not OPEN ratings, and its API may change without notice. Do not use it to rate real players. OPEN is in early development, and no OPEN rating is live.

## Technology

The engine is a Python package (`openpickleball-engine`, imported as `open_engine`) with no runtime dependencies. It requires Python 3.10 or later.

## Setup and tests

The package is not published to PyPI yet. Install it from a local checkout:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the tests, linter, formatter, and type checker:

```bash
pytest
ruff check .
ruff format .
mypy src
```

Build a wheel and source distribution:

```bash
python -m build
```

## Quick start

```python
from datetime import date
from open_engine import Match, MatchType, RatingEngine, RatingFormat

engine = RatingEngine()
result = engine.record(
    Match(
        match_id="league-2026-10-29-court-3-match-7",
        team_a=("alice", "carol"),
        team_b=("bob", "dave"),
        games=((11, 8), (9, 11), (11, 7)),
        played_on=date(2026, 10, 29),
        rating_format=RatingFormat.DOUBLES,
        match_type=MatchType.LEAGUE,
        best_of=3,
    )
)
print(result.deltas)
print(engine.player_summary("alice", RatingFormat.DOUBLES))
```

Authoritative ratings come from `replay(ledger, as_of=...)`. Calibrate the expectation scale on match history with `fit_scale(...)`. The expectation scale and the K factor together set how sharp predictions are, and in a closed pool they also set the rating spread. Run `fit_scale` with `mode="scheduled"` and a `score_from` date that skips immature ratings, then check `at_bound` and `profile` on the result: a fit that sits on a search edge or has a flat error profile is not identified. `RatingEngine.win_probability` assumes points are independent, which tends to make match win probabilities overconfident. Calibrate them with `fit_win_calibration` and store the result in `Config.win_calibration_by_scoring`.

See [`examples/`](examples) for a fuller example.

## Ledger order

`replay` and `prequential_predictions` sort the ledger the same way whatever order the rows arrive in.

- Matches sort by `played_on` first, the calendar date at the venue. The rules below apply within one `played_on` date.
- On one day, matches without a `played_at` come first, by `sequence` and then `match_id`.
- Matches with a `played_at` follow, by the moment they were played in UTC, then `sequence`, then `match_id`. A match at 21:00 at UTC-5 comes after one at 22:00 UTC on the same day.
- `played_at` must include a time zone and fall on `played_on` in that zone.
- Every row passed in is validated, including rows after `as_of`. An invalid row, such as one with a `played_at` without a time zone, raises `ValueError`.
- Rows that repeat a `match_id` count once when they are the same match: equal in every field, with `played_at` at the same moment and the same UTC offset. Any other repeat raises `LedgerConflictError`, a `ValueError` whose `match_ids` lists every conflicting id. This covers rows after `as_of` too.

## Test data

Use only made-up or properly de-identified data. Do not put real player names, contact details, or match histories in this repository, including in test fixtures and screenshots. See [`tests/fixtures/README.md`](tests/fixtures/README.md).

## Contributing

See the [contributing guide](https://github.com/openpickleball/.github/blob/main/CONTRIBUTING.md). Every commit must be signed off. Please report security problems privately, as described in the [security policy](https://github.com/openpickleball/.github/blob/main/SECURITY.md).

## License

This repository is licensed under [AGPL-3.0-or-later](LICENSE). See [how OPEN is licensed](https://github.com/openpickleball/.github/blob/main/LICENSING.md).
