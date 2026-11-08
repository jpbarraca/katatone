"""Address map loader (assets/address_map.json)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..protocol.rc6 import SYSEX_MAXLEN

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "address_map.json"


@dataclass(frozen=True)
class Param:
    name: str
    off: int          # offset within block (pre-nibble)
    size: int         # bytes
    ofs: int          # storage bias
    init: int | str
    min: int
    max: int

    @property
    def index(self) -> int:
        """Where the parameter's bytes are in the block's data.

        ``off`` is an address offset: a SysEx address byte holds 7 bits, so after 128 bytes the
        address carries into the next byte and the offset jumps to the next multiple of 256.
        The data has no such gaps (the 353 of an FX_DETAIL block are 225 bytes), so a parameter
        at ``0x127`` is the byte at index ``128 + 0x27``.  Blocks shorter than 128 bytes are
        unchanged.
        """
        return (self.off >> 8) * 128 + (self.off & 0x7F)


@dataclass
class Block:
    name: str            # e.g. 'AMP', 'FX(1)'
    start: int           # offset within region (pre-nibble)
    length: int
    params: list[Param] = field(default_factory=list)

    @property
    def base_addr(self) -> int:
        """Absolute pre-nibble address of this block: region base + block start."""
        return self._base + self.start

    _base: int = 0


@dataclass
class Region:
    name: str            # SETUP / SYSTEM / PATCH / PATCH(1..10) / TEMP / COMMAND
    base: int
    blocks: list[Block]
    length: int

    def block(self, name: str) -> Block:
        """Look up a block by name and stamp it with this region's base.

        Args:
            name: the block name to find (e.g. `AMP`).

        Returns:
            The matching `Block`, with its `_base` set to the region base.

        Raises:
            KeyError if no block of that name exists in the region.
        """
        for b in self.blocks:
            if b.name == name:
                b._base = self.base
                return b
        raise KeyError(f"block {name!r} not in region {self.name}")


@lru_cache(maxsize=1)
def load_map(path: Path | None = None) -> dict[str, Region]:
    """Load the address map JSON and build the region/block/param tree.

    Args:
        path: map file to load (defaults to `assets/address_map.json`).

    Returns:
        `{name: Region}` for every region, with each block's `_base` set.
    """
    raw = json.loads((path or ASSETS).read_text())
    out: dict[str, Region] = {}
    for reg in raw:
        blocks = []
        for b in reg["blocks"]:
            if "params" not in b:
                continue  # region-level leaf (rare)
            blocks.append(Block(
                name=b["name"],
                start=b["start"],
                length=b["length"],
                params=[Param(name=p["name"], off=p["addr"], size=p["size"],
                              ofs=p["ofs"], init=p["init"], min=p["min"],
                              max=p["max"]) for p in b["params"]],
            ))
        for blk in blocks:      # absolute base_addr even when iterating
            blk._base = reg["base"]
        out[reg["name"]] = Region(name=reg["name"], base=reg["base"],
                                  blocks=blocks, length=reg["length"])
    return out


def chunk_sizes(length: int) -> list[int]:
    """Split a block read into <= SYSEX_MAXLEN-byte chunks (see JS dividesize).

    Args:
        length: the block length in bytes.

    Returns:
        The chunk sizes in read order: full chunks first, a shorter tail if any.
    """
    return [SYSEX_MAXLEN] * (length // SYSEX_MAXLEN) + (
        [length % SYSEX_MAXLEN] if length % SYSEX_MAXLEN else [])
