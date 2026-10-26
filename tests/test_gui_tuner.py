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

def test_the_intonation_check_listens_for_both_frets_and_lists_both_notes_on_the_chips(window) -> None:
    window.intonation.value = True
    window._set_intonation()
    assert window.tuner.frets == (0, 12)
    names = [c.content.controls[0].value for c in window._chips]
    assert names == ["E2", "A2", "D3", "G3", "B3", "E4"]              # the open strings stay the labels
    low = window._chips[0].content.controls
    assert low[1].value == f"{pitch.note_freq('E2'):.1f} Hz" and low[2].value.startswith("12th E3 ")
    assert "12th" in window.help.value
    std = pitch.TUNINGS["Standard (E A D G B E)"]
    window.apply(pitch.measure(226, std, fret=(0, 12)), None)
    assert window.note.value == "A3" and "move the saddle back" in window.hint.value
    window.intonation.value = False
    window._set_intonation()
    assert window.tuner.frets == (0,) and len(window._chips[0].content.controls) == 2


def test_an_open_string_in_tune_is_followed_by_the_12th_fret(window) -> None:
    std = pitch.TUNINGS["Standard (E A D G B E)"]
    both = (0, 12)
    assert tn.direction_hint(pitch.measure(110, std, fret=both), True) == \
        ("Open string in tune: now play the 12th fret", "CHECK_CIRCLE")
    assert tn.direction_hint(pitch.measure(110, std), False)[0] == "In tune"      # no check: as before
    assert tn.direction_hint(pitch.measure(105, std, fret=both), True)[0] == "Too low: tune up"
    window.intonation.value = True
    window._set_intonation()
    window.apply(pitch.measure(110, std, fret=both), None)
    assert window.hint.value.startswith("Open string in tune") and window.note.value == "A2"


def test_the_detail_says_what_the_12th_fret_is_compared_with() -> None:
    std = pitch.TUNINGS["Standard (E A D G B E)"]
    both = (0, 12)
    assert tn.detail_text(None) == ""
    assert tn.detail_text(pitch.measure(108, std)).startswith("108.0 Hz   target 110.0 Hz")
    exact = tn.detail_text(pitch.measure(222, std, fret=both))
    assert exact.startswith("12th fret 222.0 Hz") and "exact octave 220.0 Hz" in exact and "open string first" in exact
    heard = tn.detail_text(pitch.measure(222, std, fret=both, opens={1: (109.0, 7.4)}))
    assert "twice the open string, 218.0 Hz" in heard and "109.0 Hz heard 7 s ago" in heard
    assert heard.endswith("+31 cents")


def test_the_chromatic_mode_has_no_intonation_check(window) -> None:
    window.intonation.value = True
    window._set_intonation()
    window.mode.value = pitch.CHROMATIC
    window._set_mode()
    assert window.intonation.disabled and window.tuner.frets == (0,)
    window.mode.value = "Drop D"
    window._set_mode()
    assert not window.intonation.disabled and window.tuner.frets == (0, 12)


# ---- the fine needle and the strobe ----

def test_the_fine_needle_spreads_ten_cents_over_the_scale() -> None:
    fine = tn.DISPLAYS["fine"]
    m = tn.MARGIN
    assert tn.cents_x(-10, 400, fine["span"]) == m and tn.cents_x(10, 400, fine["span"]) == 400 - m
    assert tn.cents_x(5, 400, fine["span"]) == pytest.approx(200 + (400 - 2 * m) / 4)
    shapes = tn.meter_shapes(400, 100, 3, False, **fine)
    labels = {s.value for s in shapes if isinstance(s, cv.Text)}
    assert labels == {"-10", "-5", "0", "+5", "+10"}
    ticks = [s for s in shapes if isinstance(s, cv.Line)]
    assert len(ticks) == 1 + 21 + 1                     # baseline, a tick per cent, the needle
    zone = next(s for s in shapes if isinstance(s, cv.Rect))
    assert zone.width == pytest.approx((400 - 2 * tn.MARGIN) / 2)    # +-5 cents is half the scale


def test_the_default_needle_is_unchanged_by_the_new_arguments() -> None:
    assert tn.meter_shapes(400, 100, -20, True) == tn.meter_shapes(400, 100, -20, True, **tn.DISPLAYS["needle"])


def test_strobe_drifts_right_when_sharp_left_when_flat_and_stands_still_in_tune() -> None:
    assert tn.strobe_speed(10) > 0 > tn.strobe_speed(-10)
    assert tn.strobe_speed(0) == 0 and tn.strobe_speed(None) == 0
    assert tn.strobe_speed(1000) == tn.STROBE_MAX_SPEED and tn.strobe_speed(-1000) == -tn.STROBE_MAX_SPEED
    assert tn.strobe_speed(2) == pytest.approx(2 * tn.STROBE_SPEED)


def bars(shapes) -> list[tuple[float, float]]:
    return [(round(s.x, 6), round(s.width, 6)) for s in shapes[1:-1]]


def test_strobe_bars_stay_inside_the_scale_and_repeat_every_spacing() -> None:
    a = tn.strobe_shapes(400, 100, 7.0, 5, False)
    assert all(x >= tn.MARGIN and x + w <= 400 - tn.MARGIN + 1e-9 for x, w in bars(a))
    assert bars(a) == bars(tn.strobe_shapes(400, 100, 7.0 + tn.STROBE_SPACING, 5, False))
    assert bars(a) != bars(tn.strobe_shapes(400, 100, 17.0, 5, False))
    moved = tn.strobe_shapes(400, 100, 17.0, 5, False)
    assert bars(moved)[1][0] - bars(a)[1][0] == pytest.approx(10.0)       # a positive phase moves right


def test_strobe_colour_says_in_tune_off_tune_or_silent() -> None:
    colour = lambda cents, ok: tn.strobe_shapes(400, 100, 0, cents, ok)[1].paint.color
    assert colour(0, True) == tn.IN_TUNE_COLOR and colour(20, False) == tn.OFF_COLOR
    assert colour(None, False) == tn.theme.TICK


def test_the_window_offers_the_three_displays_and_draws_each(window) -> None:
    assert [o.key for o in window.display.options] == ["needle", "fine", "strobe"]
    std = pitch.TUNINGS["Standard (E A D G B E)"]
    for style in ("needle", "fine", "strobe"):
        window.display.value = style
        window._set_display()
        window.apply(pitch.measure(108, std), None)
        assert window.meter.shapes


def test_the_strobe_in_the_window_drifts_with_the_error(window, monkeypatch) -> None:
    std = pitch.TUNINGS["Standard (E A D G B E)"]
    clock = [100.0]
    monkeypatch.setattr(tn.time, "monotonic", lambda: clock[0])
    window.display.value = "strobe"
    window._phase_time = clock[0]
    start = window._phase
    for _ in range(10):                                   # a second of a flat string
        clock[0] += 0.1
        window.apply(pitch.measure(108, std), None)
    assert window._phase < start                          # flat: left
    flat_end = window._phase
    for _ in range(10):                                   # a second of a string in tune
        clock[0] += 0.1
        window.apply(pitch.measure(110, std), None)
    assert window._phase == flat_end                      # in tune: still
    clock[0] += 5                                         # the loop stalled: no jump
    window.apply(pitch.measure(113, std), None)
    assert window._phase - flat_end <= tn.STROBE_MAX_SPEED * 0.2 + 1e-9
