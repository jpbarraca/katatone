"""Pitch detection and note maths for the guitar tuner (numpy)."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np

from .bands import _NOTES

#: Lowest and highest fundamental looked for (Hz): a 5-string bass B to the
#: top of a guitar's range.
F_LOW, F_HIGH = 55.0, 1400.0
#: Quietest input (dBFS RMS) that is listened to; below it there is no reading.
MIN_RMS_DB = -55.0
#: How clear (0..1) a periodic peak must be to count as a note.
MIN_CLARITY = 0.8
#: |cents| within which a string counts as in tune.
IN_TUNE_CENTS = 5.0
#: The tuner's analysis window (samples) and how long a reading is held after
#: the sound dies away (seconds).
WINDOW = 4096
HOLD_S = 0.8

#: Tunings offered (low string to high); the first is the default.
TUNINGS = {
    "Standard (E A D G B E)": ("E2", "A2", "D3", "G3", "B3", "E4"),
    "Drop D": ("D2", "A2", "D3", "G3", "B3", "E4"),
    "Half step down": ("D#2", "G#2", "C#3", "F#3", "A#3", "D#4"),
    "Full step down": ("D2", "G2", "C3", "F3", "A3", "D4"),
    "DADGAD": ("D2", "A2", "D3", "G3", "A3", "D4"),
    "Open G": ("D2", "G2", "D3", "G3", "B3", "D4"),
    "Bass (E A D G)": ("E1", "A1", "D2", "G2"),
}
CHROMATIC = "Chromatic"
#: Reference pitches offered for A4 (Hz).
REFERENCES = (415, 430, 432, 435, 438, 440, 442, 444, 445, 450)


def note_to_midi(name: str) -> int:
    """MIDI number of a note name such as ``"E2"`` or ``"D#3"``."""
    return _NOTES.index(name[:-1]) + 12 * (int(name[-1]) + 1)


def midi_to_name(midi: int) -> str:
    """Note name of a MIDI number (sharps only)."""
    return f"{_NOTES[midi % 12]}{midi // 12 - 1}"


def midi_to_freq(midi: float, a4: float = 440.0) -> float:
    """Frequency of a (possibly fractional) MIDI note for a given A4."""
    return a4 * 2.0 ** ((midi - 69) / 12.0)


def note_freq(name: str, a4: float = 440.0) -> float:
    """Frequency of a note name."""
    return midi_to_freq(note_to_midi(name), a4)


def cents_between(freq: float, target: float) -> float:
    """How many cents ``freq`` is above ``target`` (negative: flat)."""
    return 1200.0 * math.log2(freq / target)


def detect_pitch(buf: np.ndarray, rate: int, f_low: float = F_LOW,
                 f_high: float = F_HIGH, min_rms_db: float = MIN_RMS_DB
                 ) -> "tuple[float, float] | None":
    """Find the fundamental frequency of a block of audio.

    Uses the normalised square difference function (McLeod's pitch method):
    the autocorrelation, normalised so that a perfectly periodic signal peaks
    at 1, with the *first* strong peak chosen so an octave error (a harmonic or
    a sub-harmonic) is unlikely, refined by a parabola through the peak.

    Args:
        buf: mono samples (full scale +/-1.0); at least a few periods of the
            lowest note wanted.
        rate: the sample rate in Hz.
        f_low: lowest fundamental to look for.
        f_high: highest fundamental to look for.
        min_rms_db: below this RMS level there is no pitch.
    Returns:
        ``(frequency in Hz, clarity 0..1)``, or None for silence or noise.
    """
    x = np.asarray(buf, dtype=np.float64)
    n = x.size
    if n < 2 * int(rate / f_low):
        return None
    x = x - x.mean()
    energy = float(np.dot(x, x))
    if energy <= 0 or 10 * math.log10(energy / n + 1e-30) < min_rms_db:
        return None
    # autocorrelation by FFT
    size = 1 << (2 * n - 1).bit_length()
    spec = np.fft.rfft(x, size)
    acf = np.fft.irfft(spec * np.conj(spec), size)[:n]
    # m'(tau) = sum of the squares of both overlapping parts
    sq = np.concatenate(([0.0], np.cumsum(x * x)))
    taus = np.arange(n)
    m = sq[n - taus] + (sq[n] - sq[taus])
    nsdf = np.where(m > 0, 2.0 * acf / np.where(m > 0, m, 1.0), 0.0)

    lo = max(2, int(rate / f_high))
    hi = min(n - 2, int(rate / f_low))
    if hi <= lo:
        return None
    # the maximum of each stretch where the function is positive
    peaks: list[int] = []
    tau = lo
    while tau < hi:
        if nsdf[tau] > 0 and nsdf[tau] >= nsdf[tau - 1] and nsdf[tau] >= nsdf[tau + 1]:
            peaks.append(tau)
            # skip to the end of this positive stretch
            while tau < hi and nsdf[tau] > 0:
                tau += 1
        tau += 1
    if not peaks:
        return None
    best = max(float(nsdf[p]) for p in peaks)
    if best < MIN_CLARITY:
        return None
    pick = next(p for p in peaks if nsdf[p] >= 0.9 * best)
    a, b, c = nsdf[pick - 1], nsdf[pick], nsdf[pick + 1]
    denom = a - 2 * b + c
    shift = 0.5 * (a - c) / denom if denom != 0 else 0.0
    return float(rate / (pick + shift)), float(b)


@dataclass(frozen=True)
class Reading:
    """What the tuner shows.

    Attributes:
        freq: the detected frequency (Hz).
        note: the note it is measured against (``"E2"``).
        target: that note's frequency (Hz).
        cents: how far above the note it is (negative: flat).
        string: index of the string in the tuning (None when chromatic).
    """

    freq: float
    note: str
    target: float
    cents: float
    string: int | None

    @property
    def in_tune(self) -> bool:
        """Within :data:`IN_TUNE_CENTS` of the note."""
        return abs(self.cents) <= IN_TUNE_CENTS


def measure(freq: float, tuning: "tuple[str, ...] | None", a4: float = 440.0,
            locked: int | None = None) -> Reading:
    """Compare a frequency with a note.

    Args:
        freq: the detected frequency.
        tuning: the strings (low to high) to tune to, or None for the nearest
            semitone (chromatic).
        a4: the reference pitch.
        locked: index of a string to tune to whatever is played (None: the
            string nearest to the sound).
    Returns:
        The reading.
    """
    if tuning is None:
        midi = round(69 + 12 * math.log2(freq / a4))
        name = midi_to_name(midi)
        target = midi_to_freq(midi, a4)
        return Reading(freq, name, target, cents_between(freq, target), None)
    targets = [note_freq(n, a4) for n in tuning]
    if locked is not None and 0 <= locked < len(tuning):
        i = locked
    else:
        i = min(range(len(tuning)), key=lambda k: abs(cents_between(freq, targets[k])))
    return Reading(freq, tuning[i], targets[i], cents_between(freq, targets[i]), i)


class Tuner:
    """Turns a stream of audio blocks into steady readings."""

    def __init__(self, rate: int = 48000, mode: str = next(iter(TUNINGS)),
                 a4: float = 440.0, clock=None) -> None:
        """
        Args:
            rate: the sample rate of the blocks.
            mode: :data:`CHROMATIC` or a key of :data:`TUNINGS`.
            a4: the reference pitch.
            clock: monotonic time source (tests replace it).
        """
        import time
        self.rate = rate
        self.mode = mode
        self.a4 = a4
        self.locked: int | None = None
        self._clock = clock or time.monotonic
        self._buf = np.zeros(WINDOW, dtype=np.float32)
        self._recent: deque = deque(maxlen=5)       # last detected frequencies
        self._last: tuple[float, float] | None = None   # (freq, time) of the last one

    @property
    def tuning(self) -> "tuple[str, ...] | None":
        """The strings of the current mode (None when chromatic)."""
        return None if self.mode == CHROMATIC else TUNINGS[self.mode]

    def set_mode(self, mode: str) -> None:
        """Change the tuning (or chromatic); the string lock is released."""
        self.mode = mode
        self.locked = None

    def push(self, block: np.ndarray) -> None:
        """Append audio, keeping the last :data:`WINDOW` samples."""
        n = block.size
        if n >= WINDOW:
            self._buf[:] = block[-WINDOW:]
        else:
            self._buf = np.concatenate((self._buf[n:], block.astype(np.float32)))

    def reading(self) -> "Reading | None":
        """The current reading: the median of the last few detections, kept for
        :data:`HOLD_S` after the sound ends, then None."""
        now = self._clock()
        found = detect_pitch(self._buf, self.rate)
        if found is not None:
            freq = found[0]
            # a jump of more than a semitone is a new note: forget the old ones
            if self._recent and abs(cents_between(freq, self._recent[-1])) > 100:
                self._recent.clear()
            self._recent.append(freq)
            self._last = (float(np.median(self._recent)), now)
        elif self._last is None or now - self._last[1] > HOLD_S:
            self._recent.clear()
            self._last = None
            return None
        freq = self._last[0]
        return measure(freq, self.tuning, self.a4, self.locked)
