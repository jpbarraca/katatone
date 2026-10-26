"""Undo and redo in the app: the patch, the amp and the name follow the history."""
from __future__ import annotations

import types

import flet.controls.base_control as bc
import pytest

from katatone.gui import app as app_mod
from katatone.model.paramset import ParamSet
from test_gui_resync import StubPage

ROW = types.SimpleNamespace(block="AMP", p_index=1,
                            param=types.SimpleNamespace(min=0, max=100))


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("KATATONE_NO_BLUETOOTH", "1")
    monkeypatch.setattr(bc.BaseControl, "update", lambda self: None)
    a = app_mod.App(StubPage(), start_midi=False)
    a.sent = []
    a.param_changed = lambda block, i, v: a.sent.append((block, i, v))
    return a


def volume(app) -> int:
    return app.ps.get_param(app.region_for(app.patch_number), "AMP", 1)


def edit(app, value: int, pause: bool = True) -> None:
    if pause:
        app.history._last_key = None       # as if the previous edit were long ago
    app.editor._set_param(ROW, value)


def test_undo_restores_the_value_and_tells_the_amp(app) -> None:
    edit(app, 40)
    edit(app, 60)
    app.undo()
    assert volume(app) == 40 and app.sent[-1] == ("AMP", 1, 40)
    app.undo()
    assert volume(app) == 0 and app.sent[-1] == ("AMP", 1, 0)
    assert app.dirty


def test_redo_applies_it_again(app) -> None:
    edit(app, 40)
    app.undo()
    app.redo()
    assert volume(app) == 40 and app.sent[-1] == ("AMP", 1, 40)


def test_a_knob_drag_undoes_in_one_step(app) -> None:
    for v in range(10, 20):
        edit(app, v, pause=False)
    app.undo()
    assert volume(app) == 0


def test_nothing_to_undo_says_so(app) -> None:
    app.undo()
    assert app.last_status == "Nothing to undo."
    app.redo()
    assert app.last_status == "Nothing to redo."


def test_the_name_can_be_undone(app) -> None:
    old = app.ps.block("PATCH%COM")
    app.editor._on_name(types.SimpleNamespace(control=types.SimpleNamespace(value="Crunch")))
    assert app.patch_name() == "Crunch"
    app.undo()
    assert app.ps.block("PATCH%COM") == old
    app.redo()
    assert app.patch_name() == "Crunch"


def test_a_new_patch_clears_the_history(app) -> None:
    edit(app, 40)
    app.load_into_editor(ParamSet(), "other")
    app.undo()
    assert app.last_status == "Nothing to undo."


def test_ctrl_z_and_ctrl_shift_z(app) -> None:
    key = lambda k, shift=False: types.SimpleNamespace(key=k, ctrl=True, shift=shift, meta=False)
    edit(app, 40)
    app._on_key(key("Z"))
    assert volume(app) == 0
    app._on_key(key("Z", shift=True))
    assert volume(app) == 40
    app._on_key(key("Z"))
    app._on_key(key("Y"))
    assert volume(app) == 40


def test_the_shortcut_leaves_the_name_field_its_own_undo(app) -> None:
    edit(app, 40)
    app.editor._name_focus(True)
    app._on_key(types.SimpleNamespace(key="Z", ctrl=True, shift=False, meta=False))
    assert volume(app) == 40
