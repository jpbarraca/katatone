"""Level and spectrum maths for the analyzer (numpy)."""
from __future__ import annotations

import numpy as np

FLOOR_DB = -120.0
N_FFT = 4096
F_MIN, F_MAX = 20.0, 20000.0
#: The top of the linear frequency axis (above it a linear scale only squeezes the music).
F_MAX_LINEAR = 10000.0


def f_max(linear: bool) -> float:
    """The highest frequency shown on the logarithmic or the linear axis."""
    return F_MAX_LINEAR if linear else F_MAX


def to_db(x: float) -> float:
    """Linear amplitude to dBFS, floored at :data:`FLOOR_DB`."""
    return FLOOR_DB if x <= 1e-6 else max(FLOOR_DB, 20.0 * float(np.log10(x)))


def levels(block: np.ndarray) -> tuple[float, float]:
    """Peak and RMS of a block, in dBFS.

    Args:
        block: mono samples, full scale = +/-1.0.
    Returns:
        ``(peak_db, rms_db)``.
    """
    if block.size == 0:
        return FLOOR_DB, FLOOR_DB
    peak = float(np.max(np.abs(block)))
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
    return to_db(peak), to_db(rms)


class Spectrum:
    """Spectrum of the most recent samples, on a log or linear frequency axis.

    Magnitudes are in dBFS, scaled so a full-scale sine reads 0 dB at its
    frequency.  The strongest bin in each of ``points`` display points
    between :data:`F_MIN` and :data:`F_MAX` (:data:`F_MAX_LINEAR` on a linear axis).
    """

    def __init__(self, rate: int = 48000, points: int = 240,
                 n_fft: int = N_FFT, linear: bool = False) -> None:
        """Prepare the window and the bin-to-point mapping.

        Args:
            rate: the sample rate in Hz.
            points: number of display points.
            n_fft: FFT length (the analysis window, in samples).
            linear: space the display points evenly in frequency instead of
                logarithmically.
        """
        self.rate, self.n_fft = rate, n_fft
        self._window = np.hanning(n_fft).astype(np.float32)
        self._scale = 2.0 / float(np.sum(self._window))
        self._buf = np.zeros(n_fft, dtype=np.float32)
        df = rate / n_fft
        edges = (np.linspace if linear else np.geomspace)(F_MIN, f_max(linear), points + 1)
        self.freqs = ((edges[:-1] + edges[1:]) / 2 if linear
                      else np.sqrt(edges[:-1] * edges[1:]))   # centre of each point
        lo = np.floor(edges[:-1] / df).astype(int)
        hi = np.maximum(lo + 1, np.ceil(edges[1:] / df).astype(int))
        self._lo, self._hi = lo, np.minimum(hi, n_fft // 2)

    def push(self, block: np.ndarray) -> None:
        """Append samples, keeping the last ``n_fft``."""
        n = block.size
        if n >= self.n_fft:
            self._buf[:] = block[-self.n_fft:]
        else:
            self._buf = np.concatenate((self._buf[n:], block.astype(np.float32)))

    def compute(self) -> np.ndarray:
        """Current spectrum in dBFS at :attr:`freqs` (``points`` values)."""
        mag = np.abs(np.fft.rfft(self._buf * self._window)) * self._scale
        power = np.square(mag, dtype=np.float64)
        # Strongest bin of each display point, so a sine reads its true level.
        band = np.maximum.reduceat(power[:self._hi[-1] + 1], self._lo)
        return np.maximum(10.0 * np.log10(band + 1e-12), FLOOR_DB)


class Smoother:
    """Average (exponential, in dB) and peak-hold of successive spectra."""

    def __init__(self, points: int, alpha: float = 0.35,
                 hold_decay_db: float = 0.4) -> None:
        """Start empty.

        Args:
            points: length of the spectra.
            alpha: weight of the newest spectrum in the average (0..1).
            hold_decay_db: how far the held peak falls per update.
        """
        self.alpha, self.decay = alpha, hold_decay_db
        self.avg = np.full(points, FLOOR_DB)
        self.peak = np.full(points, FLOOR_DB)

    def update(self, spectrum: np.ndarray) -> None:
        """Fold in a new spectrum."""
        self.avg += self.alpha * (spectrum - self.avg)
        self.peak = np.maximum(spectrum, self.peak - self.decay)

    def reset(self) -> None:
        """Forget the average and the held peak."""
        self.avg[:] = FLOOR_DB
        self.peak[:] = FLOOR_DB


class PeakHold:
    """Level peak-hold: holds a value, then falls at a fixed rate."""

    def __init__(self, hold_updates: int = 30, fall_db: float = 0.8) -> None:
        """Start at the floor.

        Args:
            hold_updates: updates to hold a new peak before it falls.
            fall_db: dB per update once falling.
        """
        self.hold, self.fall = hold_updates, fall_db
        self.value, self._age = FLOOR_DB, 0

    def update(self, db: float) -> float:
        """Feed the newest peak (dBFS) and return the held value."""
        if db >= self.value:
            self.value, self._age = db, 0
        else:
            self._age += 1
            if self._age > self.hold:
                self.value = max(db, self.value - self.fall)
        return self.value


class History:
    """The last few seconds of audio, kept in a ring buffer."""

    def __init__(self, seconds: float = 12.0, rate: int = 48000) -> None:
        self.rate = rate
        self._buf = np.zeros(int(seconds * rate), dtype=np.float32)
        self._pos = 0              # where the next sample goes
        self.filled = 0            # how many samples are real

    def push(self, block: np.ndarray) -> None:
        """Append samples, overwriting the oldest."""
        size = self._buf.size
        block = np.asarray(block, dtype=np.float32)[-size:]
        n = block.size
        end = self._pos + n
        if end <= size:
            self._buf[self._pos:end] = block
        else:
            first = size - self._pos
            self._buf[self._pos:] = block[:first]
            self._buf[:n - first] = block[first:]
        self._pos = end % size
        self.filled = min(size, self.filled + n)

    def last(self, seconds: float | None = None) -> np.ndarray:
        """A copy of the newest samples, oldest first (all there are by default)."""
        n = self.filled if seconds is None else min(self.filled, int(seconds * self.rate))
        idx = (self._pos - n + np.arange(n)) % self._buf.size
        return self._buf[idx]

    def clear(self) -> None:
        """Forget everything."""
        self._buf[:] = 0
        self._pos = self.filled = 0
