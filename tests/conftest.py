"""Shared pytest fixtures: a fake KATANA amp + a hardware-free Transport."""
from __future__ import annotations

import sys
from pathlib import Path
from queue import Queue, Empty

import pytest

# Make the `katatone` package importable without an editable install.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from katatone.protocol import rc6  # noqa: E402
from katatone.protocol.transport import Transport  # noqa: E402
from katatone.amp.read import PATCH_BLOCKS  # noqa: E402
from katatone.address.map import load_map  # noqa: E402


# Tracks the nibble base address for each patch's flat memory buffer.
_patch_memory_base: dict[int, int] = {}


class FakeAmp:
    """In-memory RC6 device.

    Supports two memory models:
      * **dict mode** (legacy): ``memory={nibble_addr: bytearray}`` — each key
        is a block base; reads search for a containing block.
      * **flat mode**: ``memory=bytearray`` — a contiguous buffer covering the
        full region.  Reads index directly (offset by the buffer's base).
        This correctly handles overlapping blocks (FX_DETAIL slots are 353
        bytes but only 128 nibbles apart).

    The device's memory is indexed by *nibble* address.  The transport applies
    the pre-nibble -> nibble transform and sends RQ1/DT1 frames carrying the
    nibble-packed wire address; this amp recovers the nibble index from each
    received frame with ``rc6.nib_of_wire`` and keys its memory / write log by
    that index — mirroring how the reference matches incoming frames by wire
    address before any further decoding.

    Like the real KAT
</think>

, each FX_DETAIL block answers a single RQ1 with at most ``DETAIL_READ_WALL``
    bytes and nothing past that wall.  The read path requests the whole
    declared block length in one RQ1 and takes the amp's natural (wall-limited)
    reply, so this amp clamps an FX_DETAIL read to the wall — otherwise a 353-
    byte single read could not be replied to within the RC
</think>

 data cap.
    """

    # The KATANA amp hard-caps each FX_DETAIL block's single-RQ
    # 1 reply here (real hardware: 225 bytes); bytes 225..352 are past the wall.
    DETAIL_READ_WALL = 225

    def __init__(self, memory: "dict | bytearray | None" = None, device_id: int = 0x10):
        """Store the amp's memory and identity for the fake device.

        Args:
            memory: dict of nibble base -> bytes (block mode), a flat bytearray
                covering the region (flat mode), or None for an empty dict.
            device_id: device ID reported in the identity reply (default 0x10).
        """
        self.writes: list[tuple[int, bytes]] = []
        self.device_id = device_id
        # Nibble bases of the 6 KATANA FX_DETAIL blocks, used to apply the
        # single-RQ1 read wall below.  Computed lazily / cached.
        self._detail_bases: set[int] | None = None
        if isinstance(memory, bytearray):
            # Flat memory mode
            self._flat: bytearray | None = memory
            self._flat_base: int = 0
            self.mem: dict[int, bytearray] = {}
        else:
            # Dict mode (legacy)
            self._flat = None
            self._flat_base = 0
            self.mem = {k: bytearray(v) for k, v in (memory or {}).items()}

    def set_flat_base(self, base: int) -> None:
        """Set the nibble address corresponding to index 0 of the flat buffer."""
        self._flat_base = base

    def _detail_base_set(self) -> set[int]:
        """Nibble bases of the 6 PATCH FX_DETAIL blocks (cached)."""
        if self._detail_bases is None:
            region = load_map()["PATCH(1)"]
            self._detail_bases = {
                rc6.to_nib(region.block(f"FX_DETAIL({i})").base_addr)
                for i in range(1, 7)
            }
        return self._detail_bases

    def _read(self, nib: int, size: int) -> bytes:
        """Read `size` bytes starting at nibble address `nib`.

        For an FX_DETAIL block base this returns at most ``DETAIL_READ_WALL``
        bytes (the amp's single-RQ
        # 1 wall) — matching the real device, which answers nothing past 225.
        """
        if nib in self._detail_base_set():
            size = min(size, self.DETAIL_READ_WALL)
        if self._flat is not None:
            # Flat memory: direct index
            off = nib - self._flat_base
            if 0 <= off and off + size <= len(self._flat):
                return bytes(self._flat[off:off + size])
            elif 0 <= off < len(self._flat):
                return bytes(self._flat[off:off + size]).ljust(size, b"\x00")
            return b"\x00" * size
        # Dict mode: exact match first, then containing block.
        if nib in self.mem:
            return bytes(self.mem[nib][:size]).ljust(size, b"\x00")
        for base, data in self.mem.items():
            if base <= nib < base + len(data):
                off = nib - base
                return bytes(data[off:off + size]).ljust(size, b"\x00")
        return b"\x00" * size

    def reply_for(self, frame: bytes) -> list[bytes] | None:
        """Answer one wire frame with the device's reply, if any.

        Args:
            frame: a complete RC6 frame (identity request, RQ1 or DT1) as bytes.

        Returns:
            the list of reply frames, or None for frames that get no reply
            (DT1 writes and unknown commands).
        """
        # General reply to an inquiry.
        if frame == rc6.identity_request():
            return [rc6.identity_reply_prefix(self.device_id) + bytes([rc6.EOX])]
        f = rc6.parse(frame)
        if f is None:
            return None
        nib = f.nib            # device-memory index recovered from the wire addr
        if f.command == rc6.RQ1:
            size = rc6.decode_size(f.payload)
            data = self._read(nib, size)
            # RC
            # 6 DT1 payloads are capped at SYSEX_MAXLEN (128) bytes, so a reply
            # longer than that (e.g. an FX_DETAIL read hitting the 225-byte
            # wall) is transmitted as a run of <=128-byte DT1 frames carrying
            # the same address.  The transport accumulates all of them.
            return [rc6.dt1(nib, data[i:i + rc6.SYSEX_MAXLEN])
                    for i in range(0, len(data), rc6.SYSEX_MAXLEN)]
        if f.command == rc6.DT1:
            self.writes.append((nib, bytes(f.payload)))
            if self._flat is not None:
                off = nib - self._flat_base
                if 0 <= off and off + len(f.payload) <= len(self._flat):
                    self._flat[off:off + len(f.payload)] = f.payload
            else:
                self.mem[nib] = bytearray(f.payload)
            return None
        return None


def make_transport(amp: FakeAmp, pace: bool = True):
    """Wire a Transport to a FakeAmp via in-memory queues.

    Args:
        amp: the fake amp that answers every frame.
        pace: zero out the transport's pacing delay when True (default);
            pass False to exercise the real sleep path.

    Returns:
        a started Transport ready for reads and writes.
    """
    inbound: Queue = Queue()

    def send(frame: bytes) -> None:
        """Forward a frame to the amp and queue its reply frames inbound."""
        reply = amp.reply_for(frame)
        if reply is not None:
            for r in reply:
                inbound.put(r)

    def recv(timeout: float) -> list[bytes]:
        """Wait up to `timeout` seconds for one inbound frame; [] on timeout."""
        try:
            return [inbound.get(timeout=timeout)]
        except Empty:
            return []

    t = Transport(send, recv)
    if pace:
        # Keep tests fast but still validate the sleep path exists.
        import katatone.protocol.transport as tr
        tr.pace_delay_ms = staticmethod(lambda msg: 0.0)
    t.start()
    return t


def patch_memory(patch_number: int, block_bytes: dict[str, bytes]) -> bytearray:
    """Seed a flat FakeAmp buffer with per-block bytes for user patch N.

    Builds a contiguous bytearray covering the full patch region in nibble
    space.  Blocks are written in PATCH_BLOCKS order (later blocks overwrite
    earlier ones in overlap regions, matching the real amp's memory layout).
    Returns the flat buffer; call ``amp.set_flat_base(base_nib)`` with the
    minimum nibble address to align reads.

    Args:
        patch_number: user patch index (0-based); patch N maps to region PATCH(N+1).
        block_bytes: per-block bytes keyed by canonical name, e.g. "PATCH%AMP".
    """
    mp = load_map()
    region = mp[f"PATCH({patch_number + 1})"]
    # Compute span
    entries = []
    for bname in PATCH_BLOCKS:
        block = region.block(bname)
        nib = rc6.to_nib(block.base_addr)
        entries.append((nib, block.length, bname))
    min_nib = min(n for n, _, _ in entries)
    max_end = max(n + l for n, l, _ in entries)
    flat = bytearray(max_end - min_nib)
    # Write blocks in order (later overwrites earlier in overlaps)
    for nib, length, bname in entries:
        data = block_bytes.get(f"PATCH%{bname}")
        if data:
            off = nib - min_nib
            flat[off:off + len(data)] = data[:length]
    # Store the base on the buffer via a wrapper (caller sets it on the amp)
    # We return a tagged bytearray — use a module-level dict to pass the base
    _patch_memory_base[patch_number] = min_nib
    return flat


@pytest.fixture
def amp() -> FakeAmp:
    """A FakeAmp pre-seeded with COMM_LEVEL (1) at the editor-mode command."""
    # Default level byte at the editor-mode command must equal COMM_LEVEL (1).
    from katatone.protocol.rc6 import CMD_EDITOR_LEVEL
    return FakeAmp(memory={rc6.to_nib(CMD_EDITOR_LEVEL): b"\x01"})


@pytest.fixture
def transport(amp: FakeAmp):
    """A started Transport wired to the amp fixture; stopped after the test."""
    t = make_transport(amp)
    yield t
    t.stop()
