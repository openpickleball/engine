"""ReplayInfo.net_rating_created always has a key for every RatingFormat (#42 review).

All values are synthetic.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import date

import pytest

from open_engine import OpenEngineError, RatingFormat, SnapshotError
from open_engine.engine import ReplayInfo

S = RatingFormat.SINGLES
ON = date(2025, 1, 31)


def _info(created: object) -> ReplayInfo:
    return ReplayInfo(ON, "v", "f", None, "p", 1, 1, created)  # type: ignore[arg-type]


def test_missing_formats_are_filled_with_zero() -> None:
    info = _info({S: 0.5})
    assert list(info.net_rating_created) == list(RatingFormat)
    assert info.net_rating_created[S] == 0.5
    assert all(info.net_rating_created[f] == 0.0 for f in RatingFormat if f is not S)


def test_format_values_are_accepted_as_keys() -> None:
    info = _info({"singles": 0.5, "doubles": -0.25})
    assert info.net_rating_created[S] == 0.5
    assert info.net_rating_created[RatingFormat.DOUBLES] == -0.25
    assert info == _info({S: 0.5, RatingFormat.DOUBLES: -0.25})
    assert all(isinstance(key, RatingFormat) for key in info.net_rating_created)


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(
        SnapshotError, match=r"ReplayInfo\.net_rating_created has an unknown key: 'triples'"
    ):
        _info({"triples": 0.0})


class _BothKeys(Mapping[object, float]):
    """Yields RatingFormat.SINGLES and "singles" as separate keys.

    A dict cannot hold both, since a StrEnum member equals its value, so
    only a custom mapping can give ReplayInfo a repeated format.
    """

    _items = ((S, 0.5), ("singles", 0.25))

    def __getitem__(self, key: object) -> float:
        for item_key, value in self._items:
            if item_key is key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[object]:
        return iter(key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)


def test_a_format_given_twice_is_rejected() -> None:
    assert list(_BothKeys().items()) == [(S, 0.5), ("singles", 0.25)]
    with pytest.raises(
        SnapshotError, match=r"ReplayInfo\.net_rating_created repeats the format 'singles'"
    ):
        _info(_BothKeys())


@pytest.mark.parametrize("value", [None, [], 0.0, "singles"])
def test_a_non_mapping_is_rejected(value: object) -> None:
    with pytest.raises(SnapshotError, match=r"ReplayInfo\.net_rating_created must be a mapping"):
        _info(value)


def test_errors_are_engine_errors() -> None:
    with pytest.raises(OpenEngineError):
        _info(None)


def test_filled_info_stays_hashable() -> None:
    assert hash(_info({S: 0.5})) == hash(_info({"singles": 0.5}))
