"""What the amp volunteers: a parameter of its edit buffer that changed (the amp's own knobs,
a pedal assigned to an effect's setting).

The amp announces such a change with a DT1 at the parameter's address, outside any request of
ours (the transport hands it to ``on_unsolicited``).  :func:`decode` names the parameter and
gives its display value, so that the editor can follow the amp.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from ..address.map import Param, load_map
from ..model.paramset import ParamSet
from ..protocol import rc6
from .write import EDIT_REGION


@dataclass(frozen=True)
class Change:
    """One parameter of the edit buffer and the value the amp says it has now."""
    block: str
    p_index: int
    param: Param
    value: int

    @property
    def name(self) -> str:
        """``BLOCK.PARAM`` without the ``PRMID_PATCH_`` prefix, for logs."""
        return f"{self.block}.{self.param.name.removeprefix('PRMID_PATCH_')}"


@lru_cache(maxsize=1)
def _index() -> dict[int, tuple[str, int, Param]]:
    """The nibble address of every numeric parameter of the edit buffer -> where it is."""
    out: dict[int, tuple[str, int, Param]] = {}
    for block in load_map()[EDIT_REGION].blocks:
        for i, p in enumerate(block.params):
            if p.size in (1, 2, 4):
                out[rc6.to_nib(block.base_addr + p.off)] = (block.name, i, p)
    return out


def decode(nib: int, payload: bytes) -> Change | None:
    """The parameter a DT1 announces.

    Args:
        nib: the nibble address of the DT1.
        payload: its data bytes.
    Returns:
        The change, or None when the message is not exactly one numeric parameter of the edit
        buffer (the patch number, a block, something else).
    """
    found = _index().get(nib)
    if found is None:
        return None
    block, i, p = found
    if len(payload) != p.size:
        return None
    return Change(block, i, p, ParamSet.raw_value(p.size, bytes(payload)) - p.ofs)
