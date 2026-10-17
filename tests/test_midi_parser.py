"""MidiParser tests: message types, running status, interleaved real-time bytes."""
from __future__ import annotations

from pathlib import Path

from katatone.midi.events import (ChannelPressure, ControlChange, NoteOff, NoteOn,
                                  PitchBend, ProgramChange, describe)
from katatone.midi.parser import MidiParser


def parse(data: bytes) -> list:
    """Parse `data` with a fresh parser in one chunk."""
    return MidiParser().feed(data)


def test_each_message_type() -> None:
    """Every supported channel message decodes with the right channel and fields."""
    assert parse(b"\x93\x3c\x64") == [NoteOn(3, 60, 100)]
    assert parse(b"\x83\x3c\x10") == [NoteOff(3, 60, 0x10)]
    assert parse(b"\xb1\x2f\x5d") == [ControlChange(1, 47, 93)]
    assert parse(b"\xc2\x07") == [ProgramChange(2, 7)]
    assert parse(b"\xd0\x55") == [ChannelPressure(0, 0x55)]
    assert parse(b"\xe0\x00\x40") == [PitchBend(0, 0)]


def test_note_on_velocity_zero_is_note_off() -> None:
    """NoteOn with velocity 0 is a release."""
    assert parse(b"\x90\x3c\x00") == [NoteOff(0, 60, 64)]


def test_pitch_bend_extremes() -> None:
    """Pitch bend spans -8192..8191 with LSB first on the wire."""
    assert parse(b"\xe0\x00\x00") == [PitchBend(0, -8192)]
    assert parse(b"\xe0\x7f\x7f") == [PitchBend(0, 8191)]
    assert parse(b"\xe5\x01\x40") == [PitchBend(5, 1)]


def test_running_status() -> None:
    """Data bytes after a complete message reuse the previous status."""
    assert parse(b"\xb0\x14\x01\x14\x02\x15\x03") == [
        ControlChange(0, 20, 1), ControlChange(0, 20, 2), ControlChange(0, 21, 3)]
    assert parse(b"\xc0\x01\x02") == [ProgramChange(0, 1), ProgramChange(0, 2)]


def test_realtime_inside_message_is_ignored() -> None:
    """Clock / active sensing in the middle of a CC do not corrupt it."""
    assert parse(b"\xb0\xf8\x14\xfe\x7f") == [ControlChange(0, 20, 127)]
    assert parse(b"\xf8\xfe\xc0\x05") == [ProgramChange(0, 5)]


def test_sysex_is_skipped() -> None:
    """SysEx bytes (data and F7) produce nothing and do not leak into messages."""
    assert parse(b"\xf0\x41\x10\x01\x05\x07\x12\xf7\xc0\x03") == [ProgramChange(0, 3)]
    # Real-time inside SysEx does not end it.
    assert parse(b"\xf0\x41\xf8\x10\xf7\xc0\x03") == [ProgramChange(0, 3)]
    # A status byte without F7 also ends SysEx.
    assert parse(b"\xf0\x41\x10\xc0\x03") == [ProgramChange(0, 3)]


def test_system_common_clears_running_status() -> None:
    """After F1-F6 the old running status must not be reused for stray data."""
    assert parse(b"\xb0\x14\x01\xf3\x05\x14\x02") == [ControlChange(0, 20, 1)]
    assert parse(b"\xb0\x14\x01\xf2\x01\x02\x03") == [ControlChange(0, 20, 1)]
    assert parse(b"\xb0\x14\x01\xf6\x14\x02") == [ControlChange(0, 20, 1)]
    # A new status after the system message works normally.
    assert parse(b"\xf1\x10\xc0\x09") == [ProgramChange(0, 9)]


def test_leading_data_bytes_dropped() -> None:
    """Data bytes at stream start (no status seen) are ignored."""
    assert parse(b"\x14\x7f\xc0\x01") == [ProgramChange(0, 1)]


def test_polyphonic_aftertouch_ignored_but_consumed() -> None:
    """A0 is not routed, and its two data bytes do not become another message."""
    assert parse(b"\xa0\x3c\x40\xc0\x02") == [ProgramChange(0, 2)]


def test_message_split_across_feeds() -> None:
    """State survives between feed() calls."""
    p = MidiParser()
    assert p.feed(b"\xb0") == []
    assert p.feed(b"\x14") == []
    assert p.feed(b"\x7f\xc0") == [ControlChange(0, 20, 127)]
    assert p.feed(b"\x04") == [ProgramChange(0, 4)]
    # Running status continues across a chunk boundary.
    assert p.feed(b"\x05") == [ProgramChange(0, 5)]


def test_status_byte_abandons_incomplete_message() -> None:
    """A new status in the middle of a message discards the partial one."""
    assert parse(b"\xb0\x14\xc0\x02") == [ProgramChange(0, 2)]


def test_chocolate_plus_capture_fixture() -> None:
    """A captured burst (PC 0/2/4/6 on channel 1) decodes to four program changes."""
    data = bytes.fromhex(
        (Path(__file__).parent / "data" / "chocolate_plus.hex").read_text())
    assert parse(data) == [ProgramChange(0, n) for n in (0, 2, 4, 6)]


def test_describe() -> None:
    """The monitor text is 1-based on channel."""
    assert describe(ControlChange(0, 47, 93)) == "CC 47 = 93 · ch 1"
    assert describe(ProgramChange(0, 2)) == "PC 2 · ch 1"
    assert describe(NoteOn(15, 60, 100)) == "Note On 60 (C4) vel 100 · ch 16"
    assert describe(NoteOff(1, 61)) == "Note Off 61 (C#4) · ch 2"
    assert describe(PitchBend(0, -5)) == "Pitch bend -5 · ch 1"
    assert describe(ChannelPressure(2, 9)) == "Pressure 9 · ch 3"
