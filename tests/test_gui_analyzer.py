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


def test_open_analyzer_passes_only_the_card(tmp_path, monkeypatch) -> None:
    app, status = _app(tmp_path, monkeypatch)
    app.open_analyzer()
    assert _Proc.cmd[1:] == ["-m", "katatone.gui.analyzer", "--card", "KATANA3"]


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
    assert an.freq_x(20000, 1000, linear=True) == pytest.approx(1000)
    assert an.freq_x(10010, 1000, linear=True) == pytest.approx(500)
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
