"""The compare dialog and taking a value from it."""
from __future__ import annotations

import flet as ft
import flet.controls.base_control as bc
import pytest

from katatone.gui import app as app_mod
from katatone.gui import compare_dialog
from test_compare import patch
from test_gui_resync import StubPage


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("KATATONE_NO_BLUETOOTH", "1")
    monkeypatch.setattr(bc.BaseControl, "update", lambda self: None)
    a = app_mod.App(StubPage(), start_midi=False)
    a.sent = []
    a.param_changed = lambda block, i, v: a.sent.append((block, i, v))
    a.ps = patch("Mine", AMP_1=10)
    return a


def volume(app) -> int:
    return app.ps.get_param(app.region_for(app.patch_number), "AMP", 1)


def open_dialog(app, other):
    compare_dialog.show(app, other, "Other")
    return app.page.dialogs[-1]


def rows(dlg):
    return [c for c in dlg.content.controls[2].controls if isinstance(c, ft.Row)]


def test_the_dialog_lists_the_differences(app) -> None:
    dlg = open_dialog(app, patch("Theirs", AMP_1=30))
    assert len(rows(dlg)) == 2                      # the name and the volume
    assert "2 differences" in dlg.content.controls[0].value


def test_equal_patches_say_so(app) -> None:
    dlg = open_dialog(app, patch("Mine", AMP_1=10))
    assert dlg.content.controls[0].value == "Both patches have the same values."
    assert rows(dlg) == []


def test_taking_a_value_sets_it_sends_it_and_can_be_undone(app) -> None:
    dlg = open_dialog(app, patch("Mine", AMP_1=30))
    (row,) = rows(dlg)
    row.controls[2].on_click(None)
    assert volume(app) == 30 and app.sent == [("AMP", 1, 30)] and app.dirty
    assert row.controls[2].disabled
    app.undo()
    assert volume(app) == 10


def test_the_name_cannot_be_taken(app) -> None:
    dlg = open_dialog(app, patch("Theirs", AMP_1=10))
    (row,) = rows(dlg)
    assert row.controls[2].disabled


def test_compare_with_amp_reads_the_slot_then_opens_the_dialog(app, monkeypatch) -> None:
    app.connected, app.transport = True, object()
    monkeypatch.setattr(app, "read_amp_patch", lambda n, done: done(patch("Stored", AMP_1=50)))
    app.compare_with_amp()
    assert len(rows(app.page.dialogs[-1])) == 2
