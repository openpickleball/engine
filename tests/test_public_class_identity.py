"""Public classes report the module that defines them (issue #133).

Match digests take their type tags from a fixed table, so a class no
longer pins open_engine.engine as its module. Pickles made before still
name open_engine.engine.<Class>, which re-exports every public class and
_FrozenMapping, so they still load. All ids are synthetic.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pickle
import re
import sys
import typing
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

import open_engine
from open_engine import Config, RatingEngine, ScoringType, replay
from open_engine._config import _FrozenMapping
from test_exact_input_plain_unchanged import _ledger, _seeds

SRC = Path(open_engine.__file__).parent
PUBLIC_CLASSES = [
    getattr(open_engine, name)
    for name in open_engine.__all__
    if isinstance(getattr(open_engine, name), type)
]
# Public objects hold a _FrozenMapping (Config's mapping fields and
# ReplayInfo.net_rating_created), so their pickles name it.
PICKLED_CLASSES = [*PUBLIC_CLASSES, _FrozenMapping]


def _name(cls: type) -> str:
    return cls.__qualname__


def _defining_module(cls: type) -> ModuleType:
    """The package module whose source defines cls at top level."""
    for path in sorted(SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.ClassDef) and node.name == cls.__name__ for node in tree.body):
            return importlib.import_module(f"open_engine.{path.stem}")
    raise LookupError(cls.__name__)


@pytest.mark.parametrize("cls", PICKLED_CLASSES, ids=_name)
def test_a_class_reports_the_module_that_defines_it(cls: type) -> None:
    assert cls.__module__ == _defining_module(cls).__name__


@pytest.mark.parametrize("cls", PICKLED_CLASSES, ids=_name)
def test_get_source_finds_the_class(cls: type) -> None:
    assert inspect.getsourcefile(cls) == _defining_module(cls).__file__
    assert re.search(rf"^class {cls.__name__}\b", inspect.getsource(cls), re.MULTILINE)


@pytest.mark.parametrize("cls", PUBLIC_CLASSES, ids=_name)
def test_type_hints_resolve_as_in_the_defining_module(cls: type) -> None:
    hints = typing.get_type_hints(cls)
    assert hints == typing.get_type_hints(cls, globalns=vars(_defining_module(cls)))


@pytest.mark.parametrize("cls", PICKLED_CLASSES, ids=_name)
def test_a_pickle_finds_the_class_where_it_reports(cls: type) -> None:
    assert getattr(sys.modules[cls.__module__], cls.__qualname__) is cls


@pytest.mark.parametrize("cls", PICKLED_CLASSES, ids=_name)
def test_a_pickle_made_before_issue_133_finds_the_class(cls: type) -> None:
    # Before #133 every class but the errors reported open_engine.engine,
    # and engine.py re-exports the errors too.
    stream = f"copen_engine.engine\n{cls.__qualname__}\n.".encode()
    assert pickle.loads(stream) is cls


def test_a_config_survives_a_pickle_round_trip() -> None:
    cfg = Config(scale_by_scoring={ScoringType.RALLY: 3.0, ScoringType.SIDE_OUT: 3.5})
    loaded = pickle.loads(pickle.dumps(cfg))
    assert loaded == cfg
    assert loaded.fingerprint() == cfg.fingerprint()
    assert type(loaded.scale_by_scoring) is _FrozenMapping


def test_a_replayed_engine_survives_a_pickle_round_trip() -> None:
    engine = replay(_ledger(), _seeds(), as_of=date(2026, 6, 1))
    loaded = pickle.loads(pickle.dumps(engine))
    assert type(loaded) is RatingEngine
    assert loaded.to_snapshot() == engine.to_snapshot()
    assert loaded.replay_info == engine.replay_info
