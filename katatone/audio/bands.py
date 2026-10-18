"""Reference frequencies drawn over the analyzer's spectrum."""
from __future__ import annotations

#: Open strings of a guitar in standard tuning (name, Hz), low to high.
STRINGS = (("E2", 82.41), ("A2", 110.00), ("D3", 146.83),
           ("G3", 196.00), ("B3", 246.94), ("E4", 329.63))

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
