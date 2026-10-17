"""Unit tests for the RC6 sysex protocol (no hardware)."""
from __future__ import annotations

import pytest

from katatone.protocol import rc6


@pytest.mark.parametrize("addr", [0x00000000, 0x00000001, 0x20000000,
                                   0x7F000100, 0x0105_0700])
def test_nibble_7bitize_roundtrip(addr: int) -> None:
    """For addresses with all high bits clear, the nibble transform is exactly invertible."""
    # The transform keeps 7 bits per byte; identity holds when each byte's
    # high bit is clear (true for every address used on the wire here).
    assert (addr & 0x8080_8080) == 0
    assert rc6._7bitize(rc6.nibble(addr)) == (addr & 0xFFFFFFFF)


def test_nibble_drops_high_bits() -> None:
    """The nibble transform consumes bit 7 of each byte, and _7bitize packs 0x7F bytes."""
    # Bit 7 of each byte is consumed by the packing, so 0xFF bytes -> 0x7F.
    assert rc6.nibble(0xFFFFFFFF) == 0x0FFFFFFF
    assert rc6._7bitize(0x0FFFFFFF) == 0x7F7F_7F7F


def test_addr_hex_is_nibble_encoded() -> None:
    """addr_hex emits the nibble-transformed address as four wire bytes, dropping top bits."""
    # addr 0x00000001 -> nibble -> 1 -> wire 00 00 00 01
    assert rc6.addr_hex(0x00000001) == bytes((0x00, 0x00, 0x00, 0x01))
    # high nibble moves: 0x80000000 -> only low 7 bits of each byte survive
    v = rc6.nibble(0x80000000)
    assert v == 0  # top bit is dropped by the 7-bit transform


def test_checksum_covers_body() -> None:
    """The DT1 checksum byte completes the sum of the body (address + data) modulo 128."""
    frame = rc6.dt1(0x0000_0000, b"\x01\x02\x03\x04")
    body = frame[7:-2]            # addr(4) + data
    assert (sum(body) + frame[-2]) % 128 == 0


def test_dt1_build_and_parse_roundtrip() -> None:
    """A DT1 frame built for a nibble address and payload parses back to the same command, address and payload."""
    nib = rc6.to_nib(0x2000_0010)      # device memory index (pre-nibble -> nib)
    data = bytes(range(20))
    frame = rc6.dt1(nib, data)
    assert frame[0] == rc6.STX and frame[-1] == rc6.EOX
    assert frame[1] == rc6.ROLAND
    f = rc6.parse(frame)
    assert f is not None
    assert f.command == rc6.DT1
    assert f.nib == nib                 # nibble index round-trips
    assert f.payload == data


def test_rq1_build_and_parse_roundtrip() -> None:
    """An RQ1 frame round-trips: command, nibble address and requested size all parse back."""
    nib = rc6.to_nib(0x2000_0010)
    size = 128  # max RQ1 data size
    frame = rc6.rq1(nib, size)
    f = rc6.parse(frame)
    assert f is not None
    assert f.command == rc6.RQ1
    assert f.nib == nib
    assert rc6.decode_size(f.payload) == size


def test_dt1_payload_is_passthrough_not_7bitized() -> None:
    """DT1 payload bytes pass through the frame untransformed (device-native bytes)."""
    # DT1 data bytes are device-native (not nibble-transformed).
    data = bytes([0x00, 0x55, 0xAA, 0xFF])
    f = rc6.parse(rc6.dt1(0x2000_0000, data))
    assert f.payload == data


def test_dt1_rejects_overlong_data() -> None:
    """dt1 raises ValueError for a payload larger than SYSEX_MAXLEN."""
    with pytest.raises(ValueError):
        rc6.dt1(0x2000_0000, b"\x00" * (rc6.SYSEX_MAXLEN + 1))


def test_dt1_accepts_max_payload() -> None:
    """A DT1 with a full 128-byte payload builds a 141-byte frame and parses back intact."""
    # SYSEX_MAXLEN is a *data* cap: a 128-byte DT1 payload
    # yields a 141-byte frame (hdr 6 + cmd 1 + addr 4 + data 128 + chk 1 + eox 1).
    data = bytes(range(128))
    frame = rc6.dt1(0x2000_0000, data)
    assert len(frame) == 6 + 1 + 4 + 128 + 1 + 1
    f = rc6.parse(frame)
    assert f is not None
    assert f.command == rc6.DT1
    assert f.payload == data


def test_parse_rejects_non_katana_model() -> None:
    """parse rejects a DT1 frame addressed to another Roland model."""
    # Craft a DT1 frame addressed to a different Roland model.
    bad = bytes([rc6.STX, rc6.ROLAND, rc6.DEVICE_ID, 0x09, 0x09, 0x09,
                 rc6.DT1, 0x00, 0x00, 0x00, 0x00, rc6.checksum(b"\x00\x00\x00\x00"),
                 rc6.EOX])
    assert rc6.parse(bad) is None


def test_parse_rejects_truncated() -> None:
    """parse returns None for a truncated frame."""
    assert rc6.parse(b"\xF0\x41\x10") is None


def test_identity_request_and_prefix() -> None:
    """The identity request is the exact 6-byte probe, the reply prefix carries the device ID and model fields, and the request is not a DT1/RQ1 frame."""
    req = rc6.identity_request()
    assert req == bytes([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7])
    pref = rc6.identity_reply_prefix()
    assert len(pref) == 8
    assert pref[0] == 0xF0 and pref[1] == 0x7E
    assert pref[2] == rc6.DEVICE_ID
    assert pref[3:5] == bytes((0x06, 0x02))
    assert rc6.parse(req) is None   # identity is not a DT1/RQ1
