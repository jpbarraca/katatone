"""Headless tests for the Tone Central webview spawn in the GUI shell.

The GUI cannot host a GTK window on Linux, so it launches
:mod:`katatone.remote.webview` as a subprocess; these tests pin the
spawn contract (argv, stderr captured to the log file) and the
abnormal-exit reporting, without a display or a Flet runtime.
"""
from __future__ import annotations

import subprocess
import sys
import types

from katatone.gui import app as gui_app
from katatone.remote import webview as wv


class _FakePopen:
    """Records the spawn contract; the process is already "exited"."""

    def __init__(self, args, **kw) -> None:
        _FakePopen.seen_args = args
        _FakePopen.seen_kw = kw
        self.returncode = 0

    def poll(self):
        return 0

    def wait(self):
        return 0


def test_tc_spawn_contract(tmp_path, monkeypatch):
    """argv stays `python -m katatone.remote.webview …`, stderr -> log."""
    monkeypatch.setattr(wv, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    app = object.__new__(gui_app.App)
    app.page = types.SimpleNamespace(run_thread=lambda fn: None)
    app.tc_proc = None
    app.tonecentral_refresh = lambda: None
    app.set_status = lambda msg: None

    assert app._tc_spawn(["https://example.org/"], "busy") is True
    args = _FakePopen.seen_args
    assert args[0] == sys.executable
    assert args[1:] == ["-m", "katatone.remote.webview", "https://example.org/"]
    log_file = _FakePopen.seen_kw["stderr"]
    assert log_file.name == str(tmp_path / "webview.log")
    assert _FakePopen.seen_kw["stdout"] == subprocess.DEVNULL
    log_file.close()


def test_tc_exit_status_reports_log_tail(tmp_path, monkeypatch):
    """A non-zero exit surfaces the webview log's tail in the status."""
    monkeypatch.setattr(wv, "data_dir", lambda: tmp_path)
    (tmp_path / "webview.log").write_text(
        "line one\n"
        "webview unavailable (name 'Gtk' is not defined); "
        "opening the page in the default browser instead.\n")

    app = object.__new__(gui_app.App)
    msg = app._tc_exit_status(1)
    assert "code 1" in msg
    assert "webview unavailable" in msg


def test_tc_exit_status_without_log(tmp_path, monkeypatch):
    """No log file -> still a readable status line."""
    monkeypatch.setattr(wv, "data_dir", lambda: tmp_path)
    app = object.__new__(gui_app.App)
    msg = app._tc_exit_status(139)
    assert "code 139" in msg
    assert ":" not in msg.split(")", 1)[-1]
