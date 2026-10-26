"""Analyzer window geometry helpers and the main window's launch logic."""
from __future__ import annotations

import subprocess
import types

import pytest

from katatone.gui import analyzer as an
from katatone.gui import app as gui_app
from katatone.remote import webview as wv


def test_meter_scale_runs_from_0_db_at_the_top() -> None:
    assert an.meter_y(0, 200) == 0
    assert an.meter_y(an.METER_MIN_DB, 200) == 200
    assert an.meter_y(-30, 200) == pytest.approx(100)
    assert an.meter_y(10, 200) == 0 and an.meter_y(-200, 200) == 200   # clamped


def test_frequency_axis_is_logarithmic() -> None:
    assert an.freq_x(20, 1000) == pytest.approx(0)
    assert an.freq_x(20000, 1000) == pytest.approx(1000)
    assert an.freq_x(632.45, 1000) == pytest.approx(500, abs=1)        # geometric middle


class _Proc:
    def __init__(self, cmd, **kw):
        _Proc.cmd, _Proc.kw = cmd, kw
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0


def _app(tmp_path, monkeypatch):
    monkeypatch.setattr(wv, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "Popen", _Proc)
    app = object.__new__(gui_app.App)
    app.an_proc = None
    app.page = types.SimpleNamespace(web=False)
    app.sb_amp = types.SimpleNamespace(value="KATANA3")
    status = []
    app.set_status = status.append
    return app, status


def test_open_analyzer_passes_the_card_and_asks_for_the_apply_pipe(tmp_path, monkeypatch) -> None:
    app, status = _app(tmp_path, monkeypatch)
    app.open_analyzer()
    assert _Proc.cmd[1:] == ["-m", "katatone.gui.analyzer", "--apply-pipe", "--card", "KATANA3"]
    assert _Proc.kw["stdout"] == subprocess.PIPE


def test_analyzer_output_is_discarded_unless_log_is_on(tmp_path, monkeypatch) -> None:
    app, status = _app(tmp_path, monkeypatch)
    app.open_analyzer()
    assert _Proc.kw["stderr"] == subprocess.DEVNULL and list(tmp_path.iterdir()) == []
    app.stop_analyzer()
    monkeypatch.setattr(gui_app, "_log_files", True)
    app.open_analyzer()
    assert (tmp_path / "analyzer.log").exists()


def test_only_one_analyzer_window_at_a_time(tmp_path, monkeypatch) -> None:
    app, status = _app(tmp_path, monkeypatch)
    app.open_analyzer()
    first = app.an_proc
    app.open_analyzer()
    assert app.an_proc is first and "already open" in status[-1]
    app.stop_analyzer()
    assert first.returncode == 0 and app.an_proc is None


def test_linear_frequency_axis_is_evenly_spaced() -> None:
    assert an.freq_x(20, 1000, linear=True) == pytest.approx(0)
    assert an.freq_x(10000, 1000, linear=True) == pytest.approx(1000)       # the axis ends at 10 kHz
    assert an.freq_x(20000, 1000, linear=True) == pytest.approx(1000)       # beyond it: the edge
    assert an.freq_x(5010, 1000, linear=True) == pytest.approx(500)
    assert an.freq_x(20000, 1000) == pytest.approx(1000)                    # the log axis still goes to 20 kHz
    # log axis puts 1 kHz well past the middle of the linear scale's first tenth
    assert an.freq_x(1000, 1000) > 500 > an.freq_x(1000, 1000, linear=True)


def test_desktop_session_opens_a_window(tmp_path, monkeypatch) -> None:
    app, status = _app(tmp_path, monkeypatch)
    app.page = types.SimpleNamespace(web=False)
    app.open_analyzer()
    assert "--web" not in _Proc.cmd


def test_string_lines_slope_from_the_open_note_up_to_the_highest_note() -> None:
    import flet.canvas as cv
    shapes = an.string_shapes(1000, 400)
    lines = [x for x in shapes if isinstance(x, cv.Line)]
    assert len(lines) == 6
    for ln in lines:
        assert ln.y1 == 400 and ln.y2 == 0 and ln.x2 > ln.x1            # rises to the right
    assert lines[0].x1 == pytest.approx(an.freq_x(82.41, 1000))          # E2 open
    assert lines[0].x2 == pytest.approx(an.freq_x(82.41 * 2 ** (22 / 12), 1000))
    labels = [x.value for x in shapes if isinstance(x, cv.Text)]
    assert "E2 82" in labels and "D4 294" in labels and "D6 1175" in labels


def test_string_lines_follow_the_linear_axis_too() -> None:
    import flet.canvas as cv
    line = next(x for x in an.string_shapes(1000, 400, linear=True) if isinstance(x, cv.Line))
    assert line.x1 == pytest.approx(an.freq_x(82.41, 1000, True))
    assert line.x2 == pytest.approx(an.freq_x(82.41 * 2 ** (22 / 12), 1000, True))


# ----------------------------------------------------- comparing with a reference

import numpy as np

from katatone.audio import dsp, reference as ref_mod


class _Page:
    def __init__(self):
        self.window = types.SimpleNamespace()
        self.threads = []

    def add(self, *c): pass
    def update(self, *a, **k): pass
    def run_task(self, fn, *a, **k): pass
    def run_thread(self, fn, *a): fn(*a)


@pytest.fixture
def win(monkeypatch):
    monkeypatch.setattr(an, "list_input_devices", lambda: [])
    return an.Analyzer(_Page())


def _wav(path, seconds=4.0, f=330.0):
    import wave
    t = np.arange(int(48000 * seconds)) / 48000
    x = (0.3 * np.sin(2 * np.pi * f * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000); w.writeframes(x.tobytes())


def _heard(win, db=-40.0):
    win.smooth.avg[:] = db
    win.smooth.avg[(win.spectrum.freqs > 200) & (win.spectrum.freqs < 600)] = db + 25


def test_the_window_starts_with_the_comparison_controls_and_a_prompt(win) -> None:
    assert win.compare_dd.value == "ref" and win.match.value is True
    assert "Load a reference" in win.hints.controls[0].value


def test_a_reference_is_loaded_in_the_background_and_applied_on_the_next_frame(win, tmp_path) -> None:
    p = tmp_path / "part.wav"
    _wav(p)
    win._load_reference(str(p))
    assert win.cmp.reference is None                      # not applied until the loop's next frame
    win._apply_loaded()
    assert win.cmp.reference.name == "part.wav" and win.ref_label.value == "part.wav"
    assert any("Reference: part.wav" in c.value for c in win.hints.controls)


def test_a_bad_file_is_reported_and_leaves_no_reference(win, tmp_path) -> None:
    p = tmp_path / "x.mp3"
    p.write_bytes(b"nope")
    win._load_reference(str(p))
    win._apply_loaded()
    assert win.cmp.reference is None and win.status.color == an.theme.DANGER
    assert win.ref_label.value == "no reference"


def test_a_failed_load_keeps_the_reference_already_held_and_its_message_survives_the_frame(
        win, tmp_path) -> None:
    good = tmp_path / "part.wav"
    _wav(good)
    win._load_reference(str(good))
    win._apply_loaded()
    bad = tmp_path / "x.mp3"
    bad.write_bytes(b"nope")
    win._load_reference(str(bad))
    win._apply_loaded()
    assert win.cmp.reference.name == "part.wav" and win.ref_label.value == "part.wav"
    win._error = ""                                      # (no sound card in the test)
    win._tick()                                          # the next frame must not wipe the message
    assert win.status.color == an.theme.DANGER and win.status.value
    win._notice = (win._notice[0], win._notice[1], 0.0)  # expired
    win._tick()
    assert win.status.value == "Stopped."


def test_taking_a_snapshot_needs_sound_and_switches_the_comparison(win) -> None:
    win._capture("A")
    assert "A" not in win.cmp.takes and "Nothing heard" in win.status.value
    _heard(win)
    win._capture("A")
    assert "A" in win.cmp.takes and win.take_a.content.value == "Take A set"
    assert win.cmp.mode == "A" and win.compare_dd.value == "A"          # nothing else to compare with
    win.smooth.avg[:] = win.smooth.avg + 6.0
    win._capture("B")
    win.compare_dd.value = "AB"
    win._set_compare()
    assert win.cmp.mode == "AB" and win.cmp.difference(win.smooth.avg, win.spectrum.freqs) is not None


def test_the_difference_and_the_spectrum_are_drawn_with_the_reference(win, tmp_path) -> None:
    p = tmp_path / "part.wav"
    _wav(p)
    win._load_reference(str(p))
    win._apply_loaded()
    _heard(win)
    peak = win.smooth.avg.copy()
    win._draw_spectrum(win.smooth.avg.copy(), peak)
    win._draw_diff(win.smooth.avg.copy())
    assert win.spec_img.src and win.diff_img.src
    assert "minus the reference" in win.diff_title.value
    win.cmp.reference = None
    win._draw_diff(win.smooth.avg.copy())
    assert "load a reference" in win.diff_title.value


def test_hints_say_what_to_turn_once_listening(win, tmp_path) -> None:
    p = tmp_path / "part.wav"
    _wav(p)
    win._load_reference(str(p))
    win._apply_loaded()
    _heard(win, -50.0)
    win._refresh_hints(win.smooth.avg.copy(), listening=True, force=True)
    text = " ".join(c.value for c in win.hints.controls)
    assert "To bring your tone towards the reference" in text
    win._refresh_hints(win.smooth.avg.copy(), listening=False, force=True)
    assert "Start listening" in win.hints.controls[-1].value


def test_clearing_the_reference_and_toggling_level_matching(win, tmp_path) -> None:
    p = tmp_path / "part.wav"
    _wav(p)
    win._load_reference(str(p))
    win._apply_loaded()
    win._clear_reference()
    assert win.cmp.reference is None and win.ref_label.value == "no reference"
    win.match.value = False
    win._set_match()
    assert win.cmp.match_level is False


def test_tap_tempo_shows_the_bpm_and_delay_times(win, monkeypatch) -> None:
    clock = iter([0.0, 0.5, 1.0])
    monkeypatch.setattr(an.time, "monotonic", lambda: next(clock))
    win._tap()
    assert win.tap_label.value == "keep tapping…"
    win._tap()
    assert win.tap_label.value.startswith("120 BPM") and "dotted eighth 375 ms" in win.tap_label.value


def test_files_of_every_audio_type_can_be_chosen() -> None:
    assert {"wav", "mp3", "flac", "ogg"} <= set(ref_mod.AUDIO_TYPES)


# ------------------------------------------------- notes, EQ proposals, spectrogram

from synth import notes as _notes  # noqa: E402


def _play(win, **kw):
    """Feed notes through the same path as the capture thread."""
    x = _notes(**kw)
    for i in range(0, x.size, 960):
        win._on_block(x[i:i + 960])


def test_the_playing_is_analysed_every_few_seconds_from_the_history(win) -> None:
    _play(win, decay_s=0.3)
    assert win._insight.envelope is None                     # nothing analysed yet
    win._update_insight()
    assert win._insight.envelope is not None and win._insight.content is not None
    first = win._insight
    win._update_insight()                                    # too soon: left alone
    assert win._insight is first
    win._clear()                                             # forgets the history and the insight
    assert win.history.filled == 0 and win._insight.envelope is None


def test_a_take_keeps_what_was_played_and_is_checked_against_the_reference(win, tmp_path) -> None:
    import wave
    p = tmp_path / "riff.wav"
    x = _notes(pitches=(64, 69, 71, 76))                     # a high lead as the reference
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    win._load_reference(str(p))
    win._apply_loaded()
    assert win.cmp.reference.envelope is not None and win.cmp.reference.content is not None
    _play(win, pitches=(40, 45, 50, 52))
    _heard(win)
    win._capture("A")
    assert win.cmp.take_insight["A"].content is not None
    win._refresh_hints(win.smooth.avg.copy(), True, force=True)
    texts = [c.value for c in win.hints.controls]
    assert any("playing differs" in t for t in texts) and any("Different register" in t for t in texts)


def test_eq_proposals_are_listed_and_previewed_on_request(win, tmp_path) -> None:
    p = tmp_path / "part.wav"
    _wav(p)
    win._load_reference(str(p))
    win._apply_loaded()
    _heard(win)
    avg = win.smooth.avg.copy()
    win._refresh_hints(avg, True, force=True)
    assert win._fits and set(win._fits) == {"GE-10", "PEQ"}
    assert any("EQ settings that fit" in c.value for c in win.hints.controls)
    assert win._preview_response() is None                   # off by default
    win.eq_preview.value = "PEQ"
    assert len(win._preview_response()) == len(win.spectrum.freqs)
    win._draw_spectrum(avg, avg.copy())
    win._draw_diff(avg)
    assert "PEQ proposal" in win.diff_title.value
    win.eq_preview.value = "off"
    win._draw_diff(avg)
    assert "proposal" not in win.diff_title.value


def test_no_proposals_while_not_listening(win, tmp_path) -> None:
    p = tmp_path / "part.wav"
    _wav(p)
    win._load_reference(str(p))
    win._apply_loaded()
    win._refresh_hints(win.smooth.avg.copy(), False, force=True)
    assert win._fits is None


def test_the_lower_panel_can_show_a_spectrogram_of_the_last_seconds(win) -> None:
    assert win.lower.value == "diff"
    for k in range(30):
        win._columns.append(np.full(len(win.spectrum.freqs), -40.0 - k))
    win.lower.value = "sg"
    win._set_lower()
    assert any(isinstance(s, an.cv.Text) and "kHz" in s.value for s in win.diff_grid.shapes)
    win._draw_diff(win.smooth.avg.copy())
    assert "Spectrogram" in win.diff_title.value and win.diff_img.src
    win.lower.value = "diff"
    win._set_lower()
    win._draw_diff(win.smooth.avg.copy())
    assert "Spectrogram" not in win.diff_title.value


def test_changing_the_scale_drops_the_spectrogram_columns_and_proposals(win) -> None:
    win._columns.append(np.zeros(len(win.spectrum.freqs)))
    win._fits = {"PEQ": object()}
    win.scale.value = True
    win._set_scale()
    assert len(win._columns) == 0 and win._fits is None


def test_the_spectrum_is_recorded_for_the_spectrogram_each_frame_while_listening(win) -> None:
    win._error = ""
    win.capture = object()                                   # "listening"
    try:
        win._tick()
    finally:
        win.capture = None
    assert len(win._columns) == 1


# ------------------------------------------------------- the divider

class _Drag:
    def __init__(self, dy):
        self.primary_delta = dy
        self.local_delta = types.SimpleNamespace(y=dy)


def test_dragging_the_divider_up_makes_the_lower_panel_taller(win) -> None:
    assert win.lower_box.height == an.LOWER_DEFAULT
    win._spec_size = (700.0, 400.0)
    win._drag_divider(_Drag(-50.0))                    # up
    assert win.lower_box.height == an.LOWER_DEFAULT + 50
    win._drag_divider(_Drag(80.0))                     # down
    assert win.lower_box.height == an.LOWER_DEFAULT - 30


def test_the_lower_panel_height_stays_within_limits_and_leaves_room_for_the_spectrum(win) -> None:
    win._spec_size = (700.0, 300.0)
    win._set_lower_height(10_000)
    assert win.lower_box.height == 300.0 + an.LOWER_DEFAULT - an.SPEC_MIN_HEIGHT   # spectrum keeps its minimum
    win._set_lower_height(1)
    assert win.lower_box.height == an.LOWER_MIN
    win._spec_size = (700.0, 2000.0)
    win._set_lower_height(10_000)
    assert win.lower_box.height == an.LOWER_MAX


def test_double_clicking_the_divider_resets_the_height(win) -> None:
    win._spec_size = (700.0, 400.0)
    win._set_lower_height(300)
    win.divider.on_double_tap(None)
    assert win.lower_box.height == an.LOWER_DEFAULT
