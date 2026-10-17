"""Local patch library: livesets (groups of patches) stored as .tsl files in
~/.local/share/katatone/livesets/."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..liveset.tsl import Tsl
from ..paths import data_root

MAX_LIVESETS = 50          # library limit
NAME_MAX = 64              # liveset name length


class LibraryFull(ValueError):
    """Raised when creating a liveset beyond MAX_LIVESETS."""


@dataclass
class LibraryEntry:
    name: str
    path: Path
    mtime: float
    count: int | None = None      # patches in the liveset (None: unreadable)


def clean_name(name: str) -> str:
    """A liveset name usable as a file name: ASCII, no path separators."""
    name = name.encode("ascii", "replace").decode("ascii")
    name = name.replace("/", "-").replace("\\", "-").strip(" .")
    return name[:NAME_MAX] or "Liveset"


class Library:
    def __init__(self, root: str | Path | None = None) -> None:
        """Open (or create) the liveset directory.

        Args:
            root: where ``.tsl`` files live (defaults to ``<data dir>/livesets``).
        """
        self.root = Path(root) if root else data_root() / "livesets"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        """File path of the liveset called ``name``.

        Args:
            name: liveset name (used as the file stem).
        Returns:
            A ``Path`` under the library root.
        """
        return self.root / f"{name}.tsl"

    def exists(self, name: str) -> bool:
        """Whether a liveset with this name is stored in the library.

        Args:
            name: liveset name to check.
        """
        return self._path(name).exists()

    def list(self) -> list[LibraryEntry]:
        """List the stored livesets, sorted by name.

        Returns:
            One ``LibraryEntry`` per ``.tsl`` file; ``count`` is None for
            files that cannot be read.
        """
        out = []
        for p in sorted(self.root.glob("*.tsl"), key=lambda p: p.stem.lower()):
            try:
                count = len(Tsl.load(p))
            except Exception:                        # noqa: BLE001
                count = None
            out.append(LibraryEntry(name=p.stem, path=p,
                                    mtime=p.stat().st_mtime, count=count))
        return out

    def unique_name(self, base: str) -> str:
        """``base``, or ``base (2)``, ``base (3)``, ... if already taken."""
        base = clean_name(base)
        name, n = base, 2
        while self.exists(name):
            name = f"{base} ({n})"
            n += 1
        return name

    def save(self, tsl: Tsl) -> Path:
        """Store a liveset in the library under a cleaned-up name.

        Args:
            tsl: the liveset to store (its ``name`` is cleaned).
        Returns:
            The path of the ``.tsl`` file written.
        """
        tsl.name = clean_name(tsl.name)
        p = self._path(tsl.name)
        tsl.save(p)
        return p

    def load(self, name: str) -> Tsl:
        """Load a stored liveset by name.

        Args:
            name: liveset name (file stem, without ``.tsl``).
        Returns:
            The liveset as a ``Tsl``.
        """
        return Tsl.load(self._path(name))

    def create(self, name: str, patches: Tsl | None = None) -> Tsl:
        """New liveset (empty, or a copy of ``patches``) under a free name.

        Args:
            name: the desired liveset name (a free name is picked).
            patches: liveset to copy the patches and memos from (None: empty).
        Returns:
            The saved liveset (with its assigned name).
        Raises:
            LibraryFull if the library already has MAX_LIVESETS livesets.
        """
        if len(list(self.root.glob("*.tsl"))) >= MAX_LIVESETS:
            raise LibraryFull(f"the library already has {MAX_LIVESETS} livesets")
        tsl = Tsl(name=self.unique_name(name))
        if patches is not None:
            tsl.patches = [dict(p) for p in patches.patches]
            tsl.memos = list(patches.memos)
        self.save(tsl)
        return tsl

    def rename(self, old: str, new: str) -> str:
        """Rename a stored liveset.

        Args:
            old: the current liveset name.
            new: the new name (cleaned; must not already be used).
        Returns:
            The new name as stored.
        Raises:
            FileExistsError if a liveset with the new name already exists.
        """
        new = clean_name(new)
        if new == old:
            return old
        if self.exists(new):
            raise FileExistsError(f"a liveset named {new!r} already exists")
        tsl = self.load(old)
        tsl.name = new
        self.save(tsl)
        self._path(old).unlink(missing_ok=True)
        return new

    def duplicate(self, name: str) -> Tsl:
        """Store a copy of a liveset under ``<name> copy``.

        Args:
            name: the liveset to copy.
        Returns:
            The new liveset.
        """
        return self.create(f"{name} copy", self.load(name))

    def import_file(self, src: str | Path) -> Path:
        """Import an external ``.tsl`` file into the library.

        Args:
            src: the ``.tsl`` file to import (loaded, which validates it).
        Returns:
            The path of the file as stored in the library.
        """
        src = Path(src)
        tsl = Tsl.load(src)          # validates device
        tsl.name = self.unique_name(tsl.name or src.stem)
        return self.save(tsl)

    def remove(self, name: str) -> None:
        """Delete a stored liveset file (no error if it is missing).

        Args:
            name: liveset name to delete.
        """
        self._path(name).unlink(missing_ok=True)
