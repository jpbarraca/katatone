"""Roland RC6 MIDI sysex protocol (KATANA Gen 3).

Frames (hex):
    DT1:  F0 41 <dev:1> <model:3> 12 <addr:4> <data:0..128> <chksum:1> F7
    RQ1:  F0 41 <dev:1> <model:3> 11 <addr:4> <size:4> <chksum:1> F7
    IDNT: F0 7E 7F 06 01 F7

ADDRESS MODEL (checked on 100k random addresses, 0 collisions):

  * The address map (assets/address_map.json) stores *pre-nibble* 32-bit
    addresses (e.g. PATCH(1) base 0x20100000).
  * The device's physical memory is a linear array indexed by the *nibble*
    address  N = nibble(pre)  (a 24-bit value: 7 significant bits per byte).
    A parameter byte at pre-nibble address X lives at mem[nibble(X)].
  * Every meaningful parameter address is nibble-unique (no two real
    parameter bytes map to the same N).
  * On the wire, an address is carried as 4 big-endian bytes
    W = _7bitize(N).  Equivalently, for a pre-nibble address X,
    W = X & 0x7F7F7F7F.
  * Receiving a frame, the nibble address is recovered as N = nibble(W).

Chunked reads/writes accumulate the address in NIBBLE space: chunk i is at
N_base + cumsum_i, and the wire bytes are _7bitize(N_base + cumsum_i).  This
is NOT the same as (X_base + cumsum_i) & 0x7F7F7F7F once the low byte of an
offset crosses a 0x7F -> 0x80 boundary (bit 7 of a byte is unrepresentable).

Checksum = (128 - sum(addr_bytes + field_bytes) % 128) & 0x7F, computed over
the wire bytes.  DT1 data payloads are transmitted unchanged.
"""

from __future__ import annotations

STX = 0xF0
EOX = 0xF7
ROLAND = 0x41
RQ1 = 0x11
DT1 = 0x12

# Max data bytes per DT1/RQ1 transfer (reference dividesize limit).  A DT1
# frame carrying 128 data bytes is 141 bytes long; USB-MIDI carries it.
SYSEX_MAXLEN = 128

# KATANA Gen 3 identity
DEVICE_ID = 0x10
MODEL_ID = 0x0105_0700  # transmitted as '01 05 07' (3 bytes, high order)
MODEL_ID_BYTES = bytes((0x01, 0x05, 0x07))

# COMMAND block addresses (relative to 0x7F000000) — pre-nibble
CMD = 0x7F00_0000
CMD_EDITOR_LEVEL = CMD + 0x00
CMD_EDITOR_MODE = CMD + 0x01
CMD_RUNNING_MODE = CMD + 0x02
CMD_EDITOR_REVISION = CMD + 0x03
CMD_PATCH_SELECT = CMD + 0x100
CMD_PATCH_WRITE = CMD + 0x104
CMD_PATCH_INIT = CMD + 0x106
CMD_PATCH_CLEAR = CMD + 0x10E
CMD_PREVIEW_MUTE = CMD + 0x10109
CMD_EXPORT = CMD + 0x1010A


# ---------------------------------------------------------------- transforms

def nibble(x: int) -> int:
    """7-bit MIDI encode of a 32-bit value.

    Drops bit 7 of each byte, packing the four 7-bit fields into 24 bits.
    """
    x &= 0xFFFFFFFF
    return (
        ((x & 0x7F00_0000) >> 3)
        | ((x & 0x007F_0000) >> 2)
        | ((x & 0x0000_7F00) >> 1)
        | (x & 0x0000_007F)
    )


def _7bitize(x: int) -> int:
    """Inverse of nibble(): expand a 24-bit nibble value back to 32 bits."""
    x &= 0xFFFFFFFF
    return (
        ((x & 0x0FE0_0000) << 3)
        | ((x & 0x001F_C000) << 2)
        | ((x & 0x0000_3F80) << 1)
        | (x & 0x0000_007F)
    )


def to_nib(pre: int) -> int:
    """Pre-nibble 32-bit address -> nibble 24-bit device memory index.

    This is the address the device actually uses to index its memory.  For a
    block base B the block's byte k lives at ``to_nib(B) + k`` (contiguous in
    nibble space — the reference accumulates chunk offsets in this domain).
    """
    return nibble(pre)


def wire_int(nib: int) -> int:
    """Nibble address -> the 4 wire bytes as an integer (big-endian value)."""
    return _7bitize(nib) & 0xFFFFFFFF


def wire_addr(nib: int) -> bytes:
    """Nibble address -> the 4 wire bytes."""
    return wire_int(nib).to_bytes(4, "big")


def addr_hex(pre: int) -> bytes:
    """Pre-nibble address -> the 4 wire bytes (``_7bitize(nibble(pre))``).

    This is what actually travels on the wire for a single (non-chunked)
    request to a pre-nibble address.
    """
    return wire_addr(nibble(pre))


def nib_of_wire(w: int) -> int:
    """Wire address (4 raw bytes as int) -> nibble device memory index."""
    return nibble(w & 0xFFFFFFFF)


def nibble_le(x: int) -> int:
    """Little-endian nibble decode (``nibble_le``).

    Used to recover a block's *length* from the 4-byte descriptor the amp
    echoes back for a size<0 (FX_DETAIL) pre-read: the 4 wire bytes are
    re-packed little-endian through the 7-bit ``nibble`` transform.
    """
    x &= 0xFFFFFFFF
    return nibble(
        ((x & 0x7F00_0000) >> 24)
        | ((x & 0x007F_0000) >> 8)
        | ((x & 0x0000_7F00) << 8)
        | ((x & 0x0000_007F) << 24)
    )


def size_field(size: int) -> bytes:
    """RQ1 size field: reference does ``_7bitize(size)`` as 4 big-endian bytes."""
    return (_7bitize(size & 0xFFFFFFFF) & 0xFFFFFFFF).to_bytes(4, "big")


def decode_size(field: bytes) -> int:
    """Decode an RQ1 size field (4 wire bytes) back to a byte count."""
    return nibble(int.from_bytes(field, "big"))


def checksum(payload: bytes) -> int:
    """RC6 frame checksum: ``(128 - sum(payload) % 128) & 0x7F``.

    Computed over the wire bytes (address + field bytes); it is the single
    byte appended before ``EOX``.

    Args:
        payload: the wire bytes the checksum is computed over.

    Returns:
        The one-byte checksum value (0..127).
    """
    s = sum(payload)
    return (128 - (s % 128)) & 0x7F


# ---------------------------------------------------------------- identity

def identity_request() -> bytes:
    """Build the IDNT identity inquiry sysex (``F0 7E 7F 06 01 F7``).

    Returns:
        The complete 6-byte inquiry frame.
    """
    return bytes([STX, 0x7E, 0x7F, 0x06, 0x01, EOX])


def identity_reply_prefix(device_id: int = DEVICE_ID) -> bytes:
    """Build the prefix an identity reply is verified against (first 8 bytes).

    Args:
        device_id: device ID byte expected in the reply (KATANA Gen 3 by default).

    Returns:
        The 8-byte reply prefix.
    """
    model_tail = MODEL_ID_BYTES[-1]
    length_byte = len(MODEL_ID_BYTES) + 2
    return bytes([STX, 0x7E, device_id, 0x06, 0x02, ROLAND, model_tail, length_byte])


# ---------------------------------------------------------------- frame build

def _header() -> bytes:
    """Sysex frame header: ``STX ROLAND DEVICE_ID`` + the 3 model ID bytes.

    Returns:
        The 7-byte header prefix shared by DT1 and RQ1 frames.
    """
    return bytes([STX, ROLAND, DEVICE_ID, *MODEL_ID_BYTES])


def int2x7(value: int) -> bytes:
    """INTEGER2x7 command value: two 7-bit bytes, most significant first
    (patch N is sent as ``'00' + hex2(N)``).

    Args:
        value: the command value to encode (0..0x3FFF).

    Returns:
        The two 7-bit bytes, most significant first.

    Raises:
        ValueError if `value` is outside 0..0x3FFF.
    """
    if not 0 <= value <= 0x3FFF:
        raise ValueError(f"INTEGER2x7 out of range: {value}")
    return bytes([(value >> 7) & 0x7F, value & 0x7F])


def dt1(nib: int, data: bytes) -> bytes:
    """Build a DT1 (data transfer) sysex frame.

    Args:
        nib: 24-bit nibble device memory index (use ``to_nib(pre)`` for map
            addresses, or a nibble-space chunk address).
        data: payload bytes (device-native; length <= SYSEX_MAXLEN = 128, the
            RQ1/DT1 *data* cap; the resulting 141-byte frame travels fine
            over USB-MIDI).

    Returns:
        The complete DT1 sysex frame.

    Raises:
        ValueError if `data` is longer than SYSEX_MAXLEN bytes.
    """
    if len(data) > SYSEX_MAXLEN:
        raise ValueError(f"dt1 data too long: {len(data)} > {SYSEX_MAXLEN}")
    a = wire_addr(nib)
    body = a + data
    return _header() + bytes([DT1]) + body + bytes([checksum(body), EOX])


def rq1(nib: int, size: int) -> bytes:
    """Build an RQ1 (read request) sysex frame.

    Args:
        nib: 24-bit nibble device memory index.
        size: number of bytes to read (<= SYSEX_MAXLEN).

    Returns:
        The complete RQ1 sysex frame.
    """
    a = wire_addr(nib)
    s = size_field(size)
    body = a + s
    return _header() + bytes([RQ1]) + body + bytes([checksum(body), EOX])


# ---------------------------------------------------------------- parsing

class SysexFrame:
    __slots__ = ("command", "addr", "payload")

    def __init__(self, command: int, addr: int, payload: bytes):
        """Parsed RC6 sysex frame.

        Args:
            command: `RQ1` or `DT1` command byte.
            addr: WIRE address (4 raw bytes as int).
            payload: DT1: data bytes; RQ1: size field (4 bytes).
        """
        self.command = command     # RQ1 or DT1
        self.addr = addr           # WIRE address (4 raw bytes as int)
        self.payload = payload     # DT1: data bytes; RQ1: size field (4 bytes)

    @property
    def nib(self) -> int:
        """Nibble device memory index for this frame's wire address."""
        return nib_of_wire(self.addr)

    def __repr__(self) -> str:
        """One-line summary: frame kind, wire/nibble address, payload head."""
        kind = {RQ1: "RQ1", DT1: "DT1"}.get(self.command, f"0x{self.command:02X}")
        return (f"SysexFrame({kind} wire=0x{self.addr:08X} nib=0x{self.nib:06X} "
                f"payload={self.payload[:16].hex()!r}…)")


def parse(frame: bytes) -> SysexFrame | None:
    """Parse a received RC6 sysex frame.

    Args:
        frame: the raw received bytes (STX..EOX).

    Returns:
        A `SysexFrame` (``.addr`` is the raw 4 wire bytes as an int; ``.nib``
        is the device memory index), or None for frames not addressed to the
        expected model (identity replies, other Roland gear).
    """
    if len(frame) < 12 or frame[0] != STX or frame[-1] != EOX:
        return None
    if frame[1] != ROLAND:
        return None
    model = frame[3:6]
    if model != MODEL_ID_BYTES:
        return None
    command = frame[6]
    if command not in (RQ1, DT1):
        return None
    addr = int.from_bytes(frame[7:11], "big")
    rest = frame[11:-2]
    return SysexFrame(command, addr, rest)
