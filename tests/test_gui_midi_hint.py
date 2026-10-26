"""The MIDI tab says what the last message is and offers to map it."""
from __future__ import annotations

import time

from katatone.midi.events import ControlChange, NoteOn, ProgramChange
from test_gui_midi_pane import DEV, pane                 # noqa: F401  (fixture)
from test_gui_resync import app as _app                  # noqa: F401  (fixture)


def hear(pane, ev, labels=()) -> None:
    pane._on_notify("activity", DEV, ev, list(labels))
    time.sleep(0.11)                                      # the monitor flushes every 0.1 s
    pane._tick()


def test_before_any_message_it_says_what_to_do(pane) -> None:
    assert pane.last_text.value.startswith("No message yet")
    assert not pane.map_btn.visible


def test_a_mapped_message_shows_what_it_does_and_offers_nothing(pane) -> None:
    hear(pane, ProgramChange(0, 5), ["Channel → 1B"])
    assert pane.last_text.value == f"{DEV} · ch 1 · PC 5  →  Channel → 1B"
    assert not pane.map_btn.visible and pane._last is None


def test_an_unmapped_message_offers_to_map_it(pane) -> None:
    hear(pane, ControlChange(0, 80, 127))
    assert pane.last_text.value == f"{DEV} · ch 1 · CC 80 (switch)  →  not mapped"
    assert pane.map_btn.visible and pane._last.trigger.number == 80


def test_the_newest_message_wins(pane) -> None:
    pane._on_notify("activity", DEV, NoteOn(0, 60, 100), [])
    pane._on_notify("activity", DEV, ProgramChange(0, 3), ["x"])
    time.sleep(0.11)
    pane._tick()
    assert "PC 3" in pane.last_text.value and not pane.map_btn.visible


def test_a_message_taken_by_learn_is_not_offered(pane) -> None:
    hear(pane, ProgramChange(0, 1), ["learning"])
    assert "taken by Learn" in pane.last_text.value and not pane.map_btn.visible


def test_map_it_opens_a_dialog_starting_from_that_message(pane) -> None:
    hear(pane, ControlChange(0, 47, 52))                  # a pedal value: an expression
    pane._map_last()
    shown = pane.app.page.dialogs[-1]
    assert shown.title.value == "New mapping"
    assert shown.content.controls[0].controls[1].value.startswith("From the last message")


def test_the_dialog_starts_from_the_message_and_suggests_an_action(pane) -> None:
    from katatone.gui.mapping_dialog import MappingDialog
    hear(pane, ControlChange(0, 47, 52))
    d = MappingDialog(pane.app, pane.midi, None, lambda m, new: None, learned=pane._last)
    t = d.draft.trigger
    assert (t.device, t.channel, t.kind, t.number, t.mode) == (DEV, 0, "cc", 47, "range")
    assert d.draft.action.type == "expression"
    assert d.number_field.value == "47" and d.learn_text.value.startswith("From the last message")


def test_saving_the_dialog_adds_the_mapping_to_the_profile(pane) -> None:
    hear(pane, ControlChange(0, 80, 127))                 # a switch: suggests an effect
    assert pane.midi.profile.mappings == []
    pane._map_last()
    pane.app.page.dialogs[-1].actions[-1].on_click(None)  # Save
    (m,) = pane.midi.profile.mappings
    assert (m.trigger.kind, m.trigger.number, m.action.type) == ("cc", 80, "effect")


def test_clear_forgets_the_last_message(pane) -> None:
    hear(pane, ControlChange(0, 80, 127))
    pane._clear_monitor()
    assert pane.last_text.value.startswith("No message yet")
    assert not pane.map_btn.visible and pane._last is None
