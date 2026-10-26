"""Every data file of the package is declared, so an installed copy is the same program as the checkout."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

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


def test_the_flatpak_sandbox_has_an_alsa_config_with_a_group_id_that_exists() -> None:
    """Regression: in the sandbox the runtime's ipc_gid is the group name "audio", which does
    not exist there, so capturing from "sysdefault:CARD=..." (the Tuner's and the Analyzer's
    default) failed with "The field ipc_gid must be a valid group"."""
    manifest = (ROOT / "packaging/flatpak/io.github.jpbarraca.katatone.yml").read_text()
    assert "--env=ALSA_CONFIG_PATH=/app/share/katatone/alsa.conf" in manifest
    assert "install -Dm644 packaging/flatpak/alsa.conf -t /app/share/katatone" in manifest
    conf = [ln.strip() for ln in (ROOT / "packaging/flatpak/alsa.conf").read_text().splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]
    assert conf[0] == "</usr/share/alsa/alsa-flatpak.conf>"         # the runtime's own configuration
    assert conf[1:] == ['defaults.pcm.ipc_gid "0"']                  # a quoted string, as ALSA needs


def test_the_makefile_builds_the_flatpak_from_the_manifest_and_the_project_version() -> None:
    """`make -n flatpak` prints what would run: nothing is built."""
    import shutil
    import subprocess
    if shutil.which("make") is None:
        pytest.skip("no make")
    out = subprocess.run(["make", "-n", "flatpak"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert "flatpak-builder --user --force-clean --disable-rofiles-fuse" in out
    assert "io.github.jpbarraca.katatone.yml" in out and "cd packaging/flatpak" in out
    assert f"katatone-{version}-" in out and ".flatpak" in out and "sha256sum" in out
    help_text = subprocess.run(["make"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    for target in ("flatpak-deps", "flatpak", "flatpak-install", "flatpak-run"):
        assert f"make {target} " in help_text


def test_the_makefile_installs_the_runtime_the_manifest_asks_for() -> None:
    import shutil
    import subprocess
    if shutil.which("make") is None:
        pytest.skip("no make")
    out = subprocess.run(["make", "-n", "flatpak-deps"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    manifest = (ROOT / "packaging/flatpak/io.github.jpbarraca.katatone.yml").read_text()
    runtime = re.search(r"^runtime-version: '(\d+)'", manifest, re.MULTILINE).group(1)
    assert f"org.gnome.Platform//{runtime} org.gnome.Sdk//{runtime}" in out
