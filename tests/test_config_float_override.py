"""An int subclass whose __float__ lies is judged by its own value (issues #68 and #70)."""

from __future__ import annotations

import re
from datetime import date

import pytest

from open_engine import Config, ConfigError, Match, MatchType, RatingEngine, RatingFormat

K_PRODUCT_MESSAGE = (
    "k_max times the largest match weight and max_information_multiplier must be finite"
)


class _FloatLies(int):
    """An int whose __float__ says 1.0 whatever its value."""

    def __float__(self) -> float:
        return 1.0


HUGE = _FloatLies(10**400)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        pytest.param({"k_max": HUGE}, "k_max must be a finite number", id="k-max"),
        pytest.param(
            {"max_singles_rating_gap": HUGE},
            "max_singles_rating_gap must be None or a finite number",
            id="singles-gap",
        ),
        pytest.param(
            {"match_weights": dict.fromkeys(MatchType, HUGE)},
            "match_weights values must be finite numbers",
            id="match-weights",
        ),
    ],
)
def test_a_huge_int_is_rejected_whatever_its_float_says(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ConfigError, match=f"^{re.escape(message)}$"):
        Config(**changes)  # type: ignore[arg-type]


def test_the_k_product_uses_the_int_value_not_its_float() -> None:
    # The true product is 1e10 * 1e300, which overflows; __float__ says 1e10.
    with pytest.raises(ConfigError, match=f"^{re.escape(K_PRODUCT_MESSAGE)}$"):
        Config(k_max=1e10, match_weights=dict.fromkeys(MatchType, _FloatLies(10**300)))


def test_an_in_range_int_whose_float_lies_keeps_main_fingerprint_and_ratings() -> None:
    two = _FloatLies(2)
    cfg = Config(
        k_max=two,
        match_weights={
            MatchType.TOURNAMENT: two,
            MatchType.LEAGUE: 0.9,
            MatchType.REC_VERIFIED: 0.75,
            MatchType.SELF_REPORTED: 0.4,
        },
        max_singles_rating_gap=two,
    )
    eng = RatingEngine(cfg)
    doubles = eng.record(
        Match(
            "f1",
            ("a", "b"),
            ("c", "d"),
            ((11, 7),),
            date(2026, 1, 1),
            RatingFormat.DOUBLES,
            best_of=1,
            match_type=MatchType.TOURNAMENT,
        )
    )
    singles = eng.record(
        Match(
            "f2",
            ("a",),
            ("c",),
            ((11, 7),),
            date(2026, 1, 2),
            RatingFormat.SINGLES,
            best_of=1,
            match_type=MatchType.TOURNAMENT,
        )
    )
    # Measured on origin/main (3845bec). Re-pinned in issue #155 when params_version
    # joined the fingerprint payload, confirmed equal to the old pin without that key.
    assert cfg.fingerprint() == "9f4d1384d4ad9ac2"
    assert doubles.effective_weight == 2.0
    assert doubles.deltas == {"a": 0.44444, "b": 0.44444, "c": -0.44444, "d": -0.44444}
    assert singles.deltas == {"a": 0.44444, "c": -0.44444}
