"""Tests for the public API contract: exports, exceptions, result types, docs."""

from __future__ import annotations

import ast
import dataclasses
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

import open_engine
import open_engine.engine as engine_module
from open_engine import (
    Config,
    ExternalRatingSnapshot,
    GameFormat,
    Match,
    MatchResult,
    MatchType,
    RatingEngine,
    RatingFormat,
    prequential_predictions,
    replay,
)

SRC = Path(open_engine.__file__).parent
D = RatingFormat.DOUBLES
ON = date(2026, 10, 29)

ERROR_NAMES = [
    "ConfigError",
    "InvalidMatchError",
    "InvalidSeedError",
    "PlayerError",
    "EvaluationError",
    "LedgerConflictError",
]


def _match(match_id: str = "m1", games: tuple[tuple[int, int], ...] = ((11, 7),)) -> Match:
    return Match(match_id, ("a", "b"), ("c", "d"), games, ON, D)


# -- exceptions ----------------------------------------------------------------


def test_open_engine_error_is_a_value_error() -> None:
    assert issubclass(open_engine.OpenEngineError, ValueError)


@pytest.mark.parametrize("name", ERROR_NAMES)
def test_error_classes_share_the_base(name: str) -> None:
    cls = getattr(open_engine, name)
    assert issubclass(cls, open_engine.OpenEngineError)
    assert issubclass(cls, ValueError)


def _bad_config() -> None:
    Config(min_rating=9.0)


def _bad_match() -> None:
    _match(match_id=" ").validate()


def _bad_seed() -> None:
    RatingEngine().seed_external_rating("a", D, ExternalRatingSnapshot("ext", 99.0, ON))


def _bad_player() -> None:
    RatingEngine().player(" ")


def _bad_mode() -> None:
    prequential_predictions([], mode="sometimes")  # type: ignore[arg-type]


def _conflict() -> None:
    replay([_match(), _match(games=((11, 9),))], as_of=ON)


@pytest.mark.parametrize(
    ("name", "action", "message"),
    [
        ("ConfigError", _bad_config, "min_rating must be less than max_rating"),
        ("InvalidMatchError", _bad_match, "match_id is required"),
        ("InvalidSeedError", _bad_seed, "outside the configured scale"),
        ("PlayerError", _bad_player, "player_id is required"),
        ("EvaluationError", _bad_mode, "mode must be"),
        ("LedgerConflictError", _conflict, "Conflicting ledger rows"),
    ],
)
def test_each_error_class_is_raised(name: str, action: Callable[[], None], message: str) -> None:
    with pytest.raises(getattr(open_engine, name), match=message):
        action()


def _raised_names(path: Path) -> list[tuple[int, str]]:
    """Name of the class in every raise statement, with its line number."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        found.append((node.lineno, ast.unparse(target)))
    return found


@pytest.mark.parametrize("filename", ["engine.py", "winprob.py"])
def test_every_raise_uses_an_engine_error(filename: str) -> None:
    raised = _raised_names(SRC / filename)
    assert raised
    bad = [
        (line, name)
        for line, name in raised
        if not (
            isinstance(cls := getattr(engine_module, name, None), type)
            and issubclass(cls, open_engine.OpenEngineError)
        )
    ]
    assert bad == []


# -- rejection reasons -----------------------------------------------------------

S = RatingFormat.SINGLES
REJECTIONS = [
    "DUPLICATE_MATCH_ID",
    "GAME_FORMAT_NOT_ACCEPTED",
    "SELF_REPORTED_UNCONFIRMED",
    "SELF_REPORTED_REPEAT_LIMIT",
    "BEFORE_IMPORTED_BASELINE",
    "SINGLES_RATING_GAP",
    "DOUBLES_PARTNER_GAP",
]


def _seeded(cfg: Config, fmt: RatingFormat, ratings: dict[str, float]) -> RatingEngine:
    eng = RatingEngine(cfg)
    for pid, rating in ratings.items():
        eng.seed_external_rating(pid, fmt, ExternalRatingSnapshot("ext", rating, date(2026, 1, 1)))
    return eng


def _self_reported(match_id: str, confirmed_by: frozenset[str] = frozenset()) -> Match:
    return Match(
        match_id,
        ("a", "b"),
        ("c", "d"),
        ((11, 7),),
        ON,
        D,
        MatchType.SELF_REPORTED,
        confirmed_by=confirmed_by,
    )


def _duplicate() -> tuple[MatchResult, RatingFormat]:
    eng = RatingEngine()
    eng.record(_match())
    return eng.record(_match()), D


def _format_rejected() -> tuple[MatchResult, RatingFormat]:
    m = Match("m1", ("a",), ("c",), ((9, 5),), ON, S, game_format=GameFormat(points_to=9))
    return RatingEngine().record(m), S


def _unconfirmed() -> tuple[MatchResult, RatingFormat]:
    return RatingEngine().record(_self_reported("m1")), D


def _repeat_limit() -> tuple[MatchResult, RatingFormat]:
    eng = RatingEngine(Config(self_reported_max_repeats=1))
    confirmed = frozenset({"a", "c"})
    assert eng.record(_self_reported("m1", confirmed)).counted
    return eng.record(_self_reported("m2", confirmed)), D


def _before_baseline() -> tuple[MatchResult, RatingFormat]:
    eng = RatingEngine()
    for pid in ("a", "b", "c", "d"):
        eng.seed_external_rating(pid, D, ExternalRatingSnapshot("ext", 4.0, ON))
    return eng.record(_match()), D


def _singles_gap() -> tuple[MatchResult, RatingFormat]:
    eng = _seeded(Config(max_singles_rating_gap=0.5), S, {"a": 3.0, "c": 5.0})
    return eng.record(Match("m1", ("a",), ("c",), ((11, 7),), ON, S)), S


def _partner_gap() -> tuple[MatchResult, RatingFormat]:
    ratings = {"a": 3.0, "b": 5.0, "c": 4.0, "d": 4.0}
    eng = _seeded(Config(max_doubles_partner_gap=0.5), D, ratings)
    return eng.record(_match()), D


REJECTION_CASES: dict[str, Callable[[], tuple[MatchResult, RatingFormat]]] = {
    "DUPLICATE_MATCH_ID": _duplicate,
    "GAME_FORMAT_NOT_ACCEPTED": _format_rejected,
    "SELF_REPORTED_UNCONFIRMED": _unconfirmed,
    "SELF_REPORTED_REPEAT_LIMIT": _repeat_limit,
    "BEFORE_IMPORTED_BASELINE": _before_baseline,
    "SINGLES_RATING_GAP": _singles_gap,
    "DOUBLES_PARTNER_GAP": _partner_gap,
}


def test_rejection_reason_members_and_values() -> None:
    reason = open_engine.RejectionReason
    assert issubclass(reason, str)
    assert [member.name for member in reason] == REJECTIONS
    assert all(member.value == member.name.lower() for member in reason)


@pytest.mark.parametrize("name", REJECTIONS)
def test_each_rejection_reports_its_reason_and_format(name: str) -> None:
    result, fmt = REJECTION_CASES[name]()
    assert not result.counted
    assert result.reason is open_engine.RejectionReason[name]
    assert isinstance(result.rating_format, RatingFormat)
    assert result.rating_format is fmt


def test_counted_match_has_no_reason() -> None:
    result = RatingEngine().record(_match())
    assert result.counted
    assert result.reason is None
    assert isinstance(result.rating_format, RatingFormat)
    assert result.rating_format is D


def test_category_parent_result_reports_the_parent_format() -> None:
    m = Match("m1", ("a", "b"), ("c", "d"), ((11, 7),), ON, RatingFormat.MIXED_DOUBLES)
    result = RatingEngine().record(m)
    assert result.rating_format is RatingFormat.MIXED_DOUBLES
    assert result.parent is not None
    assert result.parent.reason is None
    assert result.parent.rating_format is D


# -- player summary --------------------------------------------------------------

SUMMARY_FIELDS = [
    "player_id",
    "rating_format",
    "rating",
    "reliability",
    "reliability_basis",
    "open_reliability",
    "rd",
    "matches_played",
    "last_played",
    "initial_rating",
    "initial_source",
    "initial_source_reliability",
    "initial_source_date",
    "calculated_as_of",
    "live_updates_since_recalculation",
    "config_version",
    "account_linked",
]


def test_reliability_basis_members() -> None:
    basis = open_engine.ReliabilityBasis
    assert issubclass(basis, str)
    assert {member.name: member.value for member in basis} == {
        "OPEN": "open",
        "IMPORTED": "imported",
    }


def _imported_engine() -> RatingEngine:
    eng = RatingEngine()
    snap = ExternalRatingSnapshot("ext", 4.123456, date(2026, 10, 1), 85.04)
    eng.seed_external_rating("a", D, snap)
    eng.record(_match())
    return eng


def test_player_summary_is_a_frozen_dataclass() -> None:
    summary = _imported_engine().player_summary("a", D)
    assert type(summary) is open_engine.PlayerSummary
    assert [f.name for f in dataclasses.fields(summary)] == SUMMARY_FIELDS
    with pytest.raises(dataclasses.FrozenInstanceError):
        summary.rating = 5.0  # type: ignore[misc]


def test_player_summary_values_for_an_imported_baseline() -> None:
    eng = _imported_engine()
    as_of = date(2026, 11, 5)
    state = eng.state("a", D)
    summary = eng.player_summary("a", D, as_of=as_of)
    assert summary.player_id == "a"
    assert summary.rating_format is D
    assert summary.rating == round(state.rating, 3)
    assert summary.reliability == 85.0
    assert summary.reliability_basis is open_engine.ReliabilityBasis.IMPORTED
    assert summary.open_reliability == engine_module.reliability(state, eng.cfg, as_of)
    assert summary.rd == round(engine_module.inflated_rd(state, as_of, eng.cfg), 4)
    assert summary.matches_played == 1
    assert summary.last_played == ON
    assert type(summary.last_played) is date
    assert summary.initial_rating == 4.123456
    assert summary.initial_source == "ext"
    assert summary.initial_source_reliability == 85.04
    assert summary.initial_source_date == date(2026, 10, 1)
    assert summary.calculated_as_of is None
    assert summary.live_updates_since_recalculation == 1
    assert summary.config_version == eng.cfg.version
    assert summary.account_linked is False


def test_player_summary_for_a_provisional_player_after_replay() -> None:
    eng = replay([_match()], as_of=date(2026, 11, 1))
    eng.link_account("b", "account-b")
    summary = eng.player_summary("b", D)
    assert summary.reliability_basis is open_engine.ReliabilityBasis.OPEN
    assert summary.reliability == summary.open_reliability
    assert summary.initial_source == "open_provisional"
    assert summary.initial_source_date is None
    assert summary.initial_source_reliability is None
    assert summary.calculated_as_of == date(2026, 11, 1)
    assert type(summary.calculated_as_of) is date
    assert summary.live_updates_since_recalculation == 0
    assert summary.account_linked is True


# -- prediction method names -----------------------------------------------------


@pytest.mark.parametrize("old_name", ["predict", "win_probability"])
def test_old_prediction_names_are_gone(old_name: str) -> None:
    assert not hasattr(RatingEngine, old_name)


def test_predict_point_share_matches_the_expectation() -> None:
    eng = _seeded(Config(), D, {"a": 4.0, "b": 4.0, "c": 3.0, "d": 3.0})
    share = eng.predict_point_share(("a", "b"), ("c", "d"), D)
    assert share == open_engine.expected_point_share(4.0, 3.0, 2.9)


def test_predict_win_probability_uses_the_point_share() -> None:
    eng = _seeded(Config(), D, {"a": 4.0, "b": 4.0, "c": 3.0, "d": 3.0})
    fmt = GameFormat()
    share = eng.predict_point_share(("a", "b"), ("c", "d"), D, fmt.scoring)
    got = eng.predict_win_probability(("a", "b"), ("c", "d"), D, fmt, best_of=3)
    assert got == pytest.approx(open_engine.match_win_probability(share, fmt, 3))


HIDDEN_HELPERS = [
    "clone_player",
    "inflated_rd",
    "player_k",
    "information_multiplier",
    "recency_multiplier",
]


@pytest.mark.parametrize("name", HIDDEN_HELPERS)
def test_internal_helpers_are_not_exported(name: str) -> None:
    assert not hasattr(open_engine, name)
    assert name not in open_engine.__all__
    assert callable(getattr(engine_module, name))


PUBLIC_API = [
    "Config",
    "ConfigError",
    "EvaluationError",
    "EvaluationReport",
    "ExternalRatingSnapshot",
    "GameFormat",
    "InvalidMatchError",
    "InvalidSeedError",
    "LedgerConflictError",
    "Match",
    "MatchResult",
    "MatchType",
    "OpenEngineError",
    "Player",
    "PlayerError",
    "PlayerSummary",
    "PrequentialPrediction",
    "RatingEngine",
    "RatingFormat",
    "RatingState",
    "RejectionReason",
    "ReliabilityBasis",
    "ReplayInfo",
    "ScaleFit",
    "ScoringType",
    "WinCalibrationFit",
    "calibrate_win_probability",
    "evaluate",
    "expected_point_share",
    "fit_scale",
    "fit_win_calibration",
    "game_win_probability",
    "ledger_order",
    "match_win_probability",
    "prequential_predictions",
    "reliability",
    "replay",
]
README = Path(__file__).resolve().parent.parent / "README.md"
START, END = "<!-- public-api:start -->", "<!-- public-api:end -->"


def _readme_api_section() -> str:
    text = README.read_text(encoding="utf-8")
    assert text.count(START) == 1 and text.count(END) == 1
    return text[text.index(START) + len(START) : text.index(END)]


def _readme_api_names() -> list[str]:
    """Each backticked name at the start of a list item in the section."""
    names: list[str] = []
    for line in _readme_api_section().splitlines():
        stripped = line.strip()
        if stripped.startswith("- `"):
            names.append(stripped[3:].split("`", 1)[0])
    return names


def test_all_is_the_planned_public_api() -> None:
    assert sorted(open_engine.__all__) == PUBLIC_API
    assert len(set(open_engine.__all__)) == len(open_engine.__all__)
    assert all(hasattr(open_engine, name) for name in open_engine.__all__)


def test_readme_lists_exactly_the_public_api() -> None:
    names = _readme_api_names()
    assert len(names) == len(set(names))
    assert sorted(names) == sorted(open_engine.__all__)


def test_readme_api_section_states_its_limits() -> None:
    section = _readme_api_section()
    assert "The API may change in any 0.x release." in section
    assert "`open_engine.engine`" in section
    assert "`open_engine.winprob`" in section
    assert "internal" in section


def _own_doc(obj: object, name: str) -> str | None:
    """obj's own docstring, ignoring generated and inherited ones."""
    doc = obj.__dict__.get("__doc__") if isinstance(obj, type) else getattr(obj, "__doc__", None)
    # A dataclass gets a generated "Name(field, ...)" docstring, and Python 3.10
    # gives an Enum without one "An enumeration.".
    if not doc or not doc.strip() or doc.startswith(f"{name}(") or doc == "An enumeration.":
        return None
    if isinstance(obj, type) and any(doc == base.__doc__ for base in obj.__mro__[1:]):
        return None
    return doc


def _missing_docstrings() -> list[str]:
    missing: list[str] = []
    for name in open_engine.__all__:
        obj = getattr(open_engine, name)
        if _own_doc(obj, name) is None:
            missing.append(name)
        if not isinstance(obj, type):
            continue
        for attr, member in vars(obj).items():
            if attr.startswith("_"):
                continue
            if isinstance(member, (staticmethod, classmethod)):
                member = member.__func__
            if isinstance(member, property) or callable(member) and not isinstance(member, type):
                if _own_doc(member, attr) is None:
                    missing.append(f"{name}.{attr}")
    return missing


def test_every_public_name_and_method_has_a_docstring() -> None:
    assert _missing_docstrings() == []


def test_prediction_methods_validate_teams() -> None:
    with pytest.raises(open_engine.InvalidMatchError, match="requires 2 player"):
        RatingEngine().predict_point_share(("a",), ("c", "d"), D)
