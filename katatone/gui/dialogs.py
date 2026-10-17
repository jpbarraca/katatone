"""Small modal dialogs shared by the MIDI pane (text prompt, confirmation)."""
from __future__ import annotations

from typing import Callable

import flet as ft

from . import params_view as pv, theme


def prompt(page: ft.Page, title: str, value: str, on_ok: Callable[[str], None],
           max_length: int = 64) -> None:
    """Show a single-line text dialog pre-filled with `value`.

    Args:
        page: the Flet page.
        title: the dialog title.
        value: initial field content (e.g. the current name).
        on_ok: called with the stripped text when Save is pressed; skipped when
            the field is empty.
        max_length: maximum number of characters the field accepts.
    """
    ft.context.disable_auto_update()
    field = ft.TextField(value=value, autofocus=True, max_length=max_length,
                         **pv.FIELD_STYLE)

    def ok(e=None) -> None:
        """Close the dialog and hand the text to `on_ok`."""
        page.pop_dialog()
        if (field.value or "").strip():
            on_ok(field.value.strip())

    field.on_submit = ok
    page.show_dialog(ft.AlertDialog(
        title=ft.Text(title), content=field, bgcolor=theme.PANEL_TOP,
        actions=[pv.link_button("Cancel", lambda e: page.pop_dialog(),
                                color=theme.MUTED),
                 pv.button("Save", ft.Icons.CHECK, ok, primary=True)]))


def confirm(page: ft.Page, text: str, on_ok: Callable[[], None],
            label: str = "Delete") -> None:
    """Show a confirmation dialog.

    Args:
        page: the Flet page.
        text: the question.
        on_ok: called when the confirm button is pressed.
        label: the confirm button's text.
    """
    ft.context.disable_auto_update()

    def ok(e) -> None:
        """Close the dialog and run the confirmed action."""
        page.pop_dialog()
        on_ok()

    page.show_dialog(ft.AlertDialog(
        title=ft.Text("Confirm"), content=ft.Text(text), bgcolor=theme.PANEL_TOP,
        actions=[pv.link_button("Cancel", lambda e: page.pop_dialog(),
                                color=theme.MUTED),
                 pv.button(label, ft.Icons.DELETE_OUTLINE, ok, primary=True)]))
