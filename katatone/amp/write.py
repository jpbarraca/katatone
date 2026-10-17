"""Write operations: patch write with echo confirmation + CMDID_EXPORT."""
from __future__ import annotations

import logging
import time

from ..address.map import load_map
from ..model.paramset import ParamSet
from ..protocol import rc6

log = logging.getLogger("katatone.write")
from ..protocol.rc6 import (CMD_EXPORT, CMD_PATCH_CLEAR, CMD_PATCH_SELECT,
                            CMD_PATCH_WRITE)
from ..protocol.transport import Transport

from .read import PATCH_BLOCKS

# Max bytes per DT1 payload chunk (RQ1/DT1 *data* is capped at
# SYSEX_MAXLEN = 128; rc6.dt1 rejects anything larger).  Blocks longer than
# this are sent in chunks.
DT1_CHUNK = rc6.SYSEX_MAXLEN


def select_patch(t: Transport, patch_number: int, settle: float = 0.1) -> None:
    """Select a user patch (CMDID_PATCH_SELECT with an INTEGER2x7 ``00 NN``).

    Args:
        t: the connected transport.
        patch_number: the patch to select, 0-9.
        settle: seconds to wait afterwards so a following read sees the new
            patch. A pedal switch passes 0: it reads nothing straight away, and
            the wait would add to the time before the sound changes.
    """
    log.debug("selecting patch %d", patch_number)
    t.dt1(CMD_PATCH_SELECT, rc6.int2x7(patch_number))
    if settle > 0:
        time.sleep(settle)


def _dt1_chunked(t: Transport, addr: int, data: bytes) -> None:
    """DT1-write `data` at pre-nibble `addr`, chunking at DT1_CHUNK bytes.

    Args:
        t: the connected transport.
        addr: the starting address (pre-nibble); chunks are written at
            addr + their byte offsets.
        data: the full block payload.
    """
    off = 0
    n = len(data)
    while off < n:
        chunk = data[off:off + DT1_CHUNK]
        t.dt1(addr + off, chunk)
        off += len(chunk)


def write_patch(t: Transport, patch_number: int, ps: ParamSet,
                name: str | None = None) -> None:
    """Write a ParamSet to user patch N.
    log.debug("writing patch %d (%d blocks)", patch_number, len(PATCH_BLOCKS))

    Sequence:
      1. DT1 patch name -> PATCH%COM of the target patch region
      2. DT1 each patch block -> target region (device echoes DT1 per write)
      3. CMDID_PATCH_WRITE  [N]
      4. CMDID_EXPORT       [N]  (final confirmation)

    Args:
        t: the connected transport.
        patch_number: the user patch to write, 0-9.
        ps: the ParamSet holding the patch's `PATCH%<BLOCK>` data.
        name: the patch name to store (None keeps the one in `ps`).
    Raises:
        ValueError: if `patch_number` is outside 0-9.
    """
    if not 0 <= patch_number <= 9:
        raise ValueError("patch_number must be 0-9")
    mp = load_map()
    region = mp[f"PATCH({patch_number + 1})"]

    # 1. name
    name_bytes = ParamSet.encode_patch_name(name or ParamSet.patch_name(
        ps.block("PATCH%COM")) or f"PATCH {patch_number + 1}")
    t.dt1(region.base + 0x0, name_bytes)

    # 2. blocks (skip COM, already sent).  Each block is sent chunked so any
    #    block longer than DT1_CHUNK (e.g. FX_DETAIL at 353 B) is fully written.
    for bname in PATCH_BLOCKS:
        if bname == "COM":
            continue
        data = ps.block(f"PATCH%{bname}")
        if not data:
            continue
        _dt1_chunked(t, region.block(bname).base_addr, data)

    # 3. commit
    t.dt1(CMD_PATCH_WRITE, rc6.int2x7(patch_number))
    time.sleep(0.2)

    # 4. final confirmation
    t.dt1(CMD_EXPORT, rc6.int2x7(patch_number))


def clear_patch(t: Transport, patch_number: int) -> None:
    """Clear a user patch (CMDID_PATCH_CLEAR with an INTEGER2x7 ``00 NN``).

    Args:
        t: the connected transport.
        patch_number: the user patch to clear, 0-9.
    """
    log.debug("clearing patch %d", patch_number)
    t.dt1(CMD_PATCH_CLEAR, rc6.int2x7(patch_number))


def set_preview_mute(t: Transport, mute: bool) -> None:
    """Mute or unmute the amp's preview (one-byte DT1 to CMD_PREVIEW_MUTE).

    Args:
        t: the connected transport.
        mute: True to mute, False to unmute.
    """
    t.dt1(rc6.CMD_PREVIEW_MUTE, b"\x01" if mute else b"\x00")


# ------------------------------------------------------- single parameters

# The amp's current patch (edit buffer).  Writes here change the sound live
# but are NOT stored in a user patch until a patch write (CMDID_PATCH_WRITE),
# so it is the only region single-parameter writes are allowed to target.
EDIT_REGION = "PATCH"


def _edit_param(block_name: str, p_index: int):
    """Resolve the (block, param) pair in the edit region for a block/param index.

    Args:
        block_name: the block name within EDIT_REGION.
        p_index: index into the block's `params` list.
    Returns:
        The (Block, Param) pair from the address map.
    """
    region = load_map()[EDIT_REGION]
    block = region.block(block_name)
    return block, block.params[p_index]


def write_param(t: Transport, block_name: str, p_index: int,
                value: int) -> bytes:
    """DT1 one parameter of the edit buffer (live, not stored).

    The value is range-checked against the address map and encoded like the
    reference ``Parameter.setValue`` (7-bit byte or 4-bit nibbles); returns
    the data bytes sent.

    Args:
        t: the connected transport.
        block_name: the block name within EDIT_REGION.
        p_index: index into the block's `params` list.
        value: the display value to write.
    Returns:
        The raw data bytes sent by DT1.
    Raises:
        ValueError: if the parameter is not numeric, or `value` is outside
        the map's min..max.
    """
    block, p = _edit_param(block_name, p_index)
    if p.size not in (1, 2, 4):
        raise ValueError(f"{p.name}: size {p.size} is not a numeric param")
    if not p.min <= value <= p.max:
        raise ValueError(f"{p.name}: {value} outside {p.min}..{p.max}")
    data = ParamSet.raw_bytes(p.size, value + p.ofs)
    log.debug("write_param %s = %d -> %s @ %#010x", p.name, value,
              data.hex(" "), block.base_addr + p.off)
    t.dt1(block.base_addr + p.off, data)
    return data


def read_param(t: Transport, block_name: str, p_index: int,
               timeout: float = 2.0) -> int:
    """RQ1 one parameter of the edit buffer; returns its display value.

    Args:
        t: the connected transport.
        block_name: the block name within EDIT_REGION.
        p_index: index into the block's `params` list.
        timeout: seconds for the RQ1 read (default 2.0).
    Returns:
        The parameter's display value.
    """
    block, p = _edit_param(block_name, p_index)
    raw = t.rq1(block.base_addr + p.off, p.size, timeout=timeout)
    return ParamSet.raw_value(p.size, raw) - p.ofs
