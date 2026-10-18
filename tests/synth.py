"""Synthetic playing for the tests: plucked notes with a chosen decay, pitch and pick."""
from __future__ import annotations

import numpy as np

RATE = 48000


def midi_hz(m: float) -> float:
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def notes(pitches=(40, 45, 50, 52), count: int = 8, decay_s: float = 0.3, gap: float = 0.5,
          harmonics=(1, 2, 3, 4), pick: float = 0.0, seed: int = 0) -> np.ndarray:
    """``count`` notes ``gap`` seconds apart, cycling through MIDI ``pitches``; ``pick``
    adds a burst of noise at the start of each (a hard pick)."""
    rng = np.random.default_rng(seed)
    n = int(RATE * gap)
    t = np.arange(n) / RATE
    x = np.zeros(int(RATE * (gap * count + 0.4)), dtype=np.float32)
    for k in range(count):
        f = midi_hz(pitches[k % len(pitches)])
        env = np.minimum(t / 0.004, 1.0) * np.exp(-t / decay_s)
        s = sum(np.sin(2 * np.pi * f * h * t) / h for h in harmonics) * env
        if pick:
            s = s + pick * rng.standard_normal(n) * np.exp(-t / 0.02)
        i = int(k * n)
        x[i:i + n] += (0.2 * s).astype(np.float32)
    return x
