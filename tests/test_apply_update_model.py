"""
RatingEngine._apply_update follows the update rule in docs/model.md (issue #128).

Each case computes the expected state from the equations in docs/model.md,
"K" and "The update", written out here, and compares it with what
_apply_update does to one working state. All ids are synthetic.
"""

from __future__ import annotations

import copy
from datetime import date, timedelta

import pytest

from open_engine import (
    Config,
    GameFormat,
    Match,
    MatchType,
    RatingEngine,
    RatingFormat,
    RatingState,
)

D0 = date(2025, 3, 1)


def _match(match_type: MatchType, day: int) -> Match:
    confirmed = frozenset({"a1", "b1"}) if match_type is MatchType.SELF_REPORTED else frozenset()
    return Match(
        match_id="m1",
        team_a=("a1", "a2"),
        team_b=("b1", "b2"),
        games=((11, 7),),
        played_on=D0 + timedelta(days=day),
        rating_format=RatingFormat.DOUBLES,
        match_type=match_type,
        game_format=GameFormat(),
        confirmed_by=confirmed,
    )


def _expected(
    state: RatingState,
    cfg: Config,
    match: Match,
    pid: str,
    team: tuple[str, ...],
    opponents: tuple[str, ...],
    sign: float,
    weight: float,
    surprise: float,
) -> tuple[RatingState, float, bool]:
    """The update rule, as docs/model.md writes it."""
    new = copy.deepcopy(state)
    f = min(1.0, max(0.0, (state.rd - cfg.rd_min) / (cfg.rd_max - cfg.rd_min)))
    k = cfg.k_min + (cfg.k_max - cfg.k_min) * f
    raw_delta = sign * k * weight * surprise
    new.rating = min(cfg.max_rating, max(cfg.min_rating, state.rating + raw_delta))
    clamped = (
        abs(new.rating - (state.rating + raw_delta)) > 1e-12
        or abs((new.rating - state.rating) - raw_delta) >= 5e-6
    )
    new.rd = max(cfg.rd_min, state.rd * (1 - cfg.rd_shrink_per_match * weight))
    played_on = match.played_on
    new.rd_as_of = played_on if state.rd_as_of is None else max(state.rd_as_of, played_on)
    new.last_played = played_on if state.last_played is None else max(state.last_played, played_on)
    new.matches_played += 1
    if match.match_type is not MatchType.SELF_REPORTED:
        new.verified_matches_played += 1
    for other in team:
        if other != pid:
            new.partners[other] = max(new.partners.get(other, played_on), played_on)
    for other in opponents:
        new.opponents[other] = max(new.opponents.get(other, played_on), played_on)
    return new, new.rating - state.rating, clamped


CASES = {
    "team A gains": (4.0, 0.5, 1.0, 0.12, 1.3, MatchType.TOURNAMENT, 10),
    "team B loses": (4.0, 0.5, -1.0, 0.12, 1.3, MatchType.LEAGUE, 10),
    "self-reported, no verified count": (3.2, 0.8, 1.0, -0.2, 0.2, MatchType.SELF_REPORTED, 3),
    "clamped at the top": (7.99, 0.8, 1.0, 0.5, 1.3, MatchType.TOURNAMENT, 10),
    "clamped at the bottom": (2.01, 0.8, -1.0, 0.5, 1.3, MatchType.TOURNAMENT, 10),
    "rd at the floor": (5.0, 0.1, 1.0, 0.3, 1.3, MatchType.REC_VERIFIED, 10),
    "late match keeps later dates": (4.0, 0.5, 1.0, 0.05, 0.9, MatchType.LEAGUE, -5),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_apply_update_follows_the_model(name: str) -> None:
    rating, rd, sign, surprise, weight, match_type, day = CASES[name]
    cfg = Config()
    engine = RatingEngine(cfg)
    match = _match(match_type, day)
    state = RatingState(
        rating=rating,
        rd=rd,
        matches_played=3,
        verified_matches_played=2,
        last_played=D0,
        rd_as_of=D0,
        partners={"a2": D0 + timedelta(days=1)},
        opponents={"zz": D0},
    )
    team, opponents = (match.team_a, match.team_b) if sign > 0 else (match.team_b, match.team_a)
    pid = team[0]
    want, want_change, want_clamped = _expected(
        state, cfg, match, pid, team, opponents, sign, weight, surprise
    )
    clamped: list[str] = []
    change = engine._apply_update(
        state, pid, match, team, opponents, sign, weight, surprise, clamped
    )
    assert state == want
    assert change == want_change
    assert clamped == ([pid] if want_clamped else [])
