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

from test_gui_resync import app  # noqa: F401  (fixture)

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
    """argv stays `python -m katatone.remote.webview …`, stderr -> log (with --log)."""
    monkeypatch.setattr(wv, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(gui_app, "_log_files", True)
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


def test_no_log_file_unless_asked(tmp_path, monkeypatch):
    """By default the helper's stderr is discarded and nothing is created."""
    monkeypatch.setattr(wv, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    app = object.__new__(gui_app.App)
    app.page = types.SimpleNamespace(run_thread=lambda fn: None)
    app.tc_proc = None
    app.tonecentral_refresh = lambda: None
    app.set_status = lambda msg: None
    assert app._tc_spawn(["https://example.org/"], "busy") is True
    assert _FakePopen.seen_kw["stderr"] == subprocess.DEVNULL
    assert list(tmp_path.iterdir()) == []


def test_tc_exit_status_reports_log_tail(tmp_path, monkeypatch):
    """A non-zero exit surfaces the webview log's tail in the status."""
    monkeypatch.setattr(wv, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(gui_app, "_log_files", True)
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


def _confirm_dialog(app):
    app.library._tone_confirm()
    return app.page.dialogs[-1]


def test_in_a_browser_tone_central_opens_in_a_new_tab(app, monkeypatch):
    """The button itself carries the link (so the browser lets the tab open),
    and no desktop webview is started."""
    from katatone.remote.webview import DEFAULT_URL
    spawned = []
    monkeypatch.setattr(app, "tonecentral_open", spawned.append)
    app.page.web = True
    dialog = _confirm_dialog(app)
    go = dialog.actions[1]
    assert go.url.url == DEFAULT_URL and go.url.target.name == "BLANK"
    go.on_click(None)
    assert spawned == [] and app.page.dialogs == []


def test_on_the_desktop_tone_central_opens_the_window(app, monkeypatch):
    from katatone.remote.webview import DEFAULT_URL
    spawned = []
    monkeypatch.setattr(app, "tonecentral_open", spawned.append)
    app.page.web = False
    go = _confirm_dialog(app).actions[1]
    assert go.url is None
    go.on_click(None)
    assert spawned == [DEFAULT_URL]
