"""Wi-Fi with an access-point fallback, through NetworkManager (``nmcli``).

Armbian configures networking with NetworkManager, so the whole feature is two
NetworkManager connection profiles:

* ``katatone-client`` joins the configured network (WPA-PSK), autoconnect
  priority 100;
* ``katatone-ap`` is the device's own access point (WPA2-PSK, addresses handed
  out by NetworkManager's ``shared`` mode), priority 0.

NetworkManager only considers a client profile when its network is in range,
so at boot it picks the client profile when it can and falls back to the
access point when it cannot.  :meth:`Wifi.ensure` does the same on demand
(e.g. after the settings changed).  Everything runs ``nmcli``, so the process
needs the right to manage connections (root, or a member of ``netdev``).
"""
from __future__ import annotations

import contextlib
import ipaddress
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

from .. import rofs

CLIENT = "katatone-client"
AP = "katatone-ap"
#: Where NetworkManager keeps the profiles, and the host name (for ``--reduce-writes``).
PROFILE_DIR = "/etc/NetworkManager/system-connections"
HOSTNAME_FILE = "/etc/hostname"
#: systemd units of the mDNS responder that makes ``<hostname>.local`` resolve,
#: and where enabling them writes (symlinks).
MDNS_UNITS = ["avahi-daemon.service", "avahi-daemon.socket"]
UNIT_DIR = "/etc/systemd/system"
DEFAULT_AP_SSID = "KataTone"
DEFAULT_AP_PSK = "KataTone"

#: A command runner: ``run(args, timeout) -> (returncode, stdout, stderr)``.
Runner = Callable[[list, float], "tuple[int, str, str]"]


class WifiError(Exception):
    """The Wi-Fi settings are invalid or NetworkManager refused them."""


def run_command(args: list, timeout: float = 30) -> "tuple[int, str, str]":
    """Run a command and capture its output (the default :data:`Runner`).

    Args:
        args: the command and its arguments.
        timeout: seconds before giving up.
    Returns:
        ``(returncode, stdout, stderr)``; returncode is 124 on timeout.
    Raises:
        WifiError: if the program is not installed.
    """
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           env={"LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
    except FileNotFoundError:
        raise WifiError(f"{args[0]} is not installed.") from None
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    return p.returncode, p.stdout, p.stderr


def validate_ssid(ssid: str, what: str = "SSID") -> str:
    """Return ``ssid`` if it is a legal Wi-Fi name (1 to 32 bytes)."""
    if not ssid or len(ssid.encode()) > 32:
        raise WifiError(f"The {what} must be 1 to 32 characters.")
    return ssid


def validate_psk(psk: str, what: str = "key") -> str:
    """Return ``psk`` if it is a legal WPA-PSK: 8 to 63 printable ASCII
    characters, or 64 hex digits."""
    printable = all(32 <= ord(c) < 127 for c in psk)
    is_hex = len(psk) == 64 and all(c in "0123456789abcdefABCDEF" for c in psk)
    if not printable or not (8 <= len(psk) <= 63 or is_hex):
        raise WifiError(f"The {what} must be 8 to 63 characters (letters, digits, symbols).")
    return psk


@dataclass
class IpConfig:
    """How the device gets its address on the network it joins.

    ``dhcp`` asks the router; otherwise ``address`` / ``mask`` (a prefix
    length like ``24`` or a dotted netmask), ``gateway`` and ``dns`` (IPv4
    addresses separated by commas or spaces) are used.
    """
    dhcp: bool = True
    address: str = ""
    mask: str = ""
    gateway: str = ""
    dns: str = ""


def _ipv4(text: str, what: str) -> ipaddress.IPv4Address:
    """Parse an IPv4 address."""
    try:
        return ipaddress.IPv4Address(text.strip())
    except ValueError:
        raise WifiError(f"{what} is not a valid IPv4 address.") from None


def _prefix(mask: str) -> int:
    """The prefix length of a mask given as ``24`` or ``255.255.255.0``."""
    mask = mask.strip()
    try:
        if "." in mask:
            bits = format(int(ipaddress.IPv4Address(mask)), "032b")
            if not re.fullmatch(r"1+0*", bits):
                raise ValueError
            n = bits.count("1")
        else:
            n = int(mask)
        if not 1 <= n <= 32:
            raise ValueError
    except ValueError:
        raise WifiError("The subnet mask must be like 255.255.255.0 or 24.") from None
    return n


def _dns_list(text: str) -> list[str]:
    """Split a list of DNS servers and check each (at most three)."""
    items = [t for t in re.split(r"[,\s]+", text.strip()) if t]
    if len(items) > 3:
        raise WifiError("Give at most three DNS servers.")
    return [str(_ipv4(t, f"DNS server {t!r}")) for t in items]


def ip_props(ip: IpConfig) -> list[str]:
    """Validate ``ip`` and return the NetworkManager properties that set it.

    Raises:
        WifiError: if a value is invalid.
    """
    if ip.dhcp:
        return ["ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", ""]
    addr = _ipv4(ip.address, "The IP address")
    prefix = _prefix(ip.mask)
    net = ipaddress.IPv4Network(f"{addr}/{prefix}", strict=False)
    if prefix < 31 and addr in (net.network_address, net.broadcast_address):
        raise WifiError("The IP address cannot be the network or broadcast address.")
    gw = ""
    if ip.gateway.strip():
        g = _ipv4(ip.gateway, "The gateway")
        if g not in net or g == addr:
            raise WifiError("The gateway must be another address on the same subnet.")
        gw = str(g)
    return ["ipv4.method", "manual", "ipv4.addresses", f"{addr}/{prefix}",
            "ipv4.gateway", gw, "ipv4.dns", ",".join(_dns_list(ip.dns))]


def validate_hostname(name: str) -> str:
    """Return ``name`` if it is a valid host name (letters, digits, hyphens;
    1 to 63 characters; not starting or ending with a hyphen)."""
    if not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?", name):
        raise WifiError("The host name may use letters, digits and hyphens (1 to 63), "
                        "and cannot start or end with a hyphen.")
    return name


@dataclass
class Settings:
    """What is configured now (keys are never read back)."""
    ssid: str = ""
    ap_ssid: str = DEFAULT_AP_SSID
    has_key: bool = False
    has_ap_key: bool = False
    ap_enabled: bool = True
    ip: IpConfig = None             # of the network to join (set by settings())
    hostname: str = ""
    mdns: bool = False              # avahi-daemon enabled: <hostname>.local resolves
    mdns_installed: bool = False

    def __post_init__(self) -> None:
        if self.ip is None:
            self.ip = IpConfig()


@dataclass
class Status:
    """What the Wi-Fi interface is doing."""
    mode: str = "down"          # "client", "ap", "other" (not ours) or "down"
    ssid: str = ""
    address: str = ""


def _split(line: str) -> list[str]:
    """Split a ``nmcli -t`` line on its unescaped colons."""
    out, cur, esc = [], "", False
    for c in line:
        if esc:
            cur += c
            esc = False
        elif c == "\\":
            esc = True
        elif c == ":":
            out.append(cur)
            cur = ""
        else:
            cur += c
    return out + [cur]


class Wifi:
    """Reads and changes the Wi-Fi configuration."""

    def __init__(self, run: Runner = run_command) -> None:
        """
        Args:
            run: runs a command (tests replace it).
        """
        self._run = run

    @staticmethod
    def available() -> bool:
        """True when ``nmcli`` is installed."""
        return shutil.which("nmcli") is not None

    @staticmethod
    def dhcp_server_available() -> bool:
        """True when ``dnsmasq`` is installed: NetworkManager's ``shared``
        mode runs it to hand out addresses (and DNS) on the access point."""
        path = "/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/sbin:/usr/local/bin"
        return shutil.which("dnsmasq", path=path) is not None

    def _nmcli(self, *args: str, timeout: float = 30, check: bool = True) -> str:
        """Run ``nmcli`` and return its output.

        Raises:
            WifiError: on a non-zero exit, unless ``check`` is false.
        """
        rc, out, err = self._run(["nmcli", *args], timeout)
        if rc != 0 and check:
            raise WifiError(err.strip().removeprefix("Error: ") or f"nmcli failed ({rc})")
        return out

    def _profiles(self) -> set[str]:
        """Names of the connection profiles that exist."""
        return {_split(l)[0] for l in self._nmcli("-t", "-f", "NAME", "connection", "show").splitlines()}

    def _field(self, profile: str, field: str) -> str:
        """One property of a profile ('' when it has none)."""
        return self._nmcli("-g", field, "connection", "show", profile).strip()

    def settings(self) -> Settings:
        """The configured networks."""
        have = self._profiles()
        s = Settings()
        if CLIENT in have:
            s.ssid = self._field(CLIENT, "802-11-wireless.ssid")
            s.has_key = bool(self._field(CLIENT, "802-11-wireless-security.key-mgmt"))
            if self._field(CLIENT, "ipv4.method") == "manual":
                addr, _, prefix = (re.split(r"[,\s]+", self._field(CLIENT, "ipv4.addresses"))[0]
                                   .partition("/"))
                s.ip = IpConfig(False, addr, prefix, self._field(CLIENT, "ipv4.gateway"),
                                " ".join(re.split(r"[,\s]+", self._field(CLIENT, "ipv4.dns"))).strip())
        if AP in have:
            s.ap_ssid = self._field(AP, "802-11-wireless.ssid") or DEFAULT_AP_SSID
            s.has_ap_key = bool(self._field(AP, "802-11-wireless-security.key-mgmt"))
            s.ap_enabled = self._field(AP, "connection.autoconnect") != "no"
        s.hostname = self._nmcli("general", "hostname", check=False).strip()
        s.mdns_installed = self.mdns_available()
        s.mdns = s.mdns_installed and self._mdns_enabled()
        return s

    @staticmethod
    def mdns_available() -> bool:
        """True when ``avahi-daemon`` (the mDNS responder) is installed."""
        path = "/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/sbin:/usr/local/bin"
        return shutil.which("avahi-daemon", path=path) is not None

    def _systemctl(self, *args: str, check: bool = True) -> str:
        """Run ``systemctl`` (through ``sudo -n`` unless root) and return its output.

        Raises:
            WifiError: on a non-zero exit, unless ``check`` is false.
        """
        cmd = ["systemctl", *args]
        if os.geteuid() != 0:
            cmd = ["sudo", "-n", *cmd]
        rc, out, err = self._run(cmd, 40)
        if rc != 0 and check:
            raise WifiError(err.strip() or f"systemctl failed ({rc})")
        return out

    def _mdns_enabled(self) -> bool:
        """Whether the avahi service starts at boot."""
        return self._run(["systemctl", "is-enabled", MDNS_UNITS[0]], 15)[0] == 0

    def set_mdns(self, on: bool) -> None:
        """Start (and enable at boot) or stop the mDNS responder, so that the
        device answers to ``<hostname>.local`` on the network.

        Args:
            on: whether it should run.
        Raises:
            WifiError: if avahi is not installed or systemd refuses.
        """
        if on and not self.mdns_available():
            raise WifiError("avahi-daemon is not installed (sudo apt install avahi-daemon).")
        if self._mdns_enabled() == on and not on:
            return
        with self._writable(UNIT_DIR):
            self._systemctl("enable" if on else "disable", "--now", *MDNS_UNITS)

    def set_hostname(self, name: str) -> None:
        """Set the device's host name (takes effect at once, no reconnect).

        Raises:
            WifiError: if the name is invalid or NetworkManager refuses.
        """
        if name != self._nmcli("general", "hostname", check=False).strip():
            validate_hostname(name)
            with self._writable(HOSTNAME_FILE):
                self._nmcli("general", "hostname", name)

    def status(self) -> Status:
        """Which profile is active on the Wi-Fi interface, and its address."""
        out = self._nmcli("-t", "-f", "TYPE,NAME,DEVICE", "connection", "show", "--active")
        for line in out.splitlines():
            kind, name, dev = (_split(line) + ["", "", ""])[:3]
            if kind != "802-11-wireless":
                continue
            mode = {CLIENT: "client", AP: "ap"}.get(name, "other")
            addr = self._nmcli("-g", "IP4.ADDRESS", "device", "show", dev, check=False)
            ssid = self._field(name, "802-11-wireless.ssid")
            return Status(mode, ssid, addr.splitlines()[0].split("/")[0] if addr.strip() else "")
        return Status()

    def configure(self, ssid: str, psk: str, ap_ssid: str, ap_psk: str,
                  ap_enabled: bool | None = None, ip: IpConfig | None = None) -> None:
        """Store the settings in the two profiles (nothing is connected yet).

        A blank key keeps the one already stored; a blank ``ssid`` removes the
        client profile (the device then only offers its access point); a blank
        AP name or key means the defaults the first time.

        Args:
            ssid: the network to join, or ''.
            psk: its WPA-PSK key, or '' to keep the stored one.
            ap_ssid: the name of the device's own network.
            ap_psk: its key, or '' to keep the stored one.
            ap_enabled: whether the access point may be used as the fallback
                (False needs a network to join, or the device could not be
                reached at all); None keeps the stored choice.
            ip: how the device gets its address on the network to join; None
                keeps the stored choice (DHCP for a new network).  The access
                point always uses its own fixed address.
        Raises:
            WifiError: if a value is invalid or NetworkManager refuses it.
        """
        if not self.available():
            raise WifiError("NetworkManager (nmcli) is not installed.")
        ap_ssid = validate_ssid(ap_ssid or DEFAULT_AP_SSID, "access point name")
        if ap_psk:
            validate_psk(ap_psk, "access point key")
        if ssid:
            validate_ssid(ssid)
        if psk:
            validate_psk(psk)
        have = self._profiles()
        if ssid and not psk and CLIENT not in have:
            raise WifiError("Enter the key of the network.")
        if not ap_psk and AP not in have:
            ap_psk = DEFAULT_AP_PSK
        if ap_enabled is None:
            ap_enabled = self.settings().ap_enabled if AP in have else True
        if ap_enabled and not self.dhcp_server_available():
            raise WifiError("The access point needs dnsmasq to give out addresses: "
                            "install it (sudo apt install dnsmasq-base) or turn the "
                            "access point off.")
        if not ap_enabled and not ssid:
            raise WifiError("Turn the access point off only with a network to join.")
        ip_set = ip_props(ip) if ip is not None and ssid else []

        with self._writable(PROFILE_DIR):
            self._store(ssid, psk, ap_ssid, ap_psk, ap_enabled, ip_set, have)

    def _store(self, ssid, psk, ap_ssid, ap_psk, ap_enabled, ip_set, have) -> None:
        """Write the two profiles (the part of :meth:`configure` that touches disk)."""
        if ssid:
            self._upsert(CLIENT, CLIENT in have, [
                "802-11-wireless.ssid", ssid, "802-11-wireless-security.key-mgmt", "wpa-psk",
                *(["802-11-wireless-security.psk", psk] if psk else []),
                *ip_set,
                "connection.autoconnect", "yes", "connection.autoconnect-priority", "100"])
        elif CLIENT in have:
            self._nmcli("connection", "delete", CLIENT)
        self._upsert(AP, AP in have, [
            "802-11-wireless.ssid", ap_ssid, "802-11-wireless.mode", "ap",
            "802-11-wireless.band", "bg", "ipv4.method", "shared", "ipv6.method", "ignore",
            "802-11-wireless-security.key-mgmt", "wpa-psk", "802-11-wireless-security.proto", "rsn",
            "802-11-wireless-security.pairwise", "ccmp", "802-11-wireless-security.group", "ccmp",
            *(["802-11-wireless-security.psk", ap_psk] if ap_psk else []),
            "connection.autoconnect", "yes" if ap_enabled else "no",
            "connection.autoconnect-priority", "0"])

    @staticmethod
    def _writable(*paths):
        """Context in which ``paths`` can be written (``--reduce-writes``);
        a failed remount is reported as a :class:`WifiError`."""
        @contextlib.contextmanager
        def ctx():
            try:
                with rofs.writable(*paths):
                    yield
            except rofs.RemountError as e:
                raise WifiError(str(e)) from None
        return ctx()

    def _upsert(self, name: str, exists: bool, props: list[str]) -> None:
        """Create the profile ``name`` or update it with ``props``."""
        if exists:
            self._nmcli("connection", "modify", name, *props)
        else:       # nothing to clear yet: leave out the empty values
            pairs = [(k, v) for k, v in zip(props[::2], props[1::2]) if v != ""]
            self._nmcli("connection", "add", "type", "wifi", "ifname", "*",
                        "con-name", name, *[x for kv in pairs for x in kv])

    def ensure(self, force: bool = False) -> str:
        """Get the interface onto the network, or else onto the access point.

        Args:
            force: reconnect even if one of our profiles is already active
                (after the settings changed).
        Returns:
            What is connected now: ``"client"``, ``"ap"``, or ``"other"`` (a
            connection that is not ours, left alone unless ``force``).
        Raises:
            WifiError: if neither could be brought up.
        """
        if not force:
            mode = self.status().mode
            if mode != "down":
                return mode
        have = self._profiles()
        if CLIENT in have:
            self._nmcli("device", "wifi", "rescan", check=False, timeout=15)
            rc, _, _ = self._run(["nmcli", "-w", "25", "connection", "up", CLIENT], 40)
            if rc == 0:
                return "client"
        if AP not in have or not self.settings().ap_enabled:
            raise WifiError("The network was not found and the access point is off.")
        self._nmcli("-w", "25", "connection", "up", AP, timeout=40)
        return "ap"

    def try_client(self) -> str:
        """Try to join the network now (e.g. while the access point is up).

        If it is not found, the access point comes back (when enabled).

        Returns:
            ``"client"`` if joined, ``"ap"`` if the access point is up again.
        Raises:
            WifiError: if no network is configured, or neither could be brought up.
        """
        if CLIENT not in self._profiles():
            raise WifiError("No network is configured.")
        return self.ensure(force=True)
