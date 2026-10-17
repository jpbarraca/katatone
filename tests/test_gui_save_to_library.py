"""The PATCH tab's "Save to library" dialog (headless, fake app)."""
from __future__ import annotations

import types

from katatone.gui import patch_editor as pe
from katatone.library.store import Library
from katatone.model.paramset import ParamSet


def _editor(tmp_path):
    shown, status = [], []
    app = types.SimpleNamespace(
        lib=Library(tmp_path), ps=ParamSet(), patch_name=lambda: "Lead",
        set_status=status.append, library_refresh=lambda: None,
        page=types.SimpleNamespace(show_dialog=shown.append,
                                   pop_dialog=lambda: None))
    ed = object.__new__(pe.PatchEditor)
    ed.app = app
    return ed, shown, status


def _open(ed, shown):
    ed._save_to_library()
    dialog = shown[-1]
    target, new = dialog.content.controls
    return dialog, target, new


def test_creates_a_new_liveset_when_none_exist(tmp_path) -> None:
    ed, shown, status = _editor(tmp_path)
    dialog, target, new = _open(ed, shown)
    assert target.disabled                     # nothing to choose from yet
    new.value = "Mine"
    dialog.actions[1].on_click(None)
    assert [(e.name, e.count) for e in ed.app.lib.list()] == [("Mine", 1)]
    assert "Mine" in status[-1]


def test_appends_to_an_existing_liveset(tmp_path) -> None:
    ed, shown, status = _editor(tmp_path)
    ed.app.lib.create("Mine")
    dialog, target, new = _open(ed, shown)
    assert target.value == "Mine" and not target.disabled
    dialog.actions[1].on_click(None)
    dialog, target, new = _open(ed, shown)
    dialog.actions[1].on_click(None)
    assert [(e.name, e.count) for e in ed.app.lib.list()] == [("Mine", 2)]


def test_nothing_chosen_saves_nothing(tmp_path) -> None:
    ed, shown, status = _editor(tmp_path)
    dialog, target, new = _open(ed, shown)
    dialog.actions[1].on_click(None)
    assert ed.app.lib.list() == []
    assert "Choose a liveset" in status[-1]
