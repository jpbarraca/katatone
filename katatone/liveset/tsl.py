"""TSL liveset file format (Tone Central / librarian .tsl files).

    {
      "name": "...",
      "formatRev": "0000",
      "device": "KATANA Gen3",
      "data": [ [ {"memo": "", "paramSet": {"PATCH%COM": ["4B","41",...], ...}} , ... ] ]
    }

paramSet values are arrays of 2-digit hex strings, one per byte.  A liveset
is a *group* of patches: a liveset holds any number up to
MAX_PATCHES.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..model.paramset import ParamSet

DEVICE = "KATANA Gen3"
FORMAT_REV = "0000"
NUM_PATCHES = 10          # amp user patches (from_paramset slot layout)
MAX_PATCHES = 15          # limit per liveset
NAME_BLOCK = "PATCH%COM"  # 16-byte patch name


class LivesetFull(ValueError):
    """Raised when adding a patch to a liveset that already has MAX_PATCHES."""


@dataclass
class Tsl:
    """A liveset: a named group of patches (any count up to MAX_PATCHES).

    ``patches[i]`` is a paramSet (block id -> hex byte strings) and
    ``memos[i]`` the per-patch memo (a string or a
    PatchInfo object), kept so round-trips do not lose it.
    """

    name: str
    format_rev: str = FORMAT_REV
    device: str = DEVICE
    patches: list[dict[str, list[str]]] = field(default_factory=list)
    memos: list = field(default_factory=list)

    def __post_init__(self) -> None:
        """Trim or pad ``memos`` so it holds one entry per patch."""
        self.memos = list(self.memos[:len(self.patches)])
        self.memos += [""] * (len(self.patches) - len(self.memos))

    # -- serialization -----------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "Tsl":
        """Load a liveset from a ``.tsl`` file.

        Args:
            path: the file to read (string or ``Path``).
        Returns:
            A ``Tsl`` with ``patches`` and ``memos`` in file order.
        Raises:
            ValueError if the file's ``device`` is not ``KATANA Gen3``.
        """
        obj = json.loads(Path(path).read_text())
        if obj.get("device") != DEVICE:
            raise ValueError(f"liveset device mismatch: {obj.get('device')!r} != {DEVICE!r}")
        rows = (obj.get("data") or [[]])[0]
        return cls(name=obj["name"],
                   format_rev=obj.get("formatRev", FORMAT_REV),
                   device=obj["device"],
                   patches=[r.get("paramSet", {}) for r in rows],
                   memos=[r.get("memo", "") for r in rows])

    def to_json(self, indent: int = 1) -> str:
        """Serialize the liveset to the librarian's JSON shape.

        Args:
            indent: JSON indentation width in spaces (1 matches the app).
        Returns:
            The JSON text.
        """
        obj = {
            "name": self.name,
            "formatRev": self.format_rev,
            "device": self.device,
            "data": [[{"memo": m, "paramSet": ps}
                      for ps, m in zip(self.patches, self.memos)]],
        }
        return json.dumps(obj, indent=indent)

    def save(self, path: str | Path) -> None:
        """Write the liveset to ``path`` as JSON.

        Args:
            path: the file to write (string or ``Path``).
        """
        Path(path).write_text(self.to_json())

    # -- ParamSet conversion -------------------------------------------------

    @staticmethod
    def encode(ps: ParamSet) -> dict[str, list[str]]:
        """ParamSet -> paramSet dict (hex byte strings, uppercase)."""
        return {bid: [f"{b:02X}" for b in data] for bid, data in ps.data.items()}

    def to_paramset(self, index: int) -> ParamSet:
        """Convert patch ``index`` of the liveset to a ``ParamSet``.

        Args:
            index: 0-based patch position in the liveset.
        Returns:
            A ``ParamSet`` with each block set from the stored hex bytes.
        """
        ps = ParamSet()
        for bid, hx in self.patches[index].items():
            ps.set_block(bid, bytes(int(x, 16) for x in hx))
        return ps

    @classmethod
    def from_paramset(cls, name: str, index: int, ps: ParamSet) -> "Tsl":
        """A liveset laid out like the amp's user patches: NUM_PATCHES slots,
        with ``ps`` in slot ``index`` (CLI read/write uses slot = patch).

        Args:
            name: the liveset name.
            index: slot (patch number) to place ``ps`` in.
            ps: the patch to place.
        Returns:
            A new liveset with one slot filled.
        """
        tsl = cls(name=name, patches=[{} for _ in range(max(NUM_PATCHES,
                                                             index + 1))])
        tsl.patches[index] = cls.encode(ps)
        return tsl

    # -- patch list editing (librarian) -------------------------------------

    def __len__(self) -> int:
        """Number of patches in the liveset."""
        return len(self.patches)

    def patch_name(self, index: int) -> str:
        """Read the patch name stored in the ``PATCH%COM`` block of patch ``index``.

        Args:
            index: 0-based patch position in the liveset.
        Returns:
            The name decoded and stripped, or ``""`` if the block is missing.
        """
        hx = self.patches[index].get(NAME_BLOCK)
        if not hx:
            return ""
        return ParamSet.patch_name(bytes(int(x, 16) for x in hx))

    def rename_patch(self, index: int, name: str) -> None:
        """Set the ``PATCH%COM`` name of patch ``index``.

        Args:
            index: 0-based patch position in the liveset.
            name: the new name (16 ASCII chars max; longer names are
                truncated, non-ASCII replaced).
        """
        hx = list(self.patches[index].get(NAME_BLOCK) or [])
        raw = ParamSet.encode_patch_name(name)
        hx[:16] = [f"{b:02X}" for b in raw]
        self.patches[index][NAME_BLOCK] = hx

    def add(self, patch: dict[str, list[str]], memo="",
            index: int | None = None) -> int:
        """Insert a patch (default: append); returns its index.

        Args:
            patch: paramSet dict (block id -> list of hex byte strings).
            memo: the per-patch memo (string, or the app's PatchInfo object).
            index: position to insert at (None: append at the end).
        Returns:
            The index of the inserted patch.
        Raises:
            LivesetFull if the liveset already has MAX_PATCHES patches.
        """
        if len(self.patches) >= MAX_PATCHES:
            raise LivesetFull(f"liveset {self.name!r} already has "
                              f"{MAX_PATCHES} patches")
        index = len(self.patches) if index is None else index
        self.patches.insert(index, {k: list(v) for k, v in patch.items()})
        self.memos.insert(index, copy.deepcopy(memo))
        return index

    def duplicate(self, index: int) -> int:
        """Copy patch ``index`` and insert the copy right after it.

        Args:
            index: 0-based position of the patch to copy.
        Returns:
            The index of the copy.
        """
        return self.add(self.patches[index], self.memos[index], index + 1)

    def move(self, old: int, new: int) -> None:
        """Move a patch from position ``old`` to ``new``.

        Args:
            old: 0-based current position of the patch.
            new: 0-based position to move it to.
        """
        self.patches.insert(new, self.patches.pop(old))
        self.memos.insert(new, self.memos.pop(old))

    def remove(self, index: int) -> None:
        """Delete patch ``index`` from the liveset.

        Args:
            index: 0-based patch position.
        """
        del self.patches[index]
        del self.memos[index]
