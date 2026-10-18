"""The tuner window's geometry helpers, its display logic and its launch from the app."""
from __future__ import annotations

import subprocess
import types

import flet.canvas as cv
import pytest

from katatone.audio import pitch
from katatone.gui import tuner as tn


def test_the_needle_is_centred_at_zero_and_clamped() -> None:
    assert tn.cents_x(0, 400) == 200
    m = tn.MARGIN
    assert tn.cents_x(-50, 400) == m and tn.cents_x(50, 400) == 400 - m
    assert tn.cents_x(-500, 400) == m and tn.cents_x(500, 400) == 400 - m
    assert tn.cents_x(25, 400) == pytest.approx(200 + (400 - 2 * m) / 4)


def test_meter_has_a_scale_a_zone_and_a_needle_only_with_a_reading() -> None:
    idle = tn.meter_shapes(400, 100, None, False)
    on = tn.meter_shapes(400, 100, -20, False)
    assert len(on) == len(idle) + 2                      # needle and its head
    needle = [s for s in on if isinstance(s, cv.Line)][-1]
    assert needle.x1 == needle.x2 == tn.cents_x(-20, 400)
    zone = next(s for s in idle if isinstance(s, cv.Rect))
    assert zone.x == pytest.approx(tn.cents_x(-5, 400)) and zone.width == pytest.approx((400 - 2 * tn.MARGIN) / 10)
    labels = [s.value for s in idle if isinstance(s, cv.Text)]
    assert {"0", "-50", "+50", "-25", "+25"} <= set(labels)


def test_the_hint_says_which_way_to_turn() -> None:
    std = pitch.TUNINGS["Standard (E A D G B E)"]
    assert tn.direction_hint(None) == ("Play a string", "")
    assert tn.direction_hint(pitch.measure(110, std))[0] == "In tune"
    assert tn.direction_hint(pitch.measure(105, std)) == ("Too low: tune up", "ARROW_UPWARD")
    assert tn.direction_hint(pitch.measure(115, std)) == ("Too high: tune down", "ARROW_DOWNWARD")


def test_the_intonation_hint_says_which_way_to_move_the_saddle() -> None:
    std = pitch.TUNINGS["Standard (E A D G B E)"]
    octave = 220.0
    assert tn.direction_hint(pitch.measure(octave, std, fret=12)) == ("Intonation is right", "CHECK_CIRCLE")
    assert tn.direction_hint(pitch.measure(226, std, fret=12)) == ("Sharp: move the saddle back", "ARROW_DOWNWARD")
    assert tn.direction_hint(pitch.measure(214, std, fret=12)) == ("Flat: move the saddle forward", "ARROW_UPWARD")


class Page:
    def __init__(self):
        self.window = types.SimpleNamespace()
        self.added = []

    def add(self, *c): self.added.extend(c)
    def update(self, *a, **k): pass
    def run_task(self, fn, *a, **k): pass


@pytest.fixture
def window(monkeypatch):
    monkeypatch.setattr(tn, "list_input_devices", lambda: [])
    return tn.TunerWindow(Page())


def test_window_builds_a_chip_per_string_and_reports_no_sound_card(window) -> None:
    assert len(window._chips) == 6
    window._tick()
    assert "No sound card" in window.status.value and window.note.value == "--"


def test_window_shows_a_reading(window) -> None:
    std = pitch.TUNINGS["Standard (E A D G B E)"]
    window.apply(pitch.measure(108, std), None)
    assert window.note.value == "A2" and "Too low" in window.hint.value
    assert "-" in window.detail.value and "110.0 Hz" in window.detail.value
    assert window.note.color == tn.OFF_COLOR
    window.apply(pitch.measure(110, std), None)
    assert window.note.color == tn.IN_TUNE_COLOR and window.hint.value == "In tune"
    window.apply(None, None)
    assert window.note.value == "--" and window.detail.value == ""


def test_the_string_matching_the_sound_is_highlighted_and_a_lock_is_marked(window) -> None:
    std = pitch.TUNINGS["Standard (E A D G B E)"]
    window.apply(pitch.measure(110, std), None)
    widths = [c.border.top.width for c in window._chips]
    assert widths == [1, 2, 1, 1, 1, 1]
    window._lock(3)
    assert window.tuner.locked == 3
    window.apply(pitch.measure(110, std, locked=3), 3)
    assert window._chips[3].bgcolor == tn.theme.TAB_ACTIVE
    window._lock(3)                                      # again: automatic
    assert window.tuner.locked is None


def test_changing_the_tuning_or_reference_rebuilds_the_chips(window) -> None:
    window.mode.value = "Bass (E A D G)"
    window._set_mode()
    assert len(window._chips) == 4
    window.mode.value = pitch.CHROMATIC
    window._set_mode()
    assert window._chips == []
    window.mode.value = "Drop D"
    window._set_mode()
    window.reference.value = "432"
    window._set_reference()
    assert window.tuner.a4 == 432
    low = window._chips[0].content.controls
    assert low[0].value == "D2" and low[1].value == f"{pitch.note_freq('D2', 432):.1f} Hz"


# ---------------------------------------------------------- opened from the app

class _Proc:
    def __init__(self, cmd, **kw):
        _Proc.cmd = cmd
        self.returncode = None

    def poll(self): return self.returncode
    def terminate(self): self.returncode = 0


def _app(monkeypatch):
    from katatone.gui import app as gui_app
    monkeypatch.setattr(subprocess, "Popen", _Proc)
    app = object.__new__(gui_app.App)
    app.an_proc = app.tuner_proc = None
    app.page = types.SimpleNamespace(web=False)
    app.sb_amp = types.SimpleNamespace(value="KATANA3")
    status = []
    app.set_status = status.append
    return app, status


def test_the_tuner_opens_in_its_own_process_with_the_amp_card(monkeypatch) -> None:
    app, status = _app(monkeypatch)
    app.open_tuner()
    assert _Proc.cmd[1:] == ["-m", "katatone.gui.tuner", "--mute-pipe", "--card", "KATANA3"]
    assert app.an_proc is None and status[-1] == "Tuner window opened."


def test_only_one_tuner_at_a_time_and_closing_it_closes_both_tools(monkeypatch) -> None:
    app, status = _app(monkeypatch)
    app.open_tuner()
    first = app.tuner_proc
    app.open_tuner()
    assert app.tuner_proc is first and "already open" in status[-1]
    app.open_analyzer()                                  # independent of the analyzer
    assert app.an_proc is not None and app.an_proc is not first
    second = app.an_proc
    app.stop_analyzer()
    assert first.returncode == 0 and second.returncode == 0
    assert app.tuner_proc is None and app.an_proc is None


def test_the_top_bar_has_a_tuner_button() -> None:
    from katatone.gui import statusbar
    from test_gui_login import Page as P, _texts
    app = types.SimpleNamespace(wifi_enabled=False, page=P(), amp_port=None,
                                auto_connect=True, opened=[])
    app.open_tuner = lambda: app.opened.append("tuner")
    bar = statusbar.build(app)
    assert "Tuner" in _texts(bar)


def _buttons(web):
    from katatone.gui import statusbar
    from test_gui_login import Page as P

    def find(c, out):
        if type(c).__name__ == "FilledButton":
            out.append(c)
        for attr in ("content", "controls"):
            sub = getattr(c, attr, None)
            for x in (sub if isinstance(sub, list) else [sub] if sub is not None else []):
                find(x, out)
        return out
    page = P()
    page.web = web
    app = types.SimpleNamespace(wifi_enabled=False, page=page, amp_port=None, auto_connect=True,
                                open_analyzer=lambda: opened.append("a"),
                                open_tuner=lambda: opened.append("t"))
    opened = []
    return {b.content.value: b for b in find(statusbar.build(app), [])}, opened


def test_in_a_browser_the_tools_are_pages_of_the_server_opened_in_a_new_tab() -> None:
    buttons, opened = _buttons(web=True)
    for name, route in (("Analyzer", "/analyzer"), ("Tuner", "/tuner")):
        assert buttons[name].url.url == route and buttons[name].url.target.name == "BLANK"
        buttons[name].on_click(None)
    assert opened == []                                  # no process is started


def test_on_the_desktop_the_tools_are_windows_of_their_own() -> None:
    buttons, opened = _buttons(web=False)
    assert buttons["Analyzer"].url is None and buttons["Tuner"].url is None
    buttons["Analyzer"].on_click(None)
    buttons["Tuner"].on_click(None)
    assert opened == ["a", "t"]


# ------------------------------------------------------------ muting the amp

@pytest.fixture
def muting(monkeypatch):
    monkeypatch.setattr(tn, "list_input_devices", lambda: [])
    calls = []
    return tn.TunerWindow(Page(), on_mute=calls.append), calls


def test_the_checkbox_mutes_and_unchecking_restores(muting) -> None:
    w, calls = muting
    assert not w.mute.disabled
    w.mute.value = True
    w.mute.on_change(None)
    w.mute.value = False
    w.mute.on_change(None)
    assert calls == [True, False]


def test_stopping_the_tuner_and_closing_the_window_restore_the_amp(muting) -> None:
    w, calls = muting
    w._set_mute(True)
    w._stop()
    assert calls == [True, False] and w.mute.value is False
    w._set_mute(True)
    w.close()
    assert calls == [True, False, True, False]
    w.close()
    assert calls[-1] is False and len(calls) == 4          # nothing sent twice


def test_without_an_amp_link_the_checkbox_is_disabled(window) -> None:
    assert window.mute.disabled
    window._set_mute(True)
    assert window._muted is False


def test_a_failing_mute_leaves_the_checkbox_off(monkeypatch) -> None:
    monkeypatch.setattr(tn, "list_input_devices", lambda: [])

    def boom(m):
        raise OSError("amp gone")
    w = tn.TunerWindow(Page(), on_mute=boom)
    w._set_mute(True)
    assert w.mute.value is False and "amp gone" in w._error


def test_the_main_window_relays_the_tuners_requests_and_restores_on_exit(monkeypatch) -> None:
    from katatone.gui import app as gui_app
    app = object.__new__(gui_app.App)
    sent = []
    app.set_mute = sent.append
    proc = types.SimpleNamespace(stdout=iter(["MUTE 1\n", "noise\n"]))
    app._follow_tuner(proc)                         # the window ended while muted
    assert sent == [True, False] and app._tuner_muted is False
    sent.clear()
    app._follow_tuner(types.SimpleNamespace(stdout=iter(["MUTE 1\n", "MUTE 0\n"])))
    assert sent == [True, False]


def test_in_a_browser_the_tuner_gets_the_backend_mute(monkeypatch) -> None:
    from katatone.gui import app as gui_app
    made = []

    class Tool:
        wants_mute = True

        def __init__(self, page, card, on_mute=None):
            made.append(on_mute)

        def close(self): pass
    monkeypatch.setattr(gui_app, "_tool_class", lambda page: Tool)
    backend = types.SimpleNamespace(amp_port=None, set_tuner_mute=lambda m: None,
                                    launch_tokens=gui_app.auth_mod.LaunchTokens())
    token = backend.launch_tokens.issue()
    page = types.SimpleNamespace(route=f"/tuner?token={token}", clean=lambda: None)
    gui_app._open_web_session(page, backend)
    assert made == [backend.set_tuner_mute]


# ------------------------------------------------------------ intonation

def test_the_intonation_check_aims_at_the_octaves_and_relabels_the_chips(window) -> None:
    window.intonation.value = True
    window._set_intonation()
    assert window.tuner.fret == 12
    names = [c.content.controls[0].value for c in window._chips]
    assert names == ["E3", "A3", "D4", "G4", "B4", "E5"]
    assert window._chips[0].content.controls[1].value == f"{pitch.note_freq('E3'):.1f} Hz"
    assert "12th" in window.help.value
    window.apply(pitch.measure(226, pitch.TUNINGS["Standard (E A D G B E)"], fret=12), None)
    assert window.note.value == "A3" and "move the saddle back" in window.hint.value
    window.intonation.value = False
    window._set_intonation()
    assert window.tuner.fret == 0 and window._chips[0].content.controls[0].value == "E2"


def test_the_chromatic_mode_has_no_intonation_check(window) -> None:
    window.intonation.value = True
    window._set_intonation()
    window.mode.value = pitch.CHROMATIC
    window._set_mode()
    assert window.intonation.disabled and window.tuner.fret == 0
    window.mode.value = "Drop D"
    window._set_mode()
    assert not window.intonation.disabled and window.tuner.fret == 12
