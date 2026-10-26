"""The SETLIST tab: play the patches of a library liveset in order.

A patch is played through the amp's edit buffer, so no slot is overwritten; it
stays until the amp changes channel.  The big buttons (and a footswitch mapped to
"Next / previous setlist patch") step through the list.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

import flet as ft

from . import params_view as pv, theme

if TYPE_CHECKING:
    from .app import App

TAB_SETLIST = "SETLIST"


def build(app: App) -> ft.Control:
    """Instantiate the pane, keep it on ``app.setlist_view`` and return its
    root control."""
    app.setlist_view = _SetlistPane(app)
    return app.setlist_view.root


def refresh(app: App) -> None:
    """Refresh the pane if the app already has one; a no-op otherwise."""
    if hasattr(app, "setlist_view"):
        app.setlist_view.refresh()


class _SetlistPane:
    def __init__(self, app: App) -> None:
        """Build the picker, the current patch with its Previous / Next buttons,
        and the list of the setlist's patches."""
        self.app = app
        self.picker = ft.Dropdown(label="Setlist (a liveset of the library)", width=300,
                                  options=[], **pv.FIELD_STYLE)
        self.now = ft.Text("No setlist open", size=44, weight=ft.FontWeight.W_900,
                           color=theme.ACCENT, text_align=ft.TextAlign.CENTER,
                           no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)
        self.position = ft.Text("", size=14, color=theme.MUTED,
                                text_align=ft.TextAlign.CENTER)
        self.items = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, expand=True)
        top = ft.Row([
            self.picker,
            pv.button("Open", ft.Icons.PLAYLIST_PLAY, lambda e: self._open(), primary=True),
            pv.button("Refresh", ft.Icons.REFRESH, lambda e: self.refresh()),
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        stage = ft.Column([
            ft.Container(self.now, alignment=ft.Alignment.CENTER, padding=10),
            ft.Container(self.position, alignment=ft.Alignment.CENTER),
            ft.Row([self._big("Previous", ft.Icons.SKIP_PREVIOUS, "prev"),
                    self._big("Next", ft.Icons.SKIP_NEXT, "next")], spacing=12),
        ], spacing=10)
        self.root = ft.Column([
            ft.Container(top, padding=ft.Padding(left=0, right=0, top=8, bottom=0)),
            ft.Text("A patch is played through the amp's edit buffer: no slot is "
                    "overwritten, and the sound is gone when the amp changes channel.",
                    size=12, color=theme.MUTED),
            pv.module_card("Now playing", [stage]),
            pv.module_card("Setlist", [self.items]),
        ], expand=True, spacing=12)
        self.refresh()

    def _big(self, text: str, icon, op: str) -> ft.Control:
        """A large button for use on stage."""
        return ft.Container(
            ft.Row([ft.Icon(icon, size=36, color=theme.FOREGROUND),
                    ft.Text(text, size=26, weight=ft.FontWeight.BOLD, color=theme.FOREGROUND)],
                   alignment=ft.MainAxisAlignment.CENTER, spacing=12),
            expand=True, height=110, border_radius=10, bgcolor=theme.TAB_ACTIVE,
            border=ft.Border.all(1, theme.PANEL_EDGE), ink=True,
            on_click=lambda e: self.app.page.run_thread(self.app.setlist_step, op))

    def _open(self) -> None:
        """Open the chosen liveset as the setlist."""
        ft.context.disable_auto_update()
        if not self.picker.value:
            self.app.set_status("Choose a liveset first.")
            return
        self.app.setlist_open(self.picker.value)

    def _row(self, i: int, name: str, playable: bool, current: bool) -> ft.Control:
        """One entry of the list; clicking it plays that patch."""
        return ft.Container(
            ft.Row([ft.Text(f"{i + 1:>2}", width=26, size=15, weight=ft.FontWeight.BOLD,
                            color=theme.ACCENT),
                    ft.Text(name or ("(no name)" if playable else "(empty)"), size=16,
                            expand=True, no_wrap=True,
                            color=theme.FOREGROUND if playable else theme.MUTED),
                    *([ft.Icon(ft.Icons.VOLUME_UP, color=theme.ACCENT, size=18)]
                      if current else [])],
                   spacing=8),
            padding=ft.Padding(left=10, right=10, top=8, bottom=8), border_radius=6,
            bgcolor=theme.TAB_ACTIVE if current else theme.KNOB_LO,
            border=ft.Border(left=ft.BorderSide(3, theme.ACCENT if current else "transparent")),
            on_click=(lambda e, i=i: self.app.page.run_thread(self.app.setlist_goto, i))
            if playable else None)

    def refresh(self) -> None:
        """Redraw from ``app.setlist`` and the library's livesets."""
        names = [e.name for e in self.app.lib.list()]
        self.picker.options = [ft.dropdown.Option(n) for n in names]
        sl = self.app.setlist
        if self.picker.value not in names:
            self.picker.value = sl.name if sl and sl.name in names else None
        if sl is None:
            self.now.value, self.position.value = "No setlist open", ""
            self.items.controls = [ft.Text("Choose a liveset and press Open.",
                                           color=theme.MUTED, size=12)]
        else:
            i = sl.index
            self.now.value = (sl.names[i] or "(no name)") if i is not None else sl.name
            self.position.value = (f"Patch {i + 1} of {len(sl.names)}" if i is not None
                                   else f"{len(sl.names)} patches; Next plays the first")
            self.items.controls = (
                [self._row(n, name, sl.playable(n), n == i) for n, name in enumerate(sl.names)]
                or [ft.Text("This liveset has no patches.", color=theme.MUTED, size=12)])
        try:
            self.root.update()
        except RuntimeError:
            pass        # not on a page yet (first build)
