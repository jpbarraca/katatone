"""MIDI channel-message events produced by the parser and consumed by the router.

All events carry a 0-based MIDI channel ``ch``; the UI shows ``ch + 1``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class NoteOn:
    """A key/switch press. `velocity` is 1-127 (velocity 0 is parsed as NoteOff)."""

    ch: int
    note: int
    velocity: int


@dataclass(frozen=True)
class NoteOff:
    """A key/switch release."""

    ch: int
    note: int
    velocity: int = 64


@dataclass(frozen=True)
class ControlChange:
    """A continuous controller or switch message, `value` 0-127."""

    ch: int
    control: int
    value: int


@dataclass(frozen=True)
class ProgramChange:
    """A program change; pedals send one per press and no release."""

    ch: int
    program: int


@dataclass(frozen=True)
class PitchBend:
    """Pitch bend, `value` -8192..8191 (0 = centre)."""

    ch: int
    value: int


@dataclass(frozen=True)
class ChannelPressure:
    """Channel aftertouch, `value` 0-127."""

    ch: int
    value: int


MidiEvent = Union[NoteOn, NoteOff, ControlChange, ProgramChange, PitchBend,
                  ChannelPressure]

_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(note: int) -> str:
    """Name a MIDI note number, middle C (60) being C4.

    Args:
        note: MIDI note number 0-127.

    Returns:
        e.g. "C4", "F#2".
    """
    return f"{_NOTE_NAMES[note % 12]}{note // 12 - 1}"


def describe(ev: MidiEvent) -> str:
    """Render an event for the monitor, e.g. "CC 47 = 93 · ch 1".

    Args:
        ev: any parsed event.

    Returns:
        A short human-readable string; the channel is shown 1-based.
    """
    ch = f"ch {ev.ch + 1}"
    if isinstance(ev, NoteOn):
        return f"Note On {ev.note} ({note_name(ev.note)}) vel {ev.velocity} · {ch}"
    if isinstance(ev, NoteOff):
        return f"Note Off {ev.note} ({note_name(ev.note)}) · {ch}"
    if isinstance(ev, ControlChange):
        return f"CC {ev.control} = {ev.value} · {ch}"
    if isinstance(ev, ProgramChange):
        return f"PC {ev.program} · {ch}"
    if isinstance(ev, PitchBend):
        return f"Pitch bend {ev.value:+d} · {ch}"
    return f"Pressure {ev.value} · {ch}"
