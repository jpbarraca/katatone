"""The App as the router's host: pedal -> amp -> editor, with fake inputs."""
from __future__ import annotations

import time

import pytest

from katatone.gui import app as app_mod
from katatone.midi import mapping as mp
from katatone.midi.events import ControlChange, ProgramChange
from katatone.midi.manager import MidiManager
from katatone.midi.mapping import (ChannelSelect, EffectAction, ExprTarget,
                                   ExpressionAction, Mapping, Profile, Trigger)
from katatone.midi.store import ProfileStore
from katatone.address.map import load_map
from test_gui_resync import _volume, app as _app            # noqa: F401  (fixture)
from test_midi_manager import FakeInput
from test_midi_router import FakeTransport, wait_for

EDIT = load_map()["PATCH"]


@pytest.fixture
def midi_app(_app, tmp_path, monkeypatch):
    """The headless connected App with a MidiManager over a fake controller."""
    a = _app
    a.transport = FakeTransport()
    a.selected_calls = []

    def fake_select(t, n, settle=0.1):
        a.selected_calls.append((n, settle))
        a.selects.append(n)

    # Replaces the fixture's select_patch so the settle argument is recorded too.
    monkeypatch.setattr("katatone.amp.write.select_patch", fake_select)
    store = ProfileStore(tmp_path / "midi")
    store.save(Profile(name="Default", devices=["SINCO MIDI 1"], mappings=[
        Mapping(Trigger(kind="pc", number=0), ChannelSelect(5), label="B1"),
        Mapping(Trigger(kind="pc", number=1), EffectAction("DELAY", "toggle")),
        Mapping(Trigger(kind="cc", number=47, mode="range"), ExpressionAction(
            ExprTarget("param", "AMP", "AMP_VOLUME")))]))
    FakeInput.instances = []
    m = MidiManager(a, store, input_factory=FakeInput,
                    list_ports=lambda: [], exclude_cards=lambda: set(a._amp_ports))
    a.start_midi(m)
    a.pedal = FakeInput.instances[0]
    yield a
    a.stop_midi()


def press(a, ev) -> None:
    a.pedal.on_event("SINCO MIDI 1", ev)
    a.midi.router.drain()


def test_pedal_switch_selects_and_the_editor_follows(midi_app) -> None:
    """PC -> select (no settle wait) -> amp_patch moves -> one resync read of that slot."""
    a = midi_app
    press(a, ProgramChange(0, 0))
    assert a.selected_calls == [(5, 0.0)]
    assert a.amp_patch == 5 and not a.live_ready
    assert "Switched to 1B from the pedal" in a.status_text.value
    time.sleep(0.4)
    a.page.join()
    assert a.reads == [5] and a.patch_number == 5 and a.live_ready


def test_pedal_effect_toggle_updates_editor_and_marks_dirty(midi_app) -> None:
    """With the editor mirroring the amp, a footswitch toggle lands in ps and the dirty flag."""
    a = midi_app
    a.live_ready = True
    block, p, _ = mp.effect_param("DELAY")
    shown = []
    a.editor.show_value = lambda *args: shown.append(args)
    press(a, ProgramChange(0, 1))
    wait_for(lambda: a.transport.values(block, p) == [1])
    assert a.ps.get_param(a.region_for(a.patch_number), block, p) == 1
    assert a.dirty
    assert shown == [(block, p, 1)]


def test_expression_updates_editor_without_dirty(midi_app) -> None:
    """Wah/volume sweeps move the knob and the patch value but never mark it edited."""
    a = midi_app
    a.live_ready = True
    shown = []
    a.editor.show_value = lambda *args: shown.append(args)
    press(a, ControlChange(0, 47, 127))
    block, p, _ = mp.resolve_expression(ExprTarget("param", "AMP", "AMP_VOLUME"))
    wait_for(lambda: a.transport.values(block, p) == [100])
    assert _volume(a, 0) == 100 and not a.dirty
    assert shown[-1] == (block, p, 100)


def test_widgets_are_not_touched_when_the_editor_does_not_mirror_the_amp(midi_app) -> None:
    """Not live_ready: the amp still changes, but the editor's patch and widgets stay put."""
    a = midi_app
    a.live_ready = False
    shown = []
    a.editor.show_value = lambda *args: shown.append(args)
    press(a, ControlChange(0, 47, 127))
    block, p, _ = mp.resolve_expression(ExprTarget("param", "AMP", "AMP_VOLUME"))
    wait_for(lambda: a.transport.values(block, p) == [100])
    assert shown == [] and _volume(a, 0) == 0


def test_write_during_a_resync_read_is_replayed_into_the_new_patch(midi_app) -> None:
    """Switch, then toggle before the slot read finishes: the editor ends up showing it."""
    a = midi_app
    a.read_delay = 0.08
    press(a, ProgramChange(0, 0))               # switch to 1B, read starts after 150 ms
    block, p, _ = mp.effect_param("DELAY")
    press(a, ProgramChange(0, 1))               # toggled while the read is pending
    time.sleep(0.9)
    a.page.join()
    assert a.patch_number == 5 and a.live_ready
    assert a.ps.get_param(a.region_for(5), block, p) == 1
    assert a.dirty


def test_offline_pedal_reports_not_connected(midi_app) -> None:
    """Controllers work without the amp; actions say so and send nothing."""
    a = midi_app
    a.connected = False
    press(a, ProgramChange(0, 0))
    assert a.selected_calls == [] and a.status_text.value == "Amp not connected"


def test_amp_ports_are_excluded_from_controllers(midi_app) -> None:
    """The card of the connected amp is never offered as a controller."""
    a = midi_app
    a._amp_ports = {"KATANA3"}
    assert a.midi._exclude() == {"KATANA3"}


def test_stop_midi_closes_inputs(midi_app) -> None:
    """Closing the window stops the controllers and the router."""
    a = midi_app
    assert a.pedal.running
    a.stop_midi()
    assert not a.pedal.running and a.midi is None
