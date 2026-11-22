"""Pin the public API surface that 1.0.0 freezes (issue #156).

The surface is every name in open_engine.__all__: each function's, constructor's,
and public method's parameters, each dataclass's options and fields, each enum's
members, each error's bases and attributes, RatingEngine's public instance
attributes, and the values of the PredictionMode type that public signatures
name. It is rendered to text and compared with tests/api_surface.txt. A few
recorded details, such as the ledger_order key type and the Config defaults,
are outside the stability promise; the file records them so a change to them
is deliberate.

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
import typing
from collections.abc import Mapping
from pathlib import Path

import open_engine
from open_engine._evaluation import PredictionMode

SURFACE_FILE = Path(__file__).resolve().parent / "api_surface.txt"
# Config.version defaults to the package version, which moves every release.
VERSION_MARK = "<__version__>"
DATACLASS_FLAGS = ("init", "repr", "eq", "order", "unsafe_hash", "frozen")


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
    if "__init__" in vars(cls):
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
        return lines + _class_members(obj)
    if issubclass(obj, BaseException):
        sample = obj(["m1"]) if obj is open_engine.LedgerConflictError else obj("message")
        attrs = sorted(a for a in vars(sample) if not a.startswith("_"))
        lines = [f"exception {name}({bases})", *_class_members(obj)]
        return lines + [f"    attribute {attr}" for attr in attrs]
    if dataclasses.is_dataclass(obj):
        params = obj.__dataclass_params__  # type: ignore[attr-defined]
        flags = " ".join(f"{flag}={getattr(params, flag)}" for flag in DATACLASS_FLAGS)
        lines = [f"dataclass {name}({bases}) {flags}"]
        for field in dataclasses.fields(obj):
            text = f"    field {field.name}: {field.type}"
            if field.default is not dataclasses.MISSING:
                text += f" = {_show(field.default)}"
            elif field.default_factory is not dataclasses.MISSING:
                text += f" = factory {_show(field.default_factory())}"
            if not field.init or field.kw_only is True:
                text += f" [init={field.init} kw_only={field.kw_only}]"
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
    # Type aliases that public signatures name but open_engine does not export.
    modes = ", ".join(repr(mode) for mode in typing.get_args(PredictionMode))
    blocks.append(f"alias PredictionMode = Literal[{modes}]")
    return "\n\n".join(blocks) + "\n"


def test_public_surface_matches_the_checked_in_file() -> None:
    expected = SURFACE_FILE.read_text(encoding="utf-8")
    assert render_surface() == expected, (
        "The public API surface changed. If that is intended, regenerate "
        "tests/api_surface.txt with `python tests/test_api_surface.py` and add a "
        "CHANGELOG.md entry (a breaking change is called out as one)."
    )


def test_the_surface_covers_every_export() -> None:
    headers = [
        line
        for line in render_surface().splitlines()
        if line and line[0] != " " and not line.startswith("alias ")
    ]
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
