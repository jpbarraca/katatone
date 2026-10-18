"""Comparing a tone with a reference recording (numpy).

The reference is an audio file of the part to match.  Its long-term average
spectrum is computed on the same display points as the analyzer's live spectrum
(:class:`~katatone.audio.dsp.Spectrum`), so the two can be drawn together and
subtracted.  From the same samples a few descriptors are estimated: brightness,
a rough idea of how much gain the guitar has, and the tempo (for delay times).
Everything here is a hint, not a measurement of the original gear.
"""
from __future__ import annotations

import shutil
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import dsp
from .capture import RATE

#: Longest part of a file that is analysed.
MAX_SECONDS = 240
#: Frames quieter than this (dBFS RMS), or this far below the loudest frame, are
#: pauses and are left out of the average.
GATE_ABS_DB, GATE_REL_DB = -70.0, 40.0
#: The band used to line two spectra up in level (the guitar's body).
MATCH_BAND = (100.0, 5000.0)
#: Tempo confidence from which a pulse counts as found (noise gives 3 to 4).
PULSE_CONFIDENCE = 6.0
#: The Katana's tone controls, as frequency bands.
EQ_BANDS = (("Bass", 80.0, 250.0), ("Middle", 250.0, 2000.0),
            ("Treble", 2000.0, 5000.0), ("Presence", 5000.0, 10000.0))
AUDIO_TYPES = ("wav", "flac", "mp3", "ogg", "m4a", "aac", "opus")


class AudioError(RuntimeError):
    """The file cannot be read."""


# --------------------------------------------------------------- loading

def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Mono float samples of a PCM WAV file (8, 16, 24 or 32 bit)."""
    with wave.open(str(path), "rb") as w:
        ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(min(n, int(MAX_SECONDS * w.getframerate())))
    if width == 1:
        data = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        data = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        data = np.where(v >= 1 << 23, v - (1 << 24), v).astype(np.float32) / (1 << 23)
    elif width == 4:
        data = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648.0
    else:
        raise AudioError(f"unsupported WAV sample width: {width}")
    return data.reshape(-1, ch).mean(axis=1), rate


def _resample(x: np.ndarray, rate: int, target: int) -> np.ndarray:
    if rate == target or x.size == 0:
        return x
    n = int(round(x.size * target / rate))
    return np.interp(np.linspace(0, x.size - 1, n), np.arange(x.size), x).astype(np.float32)


def load_audio(path: str | Path, rate: int = RATE) -> np.ndarray:
    """Mono float32 samples of an audio file at ``rate``.

    WAV files are read directly; other formats (MP3, FLAC, OGG, M4A ...) through
    ``ffmpeg`` when it is installed.  At most :data:`MAX_SECONDS` are read.

    Raises:
        AudioError: if the file cannot be read, with what to do about it.
    """
    path = Path(path)
    if not path.is_file():
        raise AudioError(f"{path.name}: no such file")
    if path.suffix.lower() == ".wav":
        try:
            x, r = _read_wav(path)
            return _resample(x, r, rate).astype(np.float32)
        except (wave.Error, EOFError, AudioError):
            pass                                    # e.g. float WAV: let ffmpeg try
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise AudioError(f"{path.name}: needs ffmpeg for this format (or use a PCM WAV file)")
    p = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-t", str(MAX_SECONDS),
                        "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"],
                       capture_output=True, timeout=120)
    if p.returncode != 0 or not p.stdout:
        msg = p.stderr.decode(errors="replace").strip().splitlines()
        raise AudioError(f"{path.name}: cannot be decoded" + (f" ({msg[-1]})" if msg else ""))
    return np.frombuffer(p.stdout, dtype="<f4").astype(np.float32)


# -------------------------------------------------------------- spectrum

def long_term_spectrum(samples: np.ndarray, rate: int = RATE, points: int = 160,
                       linear: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """The average spectrum of a recording, on the analyzer's display points.

    Frames are windowed like the live spectrum; pauses are left out and the rest
    averaged in dB (as the live average is).

    Returns:
        ``(frequencies, levels in dBFS)``; the levels are the noise floor when
        the recording is silent.
    """
    spec = dsp.Spectrum(rate, points=points, linear=linear)
    hop = spec.n_fft // 2
    frames = []
    for start in range(0, max(1, samples.size - spec.n_fft + 1), hop):
        frame = samples[start:start + spec.n_fft]
        if frame.size < spec.n_fft:
            frame = np.pad(frame, (0, spec.n_fft - frame.size))
        frames.append(frame)
    rms = np.array([dsp.levels(f)[1] for f in frames])
    keep = rms >= max(GATE_ABS_DB, rms.max() - GATE_REL_DB)
    if not keep.any():
        return spec.freqs, np.full(points, dsp.FLOOR_DB)
    acc = np.zeros(points)
    for frame in (f for f, k in zip(frames, keep) if k):
        spec.push(frame)
        acc += spec.compute()
    return spec.freqs, acc / keep.sum()


def band_mask(freqs: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Which display points lie in ``lo`` .. ``hi`` Hz."""
    return (freqs >= lo) & (freqs <= hi)


def level_offset(a: np.ndarray, b: np.ndarray, freqs: np.ndarray,
                 band: tuple[float, float] = MATCH_BAND) -> float:
    """How many dB louder ``a`` is than ``b`` over ``band`` (their mean difference)."""
    m = band_mask(freqs, *band)
    return float(np.mean(a[m] - b[m])) if m.any() else 0.0


def difference(a: np.ndarray, b: np.ndarray, freqs: np.ndarray,
               match_level: bool = True) -> np.ndarray:
    """``a - b`` in dB; with ``match_level`` the overall level difference is taken
    out, so only the shape (the tone, not the loudness) is compared."""
    d = a - b
    return d - level_offset(a, b, freqs) if match_level else d


def band_differences(diff: np.ndarray, freqs: np.ndarray) -> dict[str, float]:
    """The mean of a difference curve in each of the Katana's tone bands."""
    out = {}
    for name, lo, hi in EQ_BANDS:
        m = band_mask(freqs, lo, hi)
        out[name] = float(np.mean(diff[m])) if m.any() else 0.0
    return out


def _amount(db: float) -> str:
    db = abs(db)
    return "a little" if db < 4 else "clearly" if db < 8 else "a lot"


def suggestions(diff: np.ndarray, freqs: np.ndarray, first: str = "yours",
                second: str = "the reference") -> list[str]:
    """Plain-language hints from ``diff`` (``first`` minus ``second``): what to turn
    to bring ``first`` towards ``second``.  Bands within 1.5 dB are not mentioned.

    Returns:
        Hints, the largest difference first; a single "close" line when nothing stands out.
    """
    worst = sorted(band_differences(diff, freqs).items(), key=lambda kv: -abs(kv[1]))
    lines = []
    for name, d in worst:
        if abs(d) < 1.5:
            continue
        verb = "less" if d > 0 else "more"
        lines.append(f"{name}: {first} has {_amount(d)} {verb} ({d:+.1f} dB): "
                     f"{'lower' if d > 0 else 'raise'} {name}")
    return lines or [f"The shape of {first} is close to {second} in all four bands."]


# ------------------------------------------------------------ descriptors

def tilt_db_per_octave(levels: np.ndarray, freqs: np.ndarray, lo: float = 100.0,
                       hi: float = 8000.0) -> float:
    """Slope of the spectrum in dB per octave between ``lo`` and ``hi`` (negative:
    darker; a typical amplified guitar is around -5 to -8)."""
    m = band_mask(freqs, lo, hi)
    if m.sum() < 3:
        return 0.0
    return float(np.polyfit(np.log2(freqs[m]), levels[m], 1)[0])


def centroid_hz(levels: np.ndarray, freqs: np.ndarray) -> float:
    """Spectral centroid (the 'centre of gravity' of the sound, in Hz) over 80 Hz .. 10 kHz."""
    m = band_mask(freqs, 80.0, 10000.0)
    power = 10.0 ** (levels[m] / 10.0)
    return float(np.sum(freqs[m] * power) / np.sum(power)) if power.sum() > 0 else 0.0


@dataclass
class Gain:
    """A rough reading of how driven the guitar is."""
    label: str
    crest_db: float


def gain_character(samples: np.ndarray, rate: int = RATE) -> Gain:
    """Estimate the amount of gain from the crest factor (peak over RMS, in dB) of the
    loud parts: a clean guitar has sharp picking peaks, a distorted one is squashed.
    A guess from one number, useful as a starting point only."""
    n = 4096
    crests = []
    for start in range(0, max(1, samples.size - n + 1), n):
        f = samples[start:start + n]
        peak, rms = dsp.levels(f)
        if rms > GATE_ABS_DB:
            crests.append((rms, peak - rms))
    if not crests:
        return Gain("silence", 0.0)
    loud = [c for r, c in crests if r >= max(r for r, _ in crests) - 12]
    crest = float(np.median(loud))
    label = ("Clean" if crest >= 15 else "Edge of breakup" if crest >= 12
             else "Crunch" if crest >= 9.5 else "High gain")
    return Gain(label, crest)


def estimate_tempo(samples: np.ndarray, rate: int = RATE) -> tuple[float, float]:
    """Tempo from the rhythm of note onsets.

    Returns:
        ``(bpm, confidence)``; confidence is how much the best beat period stands out
        (3 to 4 for noise, above :data:`PULSE_CONFIDENCE` for a clear pulse).  ``(0, 0)`` for no pulse.
    """
    hop = 512
    n = samples.size // hop
    if n < 64:
        return 0.0, 0.0
    env = np.sqrt(np.mean(samples[:n * hop].reshape(n, hop) ** 2, axis=1))
    env = np.log1p(1000.0 * env)
    onset = np.maximum(np.diff(env, prepend=env[0]), 0.0)
    onset -= onset.mean()
    fps = rate / hop
    lo, hi = int(fps * 60 / 220), int(fps * 60 / 50)
    ac = np.correlate(onset, onset, mode="full")[onset.size - 1:]
    if hi >= ac.size or ac[0] <= 0:
        return 0.0, 0.0
    seg = ac[lo:hi + 1]
    lag = lo + int(np.argmax(seg))
    if lag <= 0:
        return 0.0, 0.0
    bpm = 60.0 * fps / lag
    while bpm < 70:
        bpm *= 2
    while bpm > 180:
        bpm /= 2
    mean = float(np.mean(np.abs(seg)))
    return float(bpm), float(seg.max() / mean) if mean > 0 else 0.0


def delay_times(bpm: float) -> list[tuple[str, int]]:
    """Delay times in ms that fit a tempo."""
    if bpm <= 0:
        return []
    q = 60000.0 / bpm
    return [("quarter", round(q)), ("dotted eighth", round(q * 0.75)),
            ("eighth", round(q * 0.5)), ("eighth triplet", round(q / 3.0)),
            ("half", round(q * 2))]


class TapTempo:
    """Tempo from the times of taps (median of the last few intervals)."""

    def __init__(self, reset_after: float = 2.0, keep: int = 6) -> None:
        self.reset_after, self.keep = reset_after, keep
        self._taps: list[float] = []

    def tap(self, now: float) -> float | None:
        """Register a tap at ``now`` seconds; returns the BPM once there are two taps."""
        if self._taps and now - self._taps[-1] > self.reset_after:
            self._taps = []
        self._taps = (self._taps + [now])[-(self.keep + 1):]
        if len(self._taps) < 2:
            return None
        return 60.0 / float(np.median(np.diff(self._taps)))


@dataclass
class Reference:
    """A loaded reference recording and what was learnt from it."""
    name: str
    freqs: np.ndarray
    levels: np.ndarray                   # average spectrum, dBFS
    seconds: float
    centroid: float
    tilt: float
    gain: Gain
    bpm: float
    bpm_confidence: float
    delays: list = field(default_factory=list)

    def summary(self) -> list[str]:
        """The reference's character in a few lines."""
        lines = [f"Reference: {self.name} ({self.seconds:.0f} s)",
                 f"Brightness: centre {self.centroid:.0f} Hz, slope {self.tilt:+.1f} dB/octave",
                 f"Gain: {self.gain.label.lower()} (crest factor {self.gain.crest_db:.1f} dB)"]
        if self.bpm > 0 and self.bpm_confidence >= PULSE_CONFIDENCE:
            d = ", ".join(f"{n} {ms} ms" for n, ms in self.delays[:3])
            lines.append(f"Tempo: about {self.bpm:.0f} BPM; delay: {d}")
        else:
            lines.append("Tempo: no clear pulse found (use Tap tempo)")
        return lines


def analyse(path: str | Path, points: int = 160, linear: bool = False,
            rate: int = RATE) -> Reference:
    """Load a reference file and work out its spectrum and descriptors.

    Raises:
        AudioError: if the file cannot be read or holds no sound.
    """
    samples = load_audio(path, rate)
    if samples.size < dsp.N_FFT:
        raise AudioError(f"{Path(path).name}: too short")
    freqs, levels = long_term_spectrum(samples, rate, points, linear)
    if float(levels.max()) <= dsp.FLOOR_DB + 1:
        raise AudioError(f"{Path(path).name}: no sound in the file")
    bpm, conf = estimate_tempo(samples, rate)
    return Reference(Path(path).name, freqs, levels, samples.size / rate,
                     centroid_hz(levels, freqs), tilt_db_per_octave(levels, freqs),
                     gain_character(samples, rate), bpm, conf, delay_times(bpm))
