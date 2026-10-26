"""Ready-made MIDI profiles."""
from __future__ import annotations

import pytest

from katatone.midi import presets
from katatone.midi.events import ControlChange, ProgramChange
from katatone.midi.mapping import (ChannelFromPc, ChannelStep, EffectAction, ExpressionAction,
                                   Profile, SetlistStep)
from katatone.midi.store import ProfileStore
from test_gui_midi_pane import pane                      # noqa: F401  (fixture)
from test_gui_resync import app as _app                  # noqa: F401  (fixture)

DEV = "Some pedal"


def fired(profile: Profile, ev) -> list:
    return [m.action for m, _ in profile.match(DEV, ev)]


def by_name(name: str) -> presets.Preset:
    return next(p for p in presets.PRESETS if p.name == name)


def test_every_preset_is_a_valid_profile_that_survives_the_file() -> None:
    assert len({p.name for p in presets.PRESETS}) == len(presets.PRESETS)
    for p in presets.PRESETS:
        prof = p.profile()
        assert prof.mappings and p.description
        assert all(m.enabled and m.label for m in prof.mappings)
        assert Profile.from_dict(prof.to_dict()).to_dict() == prof.to_dict()
        assert len({m.id for m in prof.mappings}) == len(prof.mappings)


def test_a_preset_gives_independent_mappings_each_time() -> None:
    a, b = by_name("Channel up / down").profile(), by_name("Channel up / down").profile()
    assert {m.id for m in a.mappings}.isdisjoint(m.id for m in b.mappings)


def test_the_pc_pedal_selects_channels_from_any_program_change() -> None:
    prof = by_name("Program change pedal").profile()
    assert fired(prof, ProgramChange(0, 5)) == [ChannelFromPc()]


def test_channel_switches() -> None:
    prof = by_name("Channel up / down").profile()
    assert fired(prof, ControlChange(0, 80, 127)) == [ChannelStep("next")]
    assert fired(prof, ControlChange(0, 81, 127)) == [ChannelStep("prev")]
    assert fired(prof, ControlChange(0, 80, 0)) == []              # the release does nothing


def test_effect_switches_toggle_four_effects() -> None:
    prof = by_name("Effect switches").profile()
    got = [fired(prof, ControlChange(0, cc, 127)) for cc in (80, 81, 82, 83)]
    assert got == [[EffectAction(e, "toggle")] for e in ("BOOST", "MOD", "DELAY", "REVERB")]


def test_the_expression_pedals_follow_the_controller_value() -> None:
    for name, cc, kind in (("Wah / expression pedal", 11, "pedal_fx"), ("Volume pedal", 7, "volume")):
        prof = by_name(name).profile()
        (mapping, value), = prof.match(DEV, ControlChange(0, cc, 100))
        assert isinstance(mapping.action, ExpressionAction) and mapping.action.target.kind == kind
        assert value == 100
        assert prof.match(DEV, ControlChange(0, cc + 1, 100)) == []


def test_setlist_switches() -> None:
    prof = by_name("Setlist switches").profile()
    assert fired(prof, ControlChange(0, 80, 127)) == [SetlistStep("next")]
    assert fired(prof, ControlChange(0, 81, 127)) == [SetlistStep("prev")]


def test_create_stores_the_profile_and_picks_a_free_name(tmp_path) -> None:
    store = ProfileStore(tmp_path)
    first = presets.create(store, by_name("Volume pedal"))
    second = presets.create(store, by_name("Volume pedal"))
    assert (first.name, second.name) == ("Volume pedal", "Volume pedal 2")
    assert store.load("Volume pedal 2").mappings[0].label == "Volume pedal → amp volume"
    assert {first.mappings[0].id} != {second.mappings[0].id}


def test_the_dialog_creates_the_chosen_preset_and_switches_to_it(pane) -> None:  # noqa: F811
    pane._from_preset()
    dlg = pane.app.page.dialogs[-1]
    picker, about, _ = dlg.content.controls
    assert about.value == presets.PRESETS[0].description
    picker.value = "Setlist switches"
    picker.on_select(type("E", (), {"control": picker})())
    assert about.value == by_name("Setlist switches").description
    dlg.actions[-1].on_click(None)
    assert pane.midi.profile.name == "Setlist switches"
    assert [m.action.type for m in pane.midi.profile.mappings] == ["setlist_step"] * 2
    assert "Setlist switches" in pane.app.last_status
    assert not pane.app.page.dialogs                          # closed
