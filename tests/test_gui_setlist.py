"""The SETLIST tab and the app's setlist: opening, stepping, playing."""
from __future__ import annotations

import flet.controls.base_control as bc
import pytest

from katatone.amp import write as w
from katatone.gui import app as app_mod
from test_gui_resync import StubPage
from test_library_meta import liveset


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("KATATONE_NO_BLUETOOTH", "1")
    monkeypatch.setattr(bc.BaseControl, "update", lambda self: None)
    a = app_mod.App(StubPage(), start_midi=False)
    liveset(a.lib, "Gig", "Intro", "Verse", "Solo")
    a.connected, a.transport = True, object()
    a.played = []
    monkeypatch.setattr(w, "write_edit_buffer", lambda t, ps: a.played.append(ps.patch_name(ps.block("PATCH%COM"))))
    return a


def test_no_setlist_open_says_so(app) -> None:
    app.setlist_step("next")
    assert app.last_status == "No setlist is open." and app.played == []


def test_opening_plays_nothing_and_lists_the_patches(app) -> None:
    app.setlist_open("Gig")
    assert app.played == [] and app.setlist.names == ["Intro", "Verse", "Solo"]
    assert app.setlist_view.now.value == "Gig"
    assert len(app.setlist_view.items.controls) == 3


def test_next_and_previous_play_through_the_edit_buffer(app) -> None:
    app.setlist_open("Gig")
    app.setlist_step("next")
    app.setlist_step("next")
    app.setlist_step("prev")
    assert app.played == ["Intro", "Verse", "Intro"]
    assert app.setlist.index == 0 and app.setlist_view.now.value == "Intro"
    assert app.last_status == "Setlist 1/3: Intro"


def test_the_editor_follows_the_played_patch_and_is_live(app) -> None:
    app.dirty = True
    app.setlist_open("Gig")
    app.setlist_step("next")
    assert app.patch_name() == "Intro" and not app.dirty and app.live_ready
    app.ps.set_block("PATCH%AMP", b"\x09")                   # editing must not touch the setlist
    assert app.setlist.patches[0].block("PATCH%AMP") == b""


def test_the_ends_do_not_wrap(app) -> None:
    app.setlist_open("Gig")
    app.setlist_step("prev")                                  # before anything: the last one
    assert app.played == ["Solo"]
    app.setlist_step("next")
    assert app.played == ["Solo"] and app.last_status == "End of the setlist."
    app.setlist_goto(0)
    app.setlist_step("prev")
    assert app.last_status == "Start of the setlist."


def test_not_connected_plays_nothing(app) -> None:
    app.setlist_open("Gig")
    app.connected = False
    app.setlist_step("next")
    assert app.played == [] and app.last_status == "Not connected." and app.setlist.index is None


def test_a_failing_write_keeps_the_position(app, monkeypatch) -> None:
    app.setlist_open("Gig")

    def boom(t, ps):
        raise OSError("link lost")
    monkeypatch.setattr(w, "write_edit_buffer", boom)
    app.setlist_step("next")
    assert app.last_status == "Setlist: link lost" and app.setlist.index is None


def test_a_missing_liveset_is_reported(app) -> None:
    app.setlist_open("Nope")
    assert app.setlist is None and app.last_status.startswith("Cannot open 'Nope'")


def test_busy_amp_is_left_alone(app) -> None:
    app.setlist_open("Gig")
    app.busy = True
    app.setlist_step("next")
    assert app.played == [] and "Wait" in app.last_status


def test_clicking_a_list_entry_plays_that_patch(app) -> None:
    app.setlist_open("Gig")
    app.setlist_view.items.controls[2].on_click(None)
    app.page.join()
    assert app.played == ["Solo"]


def test_the_tab_exists_in_order(app) -> None:
    assert app_mod.TAB_ORDER[-1] == "SETLIST"
