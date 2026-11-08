"""Which parameters the pedals drive, and writing their live values."""
from __future__ import annotations

import types

import pytest

from katatone.address.map import load_map
from katatone.amp import write as amp_write
from katatone.gui import app as app_mod, model, pedal
from katatone.model.paramset import ParamSet

REGION = load_map()["PATCH(1)"]


def _assign(ps, func_block, detail_block, func, key, choice):
    """Assign a pedal to a slot (``func``) and to the parameter ``choice`` of the effect ``key``."""
    ps.set_param(REGION, func_block, 0, func)
    params = REGION.block(detail_block).params
    i = next(i for i, p in enumerate(params) if p.name.endswith("_DETAIL_" + key))
    ps.set_param(REGION, detail_block, i, choice)


def _name(block, index):
    return REGION.block(block).params[index].name.rsplit("_", 1)[-1]


def test_a_patch_without_a_pedal_on_an_effect_drives_nothing() -> None:
    ps = ParamSet()
    assert pedal.targets(ps, REGION) == []
    _assign(ps, "ASSIGN_EXPPDL_FUNC", "ASSIGN_EXPPDL_DETAIL", 1, "DELAY", 2)     # foot volume
    assert pedal.targets(ps, REGION) == []


def test_the_delay_pedal_drives_the_chosen_parameter_of_the_playing_variation() -> None:
    ps = ParamSet()
    _assign(ps, "ASSIGN_EXPPDL_FUNC", "ASSIGN_EXPPDL_DETAIL", 7, "DELAY", 2)      # Delay: FEEDBACK
    (block, index), = pedal.targets(ps, REGION)
    assert block == "DELAY(1)" and _name(block, index) == "FEEDBACK"              # green
    ps.set_param(REGION, "COLOR", 3, 2)                                           # yellow
    (block, index), = pedal.targets(ps, REGION)
    assert block == "DELAY(3)"
    _assign(ps, "ASSIGN_EXPPDL_FUNC", "ASSIGN_EXPPDL_DETAIL", 8, "DELAY", 1)      # Delay 2: DELAY TIME
    assert [b for b, _ in pedal.targets(ps, REGION)] == ["DELAY(6)"]               # its own, in yellow


def test_booster_and_reverb_pedals_name_their_parameters() -> None:
    ps = ParamSet()
    _assign(ps, "ASSIGN_EXPPDL_FUNC", "ASSIGN_EXPPDL_DETAIL", 4, "BOOSTER", 1)     # DRIVE
    assert [(b, _name(b, i)) for b, i in pedal.targets(ps, REGION)] == [("BOOSTER(1)", "DRIVE")]
    _assign(ps, "ASSIGN_EXPPDL_FUNC", "ASSIGN_EXPPDL_DETAIL", 9, "REVERB", 6)      # EFFECT LEVEL
    (block, index), = pedal.targets(ps, REGION)
    assert block == "REVERB(1)" and "LEVEL" in REGION.block(block).params[index].name


def test_a_choice_of_preset_drives_no_parameter() -> None:
    ps = ParamSet()
    _assign(ps, "ASSIGN_EXPPDL_FUNC", "ASSIGN_EXPPDL_DETAIL", 7, "DELAY", 0)
    assert pedal.targets(ps, REGION) == []


def test_the_fx_slot_follows_the_effect_type_in_it() -> None:
    ps = ParamSet()
    keys = model.fx_detail_groups(REGION, "FX_DETAIL(4)")
    ps.set_param(REGION, "FX(4)", 0, keys.index("PHASER"))
    choices = pedal._choices()["PHASER"]
    _assign(ps, "ASSIGN_EXPPDL_FUNC", "ASSIGN_EXPPDL_DETAIL", 6, "PHASER", choices.index("RATE"))
    (block, index), = pedal.targets(ps, REGION)
    assert block == "FX_DETAIL(4)" and _name(block, index) == "RATE"


def test_a_ga_fc_pedal_is_read_like_the_amps_own() -> None:
    ps = ParamSet()
    _assign(ps, "ASSIGN_GAFCEXPPDL2_FUNC(1)", "ASSIGN_GAFCEXPPDL2_DETAIL(1)", 7, "DELAY", 3)   # HIGH CUT
    _assign(ps, "ASSIGN_EXPPDL_FUNC", "ASSIGN_EXPPDL_DETAIL", 7, "DELAY", 3)                    # the same one
    assert [(b, _name(b, i)) for b, i in pedal.targets(ps, REGION)] == [("DELAY(1)", "CUT")]   # once


@pytest.fixture
def app(monkeypatch, tmp_path):
    import flet.controls.base_control as bc
    from test_gui_resync import StubPage
    monkeypatch.setattr(bc.BaseControl, "update", lambda self: None)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("KATATONE_NO_BLUETOOTH", "1")
    a = app_mod.App(StubPage(), start_midi=False)
    a.connected, a.transport, a.live_ready, a.amp_patch = True, object(), True, 1
    return a


def test_a_write_takes_the_live_value_of_the_parameter_the_pedal_drives(app, monkeypatch) -> None:
    ps = ParamSet()
    _assign(ps, "ASSIGN_EXPPDL_FUNC", "ASSIGN_EXPPDL_DETAIL", 7, "DELAY", 2)
    (block, index), = pedal.targets(ps, REGION)
    ps.set_param(REGION, block, index, 10)                        # saved in the editor
    monkeypatch.setattr(amp_write, "read_param", lambda t, b, i: 80 if (b, i) == (block, index) else 0)
    assert app._take_pedal_values(1, ps) == 1
    assert ps.get_param(REGION, block, index) == 80               # what the amp plays, with the pedal


def test_nothing_is_read_unless_the_editor_shows_what_the_amp_plays(app, monkeypatch) -> None:
    ps = ParamSet()
    _assign(ps, "ASSIGN_EXPPDL_FUNC", "ASSIGN_EXPPDL_DETAIL", 7, "DELAY", 2)
    monkeypatch.setattr(amp_write, "read_param", lambda *a: pytest.fail("the amp must not be read"))
    app.live_ready = False
    assert app._take_pedal_values(1, ps) == 0
    app.live_ready, app.amp_patch = True, 2                       # the amp plays another slot
    assert app._take_pedal_values(1, ps) == 0


def test_a_parameter_that_cannot_be_read_keeps_its_saved_value(app, monkeypatch) -> None:
    ps = ParamSet()
    _assign(ps, "ASSIGN_EXPPDL_FUNC", "ASSIGN_EXPPDL_DETAIL", 7, "DELAY", 2)
    (block, index), = pedal.targets(ps, REGION)
    ps.set_param(REGION, block, index, 10)
    monkeypatch.setattr(amp_write, "read_param", lambda *a: (_ for _ in ()).throw(TimeoutError("no answer")))
    assert app._take_pedal_values(1, ps) == 0 and ps.get_param(REGION, block, index) == 10


def test_the_spellings_of_the_amps_choices_find_their_parameters() -> None:
    """Found on a real patch: the pitch shifter's pitch is chosen as "PS1:PITCH"."""
    ps = ParamSet()
    keys = model.fx_detail_groups(REGION, "FX_DETAIL(4)")
    ps.set_param(REGION, "FX(4)", 0, keys.index("PITCHSHIFT"))
    choices = pedal._choices()["PITCHSHIFTER"]
    _assign(ps, "ASSIGN_EXPPDL_FUNC", "ASSIGN_EXPPDL_DETAIL", 6, "PITCHSHIFTER", choices.index("PS1:PITCH"))
    (block, index), = pedal.targets(ps, REGION)
    assert block == "FX_DETAIL(4)" and _name(block, index) == "PITCH1"
    _assign(ps, "ASSIGN_EXPPDL_FUNC", "ASSIGN_EXPPDL_DETAIL", 6, "PITCHSHIFTER", choices.index("PS2:PRE DELAY"))
    (block, index), = pedal.targets(ps, REGION)
    assert _name(block, index) == "DELAY2"                                       # PRE_DELAY2
    _assign(ps, "ASSIGN_EXPPDL_FUNC", "ASSIGN_EXPPDL_DETAIL", 4, "BOOSTER", pedal._choices()["BOOSTER"].index("DIRECT MIX"))
    (block, index), = pedal.targets(ps, REGION)
    assert block == "BOOSTER(1)" and _name(block, index) == "MIX"                  # not "DIRECT LEVEL"
