"""Read operations: region / block / full-patch reads (chunked RQ1)."""
from __future__ import annotations

import logging

from ..address.map import Block, Region, load_map
from ..model.paramset import ParamSet
from ..protocol.transport import Transport

log = logging.getLogger("katatone.read")

# The 80 blocks that make up one patch
PATCH_BLOCKS = [
    "COM", "OTHER", "COLOR", "AMP", "SW",
    "BOOSTER(1)", "BOOSTER(2)", "BOOSTER(3)",
    "FX(1)", "FX(2)", "FX(3)", "FX(4)", "FX(5)", "FX(6)",
    "FX_DETAIL(1)", "FX_DETAIL(2)", "FX_DETAIL(3)",
    "FX_DETAIL(4)", "FX_DETAIL(5)", "FX_DETAIL(6)",
    "DELAY(1)", "DELAY(2)", "DELAY(3)", "DELAY(4)", "DELAY(5)", "DELAY(6)",
    "REVERB(1)", "REVERB(2)", "REVERB(3)",
    "SOLO_COM", "SOLO_EQ", "SOLO_DELAY",
    "CONTOUR_COM", "CONTOUR(1)", "CONTOUR(2)", "CONTOUR(3)",
    "PEDALFX_COM", "PEDALFX",
    "EQ_EACH(1)", "EQ_EACH(2)", "EQ_PEQ(1)", "EQ_PEQ(2)",
    "EQ_GE10(1)", "EQ_GE10(2)",
    "NS", "SENDRETURN",
    "ASSIGN_KNOBS", "ASSIGN_EXPPDL_FUNC", "ASSIGN_EXPPDL_DETAIL",
    "ASSIGN_EXPPDL_MIN", "ASSIGN_EXPPDL_MAX", "ASSIGN_FS",
    "ASSIGN_GAFCFS(1)", "ASSIGN_GAFCFS(2)",
    "ASSIGN_GAFCEXPPDL1_FUNC(1)", "ASSIGN_GAFCEXPPDL1_FUNC(2)",
    "ASSIGN_GAFCEXPPDL1_DETAIL(1)", "ASSIGN_GAFCEXPPDL1_DETAIL(2)",
    "ASSIGN_GAFCEXPPDL1_MIN(1)", "ASSIGN_GAFCEXPPDL1_MIN(2)",
    "ASSIGN_GAFCEXPPDL1_MAX(1)", "ASSIGN_GAFCEXPPDL1_MAX(2)",
    "ASSIGN_GAFCEXPPDL2_FUNC(1)", "ASSIGN_GAFCEXPPDL2_FUNC(2)",
    "ASSIGN_GAFCEXPPDL2_DETAIL(1)", "ASSIGN_GAFCEXPPDL2_DETAIL(2)",
    "ASSIGN_GAFCEXPPDL2_MIN(1)", "ASSIGN_GAFCEXPPDL2_MIN(2)",
    "ASSIGN_GAFCEXPPDL2_MAX(1)", "ASSIGN_GAFCEXPPDL2_MAX(2)",
    "ASSIGN_GAFCEXPPDL3_FUNC(1)", "ASSIGN_GAFCEXPPDL3_FUNC(2)",
    "ASSIGN_GAFCEXPPDL3_DETAIL(1)", "ASSIGN_GAFCEXPPDL3_DETAIL(2)",
    "ASSIGN_GAFCEXPPDL3_MIN(1)", "ASSIGN_GAFCEXPPDL3_MIN(2)",
    "ASSIGN_GAFCEXPPDL3_MAX(1)", "ASSIGN_GAFCEXPPDL3_MAX(2)",
    "PATCH_KNOB_READONLY", "PATCH_KNOB_SOLO_DELAY_READONLY",
]


# Each RQ1/DT1 *data* transfer is capped at SYSEX_MAXLEN (128);
# larger blocks are read as multiple sequential RQ1 sessions.  rq1_nib performs
# that internal chunking and advances each chunk by the amp's actual reply
# size (transport.rq1_nib).
from ..protocol.rc6 import SYSEX_MAXLEN, to_nib


RQ1_SESSION_CAP = SYSEX_MAXLEN


# FX_DETAIL blocks are special: the KATANA amp hard-caps each one at 225 bytes
# (the block is declared 353) and then answers *nothing* for the following gap.
# They are read with stop_on_empty so the read returns the reachable 225 bytes
# and moves on, rather than timing out (a single rq1(addr, declared_size) per
# FX_DETAIL block).
FX_DETAIL_PREFIX = "FX_DETAIL"


def _is_fx_detail_block(name: str) -> bool:
    """FX_DETAIL blocks are hard-capped by the amp (see transport.rq1_nib)."""
    return name.startswith(FX_DETAIL_PREFIX)


def _read_block_chunked(t: Transport, addr: int, length: int,
                        follow_amp: bool = False) -> bytes:
    """Read `length` bytes at pre-nibble `addr`.

    For ordinary blocks a single ``rq1_nib`` call performs the internal
    RQ1_CHUNK (128) chunking and advances each chunk by the amp's *actual*
    reply size (`addr += dt1_data.length / 2`).

    For FX_DETAIL blocks (``follow_amp=True``) the amp hard-caps each block: a
    single RQ1 with the *declared* size returns exactly the reachable bytes in
    one transaction, and answers *nothing* for the following gap.  Reading
    is therefore one ``rq1(addr, declared_size)`` per FX_DETAIL block, then an
    advance by the reply length; a second RQ1 into the dead zone past the
    wall is never issued.  So we send
    a single ``rq1(addr, length)`` and take whatever the amp returns, rather
    than looping toward the (unreachable) declared length.

    Args:
        t: the connected transport.
        addr: the block's base address (pre-nibble).
        length: the number of bytes to read.
        follow_amp: True for FX_DETAIL blocks — a single RQ1 with the
            declared size, reply taken as-is.
    Returns:
        The bytes the amp actually returned (may be shorter than `length`).
    """
    if follow_amp:
        # One RQ1 with the declared size -> amp returns min(length, WALL) bytes
        # in a SINGLE transaction (one rq1(addr,
        # declared_size), advance by reply length).  single_transaction=True
        # issues that one request and takes the reply as-is — no chunk loop,
        # so we never re-request the dead zone past the amp's 225-byte wall.
        return t.rq1(addr, length, single_transaction=True)
    return t.rq1_nib(to_nib(addr), length)


def read_block(t: Transport, region: Region, block: Block,
               progress=None) -> bytes:
    """Read one block.  Blocks > RQ1_SESSION_CAP bytes are read in multiple
    t.rq1() calls with a pause between them (each RQ1/DT1
    data transfer is capped at SYSEX_MAXLEN = 128).

    Args:
        t: the connected transport.
        region: the address-map region the block belongs to.
        block: the Block to read.
        progress: optional callback called once with (bytes_read, expected).
    Returns:
        The raw bytes of the block.
    """
    data = _read_block_chunked(
        t, block.base_addr, block.length,
        follow_amp=_is_fx_detail_block(block.name))
    if progress:
        progress(len(data), block.length)
    return data


def read_region(t: Transport, region_name: str, progress=None) -> ParamSet:
    """Read every block of a region into a ParamSet.

    Args:
        t: the connected transport.
        region_name: name of the region in the address map.
        progress: optional callback called per block with (block, total).
    Returns:
        A ParamSet with the region's blocks under their canonical `block_key`s.
    """
    log.debug("reading region %s", region_name)
    mp = load_map()
    region = mp[region_name]
    ps = ParamSet()
    total = len(region.blocks)
    for i, block in enumerate(region.blocks):
        data = read_block(t, region, block,
                          progress=(lambda *_, i=i: progress(i + 1, total))
                          if progress else None)
        ps.set_block(ParamSet.block_key(region.name, block.name), data)
    return ps


def read_patch(t: Transport, patch_number: int, progress=None) -> ParamSet:
    """Read user patch N (0-9) into a ParamSet keyed by PATCH%BLOCK.

    Amp patch N (0-based, as used by CMDID_PATCH_SELECT) is region
    'PATCH(N+1)' in the address map.

    Args:
        t: the connected transport.
        patch_number: the patch to read, 0-9.
        progress: optional callback called per block with (block, total).
    Returns:
        A ParamSet keyed `PATCH%<BLOCK>`.
    Raises:
        ValueError: if `patch_number` is outside 0-9.
    """
    if not 0 <= patch_number <= 9:
        raise ValueError("patch_number must be 0-9")
    log.debug("reading patch %d (%d blocks)", patch_number, len(PATCH_BLOCKS))
    mp = load_map()
    region = mp[f"PATCH({patch_number + 1})"]
    ps = ParamSet()
    for i, name in enumerate(PATCH_BLOCKS):
        block = region.block(name)
        # Chunked read: each RQ1/DT1 data transfer is capped at
        # SYSEX_MAXLEN (128), so larger blocks are read in multiple sessions
        # (handled inside rq1_nib).  FX_DETAIL blocks are hard-capped by the
        # amp at 225 bytes and then answer nothing — read with follow_amp so
        # the reachable bytes are returned instead of timing out.
        data = _read_block_chunked(
            t, block.base_addr, block.length,
            follow_amp=_is_fx_detail_block(name))
        ps.set_block(f"PATCH%{name}", data)
        if progress:
            progress(i + 1, len(PATCH_BLOCKS))
    return ps
