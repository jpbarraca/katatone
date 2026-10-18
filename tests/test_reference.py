"""Reference recordings: loading, the long-term spectrum, differences, hints and tempo."""
from __future__ import annotations

import struct
import wave

import numpy as np
import pytest

from katatone.audio import dsp, reference as ref

RATE = 48000


def sine(f, seconds=2.0, amp=0.3):
    t = np.arange(int(RATE * seconds)) / RATE
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)


def write_wav(path, x, width=2, rate=RATE, channels=1):
    x = np.clip(x, -1, 1)
    if width == 2:
        raw = (x * 32767).astype("<i2").tobytes()
    elif width == 3:
        v = (x * (2 ** 23 - 1)).astype(np.int32)
        raw = b"".join(struct.pack("<i", int(i))[:3] for i in v)
    elif width == 4:
        raw = (x * (2 ** 31 - 1)).astype("<i4").tobytes()
    else:
        raw = ((x * 127) + 128).astype(np.uint8).tobytes()
    if channels == 2:
        raw = b"".join(raw[i:i + width] * 2 for i in range(0, len(raw), width))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(raw)


@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_pcm_wavs_of_every_width_load(tmp_path, width) -> None:
    p = tmp_path / "a.wav"
    write_wav(p, sine(440), width)
    x = ref.load_audio(p)
    assert x.size == RATE * 2 and abs(np.max(np.abs(x)) - 0.3) < 0.02


def test_stereo_is_mixed_to_mono_and_other_rates_are_resampled(tmp_path) -> None:
    p = tmp_path / "s.wav"
    write_wav(p, sine(440, 1.0), 2, rate=44100, channels=2)
    x = ref.load_audio(p)
    # 48000 samples declared as 44.1 kHz last 1.088 s, which is 52245 samples at 48 kHz
    assert abs(x.size - 48000 * 48000 / 44100) <= 2 and abs(np.max(np.abs(x)) - 0.3) < 0.02


def test_a_missing_or_undecodable_file_is_an_audio_error(tmp_path) -> None:
    with pytest.raises(ref.AudioError, match="no such file"):
        ref.load_audio(tmp_path / "missing.wav")
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not audio at all")
    with pytest.raises(ref.AudioError):
        ref.load_audio(bad)


def test_other_formats_need_ffmpeg(tmp_path, monkeypatch) -> None:
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    monkeypatch.setattr(ref.shutil, "which", lambda n: None)
    with pytest.raises(ref.AudioError, match="ffmpeg"):
        ref.load_audio(f)


def test_a_sine_shows_up_at_its_frequency_and_pauses_are_ignored() -> None:
    quiet = np.zeros(RATE * 2, np.float32)
    x = np.concatenate([sine(1000, 2.0), quiet, sine(1000, 2.0)])
    freqs, levels = ref.long_term_spectrum(x, RATE)
    peak = freqs[int(np.argmax(levels))]
    assert 900 < peak < 1100
    assert levels.max() == pytest.approx(20 * np.log10(0.3), abs=1.5)   # silence did not dilute it


def test_silence_has_no_spectrum() -> None:
    freqs, levels = ref.long_term_spectrum(np.zeros(RATE, np.float32), RATE)
    assert np.all(levels == dsp.FLOOR_DB)


def test_level_matching_removes_loudness_but_keeps_shape() -> None:
    freqs, a = ref.long_term_spectrum(sine(1000), RATE)
    louder = a + 10.0
    assert ref.level_offset(louder, a, freqs) == pytest.approx(10.0)
    d = ref.difference(louder, a, freqs, match_level=True)
    assert np.allclose(d[ref.band_mask(freqs, *ref.MATCH_BAND)], 0.0, atol=1e-6)
    assert np.allclose(ref.difference(louder, a, freqs, match_level=False), 10.0)


def make_levels(freqs, bass=0.0, middle=0.0, treble=0.0, presence=0.0):
    lv = np.zeros_like(freqs)
    for (name, lo, hi), db in zip(ref.EQ_BANDS, (bass, middle, treble, presence)):
        lv[ref.band_mask(freqs, lo, hi)] = db
    return lv


def test_band_differences_and_suggestions() -> None:
    freqs = dsp.Spectrum(RATE, points=160).freqs
    mine = make_levels(freqs, bass=6.0, middle=0.0, treble=-9.0, presence=0.5)
    target = make_levels(freqs)
    diff = mine - target
    bands = ref.band_differences(diff, freqs)
    assert bands["Bass"] == pytest.approx(6.0) and bands["Treble"] == pytest.approx(-9.0)
    lines = ref.suggestions(diff, freqs)
    assert lines[0].startswith("Treble") and "raise Treble" in lines[0] and "a lot" in lines[0]
    assert lines[1].startswith("Bass") and "lower Bass" in lines[1] and "clearly" in lines[1]
    assert len(lines) == 2                                        # Middle and Presence are close
    assert "close" in ref.suggestions(np.zeros_like(freqs), freqs)[0]


def test_tilt_and_centroid_follow_the_brightness() -> None:
    freqs = dsp.Spectrum(RATE, points=160).freqs
    dark = -6.0 * np.log2(freqs / 100.0)
    bright = -2.0 * np.log2(freqs / 100.0)
    assert ref.tilt_db_per_octave(dark, freqs) == pytest.approx(-6.0, abs=0.2)
    assert ref.tilt_db_per_octave(bright, freqs) == pytest.approx(-2.0, abs=0.2)
    assert ref.centroid_hz(bright, freqs) > ref.centroid_hz(dark, freqs)


def test_gain_character_from_the_crest_factor() -> None:
    n = RATE * 2
    t = np.arange(n) / RATE
    clean = (0.1 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 2 * t) > 0.9)).astype(np.float32)
    clean[::997] = 0.6                                   # picking peaks
    squashed = np.clip(3.0 * np.sin(2 * np.pi * 220 * t), -0.5, 0.5).astype(np.float32)
    assert ref.gain_character(clean).label in ("Clean", "Edge of breakup")
    assert ref.gain_character(squashed).label == "High gain"
    assert ref.gain_character(np.zeros(n, np.float32)).label == "silence"


@pytest.mark.parametrize("bpm", [90, 120, 140])
def test_tempo_of_a_pulse(bpm) -> None:
    n = RATE * 20
    x = np.zeros(n, np.float32)
    step = 60.0 / bpm
    t = np.arange(2000) / RATE
    for k in range(int(20 / step)):
        i = int(k * step * RATE)
        x[i:i + 2000] += (np.hanning(2000) * np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)[:max(0, n - i)][:2000]
    found, confidence = ref.estimate_tempo(x, RATE)
    assert found == pytest.approx(bpm, abs=2.0) and confidence >= ref.PULSE_CONFIDENCE


def test_noise_has_no_clear_pulse() -> None:
    x = np.random.default_rng(3).normal(0, 0.1, RATE * 10).astype(np.float32)
    assert ref.estimate_tempo(x, RATE)[1] < ref.PULSE_CONFIDENCE
    assert ref.estimate_tempo(np.zeros(100, np.float32)) == (0.0, 0.0)


def test_delay_times_follow_the_tempo() -> None:
    d = dict(ref.delay_times(120))
    assert d == {"quarter": 500, "dotted eighth": 375, "eighth": 250, "eighth triplet": 167, "half": 1000}
    assert ref.delay_times(0) == []


def test_tap_tempo() -> None:
    tap = ref.TapTempo()
    assert tap.tap(0.0) is None
    assert tap.tap(0.5) == pytest.approx(120.0)
    assert tap.tap(1.0) == pytest.approx(120.0)
    assert tap.tap(1.52) == pytest.approx(120.0, rel=0.05)       # one late tap barely moves it
    assert tap.tap(10.0) is None                                 # a pause starts over


def test_analyse_reads_a_file_end_to_end(tmp_path) -> None:
    p = tmp_path / "part.wav"
    t = np.arange(RATE * 6) / RATE
    x = (0.3 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
    write_wav(p, x)
    r = ref.analyse(p)
    assert r.name == "part.wav" and r.seconds == pytest.approx(6.0)
    assert 250 < r.centroid < 450 and len(r.levels) == len(r.freqs) == 160
    assert "Reference: part.wav" in r.summary()[0] and "Tempo:" in r.summary()[-1]
    silent = tmp_path / "silent.wav"
    write_wav(silent, np.zeros(RATE * 2, np.float32))
    with pytest.raises(ref.AudioError, match="no sound"):
        ref.analyse(silent)
    short = tmp_path / "short.wav"
    write_wav(short, np.zeros(100, np.float32))
    with pytest.raises(ref.AudioError, match="too short"):
        ref.analyse(short)


def test_a_reference_of_notes_reports_how_they_behave_and_what_is_played(tmp_path) -> None:
    import wave
    from synth import notes
    x = notes(decay_s=0.3)
    p = tmp_path / "riff.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    r = ref.analyse(p)
    assert r.envelope is not None and r.envelope.decay_db_s < -10
    assert r.content is not None and set(r.content.top_notes(3)) == {"E", "A", "D"}
    text = "\n".join(r.summary())
    assert "attack" in text and "Plays mostly" in text
