"""Compare two patches: which parameters have different values."""
from __future__ import annotations

from dataclasses import dataclass

from ..address.map import load_map
from .paramset import ParamSet


@dataclass(frozen=True)
class Difference:
    """One parameter that differs; ``a`` and ``b`` are its display values (the
    patch name, for ``is_name``, as text)."""
    block: str
    p_index: int
    name: str           # the parameter's name in the address map
    a: int | str
    b: int | str

    @property
    def is_name(self) -> bool:
        """Whether this is the patch name, which is text and not a parameter."""
        return isinstance(self.a, str)


def compare(a: ParamSet, b: ParamSet) -> list[Difference]:
    """The parameters whose value differs between two user patches.

    A block that neither patch holds is skipped; one that only a patch holds
    counts as zeros in the other, as the rest of the app reads a missing block.

    Args:
        a: the first patch (``PATCH%<BLOCK>`` blocks).
        b: the second patch.
    Returns:
        The differences, in the order of the amp's address map.
    """
    region = load_map()["PATCH"]
    out = []
    for block in region.blocks:
        bid = ParamSet.block_key(region.name, block.name)
        if not a.block(bid) and not b.block(bid):
            continue
        for i, p in enumerate(block.params):
            if p.size == 16:                                  # the name
                va = ParamSet.patch_name(a.block(bid))
                vb = ParamSet.patch_name(b.block(bid))
            else:
                va, vb = (ps.get_param(region, block.name, i) for ps in (a, b))
            if va != vb:
                out.append(Difference(block.name, i, p.name, va, vb))
    return out
