"""How notes begin and fade: attack, decay, sustain and the brightness of the pick (numpy).

Two recordings with the same average spectrum can still feel different: a
compressed or high-gain sound sustains, a clean one decays quickly, a hard pick
is brighter at the start of the note than later.  Notes are found from the
rise of the level; for each one the attack, the slope of the decay, the level
left after a moment and the spectral centre of its first moments and of its
sustained part are measured, and the middle value of all notes is kept.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .capture import RATE

HOP_S = 0.003
WINDOW_S = 0.006
#: A note starts when the level rises this much (dB) within :data:`RISE_S`.
ONSET_RISE_DB, RISE_S = 8.0, 0.012
#: Quietest note considered, in dB below the loudest, and the shortest gap between notes.
RANGE_DB, MIN_GAP_S = 35.0, 0.15
#: The attack is the rise from this many dB under the peak to the peak.
ATTACK_FROM_DB = 20.0
#: The decay is measured from this long after the peak to the next note (at most :data:`DECAY_MAX_S`).
DECAY_FROM_S, DECAY_MAX_S, DECAY_MIN_S = 0.06, 0.8, 0.25
#: The sustain level is read this long after the peak.
SUSTAIN_AT_S = 0.3
#: Windows for the spectra of the attack and of the sustained part (seconds).
ATTACK_WIN_S, SUSTAIN_FROM_S, SUSTAIN_WIN_S = 0.04, 0.15, 0.09
CENTROID_BAND = (150.0, 8000.0)
#: Fewest notes needed for a result.
MIN_NOTES = 3


@dataclass(frozen=True)
class Envelope:
    """The typical note of a recording.

    Attributes:
        notes: how many notes it is based on.
        attack_ms: time to rise from 20 dB under the peak to the peak.
        decay_db_s: slope of the level after the peak (negative; closer to 0 sustains longer).
        sustain_db: level :data:`SUSTAIN_AT_S` after the peak, relative to the peak (None if the notes are shorter).
        attack_hz, sustain_hz: spectral centre of the first moments and of the sustained part.
    """

    notes: int
    attack_ms: float
    decay_db_s: float
    sustain_db: float | None
    attack_hz: float
    sustain_hz: float

    @property
    def pick_octaves(self) -> float:
        """How much brighter the attack is than the sustained note, in octaves."""
        return float(np.log2(self.attack_hz / self.sustain_hz)) if self.sustain_hz > 0 else 0.0

    def summary(self) -> str:
        """One line for the hints."""
        sus = f", {self.sustain_db:+.0f} dB after {SUSTAIN_AT_S * 1000:.0f} ms" if self.sustain_db is not None else ""
        return (f"Notes ({self.notes}): attack {self.attack_ms:.0f} ms, decay {self.decay_db_s:+.0f} dB/s{sus}; "
                f"pick centre {self.attack_hz:.0f} Hz vs {self.sustain_hz:.0f} Hz sustained")


def _level_db(samples: np.ndarray, rate: int) -> tuple[np.ndarray, int]:
    """RMS level in dB per hop, and the hop in samples."""
    hop, win = max(1, int(HOP_S * rate)), max(2, int(WINDOW_S * rate))
    n = (samples.size - win) // hop + 1
    if n <= 0:
        return np.zeros(0), hop
    sq = np.square(samples.astype(np.float64))
    csum = np.concatenate(([0.0], np.cumsum(sq)))
    starts = np.arange(n) * hop
    rms = np.sqrt((csum[starts + win] - csum[starts]) / win)
    return 20.0 * np.log10(rms + 1e-9), hop


def find_onsets(db: np.ndarray, hop_s: float) -> list[int]:
    """Frames (indices of ``db``) where notes start: a sharp rise to a level near the loudest."""
    rise = max(1, int(RISE_S / hop_s))
    gap = int(MIN_GAP_S / hop_s)
    if db.size <= rise + 2:
        return []
    floor = db.max() - RANGE_DB
    out: list[int] = []
    i = rise
    while i < db.size - 1:
        if db[i] - db[i - rise] >= ONSET_RISE_DB and db[i] > floor and (not out or i - out[-1] >= gap):
            out.append(i - rise)
            i += gap
        else:
            i += 1
    return out


def _centroid(frames: list[np.ndarray], rate: int) -> float:
    """Spectral centre (Hz) of the average power spectrum of equally long ``frames``."""
    n = frames[0].size
    power = np.mean([np.abs(np.fft.rfft(f * np.hanning(n))) ** 2 for f in frames], axis=0)
    freqs = np.fft.rfftfreq(n, 1.0 / rate)
    m = (freqs >= CENTROID_BAND[0]) & (freqs <= CENTROID_BAND[1])
    return float(np.sum(freqs[m] * power[m]) / np.sum(power[m])) if power[m].sum() > 0 else 0.0


def analyse(samples: np.ndarray, rate: int = RATE) -> Envelope | None:
    """Measure the typical note of a recording.

    Returns:
        The :class:`Envelope`, or None when fewer than :data:`MIN_NOTES` clear notes were found.
    """
    x = np.asarray(samples, dtype=np.float32)
    db, hop = _level_db(x, rate)
    hop_s = hop / rate
    onsets = find_onsets(db, hop_s)
    attacks, slopes, sustains, a_frames, s_frames = [], [], [], [], []
    for k, on in enumerate(onsets):
        end = onsets[k + 1] if k + 1 < len(onsets) else db.size
        seg = db[on:end]
        look = int(0.08 / hop_s)
        peak_i = int(np.argmax(seg[:max(2, look)]))
        peak = seg[peak_i]
        # attack: from ATTACK_FROM_DB under the peak up to the peak
        below = np.nonzero(seg[:peak_i + 1] <= peak - ATTACK_FROM_DB)[0]
        start_i = int(below[-1]) if below.size else 0
        attacks.append((peak_i - start_i) * hop_s * 1000.0)
        # decay: slope of the level from just after the peak to the next note
        d0, d1 = peak_i + int(DECAY_FROM_S / hop_s), min(seg.size, peak_i + int(DECAY_MAX_S / hop_s))
        if (d1 - d0) * hop_s >= DECAY_MIN_S - DECAY_FROM_S:
            t = np.arange(d0, d1) * hop_s
            slopes.append(float(np.polyfit(t, seg[d0:d1], 1)[0]))
        at = peak_i + int(SUSTAIN_AT_S / hop_s)
        if at < seg.size - 1:
            sustains.append(float(np.mean(seg[at - 1:at + 2]) - peak))
        # spectra of the attack and of the sustained part (samples)
        s0 = (on + peak_i) * hop
        a_len, s_len = int(ATTACK_WIN_S * rate), int(SUSTAIN_WIN_S * rate)
        s_from = s0 + int(SUSTAIN_FROM_S * rate)
        if (s_from + s_len <= (on + seg.size) * hop and s_from + s_len <= x.size
                and s0 + a_len <= x.size):
            a_frames.append(x[max(0, s0 - a_len // 4):max(0, s0 - a_len // 4) + a_len])
            s_frames.append(x[s_from:s_from + s_len])
    if len(attacks) < MIN_NOTES or len(slopes) < MIN_NOTES or not a_frames:
        return None
    return Envelope(
        notes=len(attacks), attack_ms=float(np.median(attacks)), decay_db_s=float(np.median(slopes)),
        sustain_db=float(np.median(sustains)) if len(sustains) >= MIN_NOTES else None,
        attack_hz=_centroid(a_frames, rate), sustain_hz=_centroid(s_frames, rate))


def compare(live: Envelope, other: Envelope, first: str = "your tone",
            second: str = "the reference") -> list[str]:
    """Plain-language differences in how the notes behave (empty when they are alike)."""
    out = []
    dd = live.decay_db_s - other.decay_db_s
    if abs(dd) >= 6.0:
        if dd < 0:
            out.append(f"Notes: {first} dies away faster ({live.decay_db_s:+.0f} vs {other.decay_db_s:+.0f} dB/s): "
                       "more gain, compression or a longer reverb sustain longer")
        else:
            out.append(f"Notes: {first} sustains longer ({live.decay_db_s:+.0f} vs {other.decay_db_s:+.0f} dB/s): "
                       "less gain or compression, or a shorter reverb, lets notes fade sooner")
    da = live.attack_ms - other.attack_ms
    if abs(da) >= 4.0 and max(live.attack_ms, other.attack_ms) >= 2 * min(live.attack_ms, other.attack_ms):
        out.append(f"Attack: {first} is {'softer' if da > 0 else 'snappier'} "
                   f"({live.attack_ms:.0f} vs {other.attack_ms:.0f} ms): "
                   f"{'less compression, or a harder pick' if da > 0 else 'more compression, or a softer pick'}")
    dp = live.pick_octaves - other.pick_octaves
    if abs(dp) >= 0.4:
        out.append(f"Pick: the start of {first}'s notes is {'brighter' if dp > 0 else 'duller'} than the rest "
                   f"compared with {second} (pick attack or the amp's presence)")
    return out
