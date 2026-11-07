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


def test_slot_rows_keep_the_name_of_a_slot_left_behind() -> None:
    from katatone.amp import slots
    state = types.SimpleNamespace(
        patch_number=1, name="Crunch", dirty=False, slot_names={},
        amp_patch=1, connected=True, slots=slots.slots,
        patch_name=lambda: state.name)
    ed = object.__new__(pe.PatchEditor)
    ed.app = state
    ed.list_box = types.SimpleNamespace(controls=[])
    ed._patch_button = lambda slot, active, playing=False: ed._patch_label(slot)
    ed._rebuild_slot_list()
    state.patch_number, state.name = 2, "Clean"
    ed._rebuild_slot_list()
    assert ed.list_box.controls[1:4] == ["Crunch", "Clean", "Bank B · CH1"]


def test_startup_reads_every_slot_name(monkeypatch) -> None:
    import threading
    from katatone.amp import read, slots
    from katatone.gui.app import App
    t = object()
    names = {0: "Panel", 1: "Crunch", 2: "", 5: "Lead", 6: ""}
    monkeypatch.setattr(read, "read_patch_name", lambda tr, n: names[n])
    painted = []
    app = types.SimpleNamespace(
        slot_names={4: "Seen"}, connected=True, transport=t,
        _read_lock=threading.Lock(), slots=lambda: slots.slots(),
        ui_attached=True,
        editor=types.SimpleNamespace(refresh_channels=lambda: painted.append(1)))
    App._read_slot_names(app, t)
    assert app.slot_names == {0: "Panel", 1: "Crunch", 5: "Lead", 4: "Seen"}
    assert painted == [1]


def test_switching_to_a_slot_read_before_does_not_read_the_amp(monkeypatch) -> None:
    import threading
    from katatone.amp import read, write
    from katatone.gui.app import App
    reads = []

    def fake_read(t, n, progress=None):
        reads.append(n)
        ps = ParamSet()
        ps.set_block("PATCH%COM", ParamSet.encode_patch_name(f"P{n}"))
        return ps

    monkeypatch.setattr(read, "read_patch", fake_read)
    monkeypatch.setattr(write, "select_patch", lambda t, n: None)
    app = types.SimpleNamespace(
        slot_cache={}, _read_gen=1, _read_lock=threading.Lock(),
        state_lock=threading.RLock(), _read_pending=True, transport=object(),
        amp_patch=0, ps=None, dirty=False, patch_number=0, live_ready=False,
        set_busy=lambda *a: None, slot_name=lambda n: str(n),
        _note_self_select=lambda n: None, _apply_recent_writes=lambda ps, n: False,
        _synced=lambda: None, set_status=lambda m: None, refresh=lambda: None,
        _progress=lambda *a: None,
        patch_name=lambda: ParamSet.patch_name(app.ps.block("PATCH%COM")))
    for n, force in [(1, False), (2, False), (1, False), (1, True)]:
        App._read_thread(app, n, 1, False, None, force)
    assert reads == [1, 2, 1]               # the third switch came from the cache
    assert app.patch_name() == "P1"
    app.ps.set_block("PATCH%COM", ParamSet.encode_patch_name("Edited"))
    App._read_thread(app, 1, 1, False, None, False)
    assert app.patch_name() == "P1"         # edits never reach the remembered slot
