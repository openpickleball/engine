"""Check that an installed wheel, not the checkout, loads the published parameter sets.

Usage: python -I scripts/check_installed_wheel.py

Run it with the interpreter of a clean virtual environment that has only the
built wheel installed. -I keeps the checkout off sys.path, so the import
comes from the wheel. Make the environment outside the checkout: one inside
it (such as .venv/) counts as the checkout. CI's package job and the release
build run it after building the wheel.

Exits 1, naming each problem, when:
- open_engine is imported from inside this checkout;
- Config.published_sets() does not list v1;
- Config.published("v1").parameter_fingerprint() is not V1_FINGERPRINT;
- a label in Config.published_sets() fails to load, or loads with a parameter
  fingerprint other than the one published for it.
Exits 0 otherwise. Standard library only, besides the installed package.
"""

from __future__ import annotations

from pathlib import Path

# The published fingerprint of v1, from PARAMETERS.md, written out here so the
# check does not trust the installed package's own table for it.
V1_FINGERPRINT = "f9149f3c38a01efc"
CHECKOUT = Path(__file__).resolve().parents[1]


def problems(checkout: Path) -> list[str]:
    """Return problem descriptions for the open_engine that imports, empty when clean."""
    import open_engine
    from open_engine import Config, OpenEngineError
    from open_engine._config import _PUBLISHED_PARAMETER_SETS

    found: list[str] = []
    location = Path(open_engine.__file__).resolve()
    if location.is_relative_to(checkout.resolve()):
        found.append(f"open_engine imported from the checkout, not an installed wheel: {location}")
    labels = Config.published_sets()
    if "v1" not in labels:
        found.append(f"published_sets() does not list v1: {labels!r}")
    table = dict(_PUBLISHED_PARAMETER_SETS)
    expected = {**table, "v1": V1_FINGERPRINT}
    for label in sorted({*labels, "v1"}):
        try:
            fingerprint = Config.published(label).parameter_fingerprint()
        except OpenEngineError as exc:
            found.append(f"published({label!r}) failed: {exc}")
            continue
        if fingerprint != expected[label]:
            found.append(
                f"published({label!r}) has parameter fingerprint {fingerprint},"
                f" not {expected[label]}"
            )
    return found


def main() -> int:
    found = problems(CHECKOUT)
    for problem in found:
        print(problem)
    if found:
        return 1
    import open_engine

    print(f"ok: open_engine {open_engine.__version__} from {Path(open_engine.__file__).parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
