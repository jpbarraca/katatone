"""The MIDI tab and the mapping dialog, headless (controls are built, never mounted)."""
from __future__ import annotations

import threading
import time

import pytest

from katatone.gui import dialogs, statusbar
from katatone.gui.mapping_dialog import ACTION_LABELS, MappingDialog
from katatone.gui.midi_pane import MONITOR_LINES, state_text
from katatone.midi.events import ControlChange, ProgramChange
from katatone.midi.manager import MidiManager
from katatone.midi.mapping import (BankAction, ChannelFromPc, ChannelSelect,
                                   EffectAction, ExprTarget, ExpressionAction,
                                   Mapping, Profile, Trigger)
from katatone.midi.store import ProfileStore
from test_gui_resync import app as _app                      # noqa: F401  (fixture)
from test_midi_manager import PORTS, FakeInput
from test_midi_router import wait_for

DEV = "SINCO MIDI 1"


@pytest.fixture
def pane(_app, tmp_path):
    """Headless App with the MIDI tab attached to a manager over fake inputs."""
    a = _app
    FakeInput.instances = []
    m = MidiManager(a, ProfileStore(tmp_path / "midi"), input_factory=FakeInput,
                    list_ports=lambda: list(PORTS))
    a.start_midi(m)
    yield a.midi_pane
    a.stop_midi()


def texts(col) -> list[str]:
    """The text of every Text control (recursively) under a column."""
    out = []

    def walk(c):
        if hasattr(c, "value") and isinstance(getattr(c, "value"), str) and c.__class__.__name__ == "Text":
            out.append(c.value)
        for attr in ("controls", "content"):
            v = getattr(c, attr, None)
            if isinstance(v, list):
                for x in v:
                    walk(x)
            elif v is not None and not isinstance(v, str):
                walk(v)
    walk(col)
    return out


# ------------------------------------------------------------- controllers


def test_controllers_card_lists_ports_without_the_amp(pane) -> None:
    joined = " ".join(texts(pane.controllers_box))
    assert "SINCO MIDI 1" in joined and "Keys MIDI 1" in joined
    assert "KATANA" not in joined
    assert set(pane._leds) == {"SINCO MIDI 1", "Keys MIDI 1"}


def test_empty_state_tells_the_user_what_to_do(_app, tmp_path) -> None:
    m = MidiManager(_app, ProfileStore(tmp_path), input_factory=FakeInput,
                    list_ports=lambda: [])
    _app.start_midi(m)
    assert any("Plug in a USB MIDI controller and press Rescan" in t
               for t in texts(_app.midi_pane.controllers_box))
    _app.stop_midi()


def test_enable_switch_opens_and_persists(pane) -> None:
    pane._enable(DEV, True)
    assert any(i.running and i.port.label == DEV for i in FakeInput.instances)
    assert pane.midi.store.load("Default").devices == [DEV]
    wait_for(lambda: "open" in texts(pane.controllers_box))
    pane._enable(DEV, False)
    assert not FakeInput.instances[0].running


@pytest.mark.parametrize("state,text", [("open", "open"), ("busy", "in use by another program"),
                                        ("gone", "unplugged"), ("closed", "closed")])
def test_state_chip_text(state, text) -> None:
    assert state_text(state)[0] == text
    assert state_text("error: boom")[0] == "error: boom"


def test_busy_state_reaches_the_chip(pane) -> None:
    pane._enable(DEV, True)
    pane.midi._on_state(DEV, "busy")
    assert pane._chips[DEV].value == "in use by another program"


def test_saved_but_unplugged_controller_is_listed(_app, tmp_path) -> None:
    store = ProfileStore(tmp_path)
    store.save(Profile(name="Default", devices=["Gone MIDI 1"]))
    m = MidiManager(_app, store, input_factory=FakeInput, list_ports=lambda: list(PORTS))
    _app.start_midi(m)
    assert "Gone MIDI 1" in " ".join(texts(_app.midi_pane.controllers_box))
    _app.midi._on_state("Gone MIDI 1", "gone")      # what MidiInput reports
    assert _app.midi_pane._chips["Gone MIDI 1"].value == "unplugged"
    _app.stop_midi()


# ----------------------------------------------------------------- monitor


def test_monitor_batches_events_newest_first_and_caps_lines(pane) -> None:
    for i in range(70):
        pane._on_notify("activity", DEV, ProgramChange(0, i), ["x"] if i == 69 else [])
    pane._tick()
    lines = texts(pane.monitor_box)
    assert len(lines) == MONITOR_LINES
    assert "PC 69" in lines[0] and "x" in lines[0] and "PC 20" in lines[-1]


def test_pause_and_clear(pane) -> None:
    pane._toggle_pause()
    pane._on_notify("activity", DEV, ProgramChange(0, 1), [])
    pane._tick()
    assert pane._lines.__len__() == 0
    pane._toggle_pause()
    pane._on_notify("activity", DEV, ProgramChange(0, 2), [])
    time.sleep(0.11)
    pane._tick()
    assert len(pane._lines) == 1
    pane._clear_monitor()
    assert texts(pane.monitor_box) == []


def test_activity_lights_the_led_then_it_goes_out(pane) -> None:
    led = pane._leds[DEV]
    pane._on_notify("activity", DEV, ControlChange(0, 1, 2), [])
    pane._tick()
    assert led.bgcolor != "#3a2a1a" and pane.app._sb_midi_lit
    time.sleep(0.2)
    pane._tick()
    assert led.bgcolor == "#3a2a1a" and not pane.app._sb_midi_lit


def test_status_bar_tooltip_lists_open_controllers(pane) -> None:
    pane._enable(DEV, True)
    assert DEV in pane.app.sb_midi.tooltip


def test_amp_dropdown_hides_enabled_controllers(pane, monkeypatch) -> None:
    monkeypatch.setattr(statusbar, "_ports", lambda: ["KATANA3", "SINCO"])
    pane._enable(DEV, True)
    assert [o.key or o.text for o in pane.app.sb_amp.options] == ["KATANA3"]
    pane._enable(DEV, False)
    assert [o.key or o.text for o in pane.app.sb_amp.options] == ["KATANA3", "SINCO"]


# ---------------------------------------------------------------- mappings


def add(pane, m: Mapping) -> None:
    pane._on_saved(m, True)


def test_mappings_are_saved_immediately_and_survive_a_restart(pane, tmp_path) -> None:
    add(pane, Mapping(Trigger(DEV, 0, "pc", 2), ChannelSelect(5), label="B1"))
    stored = ProfileStore(tmp_path / "midi").load("Default")
    assert [m.label for m in stored.mappings] == ["B1"]
    assert len(pane.mappings_box.controls) == 1


def test_enable_relabel_delete_persist(pane, tmp_path) -> None:
    m = Mapping(Trigger(DEV, 0, "pc", 2), ChannelSelect(5), label="B1")
    add(pane, m)
    pane._enabled(m, False)
    pane._relabel(m, "  Lead  ")
    store = ProfileStore(tmp_path / "midi")
    got = store.load("Default").mappings[0]
    assert (got.enabled, got.label) == (False, "Lead")
    pane._delete(m)
    assert store.load("Default").mappings == []
    assert any("No mappings yet" in t for t in texts(pane.mappings_box))


def test_unknown_action_row_is_disabled(pane) -> None:
    d = Mapping(Trigger(), ChannelSelect(1)).to_dict()
    d["action"] = {"type": "teleport"}
    pane.midi.profile.mappings.append(Mapping.from_dict(d))
    pane.rebuild_mappings()
    row = pane.mappings_box.controls[0]
    assert row.controls[0].disabled and row.controls[4].disabled
    assert "Unsupported action (teleport)" in " ".join(texts(row))


def test_amp_not_connected_note(pane) -> None:
    pane.app.connected = False
    pane.refresh_note()
    assert "Amp not connected" in pane.note.value
    pane.app.connected = True
    pane.refresh_note()
    assert pane.note.value == ""


# ---------------------------------------------------------------- profiles


def run_prompt(page, text: str) -> None:
    """Fill the open prompt dialog and press Save."""
    dlg = page.dialogs[-1]
    dlg.content.value = text
    dlg.actions[1].on_click(None)


def test_profile_new_rename_duplicate_delete(pane) -> None:
    page = pane.app.page
    store = pane.midi.store
    pane._new_profile()
    run_prompt(page, "Stage")
    assert pane.midi.profile.name == "Stage" and store.active() == "Stage"
    add(pane, Mapping(Trigger(), ChannelSelect(5), label="B1"))
    pane._rename_profile()
    run_prompt(page, "Gig")
    assert store.list() == ["Default", "Gig"] and pane.midi.profile.name == "Gig"
    pane._duplicate_profile()
    run_prompt(page, "Gig 2")
    assert store.load("Gig 2").mappings[0].label == "B1" and pane.midi.profile.name == "Gig 2"
    pane._delete_profile()
    page.dialogs[-1].actions[1].on_click(None)
    assert "Gig 2" not in store.list() and pane.midi.profile.name != "Gig 2"
    assert {o.key for o in pane.profile_dd.options} == set(store.list())


def test_profile_name_clash_is_reported(pane) -> None:
    pane._new_profile()
    run_prompt(pane.app.page, "Default")
    assert "already exists" in pane.app.status_text.value


def test_switching_profile_changes_mappings_and_controllers(pane) -> None:
    pane.midi.store.save(Profile(name="Keys", devices=["Keys MIDI 1"], mappings=[
        Mapping(Trigger(kind="pc"), ChannelSelect(5), label="K")]))
    pane._switch("Keys")
    assert [r.controls[1].value for r in pane.mappings_box.controls] == ["K"]
    assert any(i.running and i.port.label == "Keys MIDI 1" for i in FakeInput.instances)


# ------------------------------------------------------------------ dialog


def new_dialog(pane, mapping=None):
    saved = []
    d = MappingDialog(pane.app, pane.midi, mapping, lambda m, new: saved.append((m, new)))
    d.show()
    return d, saved


def test_dialog_saves_a_manual_channel_mapping(pane) -> None:
    d, saved = new_dialog(pane)
    d.number_field.value = "4"
    d.draft.trigger.kind = "pc"
    d.draft.trigger.device = DEV
    d.draft.action.slot = 6
    d._slot_chosen = True
    d.label_field.value = " Rhythm "
    d._save()
    (m, is_new), = saved
    assert is_new and m.label == "Rhythm"
    assert m.trigger.number == 4 and m.action == ChannelSelect(6)
    assert pane.app.page.dialogs == []


def test_new_channel_mapping_requires_an_explicit_channel(pane) -> None:
    """The amp's current channel must not be saved just because it was preselected."""
    pane.app.amp_patch = 0
    d, saved = new_dialog(pane)
    d.draft.trigger.number = 2
    d._save()
    assert saved == [] and "Pick a channel" in d.error.value
    pills = d.action_box.controls[0].controls
    assert all(p.bgcolor is None for p in pills)          # nothing lit
    pills[2].on_click(None)                                # click "2A"
    d._save()
    assert saved[0][0].action.slot == 2


def test_editing_an_existing_channel_mapping_keeps_its_channel(pane) -> None:
    m = Mapping(Trigger(DEV, 0, "pc", 2), ChannelSelect(6))
    d, saved = new_dialog(pane, m)
    d._save()
    assert saved[0][0].action.slot == 6


def test_second_mapping_on_the_same_switch_warns_before_saving(pane) -> None:
    """Two mappings on PC 4 both fire (the last one wins), so the dialog says so first."""
    old = Mapping(Trigger(DEV, 0, "pc", 4), ChannelSelect(2), label="A2")
    add(pane, old)
    d, saved = new_dialog(pane)
    d.draft.trigger = Trigger(DEV, 0, "pc", 4)
    d.number_field.value = "4"
    d._slot_chosen = True
    d.draft.action.slot = 5
    d._save()
    assert saved == [] and "already does: A2" in d.error.value
    d._save()                                              # second press keeps both
    assert len(saved) == 1


def test_editing_a_mapping_does_not_clash_with_itself(pane) -> None:
    old = Mapping(Trigger(DEV, 0, "pc", 4), ChannelSelect(2))
    add(pane, old)
    d, saved = new_dialog(pane, old)
    d._save()
    assert len(saved) == 1


def test_dialog_validation_blocks_save(pane) -> None:
    d, saved = new_dialog(pane)
    d.threshold_field.value = "abc"
    d._on_action_type("channel_from_pc")
    d._pc_field.value = "0=9Z"
    d.draft.action.table = {}
    d._save()
    assert saved == [] and d.error.value
    assert pane.app.page.dialogs == [d.dialog]
    d._cancel()
    assert pane.app.page.dialogs == []


def test_dialog_edit_does_not_touch_the_original_until_saved(pane) -> None:
    orig = Mapping(Trigger(DEV, 0, "pc", 1), ChannelSelect(1), label="A")
    d, saved = new_dialog(pane, orig)
    d.draft.action.slot = 6
    d._cancel()
    assert orig.action.slot == 1 and saved == []


@pytest.mark.parametrize("kind", [k for k in ACTION_LABELS])
def test_every_action_type_builds_and_saves(pane, kind) -> None:
    d, saved = new_dialog(pane)
    d._on_action_type(kind)
    d._slot_chosen = True
    assert d.draft.action.type == kind
    d._save()
    assert saved and saved[0][0].action.type == kind


def test_dialog_expression_other_parameter(pane) -> None:
    d, saved = new_dialog(pane)
    d._on_action_type("expression")
    a = d.draft.action
    d.action_box.controls[0].on_select(type("E", (), {"control": type("C", (), {"value": "param"})()})())
    assert a.target.kind == "param" and a.target.block == "AMP"
    a.target = ExprTarget("param", "NS", "NS_THRESHOLD")
    d._lo.value, d._hi.value = "10", "60"
    d.draft.trigger.mode = "range"
    d._save()
    got = saved[0][0].action
    assert (got.target.block, got.target.name, got.lo, got.hi) == ("NS", "NS_THRESHOLD", 10, 60)


def test_dialog_rejects_a_parameter_that_does_not_exist(pane) -> None:
    d, saved = new_dialog(pane)
    d._on_action_type("expression")
    d.draft.action.target = ExprTarget("param", "AMP", "NOPE")
    d._save()
    assert saved == [] and "NOPE" in d.error.value


def test_dialog_test_button_runs_the_action(pane) -> None:
    d, _ = new_dialog(pane)
    d.draft.action.slot = 5
    d._slot_chosen = True
    pane.app.transport = object()
    d._test()
    assert pane.midi.router.drain()
    wait_for(lambda: pane.app.amp_patch == 5)


def learn_via(pane, d, ev) -> None:
    pane.midi.enable(DEV, True)
    d._toggle_learn()
    assert d._learning
    FakeInput.instances[-1].on_event(DEV, ev)
    wait_for(lambda: not d._learning)


def test_learn_fills_trigger_and_suggests_channel_for_pc(pane) -> None:
    d, _ = new_dialog(pane)
    learn_via(pane, d, ProgramChange(0, 2))
    t = d.draft.trigger
    assert (t.device, t.channel, t.kind, t.number) == (DEV, 0, "pc", 2)
    assert d.number_field.value == "2" and d.kind_dd.value == "pc"
    assert d.draft.action.type == "channel_select" and "PC 2" in d.learn_text.value
    assert pane.midi.router.capture is None


def test_learn_suggests_expression_for_a_pedal_value(pane) -> None:
    """A CC whose first value is not 0/127 is a pedal; Learn ends on that message."""
    d, _ = new_dialog(pane)
    pane.midi.enable(DEV, True)
    d._toggle_learn()
    FakeInput.instances[-1].on_event(DEV, ControlChange(0, 47, 52))
    wait_for(lambda: not d._learning)
    assert d.draft.trigger.mode == "range" and d.draft.action.type == "expression"
    assert pane.midi.router.capture is None


def test_learn_does_not_override_an_action_the_user_chose(pane) -> None:
    d, _ = new_dialog(pane)
    d._on_action_type("bank")
    learn_via(pane, d, ProgramChange(0, 2))
    assert d.draft.action.type == "bank"


def test_learn_without_an_enabled_controller_says_so(pane) -> None:
    d, _ = new_dialog(pane)
    d._toggle_learn()
    assert not d._learning and "No controller is enabled" in d.learn_text.value


def test_learn_can_be_cancelled_and_dialog_close_cancels_it(pane) -> None:
    d, _ = new_dialog(pane)
    pane.midi.enable(DEV, True)
    d._toggle_learn()
    d._toggle_learn()
    assert not d._learning and pane.midi.router.capture is None
    d._toggle_learn()
    d._cancel()
    assert pane.midi.router.capture is None


def test_prompt_and_confirm_helpers(pane) -> None:
    got = []
    dialogs.prompt(pane.app.page, "T", "v", got.append)
    run_prompt(pane.app.page, "  x ")
    dialogs.prompt(pane.app.page, "T", "v", got.append)
    run_prompt(pane.app.page, "   ")
    assert got == ["x"]
