"""Every data file of the package is declared, so an installed copy is the same program as the checkout."""
from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ImportError:                 # Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent


def test_every_non_python_file_in_the_package_is_declared_as_package_data() -> None:
    declared = set(tomllib.loads((ROOT / "pyproject.toml").read_text())
                   ["tool"]["setuptools"]["package-data"]["katatone"])
    present = {str(p.relative_to(ROOT / "katatone")) for p in (ROOT / "katatone").rglob("*")
               if p.is_file() and p.suffix not in (".py", ".pyc") and "__pycache__" not in p.parts}
    assert present <= declared, f"not installed with the package: {sorted(present - declared)}"
    assert "gui/param_labels.json" in declared     # without it every selector shows as a dial
