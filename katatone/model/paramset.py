"""ParamSet: block_name -> raw bytes, with display<->storage conversion."""
from __future__ import annotations

from ..address.map import Block, Region


class ParamSet:
    """Mirrors the JS `paramSet` dict { 'PATCH%AMP': [bytes...], ... }."""

    def __init__(self) -> None:
        """Create an empty ParamSet; blocks are added with `set_block`."""
        self.data: dict[str, bytes] = {}

    # -- block access ---------------------------------------------------

    @staticmethod
    def bid(region: str, block_name: str) -> str:
        """Join a region and a block name into a ``region%block`` block id."""
        return f"{region}%{block_name}"

    @staticmethod
    def block_key(region_name: str, block_name: str) -> str:
        """Canonical block key for a region.

        The ten user-patch regions ``PATCH(1..10)`` all share the canonical
        ``PATCH`` key prefix (TSL paramSet convention: ``'PATCH%<BLOCK>'``);
        the patch index lives in the liveset row, not in the key.  Every other
        region (SYSTEM, TEMP, …) is keyed by its own name.

        ``read_patch`` stores blocks under this key, so parameter access must
        derive it the same way — otherwise lookups miss, every value decodes
        from ``b""`` to 0, and writes go to a phantom block the write path
        never reads.
        """
        if region_name.startswith("PATCH("):
            return f"PATCH%{block_name}"
        return f"{region_name}%{block_name}"

    def block(self, bid: str) -> bytes:
        """Return the raw bytes stored for a block id, or `b""` if absent."""
        return self.data.get(bid, b"")

    def set_block(self, bid: str, data: bytes) -> None:
        """Store `data` as the raw bytes for block id `bid`."""
        self.data[bid] = data

    def blocks(self) -> list[str]:
        """Return all block ids currently in the set."""
        return list(self.data)

    # -- parameter access ------------------------------------------------

    # Multi-byte parameters are stored as 4-bit nibbles, most significant
    # first (INTEGER2x4 / INTEGER4x4); single-byte
    # ones as one 7-bit byte (INTEGER1x7).  The 2-byte params in the patch /
    # system / setup maps are all INTEGER2x4 (INTEGER2x7 only occurs in the
    # COMMAND block, see ``rc6.int2x7``).

    @staticmethod
    def raw_value(size: int, data: bytes) -> int:
        """Decode a parameter's raw block bytes into its stored integer.

        Single-byte params are one 7-bit byte; multi-byte params are 4-bit
        nibbles, most significant first.

        Args:
            size: parameter size in bytes (1, 2 or 4).
            data: the raw block bytes covering the parameter.
        Returns:
            The stored value (display value + map offset).
        """
        if size == 1:
            return data[0] & 0x7F if data else 0
        v = 0
        for x in data[:size]:
            v = (v << 4) | (x & 0x0F)
        return v

    @staticmethod
    def raw_bytes(size: int, v: int) -> bytes:
        """Encode a stored integer into a parameter's raw block bytes.

        Inverse of `raw_value`: one 7-bit byte for `size` 1, otherwise 4-bit
        nibbles, most significant first.

        Args:
            size: parameter size in bytes (1, 2 or 4).
            v: the stored value (display value + map offset).
        Returns:
            `size` bytes of raw storage.
        """
        if size == 1:
            return bytes([v & 0x7F])
        return bytes((v >> (4 * (size - 1 - i))) & 0x0F for i in range(size))

    @staticmethod
    def decode(block: Block, raw: bytes, p_index: int) -> int:
        """Raw block bytes -> display value for the parameter at p_index.

        Args:
            block: the address-map Block describing the layout.
            raw: the raw bytes of that block.
            p_index: index into `block.params`.
        Returns:
            The display value (raw value minus the parameter's offset).
        """
        p = block.params[p_index]
        return ParamSet.raw_value(p.size, raw[p.off:p.off + p.size]) - p.ofs

    @staticmethod
    def encode(block: Block, raw: bytearray, p_index: int, value: int) -> None:
        """Display value -> raw block bytes in place.

        Args:
            block: the address-map Block describing the layout.
            raw: the block bytes to modify in place.
            p_index: index into `block.params`.
            value: the display value to store.
        """
        p = block.params[p_index]
        raw[p.off:p.off + p.size] = ParamSet.raw_bytes(p.size, value + p.ofs)

    def get_param(self, region: Region, block_name: str, p_index: int) -> int:
        """Display value of one parameter of a block in a region.

        Looks the block up under its canonical `block_key` and decodes it
        with `decode`.

        Args:
            region: the address-map region the block belongs to.
            block_name: name of the block within that region.
            p_index: index into the block's `params` list.
        Returns:
            The parameter's display value.
        """
        b = region.block(block_name)
        return self.decode(b,
                           self.block(self.block_key(region.name, block_name)),
                           p_index)

    def set_param(self, region: Region, block_name: str, p_index: int,
                  value: int) -> bytes:
        """Set a param; returns the full new block bytes.

        A missing block starts as zero bytes.

        Args:
            region: the address-map region the block belongs to.
            block_name: name of the block within that region.
            p_index: index into the block's `params` list.
            value: the display value to store.
        """
        b = region.block(block_name)
        bid = self.block_key(region.name, block_name)
        raw = bytearray(self.data.get(bid, b"\x00" * b.length))
        self.encode(b, raw, p_index, value)
        self.data[bid] = bytes(raw)
        return bytes(raw)

    # -- patch name -------------------------------------------------------

    @staticmethod
    def patch_name(raw: bytes) -> str:
        """Decode the patch name from a COM block's raw bytes.

        Args:
            raw: the raw bytes of the COM block.
        Returns:
            The name, stripped of trailing padding.
        """
        # Device names are 16 ASCII chars, space-padded.  Tolerate NUL padding from other tools/firmware.
        s = raw[:16].decode("ascii", "replace").split("\x00", 1)[0]
        return s.strip()

    @staticmethod
    def encode_patch_name(name: str) -> bytes:
        """Encode a patch name into its 16-byte COM block form.

        Inverse of `patch_name`: ASCII (lossy), truncated to 16 chars,
        space-padded.

        Args:
            name: the patch name.
        Returns:
            Exactly 16 bytes.
        """
        return name.encode("ascii", "replace")[:16].ljust(16, b" ")
