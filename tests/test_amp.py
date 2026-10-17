"""Tests for read_patch / write_patch round-trips against a fake amp."""
from __future__ import annotations

import pytest

from conftest import FakeAmp, make_transport, patch_memory
from katatone.address.map import load_map
from katatone.amp.read import read_patch, PATCH_BLOCKS
from katatone.amp.write import write_patch, select_patch, clear_patch
from katatone.amp.connect import handshake
from katatone.model.paramset import ParamSet
from katatone.protocol import rc6
from katatone.protocol.rc6 import (CMD_PATCH_SELECT, CMD_PATCH_WRITE, CMD_PATCH_CLEAR,
                              CMD_EXPORT)


@pytest.fixture
def region():
    """The PATCH(1) region from the address map, used as the write target."""
    return load_map()["PATCH(1)"]


def _block_bytes(region) -> dict[str, bytes]:
    """Build deterministic per-block bytes for every block of a patch region.

    Each block gets a small ramp offset by its index in PATCH_BLOCKS so the
    blocks are mutually distinguishable; the COM block carries a fixed 16-byte
    patch name.

    Args:
        region: the patch region whose block lengths seed the payloads.

    Returns:
        dict keyed by canonical block name ("PATCH%<BLOCK>") to raw bytes.
    """
    out = {}
    for idx, name in enumerate(PATCH_BLOCKS):
        length = region.block(name).length
        out[f"PATCH%{name}"] = bytes([(i + idx) & 0xFF for i in range(length)])
    # COM carries the patch name (first 16 bytes are the 16-char name).  The COM
    # block is 16 bytes, so seed exactly that length.
    out["PATCH%COM"] = b"TESTPATCH\x00" + b"\x00" * 6
    return out


def test_read_patch_roundtrip(transport, region) -> None:
    """A full read_patch of a seeded flat amp returns every block exactly, with FX_DETAIL blocks clamped at the read wall."""
    bb = _block_bytes(region)
    flat = patch_memory(0, bb)
    from conftest import _patch_memory_base
    amp = FakeAmp(memory=flat)
    amp.set_flat_base(_patch_memory_base[0])
    t = make_transport(amp)
    try:
        ps = read_patch(t, 0)
        base = _patch_memory_base[0]
        for name in PATCH_BLOCKS:
            block = region.block(name)
            nib = rc6.to_nib(block.base_addr)
            off = nib - base
            # FX_DETAIL blocks are wall-limited: the amp answers a single RQ1
            # with at most DETAIL_READ_WALL bytes, nothing past the wall.
            expected = flat[off:off + min(block.length, FakeAmp.DETAIL_READ_WALL)]
            assert ps.block(f"PATCH%{name}") == expected, f"mismatch in {name}"
    finally:
        t.stop()


def test_read_patch_invalid_number(transport) -> None:
    """read_patch raises ValueError for a patch number outside the 10 user patches."""
    with pytest.raises(ValueError):
        read_patch(t=transport, patch_number=10)


def test_write_patch_sends_expected_sequence(transport, amp: FakeAmp, region) -> None:
    """write_patch sends the patch name, the block data, the commit and the export to the right nibble addresses."""
    ps = ParamSet()
    for name in PATCH_BLOCKS:
        ps.set_block(f"PATCH%{name}", b"\x00" * region.block(name).length)
    ps.set_block("PATCH%COM", b"WriteTest\x00" + b"\x00" * 7)

    write_patch(transport, 0, ps, name="WriteTest")

    writes = dict(amp.writes)
    # 1. patch name -> start of region (PATCH%COM), keyed by nibble base
    assert writes[rc6.to_nib(region.base + 0x0)] == ParamSet.encode_patch_name("WriteTest")
    # 3. commit + 4. export
    assert writes[rc6.to_nib(CMD_PATCH_WRITE)] == b"\x00\x00"
    assert writes[rc6.to_nib(CMD_EXPORT)] == b"\x00\x00"
    # 2. at least one block write (AMP)
    assert rc6.to_nib(region.block("AMP").base_addr) in writes


def test_write_patch_falls_back_to_com_name(transport, amp: FakeAmp, region) -> None:
    """write_patch without an explicit name takes the patch name from the COM block."""
    ps = ParamSet()
    ps.set_block("PATCH%COM", b"AutoName\x00" + b"\x00" * 8)
    write_patch(transport, 0, ps)   # no explicit name; region fixture is PATCH(1)
    writes = dict(amp.writes)
    assert writes[rc6.to_nib(region.base + 0x0)] == ParamSet.encode_patch_name("AutoName")
    assert writes[rc6.to_nib(CMD_PATCH_WRITE)] == b"\x00\x00"


def test_select_and_clear(transport, amp: FakeAmp) -> None:
    """select_patch and clear_patch write the patch number as INTEGER2x7 (most significant first) to their command addresses."""
    select_patch(transport, 3)
    clear_patch(transport, 3)
    writes = dict(amp.writes)
    # INTEGER2x7, most significant first: patch 3 -> 00 03 (not 03 00,
    # which the amp reads as 3 * 128).
    assert writes[rc6.to_nib(CMD_PATCH_SELECT)] == b"\x00\x03"
    assert writes[rc6.to_nib(CMD_PATCH_CLEAR)] == b"\x00\x03"


def test_write_param_targets_edit_buffer_only(transport, amp: FakeAmp) -> None:
    """write_param lands a single param in the edit buffer at its parameter offset, encoding INTEGER4x4 as MSB-first nibbles, never touching a user patch slot."""
    from katatone.address.map import load_map
    from katatone.amp.write import write_param
    edit = load_map()["PATCH"]
    amp_blk = edit.block("AMP")
    write_param(transport, "AMP", 5, 51)                   # PRESENCE
    assert (rc6.to_nib(amp_blk.base_addr + 5), b"\x33") in amp.writes
    # INTEGER4x4 value: nibbles, most significant first
    dly = edit.block("DELAY(1)")
    i = next(i for i, p in enumerate(dly.params)
             if p.name.endswith("DELAY_TIME"))
    write_param(transport, "DELAY(1)", i, 310)
    assert (rc6.to_nib(dly.base_addr + dly.params[i].off),
            bytes([0, 1, 3, 6])) in amp.writes
    # never a user patch slot, never a commit command
    assert all(nib < rc6.to_nib(0x20100000) for nib, _ in amp.writes)


def test_write_param_rejects_out_of_range(transport, amp: FakeAmp) -> None:
    """write_param raises ValueError for a value outside the param's range and sends nothing."""
    from katatone.amp.write import write_param
    with pytest.raises(ValueError):
        write_param(transport, "AMP", 5, 101)
    assert not amp.writes
