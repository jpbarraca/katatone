"""The notes of a patch: its memo in the liveset, shown and edited in the NOTES tab and the library."""
from __future__ import annotations

import json
import types

import flet as ft
import flet.controls.base_control as bc
import pytest

from katatone.ai.proposal import Proposal
from katatone.gui import app as app_mod
from katatone.liveset.tsl import Tsl, memo_text, with_memo_text
from katatone.model.paramset import ParamSet
from katatone.setlist import Setlist
from test_gui_resync import StubPage
from test_library_meta import liveset


def find(control, kind):
    """Every control of type ``kind`` inside ``control`` (itself included), top to bottom."""
    found = [control] if isinstance(control, kind) else []
    for child in [*(getattr(control, "controls", None) or []), getattr(control, "content", None)]:
        if child is not None:
            found += find(child, kind)
    return found


# ---- the memo is the notes

def test_a_plain_memo_is_the_notes() -> None:
    assert memo_text("Neck pickup") == "Neck pickup" and memo_text("") == ""
    assert with_memo_text("old", "new") == "new" and with_memo_text("", "new") == "new"


def test_a_tone_central_memo_keeps_its_own_entries() -> None:
    memo = {"memo": "from the site", "isToneCentralPatch": True, "toneCentralPatchId": "42"}
    assert memo_text(memo) == "from the site"
    changed = with_memo_text(memo, "mine")
    assert changed == {"memo": "mine", "isToneCentralPatch": True, "toneCentralPatchId": "42"}
    assert memo["memo"] == "from the site"                       # the original is not touched
    assert memo_text({"isToneCentralPatch": True}) == ""         # no text entry: no notes
    assert with_memo_text({"isToneCentralPatch": True}, "x")["isToneCentralPatch"] is True
    assert memo_text(None) == "" and with_memo_text(None, "x") == "x"


def test_notes_survive_the_file_and_copies(tmp_path) -> None:
    tsl = Tsl(name="Mine")
    tsl.add(Tsl.encode(ParamSet()), memo={"memo": "", "isToneCentralPatch": True})
    tsl.add(Tsl.encode(ParamSet()))
    tsl.set_note(0, "Line one\nLine two — with a dash")
    tsl.set_note(1, "second")
    path = tmp_path / "Mine.tsl"
    tsl.save(path)
    loaded = Tsl.load(path)
    assert loaded.note(0) == "Line one\nLine two — with a dash" and loaded.note(1) == "second"
    assert loaded.memos[0]["isToneCentralPatch"] is True         # the object is still one
    loaded.duplicate(1)
    loaded.move(2, 0)
    assert [loaded.note(i) for i in range(3)] == ["second", "Line one\nLine two — with a dash", "second"]


# ---- the app

@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("KATATONE_NO_BLUETOOTH", "1")
    monkeypatch.setattr(bc.BaseControl, "update", lambda self: None)
    a = app_mod.App(StubPage(), start_midi=False)
    liveset(a.lib, "Blues", "Crunch", "Clean")
    tsl = a.lib.load("Blues")
    tsl.set_note(0, "Les Paul, bridge pickup")
    a.lib.save(tsl)
    a.library.refresh()
    a.library._select("Blues")
    return a


def test_loading_a_library_patch_brings_its_notes(app) -> None:
    app.library._load_patch(0)
    assert app.notes == "Les Paul, bridge pickup"
    app.library._load_patch(1)
    assert app.notes == ""                                       # not the previous patch's


def test_the_notes_dialog_edits_the_memo(app) -> None:
    app.library._edit_notes(1)
    dialog = app.page.dialogs[-1]
    assert dialog.content.value == "" and dialog.content.multiline
    dialog.content.value = "  Tele, neck.\nRoll the tone to 6.  "
    dialog.actions[1].on_click(None)
    assert app.lib.load("Blues").note(1) == "Tele, neck.\nRoll the tone to 6."     # stripped
    assert app.lib.load("Blues").note(0) == "Les Paul, bridge pickup"             # the others stay
    assert app.last_status == "Notes saved."
    app.library._edit_notes(1)
    app.page.dialogs[-1].content.value = ""
    app.page.dialogs[-1].actions[1].on_click(None)
    assert app.lib.load("Blues").note(1) == "" and app.last_status == "Notes cleared."


def test_the_notes_dialog_starts_with_the_notes_there_are(app) -> None:
    app.library._edit_notes(0)
    assert app.page.dialogs[-1].content.value == "Les Paul, bridge pickup"


def _row_icons(app, i):
    row = app.library._patch_row(i, [])
    return [c for c in row.content.controls if isinstance(c, ft.IconButton)]


def test_a_patch_with_notes_shows_a_note_icon_with_them(app) -> None:
    with_notes = [b for b in _row_icons(app, 0) if b.icon == ft.Icons.STICKY_NOTE_2]
    assert len(with_notes) == 1 and with_notes[0].tooltip == "Les Paul, bridge pickup"
    assert not [b for b in _row_icons(app, 1) if b.icon == ft.Icons.STICKY_NOTE_2]


def test_a_long_note_is_cut_in_the_tooltip(app) -> None:
    tsl = app.lib.load("Blues")
    tsl.set_note(1, "x" * 900)
    app.lib.save(tsl)
    app.library.refresh()
    tip = [b for b in _row_icons(app, 1) if b.icon == ft.Icons.STICKY_NOTE_2][0].tooltip
    assert len(tip) == 401 and tip.endswith("…")


def test_copying_a_patch_to_another_liveset_copies_its_notes(app) -> None:
    liveset(app.lib, "Other", "Mine")
    app.library._transfer(0, "Other", move=False)
    other = app.lib.load("Other")
    assert [other.note(i) for i in range(len(other))] == ["", "Les Paul, bridge pickup"]


def test_notes_added_to_the_editors(app) -> None:
    app.add_notes("  first  ")
    assert app.notes == "first"
    app.add_notes("second")
    assert app.notes == "first\n\nsecond"                        # below, nothing lost
    app.add_notes("second")
    app.add_notes("   ")
    assert app.notes == "first\n\nsecond"                        # not twice, blanks ignored


def test_reading_a_patch_from_the_amp_clears_the_notes(app, monkeypatch) -> None:
    monkeypatch.setattr("katatone.amp.read.read_patch", lambda t, n, progress=None: ParamSet())
    app.connected, app.transport = True, object()
    app.notes = "belongs to a library patch"
    app.resync(1, delay=0)
    app.page.join()
    assert app.notes == ""


def test_the_setlist_has_the_notes_of_its_patches(app, monkeypatch) -> None:
    sl = Setlist.from_tsl(app.lib.load("Blues"))
    assert sl.note(0) == "Les Paul, bridge pickup" and sl.note(1) == "" and sl.note(9) == ""
    assert Setlist("x", ["a"], [None]).note(0) == ""             # built without notes
    from katatone.amp import write as w
    monkeypatch.setattr(w, "write_edit_buffer", lambda t, ps: None)
    app.connected, app.transport = True, object()
    app.notes = "left over"
    app.setlist_open("Blues")
    app.setlist_goto(0)
    assert app.notes == "Les Paul, bridge pickup"
    app.setlist_goto(1)
    assert app.notes == ""


# ---- the Notes tab, the last one of the patch

def test_the_last_tab_of_the_patch_is_its_notes(app) -> None:
    from katatone.gui.patch_editor import NOTES_TAB
    specs = app.editor._tab_specs
    assert specs[-1] == (NOTES_TAB, []) and NOTES_TAB == "Notes"
    assert [label for label, _ in specs].count("Notes") == 1 and specs[0][0] == "Amp"
    assert app.editor._tab_bar.tabs[-1].label == "NOTES"
    assert app.editor.tabs.length == len(specs) == len(app.editor._tab_view.controls)


def test_the_notes_tab_is_built_when_visited_and_shows_the_notes(app) -> None:
    app.notes = "Strat, position 4"
    app.editor.refresh()
    last = len(app.editor._tab_specs) - 1
    assert last not in app.editor._built                          # like the other tabs: on first visit
    app.editor._build_tab(last)
    field = find(app.editor._tab_view.controls[last].content, ft.TextField)[0]
    assert field.value == "Strat, position 4" and field.multiline


def test_the_notes_card_and_its_text_box_take_the_whole_width(app) -> None:
    last = len(app.editor._tab_specs) - 1
    app.editor._build_tab(last)
    content = app.editor._tab_view.controls[last].content
    card = find(content, ft.Container)[0]
    assert card.expand is True                                    # expands in a row: the tab's whole width
    assert any(card in row.controls for row in find(content, ft.Row))
    field = find(card, ft.TextField)[0]
    row = [r for r in find(card, ft.Row) if field in r.controls][0]
    assert field.expand is True and field.width is None           # the field fills the card's width


def test_the_amp_tab_no_longer_holds_the_notes(app) -> None:
    app.editor._build_tab(0)
    shown = repr(app.editor._tab_view.controls[0].content)
    assert "Notes" not in shown and "NOTES" not in shown


def test_edits_in_the_notes_tab_reach_the_app_and_survive_a_refresh(app) -> None:
    last = len(app.editor._tab_specs) - 1
    app.editor._build_tab(last)
    field = find(app.editor._tab_view.controls[last].content, ft.TextField)[0]
    field.on_change(types.SimpleNamespace(control=types.SimpleNamespace(value="typed")))
    assert app.notes == "typed"
    app.editor.refresh()                                          # the tabs are built again
    app.editor._build_tab(last)
    again = find(app.editor._tab_view.controls[last].content, ft.TextField)[0]
    assert again.value == "typed"


def test_the_notes_field_has_the_text_undo_not_the_patchs() -> None:
    from katatone.gui import patch_editor as pe
    ed = object.__new__(pe.PatchEditor)
    ed.app = types.SimpleNamespace(notes="Strat, position 4")
    ed.name_field_focused = False
    field = find(ed._notes_card(), ft.TextField)[0]
    assert field.value == "Strat, position 4" and field.min_lines >= 10
    field.on_focus(None)
    assert ed.name_field_focused                                  # Ctrl+Z is the text's, not the patch's
    field.on_blur(None)
    assert not ed.name_field_focused


# ---- from the AI

REPLY = {"match": {"song": "Sultans of Swing", "artist": "Dire Straits", "confidence": "high"},
         "original_rig": "Strat into a Vox", "reasoning": "Bright clean.",
         "pickup": "position 4, tone at 7", "notes": "Fingerpicked.",
         "amp": {"type": "Clean", "gain": 30}}


def test_the_suggestion_as_notes() -> None:
    p = Proposal(song="Sultans of Swing", artist="Dire Straits", confidence="high",
                 rig="Strat into a Vox", reasoning="Bright\nclean.", pickup="position 4",
                 notes="Fingerpicked.")
    assert p.notes_text() == ("Tone AI suggestion for Sultans of Swing — Dire Straits\n"
                              "Original rig: Strat into a Vox\nApproach: Bright clean.\n"
                              "Pickup: position 4\nNotes: Fingerpicked.")
    assert Proposal().notes_text() == "Tone AI suggestion"
    assert "(medium confidence)" in Proposal(song="x", confidence="medium").notes_text()
    assert Proposal(song="x", rig="  ").notes_text() == "Tone AI suggestion for x"


@pytest.fixture
def tone(app, monkeypatch):
    pane = app.tone_ai
    pane.settings = pane.settings.__class__(api_key="k")
    monkeypatch.setattr("katatone.gui.tone_ai_pane.backend.chat", lambda s, a, b: json.dumps(REPLY))
    pane.query.value = "sultans of swing"
    pane.ask()
    app.page.join()
    return pane


def test_saving_the_suggestion_to_the_library_keeps_its_text_as_the_notes(app, tone) -> None:
    tone.save_to_library()
    dialog = app.page.dialogs[-1]
    new = dialog.content.controls[0].content.controls[3]
    new.value = "Songs"
    new.on_change(None)                                          # typing a name chooses "new"
    dialog.actions[1].on_click(None)
    tsl = app.lib.load("Songs")
    assert tsl.note(0) == ("Tone AI suggestion for Sultans of Swing — Dire Straits\n"
                           "Original rig: Strat into a Vox\nApproach: Bright clean.\n"
                           "Pickup: position 4, tone at 7\nNotes: Fingerpicked.")
    assert app.notes == ""                                       # the editor was left alone


def test_applying_the_suggestion_adds_its_text_to_the_editors_notes(app, tone) -> None:
    app.notes = "My own note."
    tone.apply()
    assert app.notes.startswith("My own note.\n\nTone AI suggestion for Sultans of Swing")
    assert "notes (NOTES tab)" in app.last_status
    tone.apply()                                                 # again: not added twice
    assert app.notes.count("Tone AI suggestion") == 1


def test_nothing_taken_adds_no_notes(app, tone) -> None:
    tone.proposal.select_all(False)
    tone.apply()
    assert app.notes == ""


def test_the_editors_notes_go_to_the_library_with_the_patch(app) -> None:
    app.notes = "Strat, position 4"
    app.editor._save_to_library()
    dialog = app.page.dialogs[-1]
    new = dialog.content.controls[0].content.controls[3]
    new.value = "Mine"
    new.on_change(None)
    dialog.actions[1].on_click(None)
    assert app.lib.load("Mine").note(0) == "Strat, position 4"


# ---- the suggestion's text is taken on its own, like the settings

SUGGESTION = ("Tone AI suggestion for Sultans of Swing — Dire Straits\n"
              "Original rig: Strat into a Vox\nApproach: Bright clean.\n"
              "Pickup: position 4, tone at 7\nNotes: Fingerpicked.")


def _button_labels(tone):
    row = tone._actions(tone.proposal)
    return row.controls[0].content.value, row.controls[0].disabled


def _notes_box(tone):
    return find(tone._header(tone.proposal), ft.Checkbox)[0]


def test_notes_only_adds_the_text_and_changes_nothing_else(app, tone) -> None:
    tone.proposal.select_all(False)
    tone.proposal.take_notes = True
    assert _button_labels(tone) == ("Add to notes", False)
    before = dict(app.ps.data)
    tone.apply()
    assert app.notes == SUGGESTION
    assert app.ps.data == before and not app.dirty              # no setting, nothing unsaved
    assert app.last_status == "Added the suggestion for Sultans of Swing to the notes (NOTES tab)."
    assert app.history.can_undo(app.ps) is False


def test_the_notes_tab_shows_the_added_text(app, tone) -> None:
    last = len(app.editor._tab_specs) - 1
    app.editor.tabs.selected_index = last                       # the Notes tab is the one on show
    tone.proposal.select_all(False)
    tone.proposal.take_notes = True
    tone.apply()
    assert last in app.editor._built                            # rebuilt at once
    field = find(app.editor._tab_view.controls[last].content, ft.TextField)[0]
    assert field.value == SUGGESTION
    app.editor.tabs.selected_index = 0                          # on another tab: built when visited
    app.notes = "changed"
    app.editor.refresh()
    assert last not in app.editor._built
    app.editor._build_tab(last)
    assert find(app.editor._tab_view.controls[last].content, ft.TextField)[0].value == "changed"


def test_settings_only_leaves_the_notes_alone(app, tone) -> None:
    app.notes = "mine"
    tone.proposal.take_notes = False
    assert _button_labels(tone)[0].startswith("Apply ") and "notes" not in _button_labels(tone)[0]
    tone.apply()
    assert app.notes == "mine" and app.dirty
    assert "notes" not in app.last_status and "Ctrl+Z undoes them" in app.last_status


def test_both_say_so(app, tone) -> None:
    label = _button_labels(tone)[0]
    assert label.startswith("Apply ") and label.endswith(" selected + notes")
    tone.apply()
    assert app.notes == SUGGESTION and app.dirty
    assert "text added to the notes (NOTES tab)" in app.last_status


def test_nothing_ticked_does_nothing(app, tone) -> None:
    tone.proposal.select_all(False)
    assert _button_labels(tone) == ("Nothing selected", True)
    tone.apply()
    assert app.notes == "" and tone.status.value == "Nothing is taken."


def test_the_box_is_ticked_and_follows_select_all_and_none(app, tone) -> None:
    box = _notes_box(tone)
    assert box.value is True and not box.disabled and "NOTES tab" in box.label
    tone._select(False)
    assert _notes_box(tone).value is False and tone.proposal.take_notes is False
    tone._select(True)
    assert _notes_box(tone).value is True
    box = _notes_box(tone)
    box.on_change(types.SimpleNamespace(control=types.SimpleNamespace(value=False)))
    assert tone.proposal.take_notes is False and _notes_box(tone).value is False


def test_text_already_in_the_notes_is_not_offered_again(app, tone) -> None:
    tone.apply()                                                # settings and text
    box = _notes_box(tone)
    assert box.label == "Already in the patch's notes" and box.disabled and box.value is False
    assert _button_labels(tone) == ("Nothing selected", True)
    tone._select(True)                                          # even Select all does not add it
    assert tone.proposal.notes_pending is False and app.notes.count("Tone AI suggestion") == 1
    app.notes = ""                                              # the user clears the notes
    tone._render()
    assert not _notes_box(tone).disabled                        # it can be added again


def test_asking_again_starts_ticked_unless_the_text_is_there(app, tone, monkeypatch) -> None:
    tone.apply()
    tone.query.value = "sultans of swing"
    tone.ask()
    app.page.join()
    assert tone.proposal.notes_present and tone.proposal.take_notes is False
    app.notes = "other"
    tone.ask()
    app.page.join()
    assert tone.proposal.take_notes is True


def test_the_saved_copy_has_the_editors_notes_and_the_text_when_ticked(app, tone) -> None:
    def save(name):
        tone.save_to_library()
        dialog = app.page.dialogs[-1]
        new = dialog.content.controls[0].content.controls[3]
        new.value = name
        new.on_change(None)
        dialog.actions[1].on_click(None)
        return app.lib.load(name).note(0)

    app.notes = "mine"
    assert save("A") == "mine\n\n" + SUGGESTION                 # below the editor's notes
    tone.proposal.take_notes = False
    assert save("B") == "mine"                                  # the text not taken
    app.notes = ""
    assert save("C") == ""
    assert app.notes == ""                                      # the editor was left alone


def test_a_copy_saved_after_apply_carries_the_notes_there(app, tone) -> None:
    app.notes = "mine"
    tone.apply()
    tone.save_to_library()
    dialog = app.page.dialogs[-1]
    new = dialog.content.controls[0].content.controls[3]
    new.value = "D"
    new.on_change(None)
    dialog.actions[1].on_click(None)
    assert app.lib.load("D").note(0) == "mine\n\n" + SUGGESTION


def test_apply_group_can_set_notes_without_any_change(app) -> None:
    app.apply_group([], "x", "", notes="hello")
    assert app.notes == "hello" and not app.dirty
    app.apply_group([], "x", "Nothing to do.")
    assert app.notes == "hello" and app.last_status == "Nothing to do."


def test_the_proposal_state_of_the_notes() -> None:
    p = Proposal(song="x")
    assert p.notes_pending                                      # ticked by default
    p.select_all(False)
    assert not p.notes_pending
    p.select_all(True)
    assert p.notes_pending
    p.notes_present = True
    assert not p.notes_pending
    p.select_all(True)
    assert p.take_notes is False                                # never ticked when there is nothing to add
