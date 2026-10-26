"""Web mode: option parsing and handing the amp over between sessions."""
from __future__ import annotations

import threading
import types

import pytest

from katatone.auth import DEFAULT_PASSWORD, LaunchTokens
from katatone.gui import app as gui_app


def test_flags_default_to_a_desktop_window() -> None:
    o = gui_app._parse_args([])
    assert (o.web, o.host, o.port, o.verbose) == (False, "127.0.0.1", 8550, False)


def test_wifi_is_off_unless_asked_for() -> None:
    assert gui_app._parse_args(["--web"]).wifi is False
    assert gui_app._parse_args(["--web", "--wifi"]).wifi is True


def test_web_banner_warns_about_default_password_without_printing_it() -> None:
    banner = gui_app.web_banner("127.0.0.1", 8550, default_password=True)
    assert "factory password is still set" in banner
    assert "change it at the first login" in banner
    assert DEFAULT_PASSWORD not in banner


class _FakeApp:
    def __init__(self, page=None, **kw):
        self.page, self.kw, self.auto = page, kw, False

    def start_auto_connect(self):
        self.auto = True


class _FakeBackend:
    """Records what the sessions do to the persistent app."""

    def __init__(self):
        self.events = []
        self.amp_port = None
        self.launch_tokens = LaunchTokens()

    def attach(self, page):
        self.events.append(("attach", page))

    def detach(self, page=None):
        self.events.append(("detach", page))


class _FakeLogin:
    """Stands in for the login page; the test decides when the user gets in."""
    pages = []

    def __init__(self, page, auth, on_success):
        self.page, self.auth, self.on_success = page, auth, on_success
        _FakeLogin.pages.append(self)


@pytest.fixture
def web(monkeypatch):
    monkeypatch.setattr(gui_app.login, "LoginPage", _FakeLogin)
    _FakeLogin.pages = []
    return _FakeBackend()


def test_a_browser_session_gets_the_app_only_after_login(web) -> None:
    page = types.SimpleNamespace()
    gui_app._open_web_session(page, web, auth="store")
    assert web.events == [] and _FakeLogin.pages[0].auth == "store"
    _FakeLogin.pages[0].on_success()
    assert web.events == [("attach", page)]


def test_closing_the_tab_detaches_but_keeps_the_backend(web) -> None:
    page = types.SimpleNamespace()
    gui_app._open_web_session(page, web)
    _FakeLogin.pages[0].on_success()
    page.on_disconnect(None)
    assert web.events[-1] == ("detach", page)
    page.on_close(None)                          # an expired session too
    assert web.events[-1] == ("detach", page)


def test_a_refreshed_page_gets_the_app_back_without_logging_in_again(web) -> None:
    page = types.SimpleNamespace()
    gui_app._open_web_session(page, web)
    page.on_connect(None)                        # not signed in: nothing
    assert web.events == []
    _FakeLogin.pages[0].on_success()
    page.on_disconnect(None)
    page.on_connect(None)
    assert [e[0] for e in web.events] == ["attach", "detach", "attach"]


class _FakeTool:
    made = []

    def __init__(self, page, card):
        self.page, self.card, self.closed = page, card, 0
        _FakeTool.made.append(self)

    def close(self):
        self.closed += 1


@pytest.fixture
def tools(monkeypatch):
    _FakeTool.made = []
    monkeypatch.setattr(gui_app, "_tool_class",
                        lambda page: _FakeTool if page.route.startswith("/analyzer") else None)


class _Page(types.SimpleNamespace):
    def clean(self):
        pass


def test_the_analyzer_address_serves_the_tool_after_login_not_the_app(web, tools) -> None:
    web.amp_port = "KATANA3"
    page = _Page(route="/analyzer")
    gui_app._open_web_session(page, web)
    assert _FakeTool.made == []                          # login first
    _FakeLogin.pages[0].on_success()
    assert web.events == [] and _FakeTool.made[0].card == "KATANA3"
    page.on_disconnect(None)                             # closing the tab stops listening
    assert _FakeTool.made[0].closed == 1 and web.events == []


def test_a_refreshed_tool_page_starts_a_fresh_tool(web, tools) -> None:
    page = _Page(route="/analyzer")
    gui_app._open_web_session(page, web)
    page.on_connect(None)                                # not signed in yet: nothing
    assert _FakeTool.made == []
    _FakeLogin.pages[0].on_success()
    page.on_disconnect(None)
    page.on_connect(None)
    assert len(_FakeTool.made) == 2 and _FakeTool.made[0].closed == 1


def test_other_addresses_still_serve_the_app(web, tools) -> None:
    page = _Page(route="/")
    gui_app._open_web_session(page, web)
    _FakeLogin.pages[0].on_success()
    assert web.events == [("attach", page)] and _FakeTool.made == []


def test_a_valid_token_opens_the_tool_without_a_login(web, tools) -> None:
    token = web.launch_tokens.issue()
    page = _Page(route=f"/analyzer?token={token}")
    gui_app._open_web_session(page, web)
    assert _FakeLogin.pages == [] and len(_FakeTool.made) == 1      # no login form
    page.on_disconnect(None)
    assert web.launch_tokens.valid(token)                           # reusable: more tabs


def test_a_wrong_missing_or_revoked_token_still_asks_for_the_password(web, tools) -> None:
    token = web.launch_tokens.issue()
    web.launch_tokens.revoke(token)
    for route in ("/analyzer?token=nope", "/analyzer", f"/analyzer?token={token}"):
        gui_app._open_web_session(_Page(route=route), web)
    assert len(_FakeLogin.pages) == 3 and _FakeTool.made == []


def test_a_token_never_opens_the_app(web, tools) -> None:
    token = web.launch_tokens.issue()
    gui_app._open_web_session(_Page(route=f"/?token={token}"), web)
    assert len(_FakeLogin.pages) == 1 and web.events == []


def test_tool_routes_name_the_right_classes() -> None:
    from katatone.gui.analyzer import Analyzer
    from katatone.gui.tuner import TunerWindow
    assert gui_app._tool_class(types.SimpleNamespace(route="/analyzer")) is Analyzer
    assert gui_app._tool_class(types.SimpleNamespace(route="/Tuner/")) is TunerWindow
    assert gui_app._tool_class(types.SimpleNamespace(route="/tuner?token=abc")) is TunerWindow
    assert gui_app._tool_class(types.SimpleNamespace(route="/")) is None
    assert gui_app._tool_class(types.SimpleNamespace(route="/other")) is None
    assert gui_app._tool_class(types.SimpleNamespace()) is None


def test_a_desktop_window_owns_its_app(monkeypatch) -> None:
    monkeypatch.setattr(gui_app, "App", _FakeApp)
    apps = []
    opts = gui_app._parse_args(["--wifi"])
    gui_app._open_window(types.SimpleNamespace(), apps, opts)
    assert apps[0].kw == {"wifi": True} and apps[0].auto


def test_auto_connect_can_be_turned_off(monkeypatch) -> None:
    monkeypatch.setattr(gui_app, "App", _FakeApp)
    apps = []
    gui_app._open_window(types.SimpleNamespace(), apps,
                         gui_app._parse_args(["--no-auto-connect"]))
    assert not apps[0].auto


def test_web_needs_flet_web_in_the_same_version(monkeypatch) -> None:
    fv = pytest.importorskip("flet_web.version")
    gui_app._require_web_support()                      # installed here
    monkeypatch.setattr(fv, "version", "0.0.1")
    with pytest.raises(SystemExit, match="katatone\\[web\\]"):
        gui_app._require_web_support()


def test_release_closes_the_amp_link_and_everything_else() -> None:
    app = object.__new__(gui_app.App)
    calls = []
    app.midi = None
    app.an_proc = None
    app._auto_stop = threading.Event()
    app._auto_thread = None
    app.live = types.SimpleNamespace(clear=lambda: calls.append("live"))
    app._cancel_resync = lambda: calls.append("resync")
    app.connected = True
    app.transport = types.SimpleNamespace(stop=lambda: calls.append("stop"))
    app.release()
    app.release()                                 # idempotent
    assert app.transport is None and not app.connected
    assert calls.count("stop") == 1
