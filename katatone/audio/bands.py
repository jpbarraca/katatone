"""Reference frequencies drawn over the analyzer's spectrum."""
from __future__ import annotations

#: Open strings of a guitar in standard tuning (name, Hz), low to high.
STRINGS = (("E2", 82.41), ("A2", 110.00), ("D3", 146.83),
           ("G3", 196.00), ("B3", 246.94), ("E4", 329.63))

#: Frets of the guitar whose range the string lines show (the highest note a
#: string plays is this many semitones above its open note).
FRETS = 22

_NOTES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_above(name: str, semitones: int) -> str:
    """The note ``semitones`` above ``name`` (``"E2"`` + 22 = ``"D4"``)."""
    midi = _NOTES.index(name[:-1]) + 12 * (int(name[-1]) + 1) + semitones
    return f"{_NOTES[midi % 12]}{midi // 12 - 1}"


def string_ranges(frets: int = FRETS) -> "tuple[tuple[str, float, str, float], ...]":
    """The lowest and highest note of each string.

    Args:
        frets: how many frets the guitar has.
    Returns:
        ``(open note, its Hz, highest note, its Hz)`` for each string, low to high.
    """
    return tuple((name, f, note_above(name, frets), f * 2 ** (frets / 12))
                 for name, f in STRINGS)


#: Typical frequency ranges (name, low Hz, high Hz) engineers associate with
#: how a guitar sounds.  They are rules of thumb, not exact boundaries:
#: different sources move each edge by a few tens of percent.
TONE_BANDS = (
    ("Rumble", 20, 60),
    ("Boomy", 60, 120),
    ("Warm", 120, 250),
    ("Muddy", 250, 500),
    ("Boxy", 500, 1000),
    ("Honky", 1000, 2000),
    ("Present", 2000, 4000),
    ("Harsh / fizzy", 4000, 8000),
    ("Airy", 8000, 20000),
)
