"""Tests for the address map loader and chunking helpers."""
from __future__ import annotations

import pytest

from katatone.address.map import load_map, chunk_sizes, Region, Block


def test_chunk_sizes() -> None:
    """chunk_sizes splits a length into 128-byte chunks plus a remainder; empty and short lengths are trivial."""
    assert chunk_sizes(0) == []
    assert chunk_sizes(120) == [120]
    assert chunk_sizes(128) == [128]
    assert chunk_sizes(256) == [128, 128]
    assert chunk_sizes(300) == [128, 128, 44]


def test_map_has_expected_regions() -> None:
    """load_map yields exactly the expected regions: SETUP, SYSTEM, PATCH, the 10 user patches, TEMP and COMMAND."""
    mp = load_map()
    assert set(mp) == {
        "SETUP", "SYSTEM", "PATCH",
        "PATCH(1)", "PATCH(2)", "PATCH(3)", "PATCH(4)", "PATCH(5)",
        "PATCH(6)", "PATCH(7)", "PATCH(8)", "PATCH(9)", "PATCH(10)",
        "TEMP", "COMMAND",
    }


def test_region_bases() -> None:
    """Each region sits at its expected pre-nibble base address."""
    mp = load_map()
    assert mp["SETUP"].base == 0x0000_0000
    assert mp["SYSTEM"].base == 0x1000_0000
    assert mp["PATCH"].base == 0x2000_0000
    assert mp["TEMP"].base == 0x3000_0000
    assert mp["COMMAND"].base == 0x7F00_0000


def test_block_base_addr_and_lookup() -> None:
    """A block's base_addr is the region base plus its start, and unknown block names raise KeyError."""
    mp = load_map()
    region: Region = mp["PATCH(1)"]
    block: Block = region.block("AMP")
    assert block.base_addr == region.base + block.start
    with pytest.raises(KeyError):
        region.block("NO_SUCH_BLOCK")


def test_region_lengths_pre_nibble() -> None:
    """Region lengths are reported pre-nibble (in address space), not in nibble-packed bytes."""
    # region.length is the pre-nibble (address-space) length.
    mp = load_map()
    assert mp["SETUP"].length == 2
    assert mp["PATCH"].length == 73217
    assert mp["SYSTEM"].length == 10249


def test_patch_blocks_exist() -> None:
    """The key blocks of a user patch all exist and are non-empty."""
    mp = load_map()
    region = mp["PATCH(1)"]
    for name in ("COM", "AMP", "FX(1)", "DELAY(1)", "REVERB(1)", "EQ_GE10(1)"):
        assert region.block(name).length > 0
