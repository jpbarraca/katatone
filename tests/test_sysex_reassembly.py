"""SysEx reassembly from rawmidi reads (no device)."""
from __future__ import annotations

from katatone.protocol.alsa_seq import SysexReassembler

F = bytes.fromhex("f041100105071200000001f7")      # a small sysex


def feed(*chunks: bytes) -> list[bytes]:
    """Feed chunks to one reassembler (state persists) and return all frames."""
    r = SysexReassembler()
    return [f for c in chunks for f in r.feed(c)]


def test_plain_frame() -> None:
    assert feed(F) == [F]


def test_frame_split_across_reads() -> None:
    assert feed(F[:5], F[5:10], F[10:]) == [F]


def test_two_frames_in_one_read_are_two_frames() -> None:
    assert feed(F + F) == [F, F]


def test_realtime_bytes_are_dropped_even_inside_a_frame() -> None:
    assert feed(b"\xf8\xfe" + F[:4] + b"\xf8" + F[4:] + b"\xfe") == [F]


def test_bytes_outside_a_sysex_are_discarded() -> None:
    assert feed(bytes.fromhex("c005b0107f") + F) == [F]
    assert feed(F, bytes.fromhex("0102c000")) == [F]    # trailing noise yields nothing


def test_new_f0_abandons_an_unterminated_frame() -> None:
    assert feed(bytes.fromhex("f04110") + F) == [F]


def test_unterminated_frame_is_not_emitted() -> None:
    """A frame that never gets its F7 must not leak into the next one."""
    assert feed(bytes.fromhex("f0411001"), bytes.fromhex("c005"), F) == [F]


def test_overlong_frame_is_dropped() -> None:
    assert feed(b"\xf0" + b"\x01" * 3000 + b"\xf7", F) == [F]
