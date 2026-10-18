"""Fitting the Katana's EQ blocks to a difference between two tones (numpy).

``correction`` is what has to be added to the live spectrum to reach the target
(in dB, on the analyzer's display points).  Two fits are made:

* the 10-band graphic EQ (GE-10): 31 Hz .. 16 kHz, one octave apart, whole dB;
* the parametric EQ (PEQ): a low shelf, two peaking bands (frequency, Q, gain)
  and a high shelf.  The low and high *cuts* are left alone.

The responses are standard biquad filters (RBJ cookbook).  The amp's own filters
differ in detail (the GE-10 bands' width and the PEQ shelf corners are assumed:
see :data:`GE_Q`, :data:`LOW_SHELF_HZ`, :data:`HIGH_SHELF_HZ`), so the result is a
good starting point to listen to, not an exact setting.  The fit is on top of
whatever EQ the amp is already using.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .capture import RATE

#: GE-10 band centres (Hz) and the width assumed for each (about one octave).
GE_BANDS = (31.0, 62.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0)
GE_LABELS = ("31 Hz", "62 Hz", "125 Hz", "250 Hz", "500 Hz", "1 kHz", "2 kHz", "4 kHz",
             "8 kHz", "16 kHz")
GE_Q = 1.4
#: The range the amp allows, and the range worth proposing.
GE_LIMIT_DB = 24
PROPOSE_DB = 12
#: PEQ: the 28 centre frequencies of the two mid bands, the Q steps, the gain range.
PEQ_FREQS = (20.0, 25.0, 31.5, 40.0, 50.0, 63.0, 80.0, 100.0, 125.0, 160.0, 200.0, 250.0,
             315.0, 400.0, 500.0, 630.0, 800.0, 1000.0, 1250.0, 1600.0, 2000.0, 2500.0,
             3150.0, 4000.0, 5000.0, 6300.0, 8000.0, 10000.0)
PEQ_Q = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
PEQ_LIMIT_DB = 20
#: Assumed corners of the PEQ's low and high shelves.
LOW_SHELF_HZ, HIGH_SHELF_HZ = 100.0, 5000.0
#: Frequencies that count fully in the fit; outside them the guitar has little to say.
FIT_BAND = (80.0, 8000.0)
OUTSIDE_WEIGHT = 0.25
#: Penalty (dB squared) per dB squared of gain: a change must earn its place.
GAIN_PENALTY = 1.5
#: Gains smaller than this are reported as "leave it".
MIN_GAIN_DB = 1


def _response_db(b0, b1, b2, a0, a1, a2, freqs: np.ndarray, rate: int) -> np.ndarray:
    """Magnitude (dB) of biquads at ``freqs``; coefficients broadcast over leading axes."""
    w = 2.0 * np.pi * np.asarray(freqs, dtype=np.float64) / rate
    z1 = np.exp(-1j * w)
    z2 = z1 * z1
    num = b0[..., None] + b1[..., None] * z1 + b2[..., None] * z2
    den = a0[..., None] + a1[..., None] * z1 + a2[..., None] * z2
    return 20.0 * np.log10(np.abs(num / den) + 1e-12)


def peaking_db(freq, q, gain_db, freqs: np.ndarray, rate: int = RATE) -> np.ndarray:
    """Response in dB of peaking filters (arrays of ``freq``, ``q``, ``gain_db`` broadcast)."""
    f0, q, g = np.broadcast_arrays(*(np.asarray(v, dtype=np.float64) for v in (freq, q, gain_db)))
    amp = 10.0 ** (g / 40.0)
    w0 = 2.0 * np.pi * f0 / rate
    alpha = np.sin(w0) / (2.0 * q)
    cos = np.cos(w0)
    return _response_db(1 + alpha * amp, -2 * cos, 1 - alpha * amp,
                        1 + alpha / amp, -2 * cos, 1 - alpha / amp, freqs, rate)


def shelf_db(freq: float, gain_db, freqs: np.ndarray, high: bool, rate: int = RATE) -> np.ndarray:
    """Response in dB of a low or high shelf (slope 1) with a corner at ``freq``."""
    g = np.asarray(gain_db, dtype=np.float64)
    amp = 10.0 ** (g / 40.0)
    w0 = 2.0 * np.pi * freq / rate
    cos = np.cos(w0)
    alpha = np.sin(w0) / 2.0 * np.sqrt(2.0)
    sq = 2.0 * np.sqrt(amp) * alpha
    if high:
        b0, b1, b2 = amp * ((amp + 1) + (amp - 1) * cos + sq), -2 * amp * ((amp - 1) + (amp + 1) * cos), \
            amp * ((amp + 1) + (amp - 1) * cos - sq)
        a0, a1, a2 = (amp + 1) - (amp - 1) * cos + sq, 2 * ((amp - 1) - (amp + 1) * cos), \
            (amp + 1) - (amp - 1) * cos - sq
    else:
        b0, b1, b2 = amp * ((amp + 1) - (amp - 1) * cos + sq), 2 * amp * ((amp - 1) - (amp + 1) * cos), \
            amp * ((amp + 1) - (amp - 1) * cos - sq)
        a0, a1, a2 = (amp + 1) + (amp - 1) * cos + sq, -2 * ((amp - 1) + (amp + 1) * cos), \
            (amp + 1) + (amp - 1) * cos - sq
    return _response_db(b0, b1, b2, a0, a1, a2, freqs, rate)


def fit_weights(freqs: np.ndarray) -> np.ndarray:
    """Weight of each display point in the fit (1 inside :data:`FIT_BAND`)."""
    inside = (freqs >= FIT_BAND[0]) & (freqs <= FIT_BAND[1])
    return np.where(inside, 1.0, OUTSIDE_WEIGHT)


def _cost(residual: np.ndarray, w: np.ndarray) -> float:
    return float(np.sum(w * residual * residual))


@dataclass
class EqFit:
    """A proposed EQ setting.

    Attributes:
        kind: ``"GE-10"`` or ``"PEQ"``.
        settings: ``(name, text)`` for every control, in the amp's order.
        response: what the setting does to the spectrum, in dB, on the display points.
        before, after: weighted RMS (dB) of what is left to correct without and with it.
    """

    kind: str
    settings: list
    response: np.ndarray
    before: float
    after: float

    @property
    def useful(self) -> bool:
        """Some control is worth moving and it brings the tones noticeably closer."""
        return any(t not in ("0 dB", "off") for _, t in self.settings) and self.after < self.before - 0.3

    def lines(self) -> list[str]:
        """The proposal as text."""
        head = ("Graphic EQ (GE-10)" if self.kind == "GE-10" else "Parametric EQ (PEQ)")
        if not self.useful:
            return [f"{head}: nothing worth changing"]
        return [f"{head}, on top of the current EQ: {self.before:.1f} -> {self.after:.1f} dB RMS off",
                "  " + ", ".join(f"{n} {t}" for n, t in self.settings if t not in ("0 dB", "off"))]


def _rms(residual: np.ndarray, w: np.ndarray) -> float:
    return float(np.sqrt(np.sum(w * residual * residual) / np.sum(w)))


def fit_graphic(correction: np.ndarray, freqs: np.ndarray, rate: int = RATE) -> EqFit:
    """Fit the 10 band gains (whole dB, within ±:data:`PROPOSE_DB`) to ``correction``."""
    w = fit_weights(freqs)
    gains_axis = np.arange(-PROPOSE_DB, PROPOSE_DB + 1)
    # table[band, gain index, point]: the exact response of each band at each whole dB
    table = np.stack([peaking_db(f, GE_Q, gains_axis, freqs, rate) for f in GE_BANDS])
    # start from a least-squares solution on a unit (+6 dB) basis, then refine on the exact table
    basis = table[:, PROPOSE_DB + 6, :].T / 6.0
    sw = np.sqrt(w)[:, None]
    ridge = np.sqrt(GAIN_PENALTY) * np.eye(len(GE_BANDS))
    sol = np.linalg.lstsq(np.vstack((basis * sw, ridge)),
                          np.concatenate((correction * sw[:, 0], np.zeros(len(GE_BANDS)))),
                          rcond=None)[0]
    idx = np.clip(np.round(sol).astype(int), -PROPOSE_DB, PROPOSE_DB) + PROPOSE_DB
    total = sum(table[b, idx[b]] for b in range(len(GE_BANDS)))
    for _ in range(3):
        moved = False
        for b in range(len(GE_BANDS)):
            rest = total - table[b, idx[b]]
            costs = [_cost(correction - rest - table[b, k], w) + GAIN_PENALTY * (k - PROPOSE_DB) ** 2
                     for k in range(table.shape[1])]
            best = int(np.argmin(costs))
            if best != idx[b]:
                idx[b], moved = best, True
                total = rest + table[b, best]
        if not moved:
            break
    gains = idx - PROPOSE_DB
    settings = [(label, f"{g:+d} dB" if abs(g) >= MIN_GAIN_DB else "0 dB")
                for label, g in zip(GE_LABELS, gains)]
    return EqFit("GE-10", settings, total, _rms(correction, w), _rms(correction - total, w))


_PEQ_TABLES: dict = {}


def _peq_table(freqs: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cached responses: peaks[freq, q, gain, point] and the two shelves[gain, point]."""
    key = (freqs.tobytes(), rate)
    if key not in _PEQ_TABLES:
        gains = np.arange(-PEQ_LIMIT_DB, PEQ_LIMIT_DB + 1)
        f = np.array(PEQ_FREQS)[:, None, None]
        q = np.array(PEQ_Q)[None, :, None]
        peaks = peaking_db(f, q, gains[None, None, :], freqs, rate).astype(np.float32)
        low = shelf_db(LOW_SHELF_HZ, gains, freqs, False, rate)
        high = shelf_db(HIGH_SHELF_HZ, gains, freqs, True, rate)
        _PEQ_TABLES.clear()                    # one display axis at a time
        _PEQ_TABLES[key] = (peaks, low, high)
    return _PEQ_TABLES[key]


def fit_parametric(correction: np.ndarray, freqs: np.ndarray, rate: int = RATE) -> EqFit:
    """Fit the PEQ's two shelves and two mid bands to ``correction`` by coordinate descent."""
    w = fit_weights(freqs)
    peaks, low, high = _peq_table(freqs, rate)
    zero = PEQ_LIMIT_DB
    n_g = low.shape[0]
    lo_i, hi_i = zero, zero
    mids = [(14, 1, zero), (23, 1, zero)]          # the amp's defaults: 500 Hz and 4 kHz, Q 1
    penalty = GAIN_PENALTY * (np.arange(n_g) - zero) ** 2

    def total() -> np.ndarray:
        return low[lo_i] + high[hi_i] + sum(peaks[f, q, g] for f, q, g in mids)

    for _ in range(4):
        # shelves: best whole-dB gain given the rest
        rest = total() - low[lo_i]
        lo_i = int(np.argmin(np.sum(w * (correction - rest - low) ** 2, axis=1) + penalty))
        rest = total() - high[hi_i]
        hi_i = int(np.argmin(np.sum(w * (correction - rest - high) ** 2, axis=1) + penalty))
        # each mid band: best frequency, Q and gain given the rest
        for k in (0, 1):
            f, q, g = mids[k]
            rest = total() - peaks[f, q, g]
            err = np.sum(w * (correction - rest - peaks) ** 2, axis=-1) + penalty    # (freq, q, gain)
            f, q, g = np.unravel_index(int(np.argmin(err)), err.shape)
            mids[k] = (int(f), int(q), int(g))
    resp = np.asarray(total(), dtype=np.float64)
    # report the lower band first
    mids.sort(key=lambda m: m[0])

    def gain_text(i: int) -> str:
        g = i - zero
        return f"{g:+d} dB" if abs(g) >= MIN_GAIN_DB else "0 dB"

    settings = [("Low", gain_text(lo_i))]
    for name, (f, q, g) in zip(("Low-mid", "High-mid"), mids):
        text = gain_text(g)
        settings.append((name, "off" if text == "0 dB" else
                         f"{PEQ_FREQS[f]:g} Hz Q {PEQ_Q[q]:g} {text}"))
    settings.append(("High", gain_text(hi_i)))
    return EqFit("PEQ", settings, resp, _rms(correction, w), _rms(correction - resp, w))


def fit_all(correction: np.ndarray, freqs: np.ndarray, rate: int = RATE) -> dict[str, EqFit]:
    """Both fits, by kind."""
    return {"GE-10": fit_graphic(correction, freqs, rate),
            "PEQ": fit_parametric(correction, freqs, rate)}
