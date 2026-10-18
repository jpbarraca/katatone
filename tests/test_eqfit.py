"""Fitting the amp's graphic and parametric EQ to a difference of tones."""
from __future__ import annotations

import numpy as np
import pytest

from katatone.audio import dsp, eqfit

FREQS = dsp.Spectrum(48000, points=160).freqs


def test_a_peaking_filter_gives_its_gain_at_its_centre_and_nothing_far_away() -> None:
    r = eqfit.peaking_db(1000.0, 1.0, 6.0, np.array([1000.0, 30.0, 18000.0]))
    assert r[0] == pytest.approx(6.0, abs=0.05) and abs(r[1]) < 0.2 and abs(r[2]) < 0.6


def test_shelves_lift_one_end() -> None:
    f = np.array([40.0, 15000.0])
    low = eqfit.shelf_db(eqfit.LOW_SHELF_HZ, 6.0, f, high=False)
    high = eqfit.shelf_db(eqfit.HIGH_SHELF_HZ, 6.0, f, high=True)
    assert low[0] == pytest.approx(6.0, abs=0.3) and abs(low[1]) < 0.3
    assert high[1] == pytest.approx(6.0, abs=0.3) and abs(high[0]) < 0.3


def test_nothing_to_correct_proposes_nothing() -> None:
    fits = eqfit.fit_all(np.zeros(len(FREQS)), FREQS)
    for fit in fits.values():
        assert not fit.useful and "nothing worth changing" in fit.lines()[0]
        assert np.allclose(fit.response, 0.0, atol=0.01)


def test_the_graphic_fit_follows_a_broad_difference_in_whole_db() -> None:
    target = eqfit.peaking_db(1000.0, 0.8, 6.0, FREQS)          # +6 dB around 1 kHz
    fit = eqfit.fit_graphic(target, FREQS)
    gains = dict(fit.settings)
    assert fit.useful and fit.after < fit.before / 2
    assert gains["1 kHz"].endswith("dB") and int(gains["1 kHz"].split()[0]) >= 4
    assert all(abs(int(v.split()[0])) <= eqfit.PROPOSE_DB for v in gains.values())
    assert np.abs(fit.response - target)[(FREQS > 300) & (FREQS < 3000)].max() < 3.0


def test_the_parametric_fit_finds_the_frequency_q_and_gain_of_two_bands() -> None:
    target = eqfit.peaking_db(800.0, 2.0, 5.0, FREQS) + eqfit.peaking_db(3150.0, 1.0, -4.0, FREQS)
    fit = eqfit.fit_parametric(target, FREQS)
    s = dict(fit.settings)
    assert s["Low-mid"] == "800 Hz Q 2 +4 dB" or s["Low-mid"].startswith("800 Hz Q 2 +5")
    assert s["High-mid"].startswith("3150 Hz Q 1 -4")
    assert fit.after < 0.5 and fit.after < fit.before / 3
    assert any("Low-mid 800 Hz" in ln for ln in fit.lines())


def test_the_parametric_fit_uses_the_shelves_for_the_ends() -> None:
    target = eqfit.shelf_db(eqfit.LOW_SHELF_HZ, 6.0, FREQS, high=False)
    fit = eqfit.fit_parametric(target, FREQS)
    low = int(dict(fit.settings)["Low"].split()[0])
    assert 3 <= low <= 7 and fit.after < fit.before / 2         # a little under: it must earn each dB


def test_small_differences_are_not_worth_a_change() -> None:
    # 0.4 dB hump: the gain penalty keeps every control at 0
    fit = eqfit.fit_parametric(eqfit.peaking_db(1000.0, 1.0, 0.4, FREQS), FREQS)
    assert not fit.useful


def test_fits_are_fast_enough_for_twice_a_second() -> None:
    import time
    target = eqfit.peaking_db(500.0, 1.0, 5.0, FREQS)
    eqfit.fit_all(target, FREQS)                      # builds the tables
    t = time.perf_counter()
    eqfit.fit_all(target, FREQS)
    assert time.perf_counter() - t < 0.25
