"""End-to-end integration test: full connect → read → modify → write → read-back
cycle against a FakeAmp.  Exercises every layer: transport, protocol, address
map, paramset, amp read/write, liveset, and SYSTEM region.

This is the single most valuable test — it validates the complete data path
that will be exercised against real hardware.
"""
from __future__ import annotations

import pytest

from katatone.address.map import load_map
from katatone.amp.connect import handshake
from katatone.amp.read import read_patch, read_region
from katatone.amp.write import write_patch
from katatone.liveset.tsl import Tsl
from katatone.model.paramset import ParamSet
from katatone.protocol import rc6

from conftest import FakeAmp, make_transport


def _seeded_amp() -> FakeAmp:
    """Build a FakeAmp with every region's blocks seeded to zeros,
    plus a patch name and editor level for the handshake."""
    mp = load_map()
    amp = FakeAmp()
    for region in mp.values():
        for b in region.blocks:
            amp.mem[rc6.to_nib(b.base_addr)] = bytes(b.length)
    # Patch 1 name
    com = mp["PATCH(1)"].block("COM")
    amp.mem[rc6.to_nib(com.base_addr)] = ParamSet.encode_patch_name("Test Patch")
    # Editor communication level (required by handshake)
    amp.mem[rc6.to_nib(rc6.CMD_EDITOR_LEVEL)] = b"\x01"
    return amp


def test_full_cycle() -> None:
    """connect → read → modify → write → read-back → TSL → SYSTEM."""
    mp = load_map()
    amp = _seeded_amp()
    t = make_transport(amp)
    try:
        # 1. Handshake
        dev_id = handshake(t)
        assert dev_id == 0x10

        # 2. Read patch 1 (user patch index 0)
        ps = read_patch(t, 0)
        name = ParamSet.patch_name(ps.block("PATCH%COM"))
        assert name == "Test Patch"
        assert len(ps.blocks()) == 80

        # 3. Modify AMP GAIN = 50
        region1 = mp["PATCH(1)"]
        amp_block = region1.block("AMP")
        raw = bytearray(ps.block("PATCH%AMP"))
        ParamSet.encode(amp_block, raw, 0, 50)
        ps.set_block("PATCH%AMP", bytes(raw))
        assert ParamSet.decode(amp_block, ps.block("PATCH%AMP"), 0) == 50

        # 4. Write patch back
        write_patch(t, 0, ps)
        nib_write = rc6.to_nib(rc6.CMD_PATCH_WRITE)
        assert nib_write in [w[0] for w in amp.writes]

        # 5. Read-back verify
        ps2 = read_patch(t, 0)
        val = ParamSet.decode(amp_block, ps2.block("PATCH%AMP"), 0)
        assert val == 50

        # 6. TSL structure
        tsl = Tsl.from_paramset("Test Patch", 0, ps2)
        assert tsl.device == "KATANA Gen3"
        assert len(tsl.patches) == 10

        # 7. SYSTEM region read
        sys_ps = read_region(t, "SYSTEM")
        assert len(sys_ps.blocks()) > 0
    finally:
        t.stop()


def test_all_patches_readable() -> None:
    """All 10 user patches can be read without error."""
    amp = _seeded_amp()
    t = make_transport(amp)
    try:
        for i in range(10):
            ps = read_patch(t, i)
            assert len(ps.blocks()) == 80
    finally:
        t.stop()


def test_multi_patch_write_readback() -> None:
    """Write distinct values to 3 different patches, read back all."""
    mp = load_map()
    amp = _seeded_amp()
    t = make_transport(amp)
    try:
        handshake(t)
        targets = [(0, 10), (3, 60), (7, 90)]  # (patch_idx, gain_value)
        for idx, gain in targets:
            region = mp[f"PATCH({idx + 1})"]
            ps = read_patch(t, idx)
            amp_block = region.block("AMP")
            raw = bytearray(ps.block("PATCH%AMP"))
            ParamSet.encode(amp_block, raw, 0, gain)
            ps.set_block("PATCH%AMP", bytes(raw))
            write_patch(t, idx, ps)

        # Read back all three
        for idx, gain in targets:
            region = mp[f"PATCH({idx + 1})"]
            ps = read_patch(t, idx)
            amp_block = region.block("AMP")
            val = ParamSet.decode(amp_block, ps.block("PATCH%AMP"), 0)
            assert val == gain, f"patch {idx}: expected {gain}, got {val}"
    finally:
        t.stop()
