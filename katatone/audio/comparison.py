"""What the analyzer compares: the live spectrum, a reference recording and two takes.

Kept apart from the window so it can be tested: it holds the reference, the
snapshots A and B (average spectra captured from the live input), and answers
"what to draw" and "what is the difference" for the chosen comparison.  Spectra
captured on one frequency axis (logarithmic or linear) are carried over to
another by interpolation.
"""
from __future__ import annotations

import numpy as np

from . import reference as ref

#: The comparisons offered: key -> (first, second, how they read in hints).
MODES = {
    "ref": ("live", "reference", "your tone", "the reference"),
    "A": ("live", "A", "your tone", "take A"),
    "B": ("live", "B", "your tone", "take B"),
    "AB": ("A", "B", "take A", "take B"),
}


def resample(levels: np.ndarray, from_freqs: np.ndarray, to_freqs: np.ndarray) -> np.ndarray:
    """``levels`` measured at ``from_freqs``, on ``to_freqs`` (same points: unchanged)."""
    if from_freqs.shape == to_freqs.shape and np.allclose(from_freqs, to_freqs):
        return levels
    return np.interp(to_freqs, from_freqs, levels)


class Comparison:
    """The reference, the takes and the chosen comparison."""

    def __init__(self) -> None:
        self.reference: ref.Reference | None = None
        self.takes: dict[str, tuple[np.ndarray, np.ndarray]] = {}   # name -> (freqs, levels)
        self.mode = "ref"
        self.match_level = True

    # -- what is held ----------------------------------------------------------
    def set_take(self, name: str, freqs: np.ndarray, levels: np.ndarray) -> None:
        """Keep the live average spectrum as take ``name`` (``"A"`` or ``"B"``)."""
        self.takes[name] = (freqs.copy(), levels.copy())

    def clear_take(self, name: str) -> None:
        self.takes.pop(name, None)

    def available(self) -> list[str]:
        """The comparisons that can be made now."""
        out = []
        for key in MODES:
            if self._needs(key) <= self._held():
                out.append(key)
        return out

    def _held(self) -> set[str]:
        held = set(self.takes)
        if self.reference is not None:
            held.add("reference")
        return held

    @staticmethod
    def _needs(key: str) -> set[str]:
        """What a comparison needs held (the live spectrum always exists)."""
        return {n for n in MODES[key][:2] if n != "live"}

    # -- levels on the live axis --------------------------------------------------
    def _levels(self, name: str, freqs: np.ndarray) -> np.ndarray | None:
        if name == "reference":
            r = self.reference
            return None if r is None else resample(r.levels, r.freqs, freqs)
        take = self.takes.get(name)
        return None if take is None else resample(take[1], take[0], freqs)

    def traces(self, live: np.ndarray, freqs: np.ndarray) -> list[tuple[str, np.ndarray]]:
        """The held spectra to draw behind the live one, as ``(name, levels)``, lined up
        with the live level when ``match_level`` is on."""
        out = []
        for name in ("reference", "A", "B"):
            lv = self._levels(name, freqs)
            if lv is None:
                continue
            if self.match_level:
                lv = lv + ref.level_offset(live, lv, freqs)
            out.append((name, lv))
        return out

    def difference(self, live: np.ndarray, freqs: np.ndarray,
                   mode: str | None = None) -> tuple[np.ndarray, str, str] | None:
        """The difference of the chosen comparison (first minus second).

        Returns:
            ``(difference in dB, name of the first, name of the second)`` as they read
            in hints, or None when something needed is not held yet.
        """
        key = mode or self.mode
        if key not in MODES:
            return None
        first, second, label1, label2 = MODES[key]
        a = live if first == "live" else self._levels(first, freqs)
        b = self._levels("reference" if second == "reference" else second, freqs)
        if a is None or b is None:
            return None
        return ref.difference(a, b, freqs, self.match_level), label1, label2

    def hints(self, live: np.ndarray, freqs: np.ndarray, listening: bool) -> list[str]:
        """The lines of the hints panel: what the reference is like, then what to turn."""
        lines = list(self.reference.summary()) if self.reference else []
        d = self.difference(live, freqs)
        if d is None:
            if not lines:
                lines.append("Load a reference recording, or capture takes A and B, to compare.")
            return lines
        if self.mode != "AB" and not listening:
            return lines + ["Start listening and play to get hints against this comparison."]
        diff, first, second = d
        return lines + ["", f"To bring {first} towards {second}:"] + \
            ["  " + s for s in ref.suggestions(diff, freqs, first, second)]
