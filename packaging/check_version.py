#!/usr/bin/env python3
"""Check that a release's version is written the same everywhere.

The version lives in ``pyproject.toml`` and in the newest ``<release>`` of the
Flatpak's metainfo, and a release is made from a tag ``vX.Y.Z``.  Run with the
tag as the argument to check all three; prints the version when they agree and
the differences (exit status 1) when they do not.

    python3 packaging/check_version.py [v0.5.0]
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METAINFO = Path("packaging/flatpak/io.github.jpbarraca.katatone.metainfo.xml")


def project_version(root: Path = ROOT) -> str:
    """The ``version`` of ``pyproject.toml``."""
    match = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(),
                      re.MULTILINE)
    if not match:
        raise ValueError("pyproject.toml has no version")
    return match.group(1)


def metainfo_version(root: Path = ROOT) -> str:
    """The version of the first (newest) ``<release>`` of the metainfo."""
    release = ET.parse(root / METAINFO).getroot().find("./releases/release")
    if release is None or not release.get("version"):
        raise ValueError(f"{METAINFO} lists no release")
    return release.get("version")


def check(root: Path = ROOT, tag: str | None = None) -> tuple[str, list[str]]:
    """Compare the places the version is written.

    Args:
        root: the repository.
        tag: the release tag (``v0.5.0``), if the release is being made.
    Returns:
        ``(version, problems)``: the project's version and what differs from it.
    """
    version = project_version(root)
    problems = []
    meta = metainfo_version(root)
    if meta != version:
        problems.append(f"the newest <release> of {METAINFO} is {meta}, pyproject.toml says {version}")
    if tag is not None and tag != f"v{version}":
        problems.append(f"the tag is {tag}, pyproject.toml says {version} (expected v{version})")
    return version, problems


def main(argv: list[str]) -> int:
    version, problems = check(ROOT, argv[0] if argv and argv[0] else None)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
