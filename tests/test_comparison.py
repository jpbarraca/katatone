"""The analyzer's comparison state: reference, takes A and B, level matching, hints."""
from __future__ import annotations

import numpy as np
import pytest

from katatone.audio import comparison as cmp_mod, dsp, reference as ref
from katatone.audio.comparison import Comparison

FREQS = dsp.Spectrum(48000, points=160).freqs


def levels(**bands):
    lv = np.full_like(FREQS, -60.0)
    for (name, lo, hi) in ref.EQ_BANDS:
        lv[ref.band_mask(FREQS, lo, hi)] = -60.0 + bands.get(name.lower(), 0.0)
    return lv


def reference_with(lv):
    return ref.Reference("song.wav", FREQS, lv, 30.0, 900.0, -6.0, ref.Gain("Crunch", 10.0), 120.0, 12.0,
                         ref.delay_times(120))


def test_what_can_be_compared_depends_on_what_is_held() -> None:
    c = Comparison()
    assert c.available() == []
    c.set_take("A", FREQS, levels())
    assert c.available() == ["A"]
    c.set_take("B", FREQS, levels())
    assert c.available() == ["A", "B", "AB"]
    c.reference = reference_with(levels())
    assert c.available() == ["ref", "A", "B", "AB"]
    c.clear_take("A")
    assert c.available() == ["ref", "B"]


def test_traces_are_lined_up_with_the_live_level_only_when_asked() -> None:
    c = Comparison()
    c.reference = reference_with(levels(bass=3.0) - 20.0)         # a much quieter recording
    live = levels()
    (name, tr), = c.traces(live, FREQS)
    assert name == "reference" and ref.level_offset(live, tr, FREQS) == pytest.approx(0.0, abs=1e-6)
    c.match_level = False
    (_, raw), = c.traces(live, FREQS)
    assert ref.level_offset(live, raw, FREQS) == pytest.approx(20.0 - 0.0, abs=2.0)


def test_difference_is_live_minus_the_reference_in_shape() -> None:
    c = Comparison()
    c.reference = reference_with(levels(treble=6.0) - 30.0)       # quiet, but 6 dB more treble
    diff, first, second = c.difference(levels(), FREQS)
    bands = ref.band_differences(diff, FREQS)
    assert bands["Treble"] < -3.0 and (first, second) == ("your tone", "the reference")
    assert c.difference(levels(), FREQS, "A") is None             # take A not captured yet


def test_a_versus_b_needs_no_live_signal() -> None:
    c = Comparison()
    c.set_take("A", FREQS, levels(bass=6.0))
    c.set_take("B", FREQS, levels())
    c.mode = "AB"
    diff, first, second = c.difference(np.full_like(FREQS, -120.0), FREQS)
    assert (first, second) == ("take A", "take B")
    assert ref.band_differences(diff, FREQS)["Bass"] > 2.0
    assert "lower Bass" in " ".join(c.hints(np.full_like(FREQS, -120.0), FREQS, listening=False))


def test_takes_follow_a_change_of_frequency_axis() -> None:
    c = Comparison()
    c.set_take("A", FREQS, np.log10(FREQS) * 10.0)
    linear = dsp.Spectrum(48000, points=100, linear=True).freqs
    (_, tr), = c.traces(np.log10(linear) * 10.0, linear)
    assert tr.shape == linear.shape and np.allclose(tr, np.log10(linear) * 10.0, atol=0.3)


def test_the_stored_take_is_a_copy() -> None:
    c = Comparison()
    lv = levels()
    c.set_take("A", FREQS, lv)
    lv[:] = 0.0
    assert c.takes["A"][1].max() == -60.0


def test_hints_lead_with_the_reference_then_say_what_to_turn() -> None:
    c = Comparison()
    live = levels()
    assert "Load a reference" in c.hints(live, FREQS, True)[0]
    c.reference = reference_with(levels(presence=8.0) - 25.0)
    text = "\n".join(c.hints(live, FREQS, listening=True))
    assert "Reference: song.wav" in text and "Tempo: about 120 BPM" in text
    assert "To bring your tone towards the reference" in text and "raise Presence" in text
    waiting = c.hints(live, FREQS, listening=False)
    assert "Start listening" in waiting[-1] and "raise Presence" not in " ".join(waiting)


def test_modes_name_the_things_compared() -> None:
    assert set(cmp_mod.MODES) == {"ref", "A", "B", "AB"}
    assert cmp_mod.MODES["AB"][:2] == ("A", "B")


# ---------------------------------------------- playing, notes and EQ proposals

from katatone.audio import content as content_mod, envelope as envelope_mod  # noqa: E402
from synth import notes  # noqa: E402


def insight(**kw):
    x = notes(**kw)
    return cmp_mod.Insight(envelope_mod.analyse(x), content_mod.analyse(x))


def test_hints_warn_first_when_the_playing_differs() -> None:
    c = Comparison()
    low = insight(pitches=(40, 45, 50, 52))
    c.reference = ref.Reference("s.wav", FREQS, levels(), 30.0, 900.0, -6.0, ref.Gain("Crunch", 10.0), 0.0, 0.0,
                                [], low.envelope, low.content)
    same = c.hints(levels(treble=6.0), FREQS, True, low)
    assert not any("playing differs" in line for line in same)
    other = c.hints(levels(treble=6.0), FREQS, True, insight(pitches=(64, 69, 71, 76)))
    k = next(i for i, line in enumerate(other) if "playing differs" in line)
    assert "Different register" in other[k + 1] and k < next(i for i, ln in enumerate(other) if ln.startswith("To bring"))


def test_hints_say_how_the_notes_differ() -> None:
    c = Comparison()
    ref_in = insight(decay_s=0.6)
    c.reference = ref.Reference("s.wav", FREQS, levels(), 30.0, 900.0, -6.0, ref.Gain("Crunch", 10.0), 0.0, 0.0,
                                [], ref_in.envelope, ref_in.content)
    lines = c.hints(levels(), FREQS, True, insight(decay_s=0.15))
    assert any("dies away faster" in line for line in lines)
    assert any(line.startswith("Notes (") for line in lines)                       # the reference's own line


def test_eq_fits_follow_the_difference_and_only_for_live_comparisons() -> None:
    c = Comparison()
    c.reference = reference_with(levels(treble=-6.0))        # the reference is duller than the live tone
    live = levels()
    fits = c.eq_fits(live, FREQS)
    assert set(fits) == {"GE-10", "PEQ"} and all(f.useful for f in fits.values())
    assert fits["GE-10"].response[ref.band_mask(FREQS, 2000, 5000)].mean() < -2.0       # cut the treble
    lines = c.hints(live, FREQS, True, None, fits)
    assert any("EQ settings that fit" in line for line in lines)
    assert any("Graphic EQ (GE-10)" in line for line in lines) and any("Parametric EQ" in line for line in lines)
    assert not any("EQ settings" in line for line in c.hints(live, FREQS, True, None, None))
    c.set_take("A", FREQS, levels())
    c.set_take("B", FREQS, levels(bass=4.0))
    c.mode = "AB"
    assert c.eq_fits(live, FREQS) is None                    # take A is not on the amp now


def test_takes_remember_what_was_played() -> None:
    c = Comparison()
    i = insight()
    c.set_take("A", FREQS, levels(), i)
    c.set_take("B", FREQS, levels(), insight(pitches=(36, 43, 41, 48)))
    c.mode = "AB"
    lines = c.hints(levels(), FREQS, False)
    assert any("Different notes" in line for line in lines)
    c.clear_take("A")
    assert "A" not in c.take_insight
