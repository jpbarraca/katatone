"""The analyzer compares what you play, not the silence after it: the EQ proposal must not
change when you stop playing."""
from __future__ import annotations

import numpy as np
import pytest

from katatone.audio import dsp
from katatone.gui import analyzer as an
from test_gui_analyzer import _Page, _wav                      # noqa: F401
from test_reference import RATE, sine, write_wav


TICK = RATE // an.FPS                                           # samples that arrive between two frames


@pytest.fixture
def win(monkeypatch, tmp_path):
    """An analyzer that is 'listening' (nothing is captured: the tests push audio) with a
    reference of 880 Hz, so that a 220 Hz guitar gets a proposal."""
    monkeypatch.setattr(an, "list_input_devices", lambda: [])
    sent = []
    w = an.Analyzer(_Page(), on_apply=lambda kind, values: sent.append((kind, values)))
    w.sent = sent
    ref = tmp_path / "ref.wav"
    write_wav(ref, sine(880.0, 4.0))
    w._load_reference(str(ref))
    w._apply_loaded()
    w.capture, w._error = object(), ""                         # makes it count as listening
    w._clock = 0                                               # samples played so far
    return w


def tone(win, hz: float, ticks: int, amp: float = 0.3) -> None:
    """Play a steady tone for ``ticks`` frames of the window (phase continues between calls)."""
    for _ in range(ticks):
        t = (win._clock + np.arange(TICK)) / RATE
        win._clock += TICK
        win._on_block((amp * np.sin(2 * np.pi * hz * t)).astype(np.float32))
        win._tick()


def silence(win, ticks: int, noise: float = 0.0) -> None:
    """Frames with no playing: digital silence, or a noise floor of the given amplitude."""
    rng = np.random.default_rng(7)
    for _ in range(ticks):
        win._on_block((noise * rng.standard_normal(TICK)).astype(np.float32))
        win._tick()


def proposal(win) -> dict:
    win._refresh_hints(win.smooth.avg.copy(), True, force=True)
    return {k: v.values for k, v in (win._fits or {}).items()}


# ---- the gate ----

def test_quiet_frames_are_not_playing(win) -> None:
    assert win._playing(-50.0)                                  # a quiet note
    assert not win._playing(-70.0)                              # the noise floor
    assert not win._playing(dsp.FLOOR_DB)


def test_the_dying_tail_of_a_note_is_not_playing(win) -> None:
    assert win._playing(-10.0)
    assert not win._playing(-45.0)                              # more than 30 dB under the recent loudest
    assert win._playing(-39.0)


def test_the_loudest_recent_level_forgets_slowly(win) -> None:
    win._playing(-10.0)
    for _ in range(int(an.FPS * 15)):                           # 15 s of near silence
        win._playing(-80.0)
    assert win._loud_db == pytest.approx(-40.0, abs=0.5)        # fell 2 dB per second
    assert win._playing(-55.0)                                  # a quiet passage counts again


# ---- the average and the proposal ----

def test_silence_does_not_change_the_average(win) -> None:
    tone(win, 220.0, 30)
    silence(win, 1)                                             # the frame the note ends in
    kept = win.smooth.avg.copy()
    assert float(kept.max()) > -80
    silence(win, 90)                                            # six seconds of nothing
    assert np.array_equal(win.smooth.avg, kept)


def test_the_proposal_does_not_change_when_you_stop_playing(win) -> None:
    tone(win, 220.0, 30)
    playing = proposal(win)
    assert playing["GE-10"]["gains"] != [0] * 10               # there is something to correct
    win.eq_preview.value = "GE-10"
    win._update_apply()
    assert not win.apply_btn.disabled
    silence(win, 1)
    settled = proposal(win)
    silence(win, 90)                                            # stop playing for six seconds
    assert proposal(win) == settled                             # exactly: nothing is folded in
    assert not win.apply_btn.disabled
    win._apply_eq()                                             # what is sent is what was proposed
    assert win.sent == [("GE-10", settled["GE-10"])]
    # the frame the note ended in may nudge it a little, never to a strong cut
    assert max(abs(a - b) for a, b in zip(playing["GE-10"]["gains"], settled["GE-10"]["gains"])) <= 2


def test_the_old_behaviour_would_have_produced_a_strong_cut(win, monkeypatch) -> None:
    """With the gate switched off (everything counts as playing) silence drags the proposal away:
    this is the fault the gate removes."""
    monkeypatch.setattr(an, "LIVE_GATE_DB", -400.0)
    monkeypatch.setattr(an, "LIVE_GATE_REL_DB", 400.0)
    tone(win, 220.0, 30)
    playing = proposal(win)["GE-10"]["gains"]
    silence(win, 90, noise=1e-4)                                # a quiet input: about -80 dBFS
    after = proposal(win)["GE-10"]["gains"]
    assert max(abs(a - b) for a, b in zip(playing, after)) >= 6


def test_a_noise_floor_does_not_move_the_proposal_either(win) -> None:
    tone(win, 220.0, 30)
    silence(win, 1, noise=1e-4)
    settled = proposal(win)
    silence(win, 90, noise=1e-4)
    assert proposal(win) == settled


def test_playing_again_updates_the_proposal(win) -> None:
    tone(win, 220.0, 30)
    first = proposal(win)
    silence(win, 30)
    tone(win, 3000.0, 40)
    assert proposal(win) != first


def test_nothing_is_proposed_before_anything_was_played(win) -> None:
    silence(win, 20)                                            # listening to silence from the start
    win._refresh_hints(win.smooth.avg.copy(), True, force=True)
    assert win._fits is None
    assert any("Start listening and play" in c.value for c in win.hints.controls)


def test_clearing_forgets_the_loud_level(win) -> None:
    win._playing(-5.0)
    win._clear()
    assert win._loud_db == dsp.FLOOR_DB
