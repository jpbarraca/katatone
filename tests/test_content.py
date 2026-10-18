"""Checking that the same kind of playing is being compared."""
from __future__ import annotations

import numpy as np

from katatone.audio import content
from synth import RATE, notes

LOW_E_RIFF = (40, 45, 50, 52)          # E2 A2 D3 E3
C_RIFF = (36, 43, 41, 48)              # C2 G2 F2 C3
HIGH_LEAD = (64, 69, 71, 76)           # E4 A4 B4 E5


def test_the_chroma_names_the_notes_played() -> None:
    c = content.analyse(notes(LOW_E_RIFF, count=8))
    assert abs(c.chroma.sum() - 1.0) < 1e-9 and c.chroma.shape == (12,)
    assert set(c.top_notes(3)) == {"E", "A", "D"}


def test_the_same_notes_in_another_timbre_match() -> None:
    a = content.analyse(notes(LOW_E_RIFF, harmonics=(1, 2, 3, 4)))
    b = content.analyse(notes(LOW_E_RIFF, harmonics=(1, 2), decay_s=0.1, seed=4))
    assert content.correlation(a, b) > 0.7
    assert content.warnings(a, b) == []


def test_another_key_is_called_different() -> None:
    a, c = content.analyse(notes(LOW_E_RIFF)), content.analyse(notes(C_RIFF))
    assert content.correlation(c, a) < content.MIN_CORRELATION
    (line,) = content.warnings(c, a)
    assert line.startswith("Different notes") and "C" in line


def test_another_register_is_called_different_even_with_the_same_notes() -> None:
    a, hi = content.analyse(notes(LOW_E_RIFF)), content.analyse(notes(HIGH_LEAD))
    assert content.register_octaves(hi, a) > content.MAX_REGISTER_OCTAVES
    assert any(line.startswith("Different register") and "higher" in line
               for line in content.warnings(hi, a))
    assert any("lower" in line for line in content.warnings(a, hi))


def test_too_little_or_no_sound_gives_nothing_and_no_warning() -> None:
    assert content.analyse(np.zeros(RATE * 3, dtype=np.float32)) is None
    assert content.analyse(notes()[:RATE // 2]) is None
    assert content.warnings(None, content.analyse(notes())) == []
