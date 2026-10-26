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


@pytest.mark.parametrize("tuning", list(pitch.TUNINGS))
def test_every_string_of_every_tuning_is_heard_and_read_in_tune(tuning) -> None:
    """Regression: the lowest bass strings were below the detector's range."""
    for note in pitch.TUNINGS[tuning]:
        f = pitch.midi_to_freq(pitch.note_to_midi(note))
        found = pitch.detect_pitch(tone(f), RATE)
        assert found is not None, note
        r = pitch.measure(found[0], pitch.TUNINGS[tuning])
        assert r.note == note and abs(r.cents) < 1.0 and r.in_tune, (tuning, note)


def test_the_alternate_tunings_are_the_usual_ones() -> None:
    notes = {name: " ".join(n[:-1] for n in strings) for name, strings in pitch.TUNINGS.items()}
    assert notes["Drop C"] == "C G C F A D"
    assert notes["Double drop D"] == "D A D G B D"
    assert notes["Open D"] == "D A D F# A D"
    assert notes["Open E"] == "E B E G# B E"
    assert notes["Open A"] == "E A E A C# E"
    assert notes["7-string (B E A D G B E)"] == "B E A D G B E"
    assert notes["Bass drop D"] == "D A D G"
    assert notes["5-string bass (B E A D G)"] == "B E A D G"


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


def test_the_tuner_looks_for_the_open_string_and_the_12th_fret_only_when_asked_and_not_chromatic() -> None:
    t = pitch.Tuner()
    assert t.frets == (0,)
    t.set_intonation(True)
    assert t.frets == (0, 12)
    t.set_mode(pitch.CHROMATIC)
    assert t.frets == (0,)


STD = pitch.TUNINGS["Standard (E A D G B E)"]


def test_both_the_open_string_and_its_octave_are_recognised() -> None:
    both = (0, pitch.INTONATION_FRET)
    o = pitch.measure(110.0, STD, fret=both)
    assert (o.note, o.string, o.fret) == ("A2", 1, 0) and abs(o.cents) < 0.01
    t = pitch.measure(220.0, STD, fret=both)
    assert (t.note, t.string, t.fret) == ("A3", 1, 12) and abs(t.cents) < 0.01
    assert t.open_freq is None                          # that string was not heard
    hi = pitch.measure(659.26, STD, fret=both)
    assert (hi.note, hi.fret) == ("E5", 12)             # the 12th fret of the high E


def test_the_12th_fret_is_judged_against_the_open_string_that_was_heard() -> None:
    both = (0, pitch.INTONATION_FRET)
    # the open A string is a little flat (108.9 Hz); its 12th fret at 220 Hz is then sharp of 2 x 108.9
    r = pitch.measure(220.0, STD, fret=both, opens={1: (108.9, 4.0)})
    assert r.fret == 12 and r.open_freq == 108.9 and r.open_age == 4.0
    assert r.target == pytest.approx(217.8) and r.cents == pytest.approx(pitch.cents_between(220.0, 217.8))
    # an octave exactly twice the open string is in tune, however flat the open string is
    assert pitch.measure(217.8, STD, fret=both, opens={1: (108.9, 4.0)}).in_tune
    # other strings are not affected
    assert pitch.measure(164.81, STD, fret=both, opens={1: (108.9, 4.0)}).open_freq is None


def test_a_locked_string_only_looks_at_its_own_notes() -> None:
    # in drop D the 12th fret of the low D equals the open D string: locking tells them apart
    drop_d = pitch.TUNINGS["Drop D"]
    both = (0, pitch.INTONATION_FRET)
    d3 = pitch.note_freq("D3")
    assert pitch.measure(d3, drop_d, fret=both).fret == 0 and pitch.measure(d3, drop_d, fret=both).string == 2
    r = pitch.measure(d3, drop_d, locked=0, fret=both)
    assert (r.string, r.fret, r.note) == (0, 12, "D3")


def _tuner_hearing(freqs, clock):
    """A tuner fed with stubbed detections, one per call of ``reading``."""
    t = pitch.Tuner(clock=clock)
    t.set_intonation(True)
    it = iter(freqs)
    pitch_stub = lambda buf, rate, *a, **k: (next(it), 1.0)          # noqa: E731
    return t, pitch_stub


def test_the_tuner_remembers_the_open_string_for_the_12th_fret(monkeypatch) -> None:
    now = [100.0]
    t, stub = _tuner_hearing([109.0, 109.0, 109.0, 218.6, 218.6, 218.6], lambda: now[0])
    monkeypatch.setattr(pitch, "detect_pitch", stub)
    for _ in range(3):
        r = t.reading()
        now[0] += 0.1
    assert (r.string, r.fret) == (1, 0) and r.open_freq is None
    for _ in range(3):
        r = t.reading()
        now[0] += 0.1
    assert (r.string, r.fret) == (1, 12) and r.open_freq == pytest.approx(109.0)
    assert r.target == pytest.approx(218.0) and r.cents == pytest.approx(pitch.cents_between(218.6, 218.0))
    assert 0.0 < r.open_age < 1.0


def test_an_open_string_heard_long_ago_is_forgotten(monkeypatch) -> None:
    now = [0.0]
    t, stub = _tuner_hearing([109.0, 218.0], lambda: now[0])
    monkeypatch.setattr(pitch, "detect_pitch", stub)
    t.reading()
    now[0] += pitch.OPEN_KEEP_S + 1
    r = t.reading()
    assert r.fret == 12 and r.open_freq is None and r.target == pytest.approx(pitch.note_freq("A3"))


def test_switching_the_check_off_or_changing_the_tuning_forgets_the_open_strings(monkeypatch) -> None:
    t = pitch.Tuner()
    t._opens[1] = (109.0, 0.0)
    t.set_intonation(False)
    assert t._opens == {}
    t._opens[1] = (109.0, 0.0)
    t.set_mode("Drop D")
    assert t._opens == {}
