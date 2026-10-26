import re
import subprocess
from importlib import metadata
from pathlib import Path


def _git(root: Path, *args: str) -> str | None:
    """Run ``git`` in ``root``; the output, or None if it fails."""
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                             text=True, timeout=2, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


def _version() -> str:
    """The version to show: a release's own number, and for a source checkout
    that is not exactly on a release tag, the last commit's date as well."""
    root = Path(__file__).resolve().parent.parent
    if not (root / ".git").exists():                # installed copy, not a checkout
        try:
            return metadata.version("katatone")
        except metadata.PackageNotFoundError:
            return "unknown"
    # In a checkout pyproject.toml is current; the installed metadata may be stale.
    try:
        text = (root / "pyproject.toml").read_text()
    except OSError:
        text = ""
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    version = match.group(1) if match else "unknown"
    tag = _git(root, "describe", "--tags", "--exact-match")
    if tag:
        return tag.removeprefix("v")
    date = _git(root, "log", "-1", "--format=%cs")
    return f"{version} ({date})" if date else version


__version__ = _version()
