"""The persistent backend: the App runs with no UI, browser sessions borrow it,
and it keeps the amp connected."""
from __future__ import annotations

import time
import types

import flet.controls.base_control as bc
import pytest

from katatone.gui import app as app_mod
from katatone.gui import headless
from katatone.gui.headless import HeadlessPage
from test_gui_resync import StubPage


class Page(StubPage):
    """A browser session's page."""

    def __init__(self):
        super().__init__()
        self.cleaned = 0
        self.added = []

    def clean(self):
        self.cleaned += 1
        self.added.clear()

    def add(self, *controls):
        self.added.extend(controls)


@pytest.fixture
def backend(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(bc.BaseControl, "update", lambda self: None)
    return app_mod.App(start_midi=False)


def test_the_app_runs_without_a_page(backend) -> None:
    assert not backend.ui_attached and isinstance(backend.page, HeadlessPage)
    backend.set_status("Hello")
    backend.set_busy(True, "Working")
    backend.refresh()
    backend.mark_dirty()
    backend.show_param("AMP", 1, 5)
    backend._progress(1, 3, "Reading")
    assert backend.last_status == "Working" and backend.dirty and not backend.busy


def test_worker_threads_run_headless(backend) -> None:
    done = []
    backend.page.run_thread(lambda x: done.append(x), 7)
    deadline = time.monotonic() + 2
    while not done and time.monotonic() < deadline:
        time.sleep(0.01)
    assert done == [7]


def test_attach_shows_the_current_state_and_detach_keeps_it(backend) -> None:
    backend.connected = True
    backend.amp_patch = 2
    backend.set_status("Switched to 1B from the pedal")
    page = Page()
    backend.attach(page)
    assert backend.ui_attached and backend.page is page
    assert backend.status_text.value == "Switched to 1B from the pedal"
    assert page.added                                  # the UI was built on it
    backend.detach(page)
    assert not backend.ui_attached
    assert backend.connected and backend.amp_patch == 2


def test_a_second_session_takes_over_and_the_first_is_told(backend) -> None:
    first, second = Page(), Page()
    backend.attach(first)
    backend.attach(second)
    assert backend.page is second
    assert first.cleaned and len(first.added) == 1     # just the notice
    backend.detach(first)                              # stale: ignored
    assert backend.page is second


def test_attach_reuses_the_running_controllers(backend, tmp_path) -> None:
    from katatone.midi.manager import MidiManager
    from katatone.midi.store import ProfileStore
    from test_gui_midi_pane import FakeInput, PORTS
    FakeInput.instances = []
    m = MidiManager(backend, ProfileStore(tmp_path / "midi"), input_factory=FakeInput,
                    list_ports=lambda: list(PORTS))
    backend.start_midi(m)
    assert backend.midi_pane.midi is m
    ghost = backend.midi_pane                          # the headless widgets' pane
    page = Page()
    backend.attach(page)
    assert ghost._pump is None and ghost.midi is not None and backend.midi_pane is not ghost
    assert backend.midi is m and backend.midi_pane.midi is m
    backend.detach(page)
    assert backend.midi is m and backend.midi_pane.midi is m
    backend.stop_midi()


def test_safe_update_ignores_unmounted_controls_only(monkeypatch) -> None:
    import flet as ft

    def unmounted(self):
        raise RuntimeError("Text Control must be added to the page first")

    def other(self):
        raise RuntimeError("Frozen control cannot be updated.")

    monkeypatch.setattr(headless, "_installed", False)
    monkeypatch.setattr(bc.BaseControl, "update", unmounted)
    headless.install_safe_update()
    ft.Text("x").update()                              # swallowed
    monkeypatch.setattr(headless, "_installed", False)
    monkeypatch.setattr(bc.BaseControl, "update", other)
    headless.install_safe_update()
    with pytest.raises(RuntimeError, match="Frozen"):
        ft.Text("x").update()


# ------------------------------------------------------------- auto-connect

class Ports:
    def __init__(self, backend):
        self.connects = []
        backend.connect = lambda i, o: self.connects.append((i, o))


def test_connects_to_the_katana_when_it_appears(backend) -> None:
    p = Ports(backend)
    backend._auto_connect_step(["Loopback", "KATANA3"], 100.0)
    assert p.connects == [("KATANA3", "KATANA3")]


def test_never_connects_to_other_midi_devices(backend) -> None:
    p = Ports(backend)
    backend._auto_connect_step(["SINCO", "Loopback"], 100.0)
    assert p.connects == []


def test_waits_before_trying_again_after_a_failure(backend) -> None:
    p = Ports(backend)
    backend._auto_connect_step(["KATANA3"], 100.0)
    backend._auto_connect_step(["KATANA3"], 101.0)
    assert len(p.connects) == 1
    backend._auto_connect_step(["KATANA3"], 100.0 + app_mod.AUTO_CONNECT_RETRY_S + 1)
    assert len(p.connects) == 2


def test_does_nothing_when_off_or_busy_or_already_trying(backend) -> None:
    p = Ports(backend)
    backend.set_auto_connect(False)
    backend._auto_connect_step(["KATANA3"], 100.0)
    backend.set_auto_connect(True)
    backend._connecting = True
    backend._auto_connect_step(["KATANA3"], 100.0)
    backend._connecting = False
    backend.busy = True
    backend._auto_connect_step(["KATANA3"], 100.0)
    assert p.connects == []


def test_a_controller_using_the_card_is_not_taken_for_the_amp(backend) -> None:
    p = Ports(backend)
    backend.midi = types.SimpleNamespace(enabled_cards=lambda: {"KATANA3"})
    backend._auto_connect_step(["KATANA3"], 100.0)
    assert p.connects == []


def test_prefers_the_card_it_was_connected_to(backend) -> None:
    p = Ports(backend)
    backend.amp_port = "KATANA-2"
    backend._auto_connect_step(["KATANA3", "KATANA-2"], 100.0)
    assert p.connects == [("KATANA-2", "KATANA-2")]


def test_unplugging_the_amp_drops_the_link_and_it_reconnects(backend, monkeypatch) -> None:
    stopped = []
    backend.transport = types.SimpleNamespace(stop=lambda: stopped.append(1), on_unsolicited=1)
    backend.connected = True
    backend.amp_port = "KATANA3"
    backend.amp_patch = 3
    backend._auto_connect_step(["Loopback"], 100.0)    # the card vanished
    assert not backend.connected and backend.transport is None and stopped == [1]
    assert backend.amp_patch is None and backend.last_status == "Amp disconnected."
    p = Ports(backend)
    backend._auto_connect_step(["Loopback", "KATANA3"], 101.0)   # plugged back in
    assert p.connects == [("KATANA3", "KATANA3")]


def test_a_connected_amp_that_is_still_there_is_left_alone(backend) -> None:
    backend.connected = True
    backend.transport = object()
    backend.amp_port = "KATANA3"
    p = Ports(backend)
    backend._auto_connect_step(["KATANA3"], 100.0)
    assert backend.connected and p.connects == []


def test_user_disconnect_turns_auto_connect_off(backend) -> None:
    backend.transport = types.SimpleNamespace(stop=lambda: None, on_unsolicited=None)
    backend.connected = True
    backend.disconnect()
    assert backend.auto_connect is False


def test_the_connect_flag_is_cleared_after_a_failed_attempt(backend, monkeypatch) -> None:
    def boom(i, o):
        raise OSError("no such card")
    monkeypatch.setattr("katatone.protocol.transport.make_alsa_transport", boom)
    backend.connect("KATANA3", "KATANA3")
    deadline = time.monotonic() + 3
    while backend._connecting and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not backend._connecting and "Connect failed" in backend.last_status


def test_each_session_gets_a_launch_token_that_dies_with_it(backend) -> None:
    first, second = Page(), Page()
    backend.attach(first)
    t1 = backend.launch_token
    assert t1 and backend.launch_tokens.valid(t1)
    backend.attach(second)                             # takes over: the old token is revoked
    t2 = backend.launch_token
    assert t2 != t1 and not backend.launch_tokens.valid(t1) and backend.launch_tokens.valid(t2)
    backend.detach(second)
    assert backend.launch_token is None and not backend.launch_tokens.valid(t2)


def test_the_tool_buttons_carry_the_token(backend) -> None:
    page = Page()
    page.web = True
    backend.attach(page)
    urls = []

    def walk(c):
        if type(c).__name__ == "FilledButton" and c.url:
            urls.append(c.url.url)
        for attr in ("content", "controls"):
            sub = getattr(c, attr, None)
            for x in (sub if isinstance(sub, list) else [sub] if sub is not None else []):
                walk(x)
    for c in page.added:
        walk(c)
    token = backend.launch_token
    assert f"/analyzer?token={token}" in urls and f"/tuner?token={token}" in urls


def test_updates_from_other_threads_run_on_the_pages_loop(monkeypatch) -> None:
    import asyncio
    import threading

    import flet as ft
    ran = []

    def fake_update(self, *a):
        ran.append(threading.get_ident())
    monkeypatch.setattr(bc.BaseControl, "update", fake_update)
    monkeypatch.setattr(headless, "_installed", False)
    headless.install_safe_update()
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    headless.set_loop(loop)
    try:
        ft.Text("x").update()                               # from this (other) thread
        deadline = time.monotonic() + 2
        while not ran and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ran == [t.ident]                             # it ran on the loop's thread
        ran.clear()

        async def on_loop():
            ft.Text("y").update()
        asyncio.run_coroutine_threadsafe(on_loop(), loop).result(2)
        assert ran == [t.ident]                             # on the loop: called directly
    finally:
        headless.set_loop(None)
        loop.call_soon_threadsafe(loop.stop)
        t.join(2)
