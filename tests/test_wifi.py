"""Wi-Fi configuration through a fake nmcli."""
from __future__ import annotations

import pytest

from katatone.net import wifi
from katatone.net.wifi import AP, CLIENT, IpConfig, Wifi, WifiError


class FakeNm:
    """Records nmcli calls; ``profiles`` maps name -> {property: value}."""

    def __init__(self, profiles=None, active=None, client_up=True):
        self.profiles = profiles or {}
        self.active = active          # (name, device) or None
        self.client_up = client_up
        self.calls = []
        self.hostname = "armbian"
        self.mdns = False

    def __call__(self, args, timeout=30):
        if args[0] == "sudo":
            args = args[2:]
        if args[0] == "systemctl":
            self.calls.append(args)
            if args[1] == "is-enabled":
                return (0, "enabled\n", "") if self.mdns else (1, "disabled\n", "")
            if args[1] in ("enable", "disable"):
                self.mdns = args[1] == "enable"
                return 0, "", ""
        assert args[0] == "nmcli"
        a = args[1:]
        self.calls.append(a)
        if a[:1] == ["-w"]:
            a = a[2:]
        if a[:4] == ["-t", "-f", "NAME", "connection"]:
            return 0, "".join(f"{n}\n" for n in self.profiles) + "Wired connection 1\n", ""
        if a[:2] == ["general", "hostname"]:
            if len(a) == 3:
                self.hostname = a[2]
            return 0, self.hostname + "\n", ""
        if a[:2] == ["-g", "IP4.ADDRESS"]:
            return 0, "10.42.0.1/24\n", ""
        if a[0] == "-g":
            return 0, self.profiles.get(a[-1], {}).get(a[1], "") + "\n", ""
        if a[:2] == ["-t", "-f"] and "--active" in a:
            if self.active is None:
                return 0, "802-3-ethernet:Wired connection 1:eth0\n", ""
            return 0, f"802-11-wireless:{self.active[0]}:{self.active[1]}\n", ""
        if a[:2] == ["connection", "add"]:
            name = a[a.index("con-name") + 1]
            self.profiles[name] = dict(zip(a[a.index("con-name") + 2::2], a[a.index("con-name") + 3::2]))
            return 0, "", ""
        if a[:2] == ["connection", "modify"]:
            self.profiles[a[2]].update(zip(a[3::2], a[4::2]))
            return 0, "", ""
        if a[:2] == ["connection", "delete"]:
            del self.profiles[a[2]]
            return 0, "", ""
        if a[:2] == ["connection", "up"]:
            if a[2] == CLIENT and not self.client_up:
                return 10, "", "Error: No network with SSID 'home' found.\n"
            self.active = (a[2], "wlan0")
            return 0, "", ""
        return 0, "", ""              # rescan etc.


@pytest.fixture
def nm(monkeypatch):
    monkeypatch.setattr(Wifi, "available", staticmethod(lambda: True))
    monkeypatch.setattr(Wifi, "dhcp_server_available", staticmethod(lambda: True))
    monkeypatch.setattr(Wifi, "mdns_available", staticmethod(lambda: True))
    return FakeNm()


def test_first_configuration_creates_both_profiles(nm) -> None:
    Wifi(nm).configure("home", "hunter2hunter2", "", "")
    c, ap = nm.profiles[CLIENT], nm.profiles[AP]
    assert c["802-11-wireless.ssid"] == "home"
    assert c["802-11-wireless-security.key-mgmt"] == "wpa-psk"
    assert c["802-11-wireless-security.psk"] == "hunter2hunter2"
    assert int(c["connection.autoconnect-priority"]) > int(ap["connection.autoconnect-priority"])
    assert ap["802-11-wireless.mode"] == "ap" and ap["ipv4.method"] == "shared"
    assert ap["802-11-wireless.ssid"] == "KataTone"
    assert ap["802-11-wireless-security.psk"] == "KataTone"


def test_blank_keys_keep_the_saved_ones(nm) -> None:
    w = Wifi(nm)
    w.configure("home", "hunter2hunter2", "Amp", "apsecret99")
    w.configure("other", "", "Amp2", "")
    assert nm.profiles[CLIENT]["802-11-wireless.ssid"] == "other"
    assert nm.profiles[CLIENT]["802-11-wireless-security.psk"] == "hunter2hunter2"
    assert nm.profiles[AP]["802-11-wireless.ssid"] == "Amp2"
    assert nm.profiles[AP]["802-11-wireless-security.psk"] == "apsecret99"


def test_blank_network_removes_the_client_profile(nm) -> None:
    w = Wifi(nm)
    w.configure("home", "hunter2hunter2", "", "")
    w.configure("", "", "", "")
    assert CLIENT not in nm.profiles and AP in nm.profiles


@pytest.mark.parametrize("args", [
    ("x" * 33, "hunter2hunter2", "", ""),      # SSID too long
    ("home", "short", "", ""),                 # key too short
    ("home", "hunter2hunter2", "", "short"),   # AP key too short
    ("home", "", "", ""),                      # no key for a new network
    ("home", "héllo-world", "", ""),     # non-ASCII key
])
def test_invalid_settings_change_nothing(nm, args) -> None:
    with pytest.raises(WifiError):
        Wifi(nm).configure(*args)
    assert nm.profiles == {}


def test_hex_psk_is_accepted() -> None:
    assert wifi.validate_psk("a" * 64)


def test_settings_never_expose_keys(nm) -> None:
    w = Wifi(nm)
    assert w.settings().ssid == "" and not w.settings().has_key
    w.configure("home", "hunter2hunter2", "Amp", "")
    s = w.settings()
    assert (s.ssid, s.has_key, s.ap_ssid, s.has_ap_key) == ("home", True, "Amp", True)


def test_ensure_joins_the_network_when_found(nm) -> None:
    w = Wifi(nm)
    w.configure("home", "hunter2hunter2", "", "")
    assert w.ensure(force=True) == "client" and nm.active[0] == CLIENT


def test_ensure_falls_back_to_the_access_point(nm) -> None:
    nm.client_up = False
    w = Wifi(nm)
    w.configure("home", "hunter2hunter2", "", "")
    assert w.ensure(force=True) == "ap" and nm.active[0] == AP


def test_ensure_leaves_an_active_connection_alone_unless_forced(nm) -> None:
    w = Wifi(nm)
    w.configure("home", "hunter2hunter2", "", "")
    nm.active = (AP, "wlan0")
    assert w.ensure() == "ap" and not any("up" in c for c in nm.calls)
    nm.active = ("someone-elses", "wlan0")
    assert w.ensure() == "other"
    assert w.ensure(force=True) == "client"


def test_status_reports_mode_and_address(nm) -> None:
    w = Wifi(nm)
    w.configure("home", "hunter2hunter2", "Amp", "")
    assert w.status().mode == "down"
    nm.active = (AP, "wlan0")
    st = w.status()
    assert (st.mode, st.ssid, st.address) == ("ap", "Amp", "10.42.0.1")


def test_nmcli_errors_become_wifi_errors() -> None:
    def failing(args, timeout=30):
        return 1, "", "Error: Not authorized to control networking.\n"
    with pytest.raises(WifiError, match="Not authorized"):
        Wifi(failing).settings()


def test_split_handles_escaped_colons() -> None:
    assert wifi._split(r"802-11-wireless:my\:ssid:wlan0") == ["802-11-wireless", "my:ssid", "wlan0"]


def test_access_point_can_be_switched_off_and_on(nm) -> None:
    w = Wifi(nm)
    w.configure("home", "hunter2hunter2", "", "")
    assert w.settings().ap_enabled
    w.configure("home", "", "", "", ap_enabled=False)
    assert not w.settings().ap_enabled and nm.profiles[AP]["connection.autoconnect"] == "no"
    w.configure("home", "", "", "")                 # None keeps the choice
    assert not w.settings().ap_enabled
    w.configure("home", "", "", "", ap_enabled=True)
    assert w.settings().ap_enabled


def test_access_point_cannot_be_off_without_a_network(nm) -> None:
    with pytest.raises(WifiError, match="network to join"):
        Wifi(nm).configure("", "", "", "", ap_enabled=False)


def test_no_fallback_when_the_access_point_is_off(nm) -> None:
    nm.client_up = False
    w = Wifi(nm)
    w.configure("home", "hunter2hunter2", "", "", ap_enabled=False)
    with pytest.raises(WifiError, match="access point is off"):
        w.ensure(force=True)
    assert not any("up" in c and AP in c for c in nm.calls)


def test_try_client_leaves_the_access_point_for_the_network(nm) -> None:
    w = Wifi(nm)
    w.configure("home", "hunter2hunter2", "", "")
    nm.active = (AP, "wlan0")
    assert w.try_client() == "client"


def test_try_client_returns_to_the_access_point_if_not_found(nm) -> None:
    w = Wifi(nm)
    w.configure("home", "hunter2hunter2", "", "")
    nm.active, nm.client_up = (AP, "wlan0"), False
    assert w.try_client() == "ap"


def test_try_client_needs_a_configured_network(nm) -> None:
    w = Wifi(nm)
    w.configure("", "", "", "")
    with pytest.raises(WifiError, match="No network"):
        w.try_client()


STATIC = IpConfig(False, "192.168.1.50", "255.255.255.0", "192.168.1.1", "1.1.1.1, 8.8.8.8")


def test_new_network_defaults_to_dhcp(nm) -> None:
    w = Wifi(nm)
    w.configure("home", "hunter2hunter2", "", "")
    assert w.settings().ip.dhcp
    assert "ipv4.method" not in nm.profiles[CLIENT] or nm.profiles[CLIENT]["ipv4.method"] == "auto"


def test_static_address_is_stored_and_read_back(nm) -> None:
    w = Wifi(nm)
    w.configure("home", "hunter2hunter2", "", "", ip=STATIC)
    c = nm.profiles[CLIENT]
    assert c["ipv4.method"] == "manual" and c["ipv4.addresses"] == "192.168.1.50/24"
    assert c["ipv4.gateway"] == "192.168.1.1" and c["ipv4.dns"] == "1.1.1.1,8.8.8.8"
    assert "ipv4.method" not in nm.profiles[AP] or nm.profiles[AP]["ipv4.method"] == "shared"
    ip = w.settings().ip
    assert (ip.dhcp, ip.address, ip.mask, ip.gateway) == (False, "192.168.1.50", "24", "192.168.1.1")
    assert ip.dns == "1.1.1.1 8.8.8.8"


def test_back_to_dhcp_clears_the_static_values(nm) -> None:
    w = Wifi(nm)
    w.configure("home", "hunter2hunter2", "", "", ip=STATIC)
    w.configure("home", "", "", "", ip=IpConfig())
    c = nm.profiles[CLIENT]
    assert c["ipv4.method"] == "auto" and c["ipv4.addresses"] == "" and c["ipv4.dns"] == ""
    assert w.settings().ip.dhcp


def test_ip_none_keeps_the_stored_choice(nm) -> None:
    w = Wifi(nm)
    w.configure("home", "hunter2hunter2", "", "", ip=STATIC)
    w.configure("home", "", "", "")
    assert not w.settings().ip.dhcp


@pytest.mark.parametrize("bad", [
    IpConfig(False, "300.1.1.1", "24"),
    IpConfig(False, "192.168.1.50", ""),
    IpConfig(False, "192.168.1.50", "255.0.255.0"),         # not contiguous
    IpConfig(False, "192.168.1.50", "0.0.0.255"),           # wildcard
    IpConfig(False, "192.168.1.50", "33"),
    IpConfig(False, "192.168.1.0", "24"),                   # network address
    IpConfig(False, "192.168.1.255", "24"),                 # broadcast
    IpConfig(False, "192.168.1.50", "24", "10.0.0.1"),      # gateway off-subnet
    IpConfig(False, "192.168.1.50", "24", "192.168.1.50"),  # gateway is ourselves
    IpConfig(False, "192.168.1.50", "24", "", "nope"),
    IpConfig(False, "192.168.1.50", "24", "", "1.1.1.1 2.2.2.2 3.3.3.3 4.4.4.4"),
])
def test_invalid_static_settings_change_nothing(nm, bad) -> None:
    with pytest.raises(WifiError):
        Wifi(nm).configure("home", "hunter2hunter2", "", "", ip=bad)
    assert nm.profiles == {}


def test_prefix_and_gateway_are_optional_forms(nm) -> None:
    Wifi(nm).configure("home", "hunter2hunter2", "", "",
                       ip=IpConfig(False, "10.0.0.5", "24"))
    assert nm.profiles[CLIENT]["ipv4.addresses"] == "10.0.0.5/24"
    assert "ipv4.gateway" not in nm.profiles[CLIENT]        # empty, left out on add


def test_hostname_roundtrip_and_validation(nm) -> None:
    w = Wifi(nm)
    assert w.settings().hostname == "armbian"
    w.set_hostname("katatone")
    assert w.settings().hostname == "katatone"
    for bad in ("", "-x", "x-", "has space", "a" * 64, "under_score"):
        with pytest.raises(WifiError):
            w.set_hostname(bad)
    assert nm.hostname == "katatone"


def test_access_point_needs_a_dhcp_server(nm, monkeypatch) -> None:
    monkeypatch.setattr(Wifi, "dhcp_server_available", staticmethod(lambda: False))
    w = Wifi(nm)
    with pytest.raises(WifiError, match="dnsmasq"):
        w.configure("home", "hunter2hunter2", "", "")
    assert nm.profiles == {}
    # with the access point off nothing needs it
    w.configure("home", "hunter2hunter2", "", "", ap_enabled=False)
    assert nm.profiles[AP]["connection.autoconnect"] == "no"


def test_mdns_is_reported_and_switched(nm) -> None:
    w = Wifi(nm)
    assert w.settings().mdns_installed and not w.settings().mdns
    w.set_mdns(True)
    assert w.settings().mdns
    w.set_mdns(False)
    assert not w.settings().mdns


def test_mdns_needs_avahi_installed(nm, monkeypatch) -> None:
    monkeypatch.setattr(Wifi, "mdns_available", staticmethod(lambda: False))
    w = Wifi(nm)
    assert not w.settings().mdns_installed and not w.settings().mdns
    with pytest.raises(WifiError, match="avahi-daemon is not installed"):
        w.set_mdns(True)


def test_non_root_goes_through_sudo(nm, monkeypatch) -> None:
    import os
    seen = []
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    w = Wifi(lambda a, t=30: (seen.append(a), nm(a, t))[1])
    w.set_mdns(True)
    assert any(a[:3] == ["sudo", "-n", "systemctl"] and "enable" in a for a in seen)
