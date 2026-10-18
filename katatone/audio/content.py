"""Which notes were played, and where on the neck (numpy).

An average spectrum depends on the notes as well as on the tone: a low riff has
more bass than a high lead through the very same settings.  Before the
differences between two tones are blamed on the amp, this checks that the same
kind of playing is being compared: the share of each of the 12 pitch classes
(a chroma vector) and the register (the middle of the energy between 80 Hz and
1 kHz).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .bands import _NOTES
from .capture import RATE

N_FFT, HOP = 8192, 4096
#: The part of the spectrum that tells the notes (their fundamentals and first harmonics).
NOTE_BAND = (75.0, 2000.0)
REGISTER_BAND = (75.0, 1000.0)
#: Frames quieter than this many dB below the loudest are pauses.
GATE_DB = 35.0
#: Below this correlation of the pitch classes the playing is called different.
MIN_CORRELATION = 0.5
#: A register further apart than this (octaves) is called different.
MAX_REGISTER_OCTAVES = 0.7
#: Least audio (seconds) a measurement is based on.
MIN_SECONDS = 2.0


@dataclass(frozen=True)
class Content:
    """The pitch content of a stretch of playing.

    Attributes:
        chroma: share of each pitch class (C, C#, ... B), summing to 1.
        register_hz: the geometric centre of the energy between 75 Hz and 1 kHz.
    """

    chroma: np.ndarray
    register_hz: float

    def top_notes(self, n: int = 3) -> list[str]:
        """The ``n`` most used pitch classes, most used first."""
        return [_NOTES[i] for i in np.argsort(-self.chroma)[:n]]


def analyse(samples: np.ndarray, rate: int = RATE) -> Content | None:
    """The pitch content of ``samples``, or None for too little sound to tell."""
    x = np.asarray(samples, dtype=np.float32)
    if x.size < max(N_FFT, int(MIN_SECONDS * rate)):
        return None
    window = np.hanning(N_FFT).astype(np.float32)
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / rate)
    frames = [x[s:s + N_FFT] for s in range(0, x.size - N_FFT + 1, HOP)]
    level = np.array([float(np.sqrt(np.mean(f * f))) for f in frames])
    if level.max() <= 1e-4:
        return None
    keep = level >= level.max() * 10.0 ** (-GATE_DB / 20.0)
    power = np.zeros(freqs.size)
    for f, k in zip(frames, keep):
        if k:
            power += np.abs(np.fft.rfft(f * window)) ** 2
    m = (freqs >= NOTE_BAND[0]) & (freqs <= NOTE_BAND[1])
    amp = np.sqrt(power[m])                      # magnitude: less ruled by the loudest note
    pitch_class = np.round(12.0 * np.log2(freqs[m] / 440.0)).astype(int) % 12   # 0 = A
    chroma = np.bincount(pitch_class, weights=amp, minlength=12)
    chroma = np.roll(chroma, 9)                  # C first
    if chroma.sum() <= 0:
        return None
    r = (freqs >= REGISTER_BAND[0]) & (freqs <= REGISTER_BAND[1])
    centre = float(2.0 ** (np.sum(np.log2(freqs[r]) * power[r]) / np.sum(power[r])))
    return Content(chroma / chroma.sum(), centre)


def correlation(a: Content, b: Content) -> float:
    """How alike the use of pitch classes is (-1 .. 1; 0 for unrelated playing)."""
    x, y = a.chroma - a.chroma.mean(), b.chroma - b.chroma.mean()
    den = float(np.linalg.norm(x) * np.linalg.norm(y))
    return float(np.dot(x, y) / den) if den > 0 else 0.0


def register_octaves(a: Content, b: Content) -> float:
    """How many octaves higher ``a`` plays than ``b``."""
    return float(np.log2(a.register_hz / b.register_hz))


def warnings(a: Content | None, b: Content | None, first: str = "your tone",
             second: str = "the reference") -> list[str]:
    """Reasons why the spectra of ``first`` and ``second`` may differ because of the
    notes played and not of the tone (empty when the playing is alike or unknown)."""
    if a is None or b is None:
        return []
    out = []
    corr = correlation(a, b)
    if corr < MIN_CORRELATION:
        out.append(f"Different notes: {first} is mostly {', '.join(a.top_notes())}; {second} "
                   f"{', '.join(b.top_notes())} (match {corr:.2f}). Play the same part, or read the "
                   "bass and treble hints with care")
    octaves = register_octaves(a, b)
    if abs(octaves) > MAX_REGISTER_OCTAVES:
        out.append(f"Different register: {first} is about {abs(octaves):.1f} octaves "
                   f"{'higher' if octaves > 0 else 'lower'} than {second}, so the low end differs "
                   "by the notes alone")
    return out
