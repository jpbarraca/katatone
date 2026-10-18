"""Analyzer audio: device list, capture reader and level / spectrum maths."""
from __future__ import annotations

import stat
import sys
import time

import numpy as np
import pytest

from katatone.audio import dsp
from katatone.audio.capture import Capture
from katatone.audio.devices import Device, parse_listing, preferred

LISTING = """\
null
    Discard all samples (playback) or generate zero samples (capture)
pipewire
    PipeWire Sound Server
sysdefault:CARD=KATANA3
    KATANA3, USB Audio
    Default Audio Device
front:CARD=KATANA3,DEV=0
    KATANA3, USB Audio
    Front output / input
sysdefault:CARD=SINCO
    SINCO, USB Audio
    Default Audio Device
"""


def test_listing_skips_null_and_keeps_descriptions() -> None:
    devs = parse_listing(LISTING)
    assert [d.name for d in devs] == ["pipewire", "sysdefault:CARD=KATANA3",
                                      "front:CARD=KATANA3,DEV=0",
                                      "sysdefault:CARD=SINCO"]
    assert "KATANA3, USB Audio, Default Audio Device" in devs[1].label


def test_preferred_is_the_cards_sysdefault() -> None:
    devs = parse_listing(LISTING)
    assert preferred(devs, "KATANA3").name == "sysdefault:CARD=KATANA3"
    assert preferred(devs, "NOPE").name == "pipewire"
    assert preferred([], "KATANA3") is None


def sine(f: float, amp: float, n: int = 8192, rate: int = 48000) -> np.ndarray:
    return (amp * np.sin(2 * np.pi * f * np.arange(n) / rate)).astype(np.float32)


def test_levels_of_a_known_sine() -> None:
    peak, rms = dsp.levels(sine(1000, 0.5))
    assert peak == pytest.approx(-6.02, abs=0.05)
    assert rms == pytest.approx(-9.03, abs=0.1)


def test_silence_reads_the_floor() -> None:
    assert dsp.levels(np.zeros(1000, dtype=np.float32)) == (dsp.FLOOR_DB, dsp.FLOOR_DB)
    assert dsp.levels(np.zeros(0, dtype=np.float32)) == (dsp.FLOOR_DB, dsp.FLOOR_DB)


@pytest.mark.parametrize("f", [100, 1000, 8000])
def test_spectrum_peaks_at_the_tone(f) -> None:
    s = dsp.Spectrum()
    s.push(sine(f, 0.5))
    spec = s.compute()
    i = int(np.argmax(spec))
    assert abs(np.log2(s.freqs[i] / f)) < 0.17         # one FFT bin is 0.16 octave at 100 Hz
    assert spec[i] == pytest.approx(-6.0, abs=1.6)     # Hann scalloping <= ~1.4 dB


def test_spectrum_push_keeps_the_latest_samples() -> None:
    s = dsp.Spectrum(n_fft=1024)
    s.push(np.ones(600, dtype=np.float32))
    s.push(np.full(600, 2.0, dtype=np.float32))
    assert s._buf[-1] == 2.0 and s._buf[0] == 1.0 and s._buf.size == 1024


def test_smoother_averages_and_holds_the_peak() -> None:
    sm = dsp.Smoother(3, alpha=0.5, hold_decay_db=1.0)
    sm.update(np.array([-10.0, -10.0, -10.0]))
    sm.update(np.array([-100.0, -100.0, -100.0]))
    assert sm.peak[0] == pytest.approx(-11.0)
    assert sm.avg[0] < -10.0 and sm.avg[0] > -100.0


def test_peak_hold_holds_then_falls() -> None:
    h = dsp.PeakHold(hold_updates=2, fall_db=1.0)
    assert h.update(-6.0) == -6.0
    assert h.update(-40.0) == -6.0 and h.update(-40.0) == -6.0   # held
    assert h.update(-40.0) == -7.0                                # now falling


def test_capture_reads_the_selected_channel(tmp_path, monkeypatch) -> None:
    """A fake arecord emits 2-channel frames; channel 2 is what is kept."""
    fake = tmp_path / "arecord"
    fake.write_text(f"""#!{sys.executable}
import sys, numpy as np
frames = np.zeros((2048, 2), dtype='<f4')
frames[:, 0] = 0.1
frames[:, 1] = 0.5
sys.stdout.buffer.write(frames.tobytes())
sys.stdout.buffer.flush()
""")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin")
    blocks, errors = [], []
    c = Capture("dev", 2, blocks.append, errors.append)
    c.start()
    deadline = time.time() + 5
    while len(blocks) < 2 and time.time() < deadline:
        time.sleep(0.02)
    c.stop()
    assert len(blocks) == 2 and np.allclose(blocks[0], 0.5)


def test_capture_reports_arecord_errors(tmp_path, monkeypatch) -> None:
    fake = tmp_path / "arecord"
    fake.write_text(f"#!{sys.executable}\nimport sys\nprint('no such device', file=sys.stderr)\nsys.exit(1)\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin")
    errors = []
    c = Capture("dev", 1, lambda b: None, errors.append)
    c.start()
    deadline = time.time() + 5
    while not errors and time.time() < deadline:
        time.sleep(0.02)
    c.stop()
    assert errors == ["no such device"]


def test_string_frequencies_are_standard_tuning() -> None:
    from katatone.audio import bands
    expected = {"E2": 82.41, "A2": 110.0, "D3": 146.83, "G3": 196.0,
                "B3": 246.94, "E4": 329.63}
    assert dict(bands.STRINGS) == expected
    # each string is the equal-tempered note: A4 = 440 Hz
    midi = {"E2": 40, "A2": 45, "D3": 50, "G3": 55, "B3": 59, "E4": 64}
    for name, f in bands.STRINGS:
        assert f == pytest.approx(440 * 2 ** ((midi[name] - 69) / 12), abs=0.01)


def test_tone_bands_are_contiguous_and_cover_the_display_range() -> None:
    from katatone.audio import bands
    edges = [(lo, hi) for _, lo, hi in bands.TONE_BANDS]
    assert edges[0][0] == dsp.F_MIN and edges[-1][1] == dsp.F_MAX
    assert all(a[1] == b[0] for a, b in zip(edges, edges[1:]))
    assert all(lo < hi for lo, hi in edges)
    names = {n for n, _, _ in bands.TONE_BANDS}
    assert {"Muddy", "Boxy", "Airy"} <= names


def test_linear_spectrum_has_evenly_spaced_points_and_finds_the_tone() -> None:
    s = dsp.Spectrum(points=200, linear=True)
    steps = np.diff(s.freqs)
    assert np.allclose(steps, steps[0])
    s.push(sine(5000, 0.5))
    spec = s.compute()
    assert abs(s.freqs[int(np.argmax(spec))] - 5000) < 120


def test_each_string_spans_its_open_note_to_its_highest_fret() -> None:
    from katatone.audio import bands
    rng = {name: (f_lo, top, f_hi) for name, f_lo, top, f_hi in bands.string_ranges()}
    assert rng["E2"][1] == "D4" and rng["E4"][1] == "D6" and rng["A2"][1] == "G4"
    for name, f in bands.STRINGS:
        f_lo, _, f_hi = rng[name]
        assert f_lo == f and f_hi == pytest.approx(f * 2 ** (bands.FRETS / 12))
    assert rng["E2"][2] == pytest.approx(293.66, abs=0.1)         # D4
    assert bands.string_ranges(12)[0][2:] == ("E3", pytest.approx(164.82, abs=0.05))


def test_note_names_wrap_octaves() -> None:
    from katatone.audio import bands
    assert bands.note_above("B3", 1) == "C4" and bands.note_above("E4", 0) == "E4"
    assert bands.note_above("G3", 22) == "F5"
