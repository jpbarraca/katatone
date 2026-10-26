"""A dialog that compares the patch in the editor with another patch and lets
the user take that patch's value for a parameter."""
from __future__ import annotations
from typing import TYPE_CHECKING

import flet as ft

from ..address.map import load_map
from ..model.compare import Difference, compare
from ..model.paramset import ParamSet
from . import model, params_view as pv, theme

if TYPE_CHECKING:
    from .app import App

MAX_ROWS = 300      # more than this is a different patch, not a tweak


def _text(d: Difference, value: int | str) -> str:
    """A value as shown: a combo's option name when it has one."""
    if isinstance(value, str):
        return value or "(no name)"
    p = load_map()["PATCH"].block(d.block).params[d.p_index]
    labels = model._combo_labels(p.name, p.max - p.min + 1)
    i = value - p.min
    return labels[i] if labels and 0 <= i < len(labels) else str(value)


def show(app: App, other: ParamSet, label: str) -> None:
    """Compare the editor's patch with ``other``.

    Args:
        app: the app (its editor patch is the left side).
        other: the patch to compare with (the right side).
        label: what ``other`` is, for the dialog ("Crunch from My liveset").
    """
    ft.context.disable_auto_update()
    diffs = compare(app.ps, other)
    page = app.page
    count = ft.Text("", size=12, color=theme.MUTED)
    rows = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, height=420, width=620)

    def update_count() -> None:
        left = sum(1 for c in rows.controls if getattr(c, "data", None) == "open")
        count.value = ("Both patches have the same values." if not diffs else
                       f"{len(diffs)} differences; {left} not taken.")

    def take(d: Difference, row: ft.Control, button: ft.Control):
        def handler(e) -> None:
            app.take_value(d.block, d.p_index, d.b)
            button.disabled = True
            row.data = "taken"
            button.tooltip = "Taken"
            update_count()
            page.update()
        return handler

    last_block = None
    for d in diffs[:MAX_ROWS]:
        if d.block != last_block:
            last_block = d.block
            rows.controls.append(ft.Text(model.block_title(d.block), size=12,
                                         weight=ft.FontWeight.BOLD, color=theme.ACCENT))
        btn = ft.IconButton(ft.Icons.ARROW_BACK, icon_size=16, disabled=d.is_name,
                            tooltip=("The name is changed in the name field"
                                     if d.is_name else "Take this value into the editor"))
        row = ft.Row([
            ft.Text(model.display_label(d.name, d.block), size=12, expand=True,
                    no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
            ft.Text(_text(d, d.a), size=12, width=130, color=theme.MUTED),
            btn,
            ft.Text(_text(d, d.b), size=12, width=130, color=theme.FOREGROUND),
        ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        row.data = "open"
        btn.on_click = take(d, row, btn)
        rows.controls.append(row)
    if len(diffs) > MAX_ROWS:
        rows.controls.append(ft.Text(f"… and {len(diffs) - MAX_ROWS} more.",
                                     size=12, color=theme.MUTED))
    update_count()
    header = ft.Row([ft.Text("", expand=True),
                     ft.Text("Editor", size=12, width=130, color=theme.MUTED),
                     ft.Container(width=40),
                     ft.Text(label, size=12, width=130, no_wrap=True,
                             overflow=ft.TextOverflow.ELLIPSIS)], spacing=4)
    page.show_dialog(ft.AlertDialog(
        title=ft.Text("Compare patches"), bgcolor=theme.PANEL_TOP,
        content=ft.Column([count, header, rows], tight=True, spacing=6),
        actions=[pv.link_button("Close", lambda e: page.pop_dialog())]))
