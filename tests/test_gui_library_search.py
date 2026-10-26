"""Search, tag filter and favourites in the library pane."""
from __future__ import annotations

import flet as ft
import flet.controls.base_control as bc
import pytest

from katatone.gui import app as app_mod
from katatone.gui import library_pane
from test_gui_resync import StubPage
from test_library_meta import liveset


@pytest.fixture
def pane(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("KATATONE_NO_BLUETOOTH", "1")
    monkeypatch.setattr(bc.BaseControl, "update", lambda self: None)
    app = app_mod.App(StubPage(), start_midi=False)
    liveset(app.lib, "Blues", "Crunch")
    liveset(app.lib, "Metal", "Brutal")
    liveset(app.lib, "Ambient", "Shimmer")
    app.library.refresh()
    return app.library


def shown(pane) -> list[str]:
    """Names of the livesets in the list, in order."""
    def name(row):
        col = row.content.controls[1]
        return col.controls[0].value
    return [name(r) for r in pane.liveset_list.controls if isinstance(r, ft.Container)]


def test_all_livesets_are_listed_by_name(pane) -> None:
    assert shown(pane) == ["Ambient", "Blues", "Metal"]


def test_search_narrows_the_list_and_says_when_nothing_matches(pane) -> None:
    pane.search_field.value = "brutal"                  # a patch name
    pane.refresh()
    assert shown(pane) == ["Metal"]
    pane.search_field.value = "nothing like this"
    pane.refresh()
    assert shown(pane) == [] and pane.liveset_list.controls[0].value == "No livesets match."


def test_favourites_come_first_and_can_be_filtered(pane) -> None:
    pane._set_favourite("Metal", True)
    assert shown(pane) == ["Metal", "Ambient", "Blues"]
    pane._toggle_fav_filter(None)
    assert shown(pane) == ["Metal"]
    pane._set_favourite("Metal", False)
    assert shown(pane) == []
    pane._toggle_fav_filter(None)
    assert len(shown(pane)) == 3


def test_tags_are_edited_shown_and_filtered(pane, monkeypatch) -> None:
    asked = []
    monkeypatch.setattr(pane, "_prompt", lambda title, value, on_ok, max_length, allow_empty=False:
                        asked.append((value, on_ok, allow_empty)))
    pane._edit_tags("Blues", [])
    value, done, allow_empty = asked[-1]
    assert value == "" and allow_empty               # clearing the tags must be possible
    done("gig, warm")
    assert pane.tag_filter.options[1:] and [o.key for o in pane.tag_filter.options] == ["All tags", "gig", "warm"]
    pane.tag_filter.value = "gig"
    pane.refresh()
    assert shown(pane) == ["Blues"]
    pane._edit_tags("Blues", ["gig", "warm"])
    assert asked[-1][0] == "gig, warm"
    asked[-1][1]("")                                  # cleared: the filter falls back to all
    assert pane.tag_filter.value == "All tags" and len(shown(pane)) == 3
