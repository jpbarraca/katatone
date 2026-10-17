"""Profile storage: one JSON file per profile under ``<data dir>/midi/``.

``active.txt`` names the profile in use. Saves are atomic (write to a ``.tmp``
file, then ``os.replace``) so a crash mid-save cannot leave a half-written
profile that would drop every mapping.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from ..library.store import clean_name
from .mapping import Profile

log = logging.getLogger("katatone.midi.store")

DEFAULT_NAME = "Default"


def default_dir() -> Path:
    """The profile directory (same data-dir rule as the patch library).

    Returns:
        ``<user data dir>/katatone/midi``; not created here.
    """
    return data_root() / "midi"


class ProfileStore:
    """Reads and writes profiles in one directory."""

    def __init__(self, root: str | Path | None = None) -> None:
        """Open (and create) the profile directory.

        Args:
            root: directory for the JSON files (defaults to `default_dir()`).
        """
        self.root = Path(root) if root else default_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self._active_file = self.root / "active.txt"

    def _path(self, name: str) -> Path:
        """File of the profile called `name` (the name is made file-safe).

        Args:
            name: the profile name.
        """
        return self.root / f"{clean_name(name)}.json"

    def list(self) -> list[str]:
        """The names of all stored profiles, sorted.

        Returns:
            File stems of ``*.json`` in the directory.
        """
        return sorted(p.stem for p in self.root.glob("*.json"))

    def exists(self, name: str) -> bool:
        """Whether a profile of this name is stored.

        Args:
            name: the profile name.
        """
        return self._path(name).is_file()

    def load(self, name: str) -> Profile:
        """Read one profile.

        Args:
            name: the profile name.

        Returns:
            The profile, named after the file.

        Raises:
            FileNotFoundError if there is no such profile.
            ValueError if the file is not valid JSON or is from a newer version.
        """
        path = self._path(name)
        try:
            prof = Profile.from_dict(json.loads(path.read_text()))
        except json.JSONDecodeError as e:
            raise ValueError(f"{path.name}: not valid JSON ({e})") from None
        prof.name = path.stem
        return prof

    def save(self, profile: Profile) -> None:
        """Write a profile atomically, under its (file-safe) name.

        Args:
            profile: the profile to store; its name is normalised in place.
        """
        profile.name = clean_name(profile.name)
        path = self._path(profile.name)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(profile.to_dict(), indent=2) + "\n")
        os.replace(tmp, path)

    def delete(self, name: str) -> None:
        """Remove a profile; deleting the active one falls back to Default.

        Args:
            name: the profile name (a missing profile is not an error).
        """
        self._path(name).unlink(missing_ok=True)
        if self._read_active() == clean_name(name):
            self._active_file.unlink(missing_ok=True)

    def rename(self, old: str, new: str) -> None:
        """Rename a profile, keeping it active if it was.

        Args:
            old: the current name.
            new: the new name.

        Raises:
            FileNotFoundError if `old` does not exist.
            FileExistsError if `new` already does.
        """
        prof = self.load(old)
        if clean_name(new) == clean_name(old):
            return
        if self.exists(new):
            raise FileExistsError(f"a profile named {clean_name(new)!r} exists")
        was_active = self._read_active() == clean_name(old)
        prof.name = new
        self.save(prof)
        self._path(old).unlink()
        if was_active:
            self.set_active(prof.name)

    def duplicate(self, name: str, new: str) -> Profile:
        """Copy a profile (mappings get fresh ids so the copies stay independent).

        Args:
            name: the profile to copy.
            new: the name of the copy.

        Returns:
            The stored copy.

        Raises:
            FileExistsError if `new` already exists.
        """
        from .mapping import _new_id
        if self.exists(new):
            raise FileExistsError(f"a profile named {clean_name(new)!r} exists")
        prof = self.load(name)
        prof.name = new
        for m in prof.mappings:
            m.id = _new_id()
        self.save(prof)
        return prof

    def import_file(self, path: str | Path) -> Profile:
        """Import a profile JSON from anywhere, under a free name.

        Args:
            path: the file to import.

        Returns:
            The stored profile (renamed "<name> 2", "<name> 3" ... on a clash).

        Raises:
            ValueError if the file is not a usable profile.
        """
        try:
            prof = Profile.from_dict(json.loads(Path(path).read_text()))
        except (json.JSONDecodeError, OSError, AttributeError, TypeError) as e:
            raise ValueError(f"cannot import {path}: {e}") from None
        base = clean_name(prof.name or Path(path).stem)
        name, i = base, 2
        while self.exists(name):
            name, i = f"{base} {i}", i + 1
        prof.name = name
        self.save(prof)
        return prof

    def export_file(self, name: str, path: str | Path) -> None:
        """Copy a stored profile to `path`.

        Args:
            name: the profile to export.
            path: the destination file.
        """
        Path(path).write_text(json.dumps(self.load(name).to_dict(), indent=2) + "\n")

    # -- active profile ----------------------------------------------------

    def _read_active(self) -> str | None:
        """The name in active.txt, or None."""
        try:
            return self._active_file.read_text().strip() or None
        except OSError:
            return None

    def active(self) -> str:
        """The active profile's name; creates an empty "Default" on first use.

        Returns:
            A name for which a stored profile exists.
        """
        name = self._read_active()
        if name and self.exists(name):
            return name
        if not self.exists(DEFAULT_NAME):
            self.save(Profile(name=DEFAULT_NAME))
        self.set_active(DEFAULT_NAME)
        return DEFAULT_NAME

    def set_active(self, name: str) -> None:
        """Make a profile the active one.

        Args:
            name: an existing profile.

        Raises:
            FileNotFoundError if it does not exist.
        """
        if not self.exists(name):
            raise FileNotFoundError(name)
        tmp = self._active_file.with_suffix(".txt.tmp")
        tmp.write_text(clean_name(name) + "\n")
        os.replace(tmp, self._active_file)
