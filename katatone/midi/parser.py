"""Byte stream -> MIDI events.

ALSA rawmidi hands over bytes in arbitrary chunks, possibly with running
status, real-time bytes (clock, active sensing) in the middle of a message,
SysEx, and system-common messages. This parser keeps its state across
``feed()`` calls so a message split over two USB reads still decodes.
"""
from __future__ import annotations

from .events import (ChannelPressure, ControlChange, MidiEvent, NoteOff, NoteOn,
                     PitchBend, ProgramChange)

# Data bytes that follow each system-common status (0xF1-0xF6).
_COMMON_LEN = {0xF1: 1, 0xF2: 2, 0xF3: 1, 0xF4: 0, 0xF5: 0, 0xF6: 0}


def _data_len(status: int) -> int:
    """Number of data bytes after a channel status byte.

    Args:
        status: a status byte 0x80-0xEF.

    Returns:
        1 for program change and channel pressure, 2 for the rest.
    """
    return 1 if status & 0xF0 in (0xC0, 0xD0) else 2


class MidiParser:
    """Incremental MIDI parser; one instance per input device."""

    def __init__(self) -> None:
        """Start with no running status and no message in progress."""
        self._status = 0        # running status (0 = none)
        self._data: list[int] = []
        self._need = 0          # data bytes still wanted for the current message
        self._in_sysex = False
        self._skip = 0          # data bytes of a system-common message to discard

    def feed(self, data: bytes) -> list[MidiEvent]:
        """Consume bytes and return the events completed by them.

        Args:
            data: the next chunk read from the device.

        Returns:
            Events in arrival order (possibly empty).
        """
        out: list[MidiEvent] = []
        for b in data:
            if b >= 0xF8:
                # Real-time bytes may appear anywhere, even inside a message,
                # and must not disturb it.
                continue
            if b & 0x80:
                self._on_status(b)
                continue
            if self._in_sysex:
                continue
            if self._skip:
                self._skip -= 1
                continue
            if not self._status:
                continue        # stray data byte at stream start
            if self._need == 0:
                # Running status: a new message with the same status.
                self._need = _data_len(self._status)
                self._data = []
            self._data.append(b)
            self._need -= 1
            if self._need == 0:
                ev = self._build()
                if ev is not None:
                    out.append(ev)
        return out

    def _on_status(self, b: int) -> None:
        """Handle a non-real-time status byte (0x80-0xF7).

        Args:
            b: the status byte; it ends any SysEx and abandons a half message.
        """
        self._in_sysex = False
        self._skip = 0
        self._data = []
        if b == 0xF0:
            self._in_sysex = True
            self._status = 0
            self._need = 0
        elif b == 0xF7:
            self._status = 0
            self._need = 0
        elif b >= 0xF1:
            self._skip = _COMMON_LEN.get(b, 0)
            self._status = 0    # system common cancels running status
            self._need = 0
        else:
            self._status = b
            self._need = _data_len(b)

    def _build(self) -> MidiEvent | None:
        """Turn the completed channel message into an event.

        Returns:
            The event for `self._status` and `self._data`, or None for message
            types the router does not use (polyphonic aftertouch).
        """
        kind = self._status & 0xF0
        ch = self._status & 0x0F
        d = self._data
        if kind == 0x80:
            return NoteOff(ch, d[0], d[1])
        if kind == 0x90:
            # Many controllers send NoteOn velocity 0 as a release.
            return NoteOn(ch, d[0], d[1]) if d[1] else NoteOff(ch, d[0], 64)
        if kind == 0xB0:
            return ControlChange(ch, d[0], d[1])
        if kind == 0xC0:
            return ProgramChange(ch, d[0])
        if kind == 0xD0:
            return ChannelPressure(ch, d[0])
        if kind == 0xE0:
            return PitchBend(ch, ((d[1] << 7) | d[0]) - 8192)
        return None
