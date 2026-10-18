"""The ``wifi`` command, against a recording stand-in for the Wi-Fi controller."""
from __future__ import annotations

import pytest

from katatone import cli
from katatone.net import wifi
from katatone.net.wifi import IpConfig, Settings


class Rec:
    last = None

    def __init__(self):
        Rec.last = self
        self.calls = []

    def settings(self): return Settings("home", ip=IpConfig(), hostname="amp")
    def configure(self, *a): self.calls.append(("configure", a))
    def ensure(self, force=False): self.calls.append(("ensure", force)); return "client"
    def set_hostname(self, n): self.calls.append(("hostname", n))
    def set_mdns(self, on): self.calls.append(("mdns", on))


@pytest.fixture(autouse=True)
def _rec(monkeypatch):
    monkeypatch.setattr(wifi, "Wifi", Rec)


def run(*argv):
    return cli._run(["wifi", *argv])


def test_hostname_alone_changes_only_the_name() -> None:
    assert run("set", "--hostname", "katatone") == 0
    assert Rec.last.calls == [("hostname", "katatone")]


def test_static_address_keeps_the_saved_network() -> None:
    assert run("set", "--address", "10.0.0.5", "--mask", "24", "--gateway", "10.0.0.1") == 0
    kind, a = Rec.last.calls[0]
    assert a[0] == "home" and a[5] == IpConfig(False, "10.0.0.5", "24", "10.0.0.1", "")
    assert ("ensure", True) in Rec.last.calls


def test_dhcp_flag_and_no_ip_flags() -> None:
    run("set", "--dhcp")
    assert Rec.last.calls[0][1][5] == IpConfig()
    run("set", "--ssid", "other", "--key", "hunter2hunter2")
    assert Rec.last.calls[0][1][0] == "other" and Rec.last.calls[0][1][5] is None


def test_mdns_alone_changes_only_the_responder() -> None:
    run("set", "--mdns")
    assert Rec.last.calls == [("mdns", True)]
    run("set", "--no-mdns", "--hostname", "amp2")
    assert Rec.last.calls == [("hostname", "amp2"), ("mdns", False)]


def test_mdns_rides_along_with_a_full_change() -> None:
    run("set", "--dhcp", "--mdns")
    assert ("mdns", True) in Rec.last.calls and Rec.last.calls[0][0] == "configure"
