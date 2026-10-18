"""Data-driven patch editor.

Left: the amp channel switch (Panel / A1 / A2 / B1 / B2 ...) above the
user-patch list + name field + Read / Write / Select / Clear / Save to library.
Right: one tab per logical group (see :func:`katatone.gui.model.group_blocks`); each
tab shows the group's blocks as Katana-style module cards (see
:mod:`katatone.gui.params_view`): switches, selectors, then a line of knobs.
Widget kinds come from :func:`katatone.gui.model.row_for_block` so the UI is
generated entirely from the static address table.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

import re

import flet as ft

from ..amp import slots
from ..liveset.tsl import LivesetFull, Tsl
from ..library.store import LibraryFull
from ..model.paramset import ParamSet
from . import model, params_view as pv, theme

if TYPE_CHECKING:
    from .app import App


# The right-hand tab strip (fixed heights, so the left panel can line up with
# the cards below it): tab row + divider + the content's top padding.
_TAB_HEIGHT = 40
_DIVIDER_HEIGHT = 1
_CONTENT_TOP = 12
_ALIGN_NUDGE = 8      # measured by eye: Flutter adds a little around the tab row
_CARDS_TOP = _TAB_HEIGHT + _DIVIDER_HEIGHT + _CONTENT_TOP + _ALIGN_NUDGE


def _button(text: str, icon, on_click, primary: bool = False) -> ft.Control:
    """Left-panel button: `pv.button` with `expand=True` so the paired
    buttons share the row's width."""
    return pv.button(text, icon, on_click, primary=primary, expand=True)


class PatchEditor:
    def __init__(self, app: App) -> None:
        """Build the editor: the user-patch list on the left, a tab per
        logical group on the right; the first refresh fills both."""
        self.app = app
        # Setters of the widgets built so far, for show_value(); see
        # params_view.collect_widgets.
        self._widgets: dict[tuple[str, int], list] = {}
        self.left = self._build_left()
        self._tab_bar = ft.TabBar(
            [], scrollable=True, tab_alignment=ft.TabAlignment.START,
            indicator_color=theme.ACCENT, label_color=theme.ACCENT,
            unselected_label_color=theme.MUTED, divider_color=theme.PANEL_EDGE,
            divider_height=_DIVIDER_HEIGHT,
            label_text_style=ft.TextStyle(size=12, weight=ft.FontWeight.BOLD,
                                          letter_spacing=1.2))
        self._tab_view = ft.TabBarView([], expand=True)
        self.tabs = ft.Tabs(
            length=0,
            expand=True,
            content=ft.Column(
                expand=True,
                controls=[self._tab_bar, self._tab_view],
            ),
        )
        self.root = ft.Row([self.left, self.tabs],
                           vertical_alignment=ft.CrossAxisAlignment.START,
                           spacing=12, expand=True)
        self.refresh()

    # ------------------------------------------------------------------ left

    def _build_left(self) -> ft.Control:
        """Build the left column: the "User patches" card (the slot list, the
        name field and the Read / Write / Select / Clear buttons).  Picking a
        slot only chooses what Read, Write and Clear act on; Select makes the
        amp play it.

        Returns:
            The container control (fixed 250 px width).
        """
        n = lambda: self.app.patch_number                      # noqa: E731
        self.list_box = ft.Column(spacing=2)
        self.name_field = ft.TextField(
            label="Patch name", max_length=16, value="",
            on_change=self._on_name, **pv.FIELD_STYLE)
        self.dirty_label = ft.Text("", size=12, color=theme.DANGER)
        buttons = ft.Column([
            ft.Row([_button("Read", ft.Icons.DOWNLOAD,
                            lambda e: self.app.read_patch(n())),
                    _button("Write", ft.Icons.UPLOAD,
                            lambda e: self.app.write_patch(), primary=True)],
                   spacing=6),
            ft.Row([_button("Select", ft.Icons.PLAY_ARROW,
                            lambda e: self.app.select_patch(n())),
                    _button("Clear", ft.Icons.DELETE_OUTLINE,
                            lambda e: self.app.clear_patch(n()))],
                   spacing=6),
            ft.Row([_button("Save to library", ft.Icons.LIBRARY_ADD,
                            lambda e: self._save_to_library())],
                   spacing=6),
        ], spacing=6)
        card = pv.module_card("User patches", [
            self.list_box, self.name_field, buttons, self.dirty_label])
        card.width = 250
        card.col = None
        # Start level with the cards on the right, below the tab strip.
        return ft.Container(
            ft.Column([card], spacing=12),
            width=250, padding=ft.Padding(left=0, right=0, top=_CARDS_TOP,
                                          bottom=0))

    def _save_to_library(self) -> None:
        """Ask which liveset to save the editor's patch into (an existing one
        or a new one) and append it there; the amp is not touched."""
        ft.context.disable_auto_update()
        app = self.app
        names = [e.name for e in app.lib.list()]
        chosen = getattr(getattr(app, "library", None), "selected", None)
        target = ft.Dropdown(
            label="Liveset", width=300, text_size=13,
            options=[pv._option(n) for n in names],
            value=chosen if chosen in names else (names[0] if names else None),
            disabled=not names, **{k: v for k, v in pv.FIELD_STYLE.items()
                                   if k != "text_size"})
        new = ft.TextField(label="Or a new liveset", hint_text="My liveset",
                           max_length=64, **pv.FIELD_STYLE)

        def ok(e=None) -> None:
            """Close the dialog and append the patch to the chosen liveset."""
            app.page.pop_dialog()
            new_name = (new.value or "").strip()
            if not new_name and not target.value:
                app.set_status("Choose a liveset or enter a new name.")
                return
            try:
                tsl = (app.lib.create(new_name) if new_name
                       else app.lib.load(target.value))
                tsl.add(Tsl.encode(app.ps))
                app.lib.save(tsl)
            except (LivesetFull, LibraryFull, OSError, ValueError) as err:
                app.set_status(f"Save to library failed: {err}")
                return
            if hasattr(app, "library"):
                app.library.selected = tsl.name
            app.set_status(f"Saved {app.patch_name() or 'patch'!r} to "
                           f"liveset {tsl.name!r}.")
            app.library_refresh()

        new.on_submit = ok
        app.page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"Save “{app.patch_name() or 'patch'}” to library"),
            content=ft.Column([target, new], tight=True, spacing=12, width=320),
            bgcolor=theme.PANEL_TOP,
            actions=[pv.link_button("Cancel",
                                    lambda e: app.page.pop_dialog(),
                                    color=theme.MUTED),
                     pv.button("Save", ft.Icons.CHECK, ok, primary=True)]))

    def _patch_label(self, slot) -> str:
        """Display name for a slot row: the editor's patch name when this
        slot holds the selected patch number, the slot's long name
        otherwise."""
        if slot.index == self.app.patch_number:
            return self.app.patch_name() or slot.long
        return slot.long

    def _on_pick(self, n: int) -> None:
        """Slot button handler: online, read the slot's patch from the amp without
        selecting it (Select does that); offline, just mark the slot that Write
        will store the edits in.

        Args:
            n: the amp slot index to pick.
        """
        if self.app.connected:
            self.app.read_patch(n)          # reads the stored slot; the amp keeps playing
            return
        # Offline: just choose the slot Write will store the edits in.
        self.app.patch_number = n
        self.app.set_status(f"Offline — edits will be written to "
                            f"{self.app.slot_name(n)} when you connect and "
                            f"press Write.")
        self._rebuild_left()
        self.left.update()

    def _on_name(self, e) -> None:
        """Name-field handler: encode the new name into the PATCH%COM block,
        mark the patch dirty and notify the app."""
        raw = bytearray(16)
        raw[:16] = ParamSet.encode_patch_name(e.control.value)
        self.app.ps.set_block("PATCH%COM", bytes(raw))
        self.app.mark_dirty()
        self.app.name_changed(e.control.value)

    # --------------------------------------------------------------- refresh

    def refresh(self) -> None:
        """Rebuild the left column and the tab strip, then flush the whole
        page so the rebuilt (new) controls actually reach the client."""
        self._rebuild_left()
        self._rebuild_tabs()
        # Flush the rebuilt control tree to the client.  Without this the
        # rebuilt tabs (with freshly-read ps values) are never sent, so the UI
        # keeps showing the old patch after a read.  Knobs are isolated
        # controls, but they are *new* instances here, so they are sent whole.
        try:
            self.app.page.update()
        except RuntimeError:
            pass  # page not yet attached (early construction)

    def refresh_channels(self) -> None:
        """Repaint only the slot list (the slot the amp plays moved).

        Cheap enough for a pedal switch: no tab rebuild and no page-wide
        update, which would re-serialize the whole editor.
        """
        self._rebuild_slot_list()
        try:
            self.list_box.update()
        except RuntimeError:
            pass  # not yet attached to the page

    def show_value(self, block: str, p_index: int, value: int) -> None:
        """Move the on-screen widget of one parameter to `value`.

        Used when something other than the user changed it (a MIDI pedal). Fires
        no callbacks, so nothing is written back to the amp. Widgets on tabs not
        built yet are skipped: they read the patch when first shown.

        Args:
            block: the parameter's block name.
            p_index: the parameter's index within the block.
            value: the new display value.
        """
        for setter in self._widgets.get((block, p_index), ()):
            try:
                setter(value)
            except RuntimeError:
                pass  # widget no longer mounted

    def _update_dirty(self) -> None:
        """Show the "● unsaved changes" hint while the patch is dirty."""
        self.dirty_label.value = "● unsaved changes" if self.app.dirty else ""

    def _rebuild_left(self) -> None:
        """Rebuild the slot list for the selected patch and sync the name
        field and the dirty hint."""
        self._rebuild_slot_list()
        self.name_field.value = self.app.patch_name()
        self._update_dirty()

    def _rebuild_slot_list(self) -> None:
        """The slot rows: the one shown in the editor is highlighted, and the one
        the amp plays has its label filled."""
        playing = self.app.amp_patch if self.app.connected else None
        self.list_box.controls = [
            self._patch_button(slot, slot.index == self.app.patch_number,
                               slot.index == playing)
            for slot in self.app.slots()]

    def _patch_button(self, slot, active: bool, playing: bool = False) -> ft.Container:
        """One amp slot: panel label (PN, 1A, ...) + patch name.

        Args:
            slot: the slot to render.
            active: whether this slot is the one shown in the editor (the
                target of Read, Write and Clear).
            playing: whether the amp plays this slot (its label is filled).

        Returns:
            The clickable `ft.Container` (picking the slot on click).
        """
        n = slot.index
        return ft.Container(
            content=ft.Row([
                ft.Container(
                    ft.Text(slot.short, size=12, weight=ft.FontWeight.BOLD,
                            color=theme.BG if playing else theme.ACCENT),
                    width=30, height=20, border_radius=4,
                    alignment=ft.Alignment.CENTER,
                    bgcolor=theme.ACCENT if playing else None,
                    border=ft.Border.all(1, theme.ACCENT_DIM)),
                ft.Text(self._patch_label(slot), size=13, expand=True,
                        color=theme.FOREGROUND if active else theme.MUTED,
                        no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
            ], spacing=8),
            tooltip=f"{slot.long} (patch {n + 1})" + ("; the amp plays it" if playing else ""),
            bgcolor=theme.TAB_ACTIVE if active else None,
            border=ft.Border(left=ft.BorderSide(
                3, theme.ACCENT if active else "transparent")),
            border_radius=4,
            padding=ft.Padding(left=8, right=6, top=5, bottom=5),
            on_click=lambda e, n=n: self._on_pick(n),
        )

    def _rebuild_tabs(self) -> None:
        """(Re)create the tab strip; only the visible tab is built now.

        A patch has ~1,700 numeric params; building every tab up front means
        tens of thousands of controls on each refresh.  Other tabs are built
        on first visit (see :meth:`_on_tab`).
        """
        region = self.app.region_for(self.app.patch_number)
        names = [b.name for b in region.blocks
                 if b.name != "COM" and not b.name.startswith("FX_DETAIL")]
        self._tab_specs = model.group_blocks(names)
        self._built: set[int] = set()
        self._widgets.clear()           # the old tabs' widgets are gone
        self._tab_bar.tabs = [ft.Tab(label=label.upper(), height=_TAB_HEIGHT)
                              for label, _ in self._tab_specs]
        self._tab_view.controls = [
            ft.Container(padding=ft.Padding(left=2, right=12, top=_CONTENT_TOP, bottom=4),
                         expand=True)
            for _ in self._tab_specs]
        self.tabs.length = len(self._tab_specs)
        self.tabs.on_change = self._on_tab
        if self.tabs.selected_index >= len(self._tab_specs):
            self.tabs.selected_index = 0
        self._build_tab(self.tabs.selected_index)
        try:
            self.tabs.update()
        except RuntimeError:
            pass  # not yet attached to page (initial construction)

    def _on_tab(self, e) -> None:
        """Tab change handler: build a tab on first visit (the index comes
        from the event data, falling back to the currently selected tab)."""
        ft.context.disable_auto_update()
        try:
            i = int(e.data)
        except (TypeError, ValueError):
            i = self.tabs.selected_index
        if 0 <= i < len(self._tab_specs) and i not in self._built:
            self._build_tab(i)
            self._tab_view.controls[i].update()

    def _build_tab(self, i: int) -> None:
        """Build one tab: the explicit grid from `_TAB_LAYOUTS` when the
        group has one (blocks the grid omits go last), a responsive grid
        otherwise.  Also records the active effect colours for the
        variation LEDs.

        Args:
            i: index of the tab in `_tab_specs`.
        """
        region = self.app.region_for(self.app.patch_number)
        self._active_colors = {
            r.p_index: int(r.value)
            for r in model.row_for_block(region, "COLOR", self.app.ps)}
        label, blocks = self._tab_specs[i]
        layout = self._TAB_LAYOUTS.get(label)
        with pv.collect_widgets(self._widgets):
            if layout is None:
                cards = [c for b in blocks
                         if (c := self._block_card(region, b)) is not None]
                content = pv.card_grid(cards)
            else:
                # Explicit grid; blocks the layout does not mention go last.
                named = {n for row in layout for c in row
                         for n in (c if isinstance(c, list) else [c])}
                card = lambda b: self._block_card(region, b)        # noqa: E731
                rows = [[[card(n) for n in c if n in blocks]
                         if isinstance(c, list) else card(c)
                         for c in row if self._present(c, blocks)]
                        for row in layout]
                rows += [[card(b)] for b in blocks
                         if b not in named and b != "COLOR"]
                content = pv.card_rows(rows)
        self._tab_view.controls[i].content = ft.Column(
            [content], scroll=ft.ScrollMode.AUTO, expand=True)
        self._built.add(i)

    @staticmethod
    def _present(cell, blocks) -> bool:
        """Whether a layout cell (a block name, or a list of names) names
        any of the tab's blocks."""
        names = cell if isinstance(cell, list) else [cell]
        return any(n in blocks for n in names)

    # Explicit card grids (rows of equal-height cards).  "SW" is the Effects
    # card; a nested list stacks cards in one column.
    _TAB_LAYOUTS = {
        "Amp": [["AMP"], ["SW", ["OTHER", "NS"]], ["SENDRETURN"]],
        "Contour": [["CONTOUR_COM", "CONTOUR(1)"],
                    ["CONTOUR(2)", "CONTOUR(3)"]],
        "Pedal": [["PEDALFX_COM"], ["PEDALFX"]],
    }

    @staticmethod
    def _body_style(bname: str) -> str:
        """Per-block layout: one line for compact effect modules, a separate
        switch line for the Solo blocks, the default three lines otherwise."""
        if re.fullmatch(r"(BOOSTER|CONTOUR)\(\d\)", bname) or bname in (
                "PEDALFX_COM", "NS", "SENDRETURN", "CONTOUR_COM", "SOLO_COM"):
            return "inline"
        if bname in ("SOLO_EQ", "AMP"):
            return "two_lines"           # AMP: the variations sit left of the amp type
        if bname.startswith("SOLO_"):
            return "switch_row"
        return "rows"

    # COLOR block index for each effect slot (BOOSTER, MOD, FX, DELAY, REVERB).
    _COLOR_INDEX = {"BOOSTER": 0, "MOD": 1, "FX": 2, "DELAY": 3, "DELAY 2": 3,
                    "REVERB": 4}

    def _variation_kw(self, bname: str) -> dict:
        """LED colour/state for a green/red/yellow effect variation card.

        Returns:
            `led` / `led_color` / `badge` kwargs for `pv.module_card` (an
            empty dict for non-variation blocks).
        """
        v = model.block_variation(bname)
        if v is None:
            return {}
        slot, var = v
        active = self._active_colors.get(self._COLOR_INDEX.get(slot, -1)) == var
        return dict(led=active, led_color=pv.COLOR_HEX[var],
                    badge="ACTIVE" if active else None)

    def _block_card(self, region, bname: str):
        """One block's module card: `SW` becomes the Effects card, an
        `FX(n)` block the live FX card, `COLOR` is skipped (it is rendered
        inside the Effects card).

        Args:
            region: the amp region of the selected patch.
            bname: the block name.

        Returns:
            The card control, or `None` for blocks without their own card.
        """
        # COLOR is rendered inside the "Effects" card (one LED row per effect).
        if bname == "COLOR":
            return None
        if bname == "SW":
            return self._effects_card(region)
        if re.fullmatch(r"FX\(\d\)", bname):
            return self._fx_card(region, bname)
        rows = model.row_for_block(region, bname, self.app.ps)
        return pv.block_card(model.block_title(bname), rows, self._set_param,
                             style=self._body_style(bname),
                             **self._variation_kw(bname))

    def _fx_card(self, region, bname: str) -> ft.Control:
        """MOD / FX slot: TYPE selector + only the selected effect's params
        (from the matching FX_DETAIL block), swapped live on TYPE change."""
        detail = bname.replace("FX(", "FX_DETAIL(")
        type_row = model.row_for_block(region, bname, self.app.ps)[0]
        # One line: TYPE, the effect's switches/selectors, then its knobs.
        # The TYPE dropdown stays put; only the widgets after it are swapped.
        type_dd = None
        line = pv.inline_row([])

        def fill(fx_type: int) -> None:
            """Rebuild the line with the TYPE selector plus the params of
            effect type `fx_type`."""
            rows = model.fx_detail_rows(region, detail, fx_type, self.app.ps)
            line.controls = [type_dd, *pv.inline_widgets(rows, self._set_param)]

        def on_type(row: model.RowDesc, v: int) -> None:
            """TYPE selector callback: store the new type and swap in that
            effect's widgets."""
            self._set_param(row, v)
            fill(v)
            line.update()

        type_dd = pv.select(type_row, on_type)
        fill(int(type_row.value))
        return pv.module_card(model.block_title(bname), [line], wide=True,
                              **self._variation_kw(bname))

    def _effects_card(self, region) -> ft.Control:
        """Effect on/off switches, each with its colour-variation LEDs."""
        ps = self.app.ps
        sw_rows = model.row_for_block(region, "SW", ps)
        color_by_name = {r.label: r
                         for r in model.row_for_block(region, "COLOR", ps)}
        lines = []
        for sw in sw_rows:
            color_row = color_by_name.get(sw.label)
            picker = (pv.color_picker(color_row, self._set_color,
                                      disabled=not sw.value)
                      if color_row is not None else None)
            cb = pv.checkbox(sw, self._set_param, on_toggle=(
                (lambda on, p=picker: p.set_disabled(not on)) if picker else None))
            cb.width = 130
            lines.append(ft.Row([cb, picker.root] if picker else [cb],
                                spacing=10,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER))
        return pv.module_card("Effects", [ft.Column(lines, spacing=2,
                                                    tight=True)], wide=False)

    def _set_color(self, row: model.RowDesc, val: int) -> None:
        """Colour change selects another variation: re-light the cards.

        The new colour is recorded in `_active_colors` and every tab but the
        visible one is marked unbuilt, so it re-lights on its next visit.
        """
        self._set_param(row, val)
        self._active_colors[row.p_index] = val
        current = self.tabs.selected_index
        self._built = {current}           # other tabs re-light on next visit

    # --------------------------------------------------------------- changes

    def _set_param(self, row: model.RowDesc, val: int) -> None:
        """Model write behind every widget: clamp `val` to the param's
        range, set it in the patch model, mark the patch dirty and notify
        the app.

        Args:
            row: the parameter row that changed.
            val: the new value (clamped to `param.min`..`param.max`).
        """
        # Runs once per knob step while dragging: keep it cheap (model write
        # + the small dirty label, never a page-wide update).
        val = max(row.param.min, min(row.param.max, int(val)))
        region = self.app.region_for(self.app.patch_number)
        self.app.ps.set_param(region, row.block, row.p_index, val)
        self.app.mark_dirty()
        self.app.param_changed(row.block, row.p_index, val)
