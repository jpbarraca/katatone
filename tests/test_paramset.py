"""Tests for ParamSet display<->storage conversion."""
from __future__ import annotations

import pytest

from katatone.address.map import load_map
from katatone.model.paramset import ParamSet


@pytest.fixture
def amp_region():
    """The PATCH(1) region from the address map."""
    mp = load_map()
    return mp["PATCH(1)"]


@pytest.fixture
def amp_block(amp_region):
    """The AMP block of PATCH(1), asserted to carry parameters."""
    block = amp_region.block("AMP")
    assert block.params, "AMP block should carry parameters"
    return block


def test_encode_decode_roundtrip(amp_block) -> None:
    """Encoding a value into a raw buffer and decoding it back returns the same value."""
    raw = bytearray(b"\x00" * amp_block.length)
    ParamSet.encode(amp_block, raw, 0, 42)
    assert ParamSet.decode(amp_block, bytes(raw), 0) == 42


def test_encode_respects_ofs_bias(amp_block) -> None:
    """Encoding the min and max of a biased param decodes back to exactly those bounds."""
    p = amp_block.params[0]
    raw = bytearray(b"\x00" * amp_block.length)
    ParamSet.encode(amp_block, raw, 0, p.min)
    assert ParamSet.decode(amp_block, bytes(raw), 0) == p.min
    ParamSet.encode(amp_block, raw, 0, p.max)
    assert ParamSet.decode(amp_block, bytes(raw), 0) == p.max


def test_block_set_get_param(amp_region) -> None:
    """set_param mutates the canonical 'PATCH%<BLOCK>' block that the write path reads, never a per-patch key."""
    ps = ParamSet()
    name = "AMP"
    key = ParamSet.block_key(amp_region.name, name)
    assert key == "PATCH%AMP"  # canonical key, not 'PATCH(1)%AMP'
    ps.set_block(key, b"\x00" * amp_region.block(name).length)
    val = ps.set_param(amp_region, name, 0, 17)
    assert ps.get_param(amp_region, name, 0) == 17
    assert val == ps.block(key)
    # set_param must mutate the canonical block the write path reads
    assert ps.block("PATCH%AMP") == val
    assert "PATCH(1)%AMP" not in ps.data


def test_patch_name_roundtrip() -> None:
    """A patch name encodes and decodes back to the same string."""
    assert ParamSet.patch_name(ParamSet.encode_patch_name("Drive Lead")) == "Drive Lead"


def test_patch_name_truncates_and_pads() -> None:
    """encode_patch_name truncates to the 16-byte COM block, and patch_name reads the trimmed name back."""
    raw = ParamSet.encode_patch_name("A" * 30)
    assert len(raw) == 16
    assert ParamSet.patch_name(raw) == "AAAAAAAAAAAAAAAA"


def test_block_missing_returns_empty() -> None:
    """Reading a block that was never set returns empty bytes."""
    ps = ParamSet()
    assert ps.block("PATCH%NOPE") == b""


def test_multibyte_params_are_nibbles_msb_first() -> None:
    """INTEGER4x4 (e.g. delay time 0-2000 ms): four 4-bit nibbles, most
    significant first."""
    from katatone.address.map import load_map
    blk = load_map()["PATCH"].block("DELAY(1)")
    i = next(i for i, p in enumerate(blk.params) if p.size == 4)
    p = blk.params[i]
    raw = bytearray(blk.length)
    ParamSet.encode(blk, raw, i, 1234)                  # 0x04D2
    assert bytes(raw[p.off:p.off + 4]) == bytes([0x0, 0x4, 0xD, 0x2])
    assert all(b <= 0x7F for b in raw)
    assert ParamSet.decode(blk, bytes(raw), i) == 1234


def test_block_addresses_are_absolute_when_iterating() -> None:
    """Block base addresses are absolute (region base plus start), even when iterating SYSTEM blocks."""
    from katatone.address.map import load_map
    r = load_map()["SYSTEM"]
    assert all(b.base_addr >= r.base for b in r.blocks)
