"""Tests for the layout of the engine module docstring and the public docstrings."""

from __future__ import annotations

import inspect

import pytest

import open_engine
import open_engine.engine as engine_module

MODULE_DOC_MAX_LINES = 40
PUBLIC_DOC_MAX_LINES = 30
SUMMARY_MAX_CHARS = 100


def _public_objects() -> list[tuple[str, object]]:
    """Every exported name, and every public method or property of an exported class."""
    found: list[tuple[str, object]] = []
    for name in open_engine.__all__:
        obj = getattr(open_engine, name)
        if isinstance(obj, str):
            continue
        found.append((name, obj))
        if not isinstance(obj, type):
            continue
        for attr, member in vars(obj).items():
            if attr.startswith("_"):
                continue
            if isinstance(member, (staticmethod, classmethod)):
                member = member.__func__
            if isinstance(member, property) or callable(member) and not isinstance(member, type):
                found.append((f"{name}.{attr}", member))
    return found


def _own_doc(obj: object) -> str:
    doc = obj.__dict__.get("__doc__") if isinstance(obj, type) else getattr(obj, "__doc__", None)
    assert isinstance(doc, str)
    return inspect.cleandoc(doc)


def _has_parameters(obj: object) -> bool:
    if isinstance(obj, (type, property)) or not callable(obj):
        return False
    params = list(inspect.signature(obj).parameters)
    return bool([p for p in params if p not in ("self", "cls")])


PUBLIC = _public_objects()
FUNCTIONS_WITH_PARAMETERS = [(name, obj) for name, obj in PUBLIC if _has_parameters(obj)]


def test_module_docstring_keeps_the_demonstration_label() -> None:
    doc = engine_module.__doc__
    assert doc is not None
    text = " ".join(doc.split())
    assert "Status: demonstration only." in text
    assert "the outputs are not OPEN ratings" in text
    assert "Do not use this engine to rate real players." in text


def test_module_docstring_stays_short_and_points_to_the_docs() -> None:
    doc = engine_module.__doc__
    assert doc is not None
    assert len(inspect.cleandoc(doc).splitlines()) <= MODULE_DOC_MAX_LINES
    assert "docs/model.md" in doc
    assert "docs/behavior.md" in doc


@pytest.mark.parametrize(("name", "obj"), PUBLIC, ids=[name for name, _ in PUBLIC])
def test_public_docstring_has_a_summary_line(name: str, obj: object) -> None:
    lines = _own_doc(obj).splitlines()
    assert lines and lines[0].strip(), name
    assert len(lines[0]) < SUMMARY_MAX_CHARS, name
    if len(lines) > 1:
        assert lines[1] == "", f"{name}: the summary must be one line followed by a blank line"


@pytest.mark.parametrize(("name", "obj"), PUBLIC, ids=[name for name, _ in PUBLIC])
def test_public_docstring_stays_within_the_line_limit(name: str, obj: object) -> None:
    lines = _own_doc(obj).splitlines()
    assert len(lines) <= PUBLIC_DOC_MAX_LINES, f"{name}: {len(lines)} lines"


@pytest.mark.parametrize(
    ("name", "obj"),
    FUNCTIONS_WITH_PARAMETERS,
    ids=[name for name, _ in FUNCTIONS_WITH_PARAMETERS],
)
def test_public_function_with_parameters_has_an_args_section(name: str, obj: object) -> None:
    lines = _own_doc(obj).splitlines()
    assert "Args:" in lines, name
