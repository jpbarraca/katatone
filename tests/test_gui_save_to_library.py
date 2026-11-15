"""The PATCH tab's "Save to library" dialog (headless, fake app)."""
from __future__ import annotations

import types

from katatone.gui import patch_editor as pe
from katatone.library.store import Library
from katatone.model.paramset import ParamSet


def _editor(tmp_path):
    shown, status, popped = [], [], []
    app = types.SimpleNamespace(
        lib=Library(tmp_path), ps=ParamSet(), patch_name=lambda: "Lead",
        set_status=status.append, library_refresh=lambda: None, popped=popped,
        page=types.SimpleNamespace(show_dialog=shown.append,
                                   pop_dialog=lambda: popped.append(1)))
    ed = object.__new__(pe.PatchEditor)
    ed.app = app
    return ed, shown, status


def _open(ed, shown, patch=None):
    """The dialog, its liveset dropdown and its new-name field (and the choice between them)."""
    ed._save_to_library(patch)
    dialog = shown[-1]
    mode = dialog.content.controls[0]
    assert [c.data for c in mode.content.controls[1::2]] == ["liveset", "name"]
    target, new = mode.content.controls[1], mode.content.controls[3]
    dialog.mode = mode
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
    ed.app.patch_name = lambda: "Other"
    dialog, target, new = _open(ed, shown)
    dialog.actions[1].on_click(None)
    assert [(e.name, e.count) for e in ed.app.lib.list()] == [("Mine", 2)]


def test_saving_a_patch_that_exists_asks_first(tmp_path) -> None:
    ed, shown, status = _editor(tmp_path)
    ed.app.lib.create("Mine")
    ed.app.ps.set_block("PATCH%COM", ParamSet.encode_patch_name("Lead"))
    _open(ed, shown)[0].actions[1].on_click(None)
    ed.app.ps.set_block("PATCH%AMP", b"\x07")          # a different sound
    _open(ed, shown)[0].actions[1].on_click(None)
    ask = shown[-1]
    assert "Overwrite" in ask.title.value and "Lead" in ask.content.value
    ask.actions[0].on_click(None)                                  # Cancel
    tsl = ed.app.lib.load("Mine")
    assert len(tsl) == 1 and "PATCH%AMP" not in tsl.patches[0]


def test_confirming_overwrites_the_patch_and_keeps_its_memo(tmp_path) -> None:
    ed, shown, status = _editor(tmp_path)
    tsl = ed.app.lib.create("Mine")
    tsl.add(pe.Tsl.encode(ParamSet()), memo="my notes")
    tsl.rename_patch(0, "Lead")
    ed.app.lib.save(tsl)
    ed.app.ps.set_block("PATCH%AMP", b"\x07")
    _open(ed, shown)[0].actions[1].on_click(None)
    shown[-1].actions[1].on_click(None)                            # Overwrite
    tsl = ed.app.lib.load("Mine")
    assert len(tsl) == 1 and tsl.memos[0] == "my notes"
    assert tsl.patches[0]["PATCH%AMP"] == ["07"]
    assert "Saved" in status[-1]


def test_nothing_chosen_saves_nothing(tmp_path) -> None:
    ed, shown, status = _editor(tmp_path)
    dialog, target, new = _open(ed, shown)
    dialog.actions[1].on_click(None)
    assert ed.app.lib.list() == [] and ed.app.popped == []          # the dialog stays open
    assert new.error == "Enter a name for the new liveset."


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


def test_saves_the_patch_it_is_given_not_the_editors(tmp_path) -> None:
    ed, shown, status = _editor(tmp_path)
    other = ParamSet()
    other.set_block("PATCH%COM", ParamSet.encode_patch_name("Suggested"))
    other.set_block("PATCH%AMP", b"\x07")
    dialog, target, new = _open(ed, shown, other)
    assert "Suggested" in dialog.title.value             # not the editor's "Lead"
    new.value = "Mine"
    dialog.actions[1].on_click(None)
    tsl = ed.app.lib.load("Mine")
    assert len(tsl) == 1 and tsl.patch_name(0) == "Suggested"
    assert tsl.to_paramset(0).block("PATCH%AMP")[:1] == b"\x07"
    assert "Suggested" in status[-1] and ed.app.ps.block("PATCH%AMP") == b""


# ---- adding to an existing liveset or creating a new one

def test_the_choice_follows_the_field_that_is_touched(tmp_path) -> None:
    ed, shown, status = _editor(tmp_path)
    ed.app.lib.create("Mine")
    dialog, target, new = _open(ed, shown)
    assert dialog.mode.value == "existing" and target.value == "Mine" and not target.disabled
    new.on_focus(None)                                  # click in the name field
    assert dialog.mode.value == "new"
    target.on_select(None)                              # pick from the list
    assert dialog.mode.value == "existing"
    new.value = "x"
    new.on_change(None)                                 # type a name
    assert dialog.mode.value == "new"


def test_a_new_liveset_next_to_existing_ones(tmp_path) -> None:
    ed, shown, status = _editor(tmp_path)
    ed.app.lib.create("Mine")
    dialog, target, new = _open(ed, shown)
    new.value = "Fresh"
    new.on_change(None)
    dialog.actions[1].on_click(None)
    assert [(e.name, e.count) for e in ed.app.lib.list()] == [("Fresh", 1), ("Mine", 0)]
    assert ed.app.popped == [1]


def test_adding_to_the_liveset_that_is_picked(tmp_path) -> None:
    ed, shown, status = _editor(tmp_path)
    ed.app.lib.create("A")
    ed.app.lib.create("B")
    dialog, target, new = _open(ed, shown)
    target.value = "B"
    target.on_select(None)
    new.value = "left over from before"                  # ignored: the dropdown was chosen last
    dialog.actions[1].on_click(None)
    assert [(e.name, e.count) for e in ed.app.lib.list()] == [("A", 0), ("B", 1)]


def test_a_new_name_that_is_taken_is_refused_not_renamed(tmp_path) -> None:
    ed, shown, status = _editor(tmp_path)
    ed.app.lib.create("Mine")
    dialog, target, new = _open(ed, shown)
    new.value = "Mine"
    new.on_change(None)
    dialog.actions[1].on_click(None)
    assert "exists" in new.error and ed.app.popped == []         # the dialog stays open
    assert [(e.name, e.count) for e in ed.app.lib.list()] == [("Mine", 0)]   # no "Mine 2"
    new.on_focus(None)
    target.on_select(None)                              # choosing the existing one clears it
    assert new.error is None
    dialog.actions[1].on_click(None)
    assert [(e.name, e.count) for e in ed.app.lib.list()] == [("Mine", 1)]


def test_no_livesets_yet_offers_only_a_new_one(tmp_path) -> None:
    ed, shown, status = _editor(tmp_path)
    dialog, target, new = _open(ed, shown)
    assert dialog.mode.value == "new" and target.disabled
    assert dialog.mode.content.controls[0].disabled          # the "existing" radio


# ---- the notes of the patch go with it

def _overwrite(ed, shown, notes=None) -> None:
    ed.app.ps.set_block("PATCH%AMP", b"\x07")
    ed._save_to_library(notes=notes)
    shown[-1].actions[1].on_click(None)                            # Save
    shown[-1].actions[1].on_click(None)                            # Overwrite


def _liveset_with_lead(ed, memo):
    ed.app.ps.set_block("PATCH%COM", ParamSet.encode_patch_name("Lead"))
    tsl = ed.app.lib.create("Mine")
    tsl.add(pe.Tsl.encode(ParamSet()), memo=memo)
    tsl.rename_patch(0, "Lead")
    ed.app.lib.save(tsl)


def test_a_new_patch_is_saved_with_the_editors_notes(tmp_path) -> None:
    ed, shown, status = _editor(tmp_path)
    ed.app.notes = "  Strat, position 4\nTone at 7  "
    ed.app.lib.create("Mine")
    _open(ed, shown)[0].actions[1].on_click(None)
    assert ed.app.lib.load("Mine").note(0) == "Strat, position 4\nTone at 7"


def test_overwriting_replaces_the_notes_only_when_there_are_new_ones(tmp_path) -> None:
    ed, shown, status = _editor(tmp_path)
    _liveset_with_lead(ed, "old notes")
    ed.app.notes = ""
    _overwrite(ed, shown)
    assert ed.app.lib.load("Mine").note(0) == "old notes"          # none to give: kept
    ed.app.ps.set_block("PATCH%AMP", b"\x08")
    ed.app.notes = "new notes"
    _overwrite(ed, shown)
    assert ed.app.lib.load("Mine").note(0) == "new notes"


def test_overwriting_a_tone_central_patch_keeps_its_other_memo_entries(tmp_path) -> None:
    ed, shown, status = _editor(tmp_path)
    _liveset_with_lead(ed, {"memo": "", "isToneCentralPatch": True})
    ed.app.notes = "my notes"
    _overwrite(ed, shown)
    assert ed.app.lib.load("Mine").memos[0] == {"memo": "my notes", "isToneCentralPatch": True}


def test_notes_can_be_given_for_a_patch_that_is_not_the_editors(tmp_path) -> None:
    ed, shown, status = _editor(tmp_path)
    ed.app.notes = "the editor's"
    ed._save_to_library(ParamSet(), notes="the suggestion's")
    shown[-1].content.controls[0].content.controls[3].value = "Mine"
    shown[-1].actions[1].on_click(None)
    assert ed.app.lib.load("Mine").note(0) == "the suggestion's"
