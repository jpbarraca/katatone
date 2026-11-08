"""The SYSTEM tab reads the amp's settings by itself when it opens (or becomes readable) empty."""
from __future__ import annotations

import types

import flet.controls.base_control as bc
import pytest

from katatone.gui import app as app_mod
from katatone.model.paramset import ParamSet
from test_gui_resync import StubPage

SYSTEM = app_mod.TAB_ORDER.index(app_mod.TAB_SYSTEM)
PATCH = app_mod.TAB_ORDER.index(app_mod.TAB_PATCH)


def tab(index: int):
    return types.SimpleNamespace(control=types.SimpleNamespace(selected_index=index))


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("KATATONE_NO_BLUETOOTH", "1")
    monkeypatch.setattr(bc.BaseControl, "update", lambda self: None)
    a = app_mod.App(StubPage(), start_midi=False)
    a.reads = []
    a.load_system = lambda: a.reads.append("system")       # the worker thread is not what is tested
    a.connected, a.transport = True, object()
    return a


def test_opening_the_tab_empty_reads_the_settings(app) -> None:
    app._on_tab_change(tab(SYSTEM))
    assert app.reads == ["system"]


def test_other_tabs_do_not_read_them(app) -> None:
    app._on_tab_change(tab(PATCH))
    app.refresh()
    assert app.reads == []


def test_a_tab_that_already_has_its_settings_is_left_alone(app) -> None:
    app.system_ps = ParamSet()
    app._on_tab_change(tab(SYSTEM))
    app.refresh()
    assert app.reads == []


def test_without_the_amp_nothing_is_read_until_it_is_connected(app) -> None:
    app.connected, app.transport = False, None
    app._on_tab_change(tab(SYSTEM))
    assert app.reads == []
    app.connected, app.transport = True, object()           # connected while the tab is open
    app.refresh()                                            # every amp operation ends with a refresh
    assert app.reads == ["system"]


def test_a_busy_amp_is_waited_for(app) -> None:
    app.busy = True
    app._on_tab_change(tab(SYSTEM))
    assert app.reads == []
    app.refresh()                                            # the operation ended (this clears busy)
    assert app.reads == ["system"]


def test_it_waits_while_connecting_and_while_a_patch_is_being_read(app) -> None:
    app._tab_index = SYSTEM
    app._connecting = True
    app.refresh()
    app._connecting, app._read_pending = False, True
    app.refresh()
    assert app.reads == []
    app._read_pending = False                                # the patch read finished and refreshed
    app.refresh()
    assert app.reads == ["system"]


def test_one_attempt_per_visit_so_a_failed_read_is_not_retried_in_a_loop(app) -> None:
    app._on_tab_change(tab(SYSTEM))
    app.refresh()
    app.refresh()                                            # the read failed: system_ps is still None
    assert app.reads == ["system"]
    app._on_tab_change(tab(PATCH))
    app._on_tab_change(tab(SYSTEM))                          # a new visit tries again
    assert app.reads == ["system", "system"]


def fake_amp():
    return types.SimpleNamespace(on_unsolicited=None, start=lambda: None, stop=lambda: None,
                                 identity_reply=None)


def test_disconnecting_forgets_the_settings_and_allows_a_new_attempt(app) -> None:
    app.transport = fake_amp()
    app.system_ps = ParamSet()
    app._system_auto_done = True
    app._disconnect_thread(graceful=False)
    assert app.system_ps is None and not app._system_auto_done


def test_connecting_while_the_tab_is_open_reads_it_once_the_patch_has_been_read(app, monkeypatch) -> None:
    """The connection reads the current patch first; the settings must not share the wire with it."""
    import time
    from katatone.amp import connect as amp_connect
    from katatone.protocol import transport as proto
    app.connected, app.transport = False, None
    app._tab_index = SYSTEM
    app._system_auto_done = True                              # an attempt from before
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(proto, "make_alsa_transport", lambda i, o: fake_amp())
    monkeypatch.setattr(amp_connect, "handshake", lambda t: 0x10)
    monkeypatch.setattr(app, "_adopt_amp_patch", lambda t: None)
    monkeypatch.setattr(app, "read_patch", lambda n: app._new_read_request())   # the read is under way
    app.dirty = False
    app._connecting = True
    app._connect_thread("A", "A")
    assert app.connected and app._read_pending
    assert app.reads == []                                    # not while the patch is being read
    app._read_pending = False
    app.refresh()                                             # the read ended
    assert app.reads == ["system"]


def test_the_empty_tab_says_what_it_waits_for(app) -> None:
    app.refresh()
    assert "Reading the SYSTEM settings" in app.system.content_box.controls[0].value
    app.connected = False
    app.system.refresh()
    assert app.system.content_box.controls[0].value == "Connect the amp to read its SYSTEM settings."


def test_the_system_read_waits_for_other_reads_on_the_wire(app, monkeypatch) -> None:
    import threading
    import time
    from katatone.amp import read
    monkeypatch.setattr(read, "read_region", lambda t, name, progress=None: ParamSet())
    done = threading.Event()
    with app._read_lock:                                     # e.g. the slot names are being read
        worker = threading.Thread(
            target=lambda: (app._system_thread("load"), done.set()), daemon=True)
        worker.start()
        time.sleep(0.2)
        assert not done.is_set() and app.system_ps is None   # held back, wire untouched
    assert done.wait(2) and app.system_ps is not None


def test_the_assign_tab_has_a_general_sub_tab_and_one_per_effect_in_two_lines(app) -> None:
    from katatone.gui import patch_editor as pe
    ed = pe.PatchEditor(app)
    region = app.region_for(app.patch_number)
    names = [b.name for b in region.blocks if b.name.startswith(("ASSIGN", "PATCH_KNOB"))]
    view = ed._assign_view(region, names)
    line1, line2, body = view.controls
    chips = line1.controls + line2.controls
    labels = [c.content.value for c in chips]
    assert labels[:3] == ["GENERAL", "BOOSTER", "DELAY"] and len(labels) == 35
    assert abs(len(line1.controls) - len(line2.controls)) <= 1      # two even lines
    assert all(c.expand for c in chips)                              # they fill the width
    general = body.content
    assert chips[0].bgcolor and not chips[1].bgcolor                 # General is on show
    chips[1].on_click(None)
    assert body.content is not general and chips[1].bgcolor and not chips[0].bgcolor
    chips[0].on_click(None)
    assert body.content is general                                   # kept, not rebuilt


def test_a_press_anywhere_on_the_interface_unselects_the_knob(app, monkeypatch) -> None:
    import flet as ft
    from katatone.gui.knob import Knob
    added = []
    monkeypatch.setattr(app.page, "add", lambda *c: added.extend(c), raising=False)
    app._build()
    root = added[-1]
    assert isinstance(root, ft.GestureDetector) and root.expand
    knob = Knob(value=5, min=0, max=10)
    knob._select()
    assert Knob.selected is knob
    root.on_pan_down(None)
    assert Knob.selected is None
