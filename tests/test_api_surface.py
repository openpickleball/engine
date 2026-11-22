"""Pin the public API surface that 1.0.0 freezes (issue #156).

The surface is every name in open_engine.__all__: each function's and public
method's parameters, each dataclass's fields, each enum's members, each error's
bases, and RatingEngine's public instance attributes. It is rendered to text
and compared with tests/api_surface.txt.

A change to the public surface must change that file in the same pull request,
with a CHANGELOG.md entry (a breaking one called out as breaking). Regenerate
the file with:

    python tests/test_api_surface.py
"""

from __future__ import annotations

import dataclasses
import enum
import inspect
import sys
from collections.abc import Mapping
from pathlib import Path

import open_engine

SURFACE_FILE = Path(__file__).resolve().parent / "api_surface.txt"
# Config.version defaults to the package version, which moves every release.
VERSION_MARK = "<__version__>"


def _show(value: object) -> str:
    """A repr that does not depend on hash order or the package version."""
    if isinstance(value, enum.Enum):
        return f"{type(value).__name__}.{value.name}"
    if isinstance(value, (set, frozenset)):
        return f"{type(value).__name__}({{{', '.join(sorted(_show(v) for v in value))}}})"
    if isinstance(value, Mapping):
        return "{" + ", ".join(f"{_show(k)}: {_show(v)}" for k, v in value.items()) + "}"
    if isinstance(value, tuple):
        inner = ", ".join(_show(v) for v in value)
        return f"({inner},)" if len(value) == 1 else f"({inner})"
    if value == open_engine.__version__ and type(value) is str:
        return VERSION_MARK
    return repr(value)


def _signature(func: object) -> str:
    """Parameters with their annotation and default, and the return type.

    A parameter that is not positional-or-keyword is marked with its kind.
    """
    sig = inspect.signature(func)  # type: ignore[arg-type]
    parts: list[str] = []
    for param in sig.parameters.values():
        text = param.name
        if param.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD:
            text += f" [{param.kind.name.lower()}]"
        if param.annotation is not inspect.Parameter.empty:
            text += f": {param.annotation}"
        if param.default is not inspect.Parameter.empty:
            text += f" = {_show(param.default)}"
        parts.append(text)
    returns = sig.return_annotation
    tail = "" if returns is inspect.Signature.empty else f" -> {returns}"
    return f"({', '.join(parts)}){tail}"


def _class_members(cls: type) -> list[str]:
    lines: list[str] = []
    if "__init__" in vars(cls) and not dataclasses.is_dataclass(cls):
        lines.append(f"    __init__{_signature(vars(cls)['__init__'])}")
    for attr, member in sorted(vars(cls).items()):
        if attr.startswith("_"):
            continue
        if isinstance(member, classmethod):
            lines.append(f"    classmethod {attr}{_signature(member.__func__)}")
        elif isinstance(member, staticmethod):
            lines.append(f"    staticmethod {attr}{_signature(member.__func__)}")
        elif isinstance(member, property):
            lines.append(f"    property {attr}")
        elif inspect.isfunction(member):
            lines.append(f"    def {attr}{_signature(member)}")
    return lines


def _render_name(name: str) -> list[str]:
    obj = getattr(open_engine, name)
    if not isinstance(obj, type):
        return [f"def {name}{_signature(obj)}"]
    bases = ", ".join(base.__name__ for base in obj.__bases__)
    if issubclass(obj, enum.Enum):
        lines = [f"enum {name}({bases})"]
        lines += [f"    {member.name} = {member.value!r}" for member in obj]
        return lines
    if issubclass(obj, BaseException):
        return [f"exception {name}({bases})", *_class_members(obj)]
    if dataclasses.is_dataclass(obj):
        frozen = obj.__dataclass_params__.frozen  # type: ignore[attr-defined]
        lines = [f"dataclass {name}({bases}) frozen={frozen}"]
        for field in dataclasses.fields(obj):
            text = f"    field {field.name}: {field.type}"
            if field.default is not dataclasses.MISSING:
                text += f" = {_show(field.default)}"
            elif field.default_factory is not dataclasses.MISSING:
                text += f" = factory {_show(field.default_factory())}"
            lines.append(text)
        return lines + _class_members(obj)
    lines = [f"class {name}({bases})", *_class_members(obj)]
    if obj is open_engine.RatingEngine:
        attrs = sorted(a for a in vars(open_engine.RatingEngine()) if not a.startswith("_"))
        lines += [f"    attribute {attr}" for attr in attrs]
    return lines


def render_surface() -> str:
    """The public surface as text, one block per exported name, sorted by name."""
    blocks = ["\n".join(_render_name(name)) for name in sorted(open_engine.__all__)]
    return "\n\n".join(blocks) + "\n"


def test_public_surface_matches_the_checked_in_file() -> None:
    expected = SURFACE_FILE.read_text(encoding="utf-8")
    assert render_surface() == expected, (
        "The public API surface changed. If that is intended, regenerate "
        "tests/api_surface.txt with `python tests/test_api_surface.py` and add a "
        "CHANGELOG.md entry (a breaking change is called out as one)."
    )


def test_the_surface_covers_every_export() -> None:
    headers = [line for line in render_surface().splitlines() if line and line[0] != " "]
    names = [header.split()[1].split("(", 1)[0] for header in headers]
    assert names == sorted(open_engine.__all__)


def test_the_self_reported_log_is_internal() -> None:
    engine = open_engine.RatingEngine()
    assert not hasattr(engine, "self_reported_log")
    assert "attribute self_reported_log" not in render_surface()


def test_the_surface_ignores_the_package_version() -> None:
    assert open_engine.__version__ not in render_surface()
    assert f"field version: str = {VERSION_MARK}" in render_surface()


if __name__ == "__main__":
    SURFACE_FILE.write_text(render_surface(), encoding="utf-8")
    sys.stdout.write(f"wrote {SURFACE_FILE}\n")
