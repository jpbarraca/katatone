"""Local patch library: livesets (groups of patches) stored as .tsl files in
~/.local/share/katatone/livesets/."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import rofs
from ..liveset.tsl import Tsl
from ..paths import data_root

MAX_LIVESETS = 50          # library limit
NAME_MAX = 64              # liveset name length
META_FILE = "library.json"  # tags and favourites, next to the .tsl files
MAX_TAGS = 10              # per liveset
TAG_MAX = 24               # tag length


class LibraryFull(ValueError):
    """Raised when creating a liveset beyond MAX_LIVESETS."""


@dataclass
class LibraryEntry:
    name: str
    path: Path
    mtime: float
    count: int | None = None      # patches in the liveset (None: unreadable)
    patches: list[str] = field(default_factory=list)    # their names
    tags: list[str] = field(default_factory=list)
    favourite: bool = False


def clean_name(name: str) -> str:
    """A liveset name usable as a file name: ASCII, no path separators."""
    name = name.encode("ascii", "replace").decode("ascii")
    name = name.replace("/", "-").replace("\\", "-").strip(" .")
    return name[:NAME_MAX] or "Liveset"


def clean_tags(tags: "str | list[str]") -> list[str]:
    """Tags from a list or comma-separated text: trimmed, without duplicates
    (ignoring case), at most MAX_TAGS of at most TAG_MAX characters."""
    if isinstance(tags, str):
        tags = tags.split(",")
    out: list[str] = []
    for t in tags:
        t = " ".join(str(t).split())[:TAG_MAX]
        if t and t.lower() not in (o.lower() for o in out):
            out.append(t)
    return out[:MAX_TAGS]


def filter_entries(entries: list[LibraryEntry], query: str = "", tag: str | None = None,
                   favourites_only: bool = False) -> list[LibraryEntry]:
    """The livesets to show: favourites first, then by name.

    Args:
        entries: ``Library.list()``.
        query: words that must all appear (ignoring case) in the liveset's
            name, its tags or the name of one of its patches.
        tag: only livesets with this tag (ignoring case).
        favourites_only: only favourites.
    """
    words = query.lower().split()
    out = []
    for e in entries:
        if favourites_only and not e.favourite:
            continue
        if tag and tag.lower() not in (t.lower() for t in e.tags):
            continue
        haystack = " ".join([e.name, *e.tags, *e.patches]).lower()
        if all(w in haystack for w in words):
            out.append(e)
    return sorted(out, key=lambda e: not e.favourite)      # stable: name order is kept


def all_tags(entries: list[LibraryEntry]) -> list[str]:
    """Every tag in use, sorted ignoring case."""
    seen: dict[str, str] = {}
    for e in entries:
        for t in e.tags:
            seen.setdefault(t.lower(), t)
    return sorted(seen.values(), key=str.lower)


class Library:
    def __init__(self, root: str | Path | None = None) -> None:
        """Open (or create) the liveset directory.

        Args:
            root: where ``.tsl`` files live (defaults to ``<data dir>/livesets``).
        """
        self.root = Path(root) if root else data_root() / "livesets"
        with rofs.writable(self.root):
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
        meta = self._read_meta()
        for p in sorted(self.root.glob("*.tsl"), key=lambda p: p.stem.lower()):
            try:
                tsl = Tsl.load(p)
                count, names = len(tsl), [tsl.patch_name(i) for i in range(len(tsl))]
            except Exception:                        # noqa: BLE001
                count, names = None, []
            m = meta.get(p.stem, {})
            out.append(LibraryEntry(name=p.stem, path=p, mtime=p.stat().st_mtime,
                                    count=count, patches=names,
                                    tags=clean_tags(m.get("tags", [])),
                                    favourite=bool(m.get("favourite"))))
        return out

    # -- tags and favourites -------------------------------------------------

    def _read_meta(self) -> dict:
        """The tags and favourites file: liveset name -> {tags, favourite}
        (empty when it is missing or damaged)."""
        try:
            meta = json.loads((self.root / META_FILE).read_text())
        except (OSError, ValueError):
            return {}
        return meta if isinstance(meta, dict) else {}

    def _write_meta(self, meta: dict) -> None:
        """Store the tags and favourites; a liveset with neither is left out."""
        meta = {k: v for k, v in meta.items() if v.get("tags") or v.get("favourite")}
        with rofs.writable(self.root):
            (self.root / META_FILE).write_text(json.dumps(meta, indent=1, sort_keys=True))

    def _update_meta(self, name: str, **fields) -> None:
        """Change the tags or favourite flag of one liveset."""
        meta = self._read_meta()
        meta[name] = {**meta.get(name, {}), **fields}
        self._write_meta(meta)

    def set_favourite(self, name: str, on: bool) -> None:
        """Mark a liveset as a favourite, or not."""
        self._update_meta(name, favourite=bool(on))

    def set_tags(self, name: str, tags: "str | list[str]") -> list[str]:
        """Replace a liveset's tags.

        Args:
            name: the liveset.
            tags: a list, or comma-separated text (see :func:`clean_tags`).
        Returns:
            The tags as stored.
        """
        tags = clean_tags(tags)
        self._update_meta(name, tags=tags)
        return tags

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
        with rofs.writable(self.root):
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
        with rofs.writable(self.root):
            self.save(tsl)
            self._path(old).unlink(missing_ok=True)
        meta = self._read_meta()
        if old in meta:
            meta[new] = meta.pop(old)
            self._write_meta(meta)
        return new

    def duplicate(self, name: str) -> Tsl:
        """Store a copy of a liveset under ``<name> copy``.

        Args:
            name: the liveset to copy.
        Returns:
            The new liveset.
        """
        tsl = self.create(f"{name} copy", self.load(name))
        tags = self._read_meta().get(name, {}).get("tags")
        if tags:
            self.set_tags(tsl.name, tags)
        return tsl

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
        with rofs.writable(self.root):
            self._path(name).unlink(missing_ok=True)
        meta = self._read_meta()
        if meta.pop(name, None) is not None:
            self._write_meta(meta)
