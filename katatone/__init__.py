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
    try:
        version = metadata.version("katatone")
    except metadata.PackageNotFoundError:           # not installed
        version = "unknown"
    root = Path(__file__).resolve().parent.parent
    if not (root / ".git").exists():                # installed copy, not a checkout
        return version
    tag = _git(root, "describe", "--tags", "--exact-match")
    if tag:
        return tag.removeprefix("v")
    date = _git(root, "log", "-1", "--format=%cs")
    return f"{version} ({date})" if date else version


__version__ = _version()
