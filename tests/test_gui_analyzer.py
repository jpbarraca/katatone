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
        _Proc.cmd = cmd
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
    app.sb_amp = types.SimpleNamespace(value="KATANA3")
    status = []
    app.set_status = status.append
    return app, status


def test_open_analyzer_passes_only_the_card(tmp_path, monkeypatch) -> None:
    app, status = _app(tmp_path, monkeypatch)
    app.open_analyzer()
    assert _Proc.cmd[1:] == ["-m", "katatone.gui.analyzer", "--card", "KATANA3"]


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
