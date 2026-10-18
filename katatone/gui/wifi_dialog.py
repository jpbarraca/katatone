"""The Wi-Fi settings dialog (web mode): the network to join, and the access
point the device offers when that network is not found."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

import flet as ft

from ..net.wifi import (DEFAULT_AP_SSID, IpConfig, Wifi, WifiError,
                        validate_hostname)
from . import params_view as pv, theme

if TYPE_CHECKING:
    from .app import App

#: Seconds between saving and switching networks, so the page can still
#: report before the browser's connection to the device drops.
SWITCH_DELAY_S = 2.0

_MODES = {"client": "connected to the network", "ap": "running its own access point",
          "other": "connected through another connection", "down": "not connected"}


def describe(wifi: Wifi) -> str:
    """One line on what the interface is doing now.

    Args:
        wifi: the Wi-Fi controller.
    Returns:
        E.g. ``"Now: running its own access point KataTone, at 10.42.0.1"``.
    """
    st = wifi.status()
    text = f"Now: {_MODES[st.mode]}"
    if st.ssid and st.mode != "down":
        text += f" {st.ssid}"
    if st.address:
        text += f", at {st.address}"
    return text


def open_dialog(app: App, wifi: Wifi | None = None) -> None:
    """Show the dialog, filled with the current settings.

    Saving stores the settings, then reconnects: to the network if it is in
    range, else to the access point.  The browser's own connection to the
    device may drop meanwhile; reconnect to the network that is up and reload.

    Args:
        app: the application.
        wifi: the Wi-Fi controller (default: NetworkManager).
    """
    wifi = wifi or Wifi()
    page = app.page
    ft.context.disable_auto_update()
    try:
        cur, st = wifi.settings(), wifi.status()
        now = describe(wifi)
    except WifiError as e:
        app.set_status(f"Wi-Fi: {e}")
        return
    ssid = ft.TextField(label="Network (SSID)", value=cur.ssid, width=330, **pv.FIELD_STYLE)
    key = ft.TextField(label="Network key (WPA-PSK)", password=True, can_reveal_password=True,
                       width=330, helper="Leave blank to keep the saved key" if cur.has_key else None,
                       **pv.FIELD_STYLE)
    ap_ssid = ft.TextField(label="Access point name", value=cur.ap_ssid or DEFAULT_AP_SSID,
                           width=330, **pv.FIELD_STYLE)
    ap_key = ft.TextField(label="Access point key", password=True, can_reveal_password=True,
                          width=330, helper="Leave blank to keep the saved key" if cur.has_ap_key
                          else "Default: KataTone", **pv.FIELD_STYLE)
    ap_on = ft.Switch(label="Start the access point when the network is not found",
                      value=cur.ap_enabled, label_text_style=ft.TextStyle(size=12),
                      active_color=theme.ACCENT)
    ip = cur.ip
    dhcp = ft.Switch(label="Get the address automatically (DHCP)", value=ip.dhcp,
                     label_text_style=ft.TextStyle(size=12), active_color=theme.ACCENT,
                     on_change=lambda e: set_static_enabled())
    address = ft.TextField(label="IP address", value=ip.address, width=160, **pv.FIELD_STYLE)
    mask = ft.TextField(label="Subnet mask", value=ip.mask, hint_text="255.255.255.0 or 24",
                        width=160, **pv.FIELD_STYLE)
    gateway = ft.TextField(label="Gateway", value=ip.gateway, width=160, **pv.FIELD_STYLE)
    dns = ft.TextField(label="DNS servers", value=ip.dns, hint_text="1.1.1.1 8.8.8.8",
                       width=160, **pv.FIELD_STYLE)
    static_fields = (address, mask, gateway, dns)
    hostname = ft.TextField(label="Host name", value=cur.hostname, width=330, **pv.FIELD_STYLE)
    mdns = ft.Switch(
        label="Reachable as <host name>.local (avahi)", value=cur.mdns,
        disabled=not cur.mdns_installed, active_color=theme.ACCENT,
        tooltip=None if cur.mdns_installed else "Install it: sudo apt install avahi-daemon",
        label_text_style=ft.TextStyle(size=12))

    def set_static_enabled() -> None:
        """The address fields apply only without DHCP."""
        for f in static_fields:
            f.disabled = bool(dhcp.value)
        page.update()

    for f in static_fields:
        f.disabled = ip.dhcp
    error = ft.Text("", color=theme.DANGER, size=12)
    save = pv.button("Save and apply", ft.Icons.CHECK, lambda e: start(), primary=True)

    def switch(action) -> None:
        """Close the dialog, give the page a moment to report, then run
        ``action`` (which changes network) and report where the device ended up."""
        page.pop_dialog()
        app.set_status("Switching networks; if this page stops responding, "
                       "join the network that is up and reload it.")
        time.sleep(SWITCH_DELAY_S)
        try:
            action()
            app.set_status("Wi-Fi: " + describe(wifi))
        except WifiError as e:
            app.set_status(f"Wi-Fi: {e}")

    def work() -> None:
        """Store the settings, then switch networks."""
        try:
            name = validate_hostname(hostname.value.strip()) if hostname.value.strip() else ""
            wifi.configure(ssid.value.strip(), key.value or "", ap_ssid.value.strip(),
                           ap_key.value or "", ap_on.value,
                           IpConfig(bool(dhcp.value), address.value, mask.value,
                                    gateway.value, dns.value))
            if name:
                wifi.set_hostname(name)
            if cur.mdns_installed and bool(mdns.value) != cur.mdns:
                wifi.set_mdns(bool(mdns.value))
        except WifiError as e:
            error.value = str(e)
            save.disabled = False
            page.update()
            return
        switch(lambda: wifi.ensure(force=True))

    def start() -> None:
        """Validate on a worker thread (nmcli can take a while)."""
        error.value = ""
        save.disabled = True
        page.update()
        page.run_thread(work)

    def retry() -> None:
        """Leave the access point for the saved network, if it is in range."""
        page.run_thread(lambda: switch(wifi.try_client))

    # Offered while the access point is up and there is a network to try.
    retry_btn = ([pv.button("Connect to the network now", ft.Icons.WIFI_FIND,
                            lambda e: retry())]
                 if st.mode == "ap" and cur.ssid else [])

    page.show_dialog(ft.AlertDialog(
        title=ft.Text("Wi-Fi"), bgcolor=theme.PANEL_TOP,
        content=ft.Column([
            ft.Text(now, size=12, color=theme.MUTED),
            pv.caption("Network to join"), ssid, key, dhcp,
            ft.Row([address, mask], spacing=10), ft.Row([gateway, dns], spacing=10),
            ft.Text("If this network is not found, the device starts its own:",
                    size=12, color=theme.MUTED),
            pv.caption("Own access point"), ap_on, ap_ssid, ap_key,
            pv.caption("This device"), hostname, mdns,
            *retry_btn, error,
        ], tight=True, spacing=10, scroll=ft.ScrollMode.AUTO),
        actions=[pv.link_button("Cancel", lambda e: page.pop_dialog(), color=theme.MUTED), save]))
