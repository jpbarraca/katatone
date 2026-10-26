"""A setlist: the patches of a library liveset, played in order on stage.

A liveset already is an ordered list of patches, so a setlist is just a
liveset that is open for playing: no file of its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .liveset.tsl import Tsl
from .model.paramset import ParamSet


@dataclass
class Setlist:
    """The patches of a liveset and the one that plays now.

    ``index`` is None until the first patch is played.  Empty slots of the
    liveset stay in the list (so the numbers match the library) but are skipped
    when stepping.
    """

    name: str
    names: list[str]
    patches: list[ParamSet | None]
    index: int | None = field(default=None)

    @classmethod
    def from_tsl(cls, tsl: Tsl) -> "Setlist":
        """The setlist of a liveset."""
        return cls(tsl.name,
                   [tsl.patch_name(i) for i in range(len(tsl))],
                   [tsl.to_paramset(i) if tsl.patches[i] else None
                    for i in range(len(tsl))])

    def playable(self, i: int) -> bool:
        """Whether slot ``i`` holds a patch."""
        return 0 <= i < len(self.patches) and self.patches[i] is not None

    def target(self, op: str) -> int | None:
        """The slot a step leads to: ``"next"`` or ``"prev"`` from the current
        one (the first, or the last, before anything played), skipping empty
        slots.  It does not wrap round: stepping past the end finds nothing.

        Args:
            op: ``"next"`` or ``"prev"``.
        Returns:
            The slot, or None at the end (or start) of the list.
        """
        step = 1 if op == "next" else -1
        i = (-1 if step > 0 else len(self.patches)) if self.index is None else self.index
        i += step
        while 0 <= i < len(self.patches):
            if self.playable(i):
                return i
            i += step
        return None

    def patch(self, i: int) -> ParamSet:
        """A copy of the patch in slot ``i``, so editing it leaves the list alone."""
        ps = ParamSet()
        ps.data = dict(self.patches[i].data)
        return ps
