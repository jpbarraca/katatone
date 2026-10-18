"""Pitch detection, note maths and the tuner's steadiness."""
from __future__ import annotations

import numpy as np
import pytest

from katatone.audio import bands, pitch

RATE = 48000


def tone(f, n=4096, harmonics=(1.0, 0.6, 0.4, 0.25, 0.15), amp=0.3, phase=0.0, decay=0.0):
    t = np.arange(n) / RATE
    env = np.exp(-decay * t)
    return (amp * env * sum(a * np.sin(2 * np.pi * f * (k + 1) * t + phase)
                            for k, a in enumerate(harmonics))).astype(np.float32)


@pytest.mark.parametrize("name,f", bands.STRINGS)
def test_every_open_string_is_found_within_a_cent(name, f) -> None:
    found = pitch.detect_pitch(tone(f), RATE)
    assert found is not None
    assert abs(pitch.cents_between(found[0], f)) < 1.0 and found[1] > 0.9


@pytest.mark.parametrize("f", [61.7, 98.0, 440.0, 880.0, 1175.0])
def test_other_notes_and_the_ends_of_the_range(f) -> None:
    found = pitch.detect_pitch(tone(f), RATE)
    assert found is not None and abs(pitch.cents_between(found[0], f)) < 2.0


def test_a_weak_or_missing_fundamental_is_not_an_octave_error() -> None:
    # a pickup near the bridge: the 110 Hz fundamental is the weakest component
    found = pitch.detect_pitch(tone(110, harmonics=(0.05, 1.0, 0.8, 0.6, 0.4)), RATE)
    assert found is not None and abs(pitch.cents_between(found[0], 110)) < 2.0
    found = pitch.detect_pitch(tone(110, harmonics=(0, 1.0, 0.8, 0.6)), RATE)
    assert found is not None and abs(pitch.cents_between(found[0], 110)) < 2.0


def test_a_decaying_note_with_a_dc_offset_and_noise() -> None:
    rng = np.random.default_rng(1)
    x = tone(82.41, decay=3.0, phase=1.3) + 0.02 + rng.normal(0, 0.003, 4096).astype(np.float32)
    found = pitch.detect_pitch(x, RATE)
    assert found is not None and abs(pitch.cents_between(found[0], 82.41)) < 3.0


def test_sharp_and_flat_notes_read_their_offset() -> None:
    for cents in (-30, -8, 8, 30):
        f = 110 * 2 ** (cents / 1200)
        found = pitch.detect_pitch(tone(f), RATE)
        assert pitch.cents_between(found[0], 110) == pytest.approx(cents, abs=1.0)


def test_silence_noise_and_quiet_input_have_no_pitch() -> None:
    rng = np.random.default_rng(2)
    assert pitch.detect_pitch(np.zeros(4096, np.float32), RATE) is None
    assert pitch.detect_pitch(rng.normal(0, 0.1, 4096).astype(np.float32), RATE) is None
    assert pitch.detect_pitch(tone(110, amp=0.0005), RATE) is None      # below the gate
    assert pitch.detect_pitch(np.zeros(100, np.float32), RATE) is None  # too short


def test_note_maths() -> None:
    assert pitch.note_to_midi("A4") == 69 and pitch.note_to_midi("E2") == 40
    assert pitch.note_to_midi("D#3") == 51 and pitch.midi_to_name(51) == "D#3"
    assert pitch.note_freq("A4") == 440 and pitch.note_freq("A4", 432) == 432
    assert pitch.note_freq("E2") == pytest.approx(82.41, abs=0.01)
    assert pitch.cents_between(440 * 2 ** (1 / 12), 440) == pytest.approx(100)
    assert pitch.cents_between(220, 440) == pytest.approx(-1200)


def test_every_tuning_has_valid_notes_in_ascending_order() -> None:
    for name, strings in pitch.TUNINGS.items():
        midis = [pitch.note_to_midi(n) for n in strings]
        assert midis == sorted(midis) and len(set(midis)) == len(midis), name
    assert pitch.TUNINGS["Standard (E A D G B E)"] == tuple(n for n, _ in bands.STRINGS)


def test_chromatic_reads_the_nearest_semitone() -> None:
    r = pitch.measure(445, None)
    assert (r.note, r.string) == ("A4", None) and r.cents == pytest.approx(19.56, abs=0.05)
    assert pitch.measure(430, None).note == "A4" and pitch.measure(430, None).cents < 0
    assert pitch.measure(466.16, None).note == "A#4"


def test_a_tuning_picks_the_nearest_string_or_the_locked_one() -> None:
    std = pitch.TUNINGS["Standard (E A D G B E)"]
    r = pitch.measure(108.0, std)                      # a flat A
    assert (r.note, r.string) == ("A2", 1) and r.cents < 0 and not r.in_tune
    locked = pitch.measure(108.0, std, locked=0)       # tuning the low E regardless
    assert (locked.note, locked.string) == ("E2", 0) and locked.cents > 100


def test_reference_pitch_moves_the_targets() -> None:
    std = pitch.TUNINGS["Standard (E A D G B E)"]
    a = pitch.measure(110.0, std, a4=440)
    b = pitch.measure(110.0, std, a4=432)
    assert a.in_tune and not b.in_tune and b.cents == pytest.approx(31.8, abs=0.2)


def test_in_tune_window() -> None:
    std = pitch.TUNINGS["Standard (E A D G B E)"]
    assert pitch.measure(110 * 2 ** (4 / 1200), std).in_tune
    assert not pitch.measure(110 * 2 ** (7 / 1200), std).in_tune


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def feed(tuner, x):
    for i in range(0, x.size, 1024):
        tuner.push(x[i:i + 1024])


def test_tuner_gives_a_steady_reading_and_holds_it_briefly() -> None:
    clock = Clock()
    t = pitch.Tuner(RATE, clock=clock)
    assert t.reading() is None
    feed(t, tone(110.0, n=8192))
    r = t.reading()
    assert r.note == "A2" and r.in_tune and r.string == 1
    feed(t, np.zeros(8192, np.float32))                # the sound dies away
    clock.t = 0.5
    assert t.reading().note == "A2"                    # held
    clock.t = pitch.HOLD_S + 0.1
    assert t.reading() is None


def test_one_stray_detection_does_not_move_the_reading() -> None:
    t = pitch.Tuner(RATE, clock=Clock())
    for _ in range(4):
        feed(t, tone(110.0, n=8192))
        t.reading()
    feed(t, tone(116.0, n=8192))                       # one odd block
    assert abs(pitch.cents_between(t.reading().freq, 110)) < 2


def test_a_new_note_replaces_the_old_one_at_once() -> None:
    t = pitch.Tuner(RATE, clock=Clock())
    for _ in range(4):
        feed(t, tone(110.0, n=8192))
        t.reading()
    feed(t, tone(146.83, n=8192))
    assert t.reading().note == "D3"


def test_changing_mode_releases_the_string_lock_and_chromatic_has_no_strings() -> None:
    t = pitch.Tuner(RATE, clock=Clock())
    t.locked = 3
    t.set_mode("Drop D")
    assert t.locked is None and t.tuning[0] == "D2"
    t.set_mode(pitch.CHROMATIC)
    assert t.tuning is None
    feed(t, tone(110.0, n=8192))
    assert t.reading().string is None


def test_the_intonation_check_compares_with_the_octave_of_each_string() -> None:
    std = pitch.TUNINGS["Standard (E A D G B E)"]
    r = pitch.measure(220.0, std, fret=pitch.INTONATION_FRET)
    assert (r.note, r.string, r.fret) == ("A3", 1, 12) and abs(r.cents) < 0.01
    sharp = pitch.measure(220.0 * 2 ** (8 / 1200), std, fret=12)
    assert sharp.cents == pytest.approx(8) and not sharp.in_tune
    assert pitch.measure(220.0, None, fret=12).fret == 0           # chromatic: no octaves


def test_the_tuner_measures_at_the_12th_fret_only_when_asked_and_not_chromatic() -> None:
    t = pitch.Tuner()
    assert t.fret == 0
    t.intonation = True
    assert t.fret == 12
    t.set_mode(pitch.CHROMATIC)
    assert t.fret == 0
