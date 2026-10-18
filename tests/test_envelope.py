"""How notes begin and fade: attack, decay, sustain and pick brightness."""
from __future__ import annotations

import numpy as np
import pytest

from katatone.audio import envelope
from synth import RATE, notes


def test_the_decay_slope_matches_the_notes_that_made_it() -> None:
    e = envelope.analyse(notes(decay_s=0.15))
    assert e.notes >= 5
    assert e.decay_db_s == pytest.approx(-8.69 / 0.15, rel=0.15)
    slow = envelope.analyse(notes(decay_s=0.6))
    assert slow.decay_db_s == pytest.approx(-8.69 / 0.6, rel=0.15)
    assert slow.sustain_db > e.sustain_db            # more is left after 300 ms


def test_a_hard_pick_makes_the_attack_brighter_than_the_sustain() -> None:
    soft = envelope.analyse(notes(pick=0.0))
    hard = envelope.analyse(notes(pick=0.6))
    assert hard.pick_octaves > soft.pick_octaves + 0.3
    assert hard.attack_hz > hard.sustain_hz


def test_noise_and_silence_have_no_notes() -> None:
    rng = np.random.default_rng(1)
    assert envelope.analyse((rng.standard_normal(RATE * 3) * 0.01).astype(np.float32)) is None
    assert envelope.analyse(np.zeros(RATE * 3, dtype=np.float32)) is None
    assert envelope.analyse(np.zeros(10, dtype=np.float32)) is None


def test_comparing_says_how_the_notes_differ() -> None:
    short, long_ = envelope.analyse(notes(decay_s=0.15)), envelope.analyse(notes(decay_s=0.6))
    out = envelope.compare(short, long_)
    assert any("dies away faster" in line for line in out)
    assert any("sustains longer" in line for line in envelope.compare(long_, short))
    assert envelope.compare(short, short) == []
    bright = envelope.analyse(notes(pick=0.6))
    assert any("Pick" in line and "brighter" in line
               for line in envelope.compare(bright, envelope.analyse(notes(pick=0.0))))


def test_the_summary_is_one_readable_line() -> None:
    line = envelope.analyse(notes()).summary()
    assert "attack" in line and "dB/s" in line and "\n" not in line
