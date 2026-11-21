"""The Pedal tab's Test mode: the pedal is set to move the Pedal FX, and put back afterwards."""
from __future__ import annotations

import types

import pytest

from katatone.address.map import load_map
from katatone.amp import write
from katatone.gui import app as app_mod, pedal
from katatone.liveset.tsl import Tsl
from katatone.model.paramset import ParamSet

from test_follow import nib_of, payload_of
from test_gui_resync import app  # noqa: F401  (fixture)
from test_pedal_meter import find

REGION = load_map()["PATCH(1)"]
FUNC = ("ASSIGN_EXPPDL_FUNC", 0)
MOD = 5                                                             # the pedal drives the Mod effect


class Wire:
    """The amp: takes what is sent, remembers the pedal function written to it."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, int, int]] = []

    def dt1(self, addr, data) -> None:
        pass

    def stop(self) -> None:
        pass

    def on_write_param(self, t, block, index, value):
        self.writes.append((block, index, value))
        return b""

    @property
    def functions(self) -> list[int]:
        return [v for b, i, v in self.writes if (b, i) == FUNC]


@pytest.fixture
def rig(app, monkeypatch):  # noqa: F811
    wire = Wire()
    monkeypatch.setattr(write, "write_param", lambda t, b, i, v: wire.on_write_param(t, b, i, v))
    app.transport, app.connected, app.live_ready, app.amp_patch, app.patch_number = wire, True, True, 0, 0
    region = app.region_for(0)
    app.ps.set_param(region, *FUNC, MOD)                            # the patch uses the pedal for Mod
    app.wire = wire
    return app


def function_in_patch(app):  # noqa: F811
    return app.ps.get_param(app.region_for(app.patch_number), *FUNC)


# ---- on and off

def test_on_sets_the_pedal_to_the_pedal_fx_and_off_puts_it_back(rig) -> None:
    assert rig.set_test_mode(True) is True
    assert rig.test_mode and rig.wire.functions == [pedal_fx()]
    assert "Test mode on" in rig.last_status and "MOD" in rig.last_status          # what it was
    assert rig.set_test_mode(False) is True
    assert not rig.test_mode and rig.wire.functions == [pedal_fx(), MOD]
    assert "Test mode off" in rig.last_status and "MOD" in rig.last_status


def pedal_fx() -> int:
    return app_mod.FUNC_PEDAL_FX


def test_the_pedal_fx_value_is_the_one_named_that(rig) -> None:
    assert rig._func_label(app_mod.FUNC_PEDAL_FX) == "PEDAL FX"
    assert rig._func_label(MOD) == "MOD"


def test_the_patch_in_the_editor_is_never_changed(rig) -> None:
    before = dict(rig.ps.data)
    rig.set_test_mode(True)
    assert rig.ps.data == before and not rig.dirty and function_in_patch(rig) == MOD
    rig.set_test_mode(False)
    assert rig.ps.data == before and not rig.dirty


def test_what_is_saved_has_the_patchs_own_function_not_the_tests(rig) -> None:
    rig.set_test_mode(True)
    saved = ParamSet()
    saved.data = dict(rig.ps.data)                                   # as Save to library copies it
    tsl = Tsl(name="x")
    tsl.add(Tsl.encode(saved))
    assert tsl.to_paramset(0).get_param(REGION, *FUNC) == MOD


def test_turning_it_on_twice_or_off_when_off_does_nothing(rig) -> None:
    rig.set_test_mode(True)
    rig.set_test_mode(True)
    assert rig.wire.functions == [pedal_fx()]
    rig.set_test_mode(False)
    rig.set_test_mode(False)
    assert rig.wire.functions == [pedal_fx(), MOD]


def test_a_pedal_that_already_moves_the_pedal_fx(rig) -> None:
    rig.ps.set_param(rig.region_for(0), *FUNC, pedal_fx())
    rig.set_test_mode(True)
    rig.set_test_mode(False)
    assert rig.wire.functions == [pedal_fx(), pedal_fx()]            # restored to what it was


@pytest.mark.parametrize("connected, live, busy", [(False, True, False), (True, False, False),
                                                   (True, True, True)])
def test_it_is_refused_unless_the_editor_follows_the_amp(rig, connected, live, busy) -> None:
    rig.connected, rig.live_ready, rig.busy = connected, live, busy
    assert rig.set_test_mode(True) is False
    assert not rig.test_mode and rig.wire.writes == []
    assert "Test mode needs the amp connected" in rig.last_status


def test_an_amp_that_does_not_take_it_leaves_the_mode_off(rig, monkeypatch) -> None:
    def boom(t, b, i, v):
        raise TimeoutError("no answer")
    monkeypatch.setattr(write, "write_param", boom)
    assert rig.set_test_mode(True) is False
    assert not rig.test_mode and "did not take" in rig.last_status


# ---- what ends it

def test_changing_the_amps_patch_ends_it_without_touching_the_new_patch(rig, monkeypatch) -> None:
    monkeypatch.setattr(rig, "resync", lambda n: None)
    rig.set_test_mode(True)
    rig.wire.writes.clear()
    rig.on_amp_patch_changed(2)                                      # a channel button on the amp
    assert not rig.test_mode and rig.wire.writes == []              # the new patch has its own function


def test_loading_a_library_patch_puts_the_function_back_first(rig) -> None:
    rig.set_test_mode(True)
    other = ParamSet()
    rig.load_into_editor(other, "another")
    assert not rig.test_mode and rig.wire.functions == [pedal_fx(), MOD]


def test_reading_the_same_patch_again_puts_the_function_back(rig) -> None:
    rig.set_test_mode(True)
    rig.set_live_ready(False)                                        # a read starts
    assert not rig.test_mode and rig.wire.functions == [pedal_fx(), MOD]


def test_writing_a_patch_ends_it_and_the_write_carries_the_patchs_function(rig, monkeypatch) -> None:
    stored = []
    monkeypatch.setattr(rig, "_store", lambda n, ps, name: stored.append(ps.get_param(REGION, *FUNC)))
    rig.set_test_mode(True)
    rig.wire.writes.clear()
    rig._write_amp_thread(1, rig.ps, "x")
    assert not rig.test_mode and stored == [MOD]
    assert rig.wire.writes == []                                     # no separate restore: the write does it


def test_disconnecting_puts_the_function_back(rig) -> None:
    rig.set_test_mode(True)
    rig._disconnect_thread(graceful=True)
    assert not rig.test_mode and rig.wire.functions == [pedal_fx(), MOD]


def test_an_amp_that_is_gone_is_not_written_to(rig) -> None:
    rig.set_test_mode(True)
    rig._disconnect_thread(graceful=False)
    assert not rig.test_mode and rig.wire.functions == [pedal_fx()]


def test_choosing_the_function_by_hand_ends_it_and_stands(rig) -> None:
    rig.set_test_mode(True)
    rig.wire.writes.clear()
    rig.param_changed(*FUNC, 7)                                      # the dropdown of the Assign tab
    assert not rig.test_mode
    rig.set_test_mode(False)                                         # nothing left to put back
    assert rig.wire.functions == []


# ---- what the follower makes of it

def test_the_test_settings_own_announcement_is_not_the_patchs(rig) -> None:
    rig.set_test_mode(True)
    nib, i, p = nib_of("ASSIGN_EXPPDL_FUNC", "FUNC_FUNCTION")
    rig._on_unsolicited(nib, payload_of(p, pedal_fx()))
    assert function_in_patch(rig) == MOD and not rig.dirty


def test_in_the_test_the_pedal_position_is_not_an_edit(rig) -> None:
    nib, i, p = nib_of("PEDALFX", "_PEDALWAH_PEDAL_POSITION")
    rig._on_unsolicited(nib, payload_of(p, 10))
    assert rig.dirty                                                 # without it, the pedal is a knob
    rig.dirty = False
    rig.set_test_mode(True)
    rig._on_unsolicited(nib, payload_of(p, 70))
    assert rig.ps.get_param(rig.region_for(0), "PEDALFX", i) == 70 and not rig.dirty


@pytest.mark.parametrize("function", [2, 3])
def test_a_pedal_set_to_the_pedal_fx_is_not_an_edit_even_without_the_test(rig, function) -> None:
    rig.ps.set_param(rig.region_for(0), *FUNC, function)
    nib, i, p = nib_of("PEDALFX", "_PEDALWAH_PEDAL_POSITION")
    rig._on_unsolicited(nib, payload_of(p, 70))
    assert rig.ps.get_param(rig.region_for(0), "PEDALFX", i) == 70 and not rig.dirty


def test_the_position_of_the_selected_pedal_effect_is_the_one_that_counts(rig) -> None:
    region = rig.region_for(0)
    rig.ps.set_param(region, "PEDALFX_COM", pedal.param_index(region, *pedal.PEDAL_TYPE_PARAM), 1)   # bend
    rig.set_test_mode(True)
    assert pedal.position_param(region, 1) in rig.pedal_targets()
    assert pedal.position_param(region, 0) not in rig.pedal_targets()


# ---- the button

def _button(app):  # noqa: F811
    from katatone.gui import params_view as pv
    i = next(i for i, (label, _) in enumerate(app.editor._tab_specs) if label == "Pedal")
    app.editor._build_tab(i)
    return i, next(b for b in find(app.editor._tab_view.controls[i].content, pv.ToggleButton)
                   if b.label == "Test mode")


def test_the_button_turns_the_mode_on_and_off(rig) -> None:
    i, button = _button(rig)
    assert button.value is False
    button._clicked()
    assert button.value is True and rig.test_mode and rig.wire.functions == [pedal_fx()]
    button._clicked()
    assert button.value is False and not rig.test_mode and rig.wire.functions == [pedal_fx(), MOD]


def test_the_button_springs_back_when_the_amp_cannot_do_it(rig) -> None:
    rig.live_ready = False
    i, button = _button(rig)
    button._clicked()
    assert button.value is False and not rig.test_mode and "needs the amp connected" in rig.last_status


def test_the_button_shows_the_mode_when_the_tab_is_built_again(rig) -> None:
    rig.set_test_mode(True)
    i, button = _button(rig)
    assert button.value is True
    rig.set_test_mode(False)
    i, button = _button(rig)
    assert button.value is False
