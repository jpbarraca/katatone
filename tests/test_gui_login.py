"""The login page and the Wi-Fi dialog, headless."""
from __future__ import annotations

import types

import flet.controls.base_control as bc
import pytest

from katatone.auth import DEFAULT_PASSWORD, MAX_FAILURES, Auth
from katatone.gui import wifi_dialog
from katatone.gui.login import LoginPage
from katatone.net.wifi import IpConfig, Settings, Status, WifiError


@pytest.fixture(autouse=True)
def _no_update(monkeypatch):
    monkeypatch.setattr(bc.BaseControl, "update", lambda self: None)


class Page:
    def __init__(self):
        self.controls, self.dialogs, self.threads = [], [], []

    def clean(self): self.controls.clear()
    def add(self, *c): self.controls.extend(c)
    def update(self, *a, **k): pass
    def show_dialog(self, d): self.dialogs.append(d)
    def pop_dialog(self): self.dialogs.pop()
    def run_thread(self, fn, *a): fn(*a)             # synchronous


def make(tmp_path, password=None):
    auth = Auth(tmp_path / "auth.json")
    if password:
        auth.set_password(password)
    entered = []
    page = Page()
    return LoginPage(page, auth, lambda: entered.append(True)), page, entered


def test_the_page_holds_only_the_form_until_login(tmp_path) -> None:
    lp, page, entered = make(tmp_path, "correct horse")
    assert len(page.controls) == 1 and not entered


def test_wrong_password_stays_on_the_form(tmp_path) -> None:
    lp, page, entered = make(tmp_path, "correct horse")
    lp.password.value = "nope"
    lp.submit()
    assert not entered and lp.error.value == "Wrong password." and lp.password.value == ""


def test_right_password_enters(tmp_path) -> None:
    lp, page, entered = make(tmp_path, "correct horse")
    lp.password.value = "correct horse"
    lp.submit()
    assert entered == [True] and page.controls == []


def test_factory_password_must_be_replaced_before_entering(tmp_path) -> None:
    lp, page, entered = make(tmp_path)
    lp.password.value = DEFAULT_PASSWORD
    lp.submit()
    assert not entered and hasattr(lp, "new1")
    lp.new1.value, lp.new2.value = "correct horse", "different"
    lp.submit_new()
    assert not entered and lp.error.value == "The passwords differ."
    lp.new1.value = lp.new2.value = DEFAULT_PASSWORD
    lp.submit_new()
    assert not entered and "default" in lp.error.value
    lp.new1.value = lp.new2.value = "correct horse"
    lp.submit_new()
    assert entered == [True] and lp.auth.check("correct horse")


def test_lockout_is_reported(tmp_path) -> None:
    lp, page, entered = make(tmp_path, "correct horse")
    for _ in range(MAX_FAILURES):
        lp.password.value = "nope"
        lp.submit()
    assert "Try again" in lp.error.value
    lp.password.value = "correct horse"
    lp.submit()
    assert not entered


# ------------------------------------------------------------------ Wi-Fi dialog

class FakeWifi:
    def __init__(self, fail=None, mode="ap"):
        self.fail, self.saved, self.ensured, self.retried = fail, None, None, False
        self.mode, self.hostname_set = mode, None
        self.avahi, self.mdns_set = True, None

    def settings(self):
        return Settings("home", "Amp", True, True, False,
                        IpConfig(False, "192.168.1.50", "24", "192.168.1.1", "1.1.1.1"), "amp",
                        mdns=False, mdns_installed=self.avahi)
    def status(self): return Status(self.mode, "Amp", "10.42.0.1")

    def set_hostname(self, name): self.hostname_set = name
    def set_mdns(self, on): self.mdns_set = on

    def try_client(self): self.retried = True; return "client"

    def configure(self, *a):
        if self.fail:
            raise WifiError(self.fail)
        self.saved = a

    def ensure(self, force=False): self.ensured = force; return "ap"


def wifi_app():
    msgs = []
    return types.SimpleNamespace(page=Page(), set_status=msgs.append, msgs=msgs)


def test_describe_names_the_mode_and_address() -> None:
    assert wifi_dialog.describe(FakeWifi()) == "Now: running its own access point Amp, at 10.42.0.1"


def by_label(dlg) -> dict:
    """Every labelled input of the dialog (looking inside rows), by label."""
    out = {}

    def walk(controls):
        for c in controls:
            if getattr(c, "label", None) and not isinstance(c.label, type(None)):
                out[c.label if isinstance(c.label, str) else c.label.value] = c
            walk(getattr(c, "controls", None) or [])
    walk(dlg.content.controls)
    return out


def test_dialog_applies_the_settings(monkeypatch) -> None:
    monkeypatch.setattr(wifi_dialog, "SWITCH_DELAY_S", 0)
    app, w = wifi_app(), FakeWifi()
    wifi_dialog.open_dialog(app, w)
    dlg = app.page.dialogs[0]
    f = by_label(dlg)
    assert (f["Network (SSID)"].value, f["Access point name"].value) == ("home", "Amp")
    ap_on = f["Start the access point when the network is not found"]
    assert ap_on.value is False
    ap_on.value = True
    f["Network key (WPA-PSK)"].value, f["Access point key"].value = "hunter2hunter2", "apsecret99"
    f["Host name"].value = "katatone"
    dlg.actions[1].on_click(None)
    assert w.saved[:5] == ("home", "hunter2hunter2", "Amp", "apsecret99", True)
    assert w.saved[5] == IpConfig(False, "192.168.1.50", "24", "192.168.1.1", "1.1.1.1")
    assert w.hostname_set == "katatone"
    assert w.ensured is True and app.page.dialogs == []
    assert app.msgs[-1].startswith("Wi-Fi: Now:")


def test_dialog_dhcp_switch_enables_the_address_fields_and_is_sent(monkeypatch) -> None:
    monkeypatch.setattr(wifi_dialog, "SWITCH_DELAY_S", 0)
    app, w = wifi_app(), FakeWifi()
    wifi_dialog.open_dialog(app, w)
    dlg = app.page.dialogs[0]
    f = by_label(dlg)
    names = ("IP address", "Subnet mask", "Gateway", "DNS servers")
    assert not any(f[n].disabled for n in names)         # static in the fake
    dhcp = f["Get the address automatically (DHCP)"]
    dhcp.value = True
    dhcp.on_change(None)
    assert all(f[n].disabled for n in names)
    dlg.actions[1].on_click(None)
    assert w.saved[5].dhcp is True


def test_dialog_bad_hostname_is_reported_before_anything_is_saved(monkeypatch) -> None:
    app, w = wifi_app(), FakeWifi()
    wifi_dialog.open_dialog(app, w)
    dlg = app.page.dialogs[0]
    by_label(dlg)["Host name"].value = "-bad-"
    dlg.actions[1].on_click(None)
    assert w.saved is None and "host name" in dlg.content.controls[-1].value


def test_dialog_shows_the_error_and_stays_open(monkeypatch) -> None:
    monkeypatch.setattr(wifi_dialog, "SWITCH_DELAY_S", 0)
    app, w = wifi_app(), FakeWifi(fail="The key must be 8 to 63 characters")
    wifi_dialog.open_dialog(app, w)
    dlg = app.page.dialogs[0]
    dlg.actions[1].on_click(None)
    assert app.page.dialogs and w.ensured is None
    assert not dlg.actions[1].disabled
    assert "8 to 63" in dlg.content.controls[-1].value


def buttons(dlg):
    return [c for c in dlg.content.controls if type(c).__name__ == "FilledButton"]


def test_connect_now_button_tries_the_network_from_the_access_point(monkeypatch) -> None:
    monkeypatch.setattr(wifi_dialog, "SWITCH_DELAY_S", 0)
    app, w = wifi_app(), FakeWifi(mode="ap")
    wifi_dialog.open_dialog(app, w)
    dlg = app.page.dialogs[0]
    (btn,) = buttons(dlg)
    btn.on_click(None)
    assert w.retried and app.page.dialogs == []


def test_connect_now_button_only_while_the_access_point_is_up() -> None:
    app = wifi_app()
    wifi_dialog.open_dialog(app, FakeWifi(mode="client"))
    assert buttons(app.page.dialogs[0]) == []


def _texts(c) -> list[str]:
    """Every Text value under a control."""
    out = [c.value] if type(c).__name__ == "Text" else []
    for attr in ("content", "controls"):
        sub = getattr(c, attr, None)
        for x in (sub if isinstance(sub, list) else [sub] if sub is not None else []):
            out += _texts(x)
    return out


def test_wifi_button_only_with_the_flag() -> None:
    from katatone.gui import statusbar
    for flag in (True, False):
        app = types.SimpleNamespace(wifi_enabled=flag, page=Page(), amp_port=None, auto_connect=True)
        assert ("Wi-Fi" in _texts(statusbar.build(app))) is flag


def test_dialog_switches_avahi_on_when_ticked(monkeypatch) -> None:
    monkeypatch.setattr(wifi_dialog, "SWITCH_DELAY_S", 0)
    app, w = wifi_app(), FakeWifi()
    wifi_dialog.open_dialog(app, w)
    dlg = app.page.dialogs[0]
    sw = by_label(dlg)["Reachable as <host name>.local (avahi)"]
    assert not sw.disabled
    sw.value = True
    dlg.actions[1].on_click(None)
    assert w.mdns_set is True


def test_dialog_leaves_avahi_alone_if_unchanged_or_missing(monkeypatch) -> None:
    monkeypatch.setattr(wifi_dialog, "SWITCH_DELAY_S", 0)
    app, w = wifi_app(), FakeWifi()
    wifi_dialog.open_dialog(app, w)
    app.page.dialogs[0].actions[1].on_click(None)
    assert w.mdns_set is None
    app, w = wifi_app(), FakeWifi()
    w.avahi = False
    wifi_dialog.open_dialog(app, w)
    sw = by_label(app.page.dialogs[0])["Reachable as <host name>.local (avahi)"]
    assert sw.disabled
