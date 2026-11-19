"""The package's modules import in one order, with no cycle (issue #127)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "open_engine"

# Modules any module may import: they import nothing else from the package.
BASE = ("_errors", "_version")

# The engine modules in import order. Each may import from the package only
# BASE and the modules above it.
ORDER = (
    "_validation",
    "_types",
    "winprob",
    "_config",
    "_model",
    "_ledger",
    "_math",
    "_connectivity",
    "_seeds",
    "_snapshot",
    "_engine",
    "_replay",
    "_evaluation",
)

# Modules nothing in the package imports: the re-export modules.
TOP = ("engine", "__init__")

# The only imports inside a function, as on main: winprob reads GameFormat
# when it checks a format.
FUNCTION_IMPORTS = {("winprob", "_check_format", "_types")}


def _tree(name: str) -> ast.Module:
    return ast.parse((SRC / f"{name}.py").read_text(encoding="utf-8"))


def _package_imports(tree: ast.Module) -> set[str]:
    """Every package module an import in tree names, at any depth."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module is not None:
                found.add(node.module.split(".")[0])
            elif node.level == 1:
                found.update(alias.name for alias in node.names)
            elif node.module is not None and node.module.split(".")[0] == "open_engine":
                parts = node.module.split(".")
                found.add(parts[1] if len(parts) > 1 else "__init__")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "open_engine":
                    found.add(parts[1] if len(parts) > 1 else "__init__")
    return found


def test_every_module_has_a_place_in_the_order() -> None:
    on_disk = {path.stem for path in SRC.glob("*.py")}
    assert on_disk == {*BASE, *ORDER, *TOP}


@pytest.mark.parametrize("name", BASE)
def test_base_modules_import_nothing_from_the_package(name: str) -> None:
    assert _package_imports(_tree(name)) == set()


@pytest.mark.parametrize("name", ORDER)
def test_each_module_imports_only_modules_above_it(name: str) -> None:
    allowed = {*BASE, *ORDER[: ORDER.index(name)]}
    assert _package_imports(_tree(name)) - allowed == set()


@pytest.mark.parametrize("name", TOP)
def test_no_module_imports_a_re_export_module(name: str) -> None:
    importers = [other for other in (*BASE, *ORDER) if name in _package_imports(_tree(other))]
    assert importers == []


def test_imports_inside_functions_are_only_the_ones_main_has() -> None:
    found: set[tuple[str, str, str]] = set()
    for name in (*BASE, *ORDER, *TOP):
        for node in ast.walk(_tree(name)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, (ast.Import, ast.ImportFrom)) and inner is not node:
                    targets = _package_imports(ast.Module(body=[inner], type_ignores=[]))
                    found.update((name, node.name, target) for target in targets or {"stdlib"})
    assert found == FUNCTION_IMPORTS
