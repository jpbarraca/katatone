"""The METRONOME tab: its controls and what a MIDI mapping can do to it."""
from __future__ import annotations

import types

import flet.controls.base_control as bc
import pytest

from katatone.audio import devices, metronome as mt
from katatone.gui import metronome_pane as mp
from katatone.midi.mapping import METRONOME_OPS


class StubApp:
    def __init__(self) -> None:
        self.statuses: list[str] = []

    def set_status(self, msg: str) -> None:
        self.statuses.append(msg)


@pytest.fixture
def pane(monkeypatch):
    monkeypatch.setattr(bc.BaseControl, "update", lambda self: None)
    monkeypatch.setattr(mp, "list_output_devices",
                        lambda: [devices.Device("default", "default"), devices.Device("hw:K", "K")])
    started: list[mt.Metronome] = []
    monkeypatch.setattr(mt.Metronome, "start", lambda self: (started.append(self), setattr(self, "_proc", object())))
    monkeypatch.setattr(mt.Metronome, "stop", lambda self: setattr(self, "_proc", None))
    p = mp._MetronomePane(StubApp())
    p._outputs_thread.join(2)
    p.started = started
    return p


def test_every_midi_op_is_handled(pane) -> None:
    for op in METRONOME_OPS:
        pane.op(op, 5)                               # none may raise
    assert pane.app.statuses == []


def test_toggle_starts_and_stops_and_the_button_follows(pane) -> None:
    pane.op("toggle")
    assert pane.engine.running and pane.start_btn.content.value == "Stop"
    pane.op("toggle")
    assert not pane.engine.running and pane.start_btn.content.value == "Start"
    pane.op("start"); pane.op("start")
    assert len(pane.started) == 3 and pane.engine.running      # a second start is the engine's no-op
    pane.op("stop")
    assert not pane.engine.running


def test_tempo_steps_stay_inside_the_limits(pane) -> None:
    pane.engine.set_bpm(100)
    pane.op("tempo_up", 7)
    assert pane.engine.bpm == 107 and pane.bpm_text.value == "107"
    pane.op("tempo_down", 50)
    assert pane.engine.bpm == 57
    pane.op("tempo_down", 50)
    assert pane.engine.bpm == mt.BPM_MIN
    pane.engine.set_bpm(298)
    pane.op("tempo_up", 50)
    assert pane.engine.bpm == mt.BPM_MAX


def test_tap_sets_the_tempo_from_two_taps(pane, monkeypatch) -> None:
    times = iter([10.0, 10.5, 11.0])
    monkeypatch.setattr(mp.time, "monotonic", lambda: next(times))
    pane.op("tap")
    before = pane.engine.bpm
    assert pane.engine.bpm == before                 # one tap is not a tempo
    pane.op("tap")
    assert pane.engine.bpm == 120
    pane.op("tap")
    assert pane.engine.bpm == 120


def test_start_uses_the_settings_on_screen(pane) -> None:
    pane.output.value, pane.channel.value, pane.beats.value, pane.subdivision.value = "hw:K", "3", "7", "2"
    pane.op("start")
    e = pane.engine
    assert (e.device, e.channels, e.route) == ("hw:K", 3, (2,))
    assert (e.gen.beats, e.gen.subdivision) == (7, 2)
    pane.channel.value = "all"
    pane._output_changed()                           # restarts on the new routing while playing
    assert (e.channels, e.route) == (2, None) and e.running


def test_bar_changes_are_applied_while_playing_and_the_lamps_follow(pane) -> None:
    pane.op("start")
    pane.beats.value = "6"
    pane._bar_changed()
    assert pane.engine.gen.beats == 6 and len(pane.lights.controls) == 6
    pane._clicked(2, 0)
    on = [i for i, c in enumerate(pane.lights.controls) if c.bgcolor != pane.lights.controls[0].bgcolor]
    assert on == [2]
    pane._clicked(2, 1)                              # a subdivision does not move the lamp
    assert pane._lit == 2


def test_an_aplay_failure_is_reported(pane) -> None:
    pane.op("start")
    pane._failed("no such device")
    assert pane.app.statuses == ["Metronome stopped: no such device"]
    assert pane._lit == -1


def test_the_outputs_are_listed_by_a_thread_and_the_choice_is_kept(monkeypatch) -> None:
    import threading
    monkeypatch.setattr(bc.BaseControl, "update", lambda self: None)
    gate = threading.Event()

    def slow():
        gate.wait(2)
        return [devices.Device("default", "default"), devices.Device("hw:K", "K")]

    monkeypatch.setattr(mp, "list_output_devices", slow)
    p = mp._MetronomePane(StubApp())                 # returns at once, the list is not in yet
    assert [o.key for o in p.output.options] == ["default"]
    p.output.value = "hw:K"                           # chosen while the list loads
    gate.set()
    p._outputs_thread.join(2)
    assert [o.key for o in p.output.options] == ["default", "hw:K"]
    assert p.output.value == "hw:K"


def test_releasing_the_app_stops_the_click(pane) -> None:
    from katatone.gui.app import App
    pane.op("start")
    assert pane.engine.running
    App.stop_metronome(types.SimpleNamespace(metronome_view=pane))
    assert not pane.engine.running
    App.stop_metronome(types.SimpleNamespace())      # no pane: nothing to stop
