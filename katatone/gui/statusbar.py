"""Top status bar: MIDI devices, connect, lamp, current patch, preview-mute.

Widgets are stored on the :class:`~katatone.gui.app.App` (``app.sb_*``) so
:func:`refresh` can update them from background ops.  MIDI port enumeration is
best-effort so the UI still loads when ALSA/midiutil is unavailable.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

import flet as ft

from . import params_view as pv, theme

if TYPE_CHECKING:
    from .app import App


def _option(text: str):
    """Create a dropdown option, using whichever Flet spelling exists."""
    opt = getattr(ft, "DropdownOption", None)
    return opt(text) if opt else ft.dropdown.Option(text)


def _ports() -> list[str]:
    """Enumerate the MIDI cards the amp could be on.

    A USB-MIDI card carries both directions on one device, so a single list
    serves for input and output.

    Returns:
        The card names, empty when ALSA is unavailable.
    """
    try:
        from ..protocol.transport import list_alsa_ports
        return list(list_alsa_ports().get("out", []))
    except Exception:                                # noqa: BLE001
        return []


def _default_port(ports: list[str]) -> str | None:
    """The port to preselect: the first Katana, else the first port."""
    return next((p for p in ports if "katana" in p.lower()),
                ports[0] if ports else None)


def _brand() -> ft.Control:
    """The KataTone wordmark shown at the left of the bar."""
    return ft.Column([
        ft.Text("KataTone", size=18, weight=ft.FontWeight.W_900, italic=True,
                color=theme.ACCENT, style=ft.TextStyle(letter_spacing=4)),
        ft.Text("KATANA GEN 3 · EDITOR", size=9, color=theme.MUTED,
                style=ft.TextStyle(letter_spacing=2)),
    ], spacing=0, tight=True)


def build(app: App) -> ft.Control:
    """Build the status bar and stash its widgets on the app.

    Args:
        app: the App instance; the widgets are stored as ``app.sb_*`` so
            :func:`refresh` can update them.

    Returns:
        The status bar container control.
    """
    ports = _ports()
    sb_amp = ft.Dropdown(label="Amp (MIDI port)", width=210,
                         options=[_option(x) for x in ports],
                         value=app.amp_port if app.amp_port in ports else _default_port(ports),
                         **pv.FIELD_STYLE)
    sb_lamp = ft.Container(width=12, height=12, border_radius=6,
                           bgcolor=theme.MUTED)
    sb_connect = pv.button("Connect", ft.Icons.LINK,
                         lambda e: _on_connect(app), primary=True)
    sb_auto = ft.Switch(
        label="Auto", value=app.auto_connect, active_color=theme.ACCENT,
        tooltip="Connect to the amp when it appears, and again if it comes back",
        label_text_style=ft.TextStyle(size=11, color=theme.MUTED),
        on_change=lambda e: app.set_auto_connect(bool(e.control.value)))
    sb_midi = ft.Container(width=10, height=10, border_radius=5, bgcolor=theme.LED_OFF,
                           border=ft.Border.all(1, theme.ACCENT_DIM),
                           tooltip="MIDI controllers: none")
    sb_patch = ft.Text("", size=13, color=theme.FOREGROUND, no_wrap=True)
    # In a browser the tools are pages of the server, opened in a new tab by the
    # button itself; on the desktop they are windows of their own.
    web = bool(getattr(app.page, "web", False))

    token = getattr(app, "launch_token", None)

    def tool_url(route: str):
        """The tool's link; the token lets the new tab skip the login."""
        suffix = f"?token={token}" if token else ""
        return ft.Url(f"/{route}{suffix}", ft.UrlTarget.BLANK) if web else None

    sb_analyzer = pv.button("Analyzer", ft.Icons.GRAPHIC_EQ,
                            lambda e: None if web else app.open_analyzer(),
                            url=tool_url("analyzer"))
    sb_tuner = pv.button("Tuner", ft.Icons.MUSIC_NOTE,
                         lambda e: None if web else app.open_tuner(),
                         url=tool_url("tuner"))
    sb_mute = pv.button("Preview mute", ft.Icons.VOLUME_OFF,
                      lambda e: app.set_mute(True))
    sb_unmute = pv.button("Unmute", ft.Icons.VOLUME_UP,
                        lambda e: app.set_mute(False))

    # Network setup is opt-in (--wifi): only for a device that manages its own network.
    sb_wifi = (pv.button("Wi-Fi", ft.Icons.WIFI, lambda e: _on_wifi(app))
               if getattr(app, "wifi_enabled", False) else None)

    app.sb_amp = sb_amp
    app.sb_lamp = sb_lamp
    app.sb_connect = sb_connect
    app.sb_auto = sb_auto
    app.sb_patch = sb_patch
    app.sb_midi = sb_midi
    app._sb_midi_lit = False
    app.sb_mute = sb_mute
    app.sb_unmute = sb_unmute

    return ft.Container(
        content=ft.Row([
            _brand(),
            ft.Container(width=1, height=36, bgcolor=theme.PANEL_EDGE),
            sb_amp, sb_connect, sb_auto,
            ft.Row([sb_lamp, sb_patch], spacing=6, tight=True),
            ft.Container(expand=True),
            ft.Row([ft.Text("MIDI", size=10, color=theme.MUTED), sb_midi],
                   spacing=4, tight=True),
            *([sb_wifi] if sb_wifi else []),
            sb_analyzer, sb_tuner, sb_mute, sb_unmute,
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=12),
        gradient=ft.LinearGradient(begin=ft.Alignment.TOP_CENTER,
                                   end=ft.Alignment.BOTTOM_CENTER,
                                   colors=[theme.PANEL_TOP, theme.PANEL_BOTTOM]),
        border=ft.Border(bottom=ft.BorderSide(2, theme.ACCENT)),
        border_radius=ft.BorderRadius(top_left=10, top_right=10,
                                      bottom_left=0, bottom_right=0),
        padding=ft.Padding(left=16, right=16, top=10, bottom=10),
    )


def _on_wifi(app: App) -> None:
    """Open the Wi-Fi settings dialog."""
    from . import wifi_dialog
    wifi_dialog.open_dialog(app)


def _on_connect(app: App) -> None:
    """Toggle the connection: disconnects when connected, else connects the selected amp port."""
    if app.connected:
        app.disconnect()
        return
    if not app.sb_amp.value:
        app.set_status("Choose the amp's MIDI port first.")
        return
    app.set_auto_connect(True)
    app.connect(app.sb_amp.value, app.sb_amp.value)


def refresh(app: App) -> None:
    """Update lamp / connect-button / patch label from app state."""
    if not hasattr(app, "sb_lamp"):
        return
    app.sb_lamp.bgcolor = theme.OK if app.connected else theme.DANGER
    app.sb_connect.content = ft.Text(
        "Disconnect" if app.connected else "Connect", no_wrap=True)
    app.sb_connect.icon = ft.Icons.LINK_OFF if app.connected else ft.Icons.LINK
    if app.connected:
        app.sb_patch.value = (f"{app.slot_name(app.patch_number)}  "
                              f"{app.patch_name()!r}"
                              + ("  ● unsaved" if app.dirty else ""))
    else:
        app.sb_patch.value = "not connected"


def refresh_ports(app: App) -> None:
    """Re-list the amp dropdown, without enabled controllers.

    A controller the MIDI tab has open must not be offered as the amp (opening
    it twice fails, and it is not the amp). The selection is kept when still valid.

    Args:
        app: the application.
    """
    if not hasattr(app, "sb_amp"):
        return
    ports = _ports()
    midi = getattr(app, "midi", None)
    if midi is not None:
        taken = midi.enabled_cards()
        ports = [p for p in ports if p not in taken]
    dd = app.sb_amp
    dd.options = [_option(x) for x in ports]
    if dd.value not in ports:
        dd.value = _default_port(ports)
    try:
        dd.update()
    except RuntimeError:
        pass  # not mounted yet


def refresh_midi(app: App) -> None:
    """Update the MIDI LED's tooltip with the controllers that are open.

    Args:
        app: the application.
    """
    midi = getattr(app, "midi", None)
    if midi is None or not hasattr(app, "sb_midi"):
        return
    open_ = [l for l, st in midi.states.items() if st == "open"]
    app.sb_midi.tooltip = "MIDI controllers: " + (", ".join(open_) if open_ else "none open")
    try:
        app.sb_midi.update()
    except RuntimeError:
        pass


def set_midi_activity(app: App, lit: bool) -> None:
    """Light or dim the MIDI LED (called by the MIDI pane's UI pump).

    Only changes are pushed to the client, so a burst of events costs two updates.

    Args:
        app: the application.
        lit: whether a controller event happened a moment ago.
    """
    if not hasattr(app, "sb_midi") or lit == app._sb_midi_lit:
        return
    app._sb_midi_lit = lit
    app.sb_midi.bgcolor = theme.ACCENT if lit else theme.LED_OFF
    try:
        app.sb_midi.update()
    except RuntimeError:
        pass  # not mounted yet
