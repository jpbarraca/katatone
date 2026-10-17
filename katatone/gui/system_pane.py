"""SYSTEM-region pane — the same data-driven renderer over region ``SYSTEM``.

System settings have no patch selector: Read / Write operate on the whole
``SYSTEM`` region.  Rendering reuses :mod:`katatone.gui.model` exactly like the patch
editor, so a new address map produces a new UI here too.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

import flet as ft

from ..address.map import load_map
from . import model, params_view as pv, theme

#: Blocks with at most this many parameters are laid out on a single line.
INLINE_MAX_ROWS = 5

#: Blocks forced to half width (they would otherwise be full width), so
#: they sit next to the preceding half-width card.
HALF_WIDTH = {"USB_COM"}

#: Blocks shown inside another block's card: {host block: [merged blocks]}.
MERGED = {"GLOBAL_COM": ["COM"]}
_MERGED_AWAY = {b for bs in MERGED.values() for b in bs}

if TYPE_CHECKING:
    from .app import App


def _safe_update(ctrl):
    """Call ``ctrl.update()``, swallowing the RuntimeError raised while it is not yet attached to a page."""
    try:
        ctrl.update()
    except RuntimeError:
        pass  # not yet attached to page


def build(app: App) -> ft.Control:
    """Create the SYSTEM pane and return its root control.

    Args:
        app: the App instance the pane is attached to.
    """
    app.system = _SystemPane(app)
    return app.system.root


def refresh(app: App) -> None:
    """Refresh the system pane, if one has been built yet.

    Args:
        app: the App instance the pane is attached to.
    """
    if hasattr(app, "system"):
        app.system.refresh()


class _SystemPane:
    def __init__(self, app: App) -> None:
        """Wire up the Read/Write buttons and render the initial content.

        Args:
            app: the App instance this pane renders for.
        """
        self.app = app
        self.region = load_map()["SYSTEM"]
        self.content_box = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO,
                                     expand=True)
        load_btn = pv.button("Read system", ft.Icons.DOWNLOAD,
                             lambda e: app.load_system())
        save_btn = pv.button("Write system", ft.Icons.UPLOAD,
                             lambda e: app.save_system(), primary=True)
        self.root = ft.Column([
            ft.Row([load_btn, save_btn], spacing=8),
            ft.Container(content=self.content_box, expand=True,
                         padding=ft.Padding(left=2, right=12, top=4, bottom=4)),
        ], expand=True, spacing=10)
        self.refresh()

    def refresh(self) -> None:
        """Re-render the block cards from the current system param set."""
        ps = self.app.system_ps
        self.content_box.controls = []
        if ps is None:
            self.content_box.controls.append(
                ft.Text("Press 'Read system' to load the SYSTEM region.",
                        color=theme.MUTED))
            _safe_update(self.root)
            return
        cards = []
        for _label, blocks in model.group_blocks([b.name for b in self.region.blocks]):
            for bname in blocks:
                if bname in _MERGED_AWAY:
                    continue
                rows = [r for b in (*MERGED.get(bname, ()), bname)
                        for r in model.row_for_block(self.region, b, ps)]
                style = "inline" if len(rows) <= INLINE_MAX_ROWS else "rows"
                card = pv.block_card(model.friendly_block_label(bname), rows,
                                     self._set_param, style,
                                     **({"wide": False}
                                        if bname in HALF_WIDTH else {}))
                if card is not None:
                    cards.append(card)
        self.content_box.controls.append(pv.card_grid(cards))
        _safe_update(self.root)

    def _set_param(self, row: model.RowDesc, val: int) -> None:
        """Clamp an edited value into the param's range and store it.

        Args:
            row: the edited row (identifies the block and param).
            val: the new raw value coming from the widget.
        """
        if self.app.system_ps is None:
            return
        val = max(row.param.min, min(row.param.max, int(val)))
        self.app.system_ps.set_param(self.region, row.block, row.p_index, val)
