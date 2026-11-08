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

import dataclasses
import re

import flet as ft

from ..amp import slots
from ..liveset.tsl import LivesetFull, Tsl
from ..library.store import LibraryFull
from ..model.paramset import ParamSet
from . import model, params_view as pv, theme
from . import chain
from .knob import Knob

if TYPE_CHECKING:
    from .app import App


# The right-hand tab strip (fixed heights, so the left panel can line up with
# the cards below it): tab row + divider + the content's top padding.
_TAB_HEIGHT = 40
_DIVIDER_HEIGHT = 1
_CONTENT_TOP = 12
_ALIGN_NUDGE = 8      # measured by eye: Flutter adds a little around the tab row
_ASSIGN_CHIP_HEIGHT = 30
_ASSIGN_GAP = 14       # between the pedal and the GA-FC rows of an effect's Assign card
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
            on_change=self._on_name, on_focus=lambda e: self._name_focus(True),
            on_blur=lambda e: self._name_focus(False), **pv.FIELD_STYLE)
        self.name_field_focused = False
        self.dirty_label = ft.Text("", size=12, color=theme.DANGER)
        buttons = ft.Column([
            ft.Row([_button("Read", ft.Icons.DOWNLOAD,
                            lambda e: self.app.read_patch(n(), force=True)),
                    _button("Write", ft.Icons.UPLOAD,
                            lambda e: self.app.write_patch(), primary=True)],
                   spacing=6),
            ft.Row([_button("Select", ft.Icons.PLAY_ARROW,
                            lambda e: self.app.select_patch(n())),
                    _button("Clear", ft.Icons.DELETE_OUTLINE,
                            lambda e: self.app.clear_patch(n()))],
                   spacing=6),
            ft.Row([_button("Undo", ft.Icons.UNDO, lambda e: self.app.undo()),
                    _button("Redo", ft.Icons.REDO, lambda e: self.app.redo())],
                   spacing=6),
            ft.Row([_button("Save to library", ft.Icons.LIBRARY_ADD,
                            lambda e: self._save_to_library())],
                   spacing=6),
            ft.Row([_button("Compare with amp", ft.Icons.COMPARE_ARROWS,
                            lambda e: self.app.compare_with_amp())],
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
        Knob.deselect()                     # the dialog has text fields: the arrows are theirs
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

        def store(tsl, index=None) -> None:
            """Append the patch to ``tsl``, or overwrite patch ``index``."""
            try:
                if index is None:
                    tsl.add(Tsl.encode(app.ps))
                else:
                    tsl.replace(index, Tsl.encode(app.ps))
                app.lib.save(tsl)
            except (LivesetFull, LibraryFull, OSError, ValueError) as err:
                app.set_status(f"Save to library failed: {err}")
                return
            if hasattr(app, "library"):
                app.library.selected = tsl.name
            app.set_status(f"Saved {app.patch_name() or 'patch'!r} to "
                           f"liveset {tsl.name!r}.")
            app.library_refresh()

        def confirm_overwrite(tsl, index: int) -> None:
            """Ask before replacing the same-named patch of ``tsl``."""
            def yes(e=None) -> None:
                app.page.pop_dialog()
                store(tsl, index)
            app.page.show_dialog(ft.AlertDialog(
                title=ft.Text("Overwrite patch?"),
                content=ft.Text(f"Liveset “{tsl.name}” already has a patch named "
                                f"“{tsl.patch_name(index)}”. Overwrite it?"),
                bgcolor=theme.PANEL_TOP,
                actions=[pv.link_button("Cancel",
                                        lambda e: app.page.pop_dialog(),
                                        color=theme.MUTED),
                         pv.button("Overwrite", ft.Icons.CHECK, yes, primary=True)]))

        def ok(e=None) -> None:
            """Close the dialog and save the patch to the chosen liveset,
            after confirmation if that liveset has a patch of that name."""
            app.page.pop_dialog()
            new_name = (new.value or "").strip()
            if not new_name and not target.value:
                app.set_status("Choose a liveset or enter a new name.")
                return
            try:
                tsl = (app.lib.create(new_name) if new_name
                       else app.lib.load(target.value))
            except (LibraryFull, OSError, ValueError) as err:
                app.set_status(f"Save to library failed: {err}")
                return
            name = app.patch_name()
            same = [i for i in range(len(tsl)) if tsl.patch_name(i) == name]
            if name and same:
                confirm_overwrite(tsl, same[0])
            else:
                store(tsl)

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
        slot holds the selected patch number, else the last name seen in the
        slot, else the slot's long name."""
        if slot.index == self.app.patch_number:
            return self.app.patch_name() or slot.long
        return self.app.slot_names.get(slot.index) or slot.long

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

    def _name_focus(self, focused: bool) -> None:
        """Remember whether the name field has the focus, so that Ctrl+Z there
        is the field's own undo and not the editor's."""
        self.name_field_focused = focused
        if focused:
            Knob.deselect()                 # the arrow keys move the text cursor now

    def _on_name(self, e) -> None:
        """Name-field handler: encode the new name into the PATCH%COM block,
        mark the patch dirty and notify the app."""
        raw = bytearray(16)
        raw[:16] = ParamSet.encode_patch_name(e.control.value)
        ps = self.app.ps
        self.app.history.record(ps, ("name",), ps.block("PATCH%COM"), bytes(raw))
        ps.set_block("PATCH%COM", bytes(raw))
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
        # Remember the name of the slot on show, so that it stays on its row
        # once another slot is picked (unsaved edits are not the slot's name).
        name = self.app.patch_name()
        if name and not self.app.dirty:
            self.app.slot_names[self.app.patch_number] = name
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
        if label == "Assign":
            with pv.collect_widgets(self._widgets):
                view = self._assign_view(region, blocks)
            self._tab_view.controls[i].content = view
            self._built.add(i)
            return
        with pv.collect_widgets(self._widgets):
            if layout is None:
                cards = [c for b in blocks
                         if (c := self._block_card(
                             region, b, **self._BLOCK_WIDTH.get(b.split("(")[0], {})))
                         is not None]
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
                         if b not in named and b not in ("COLOR", "NS", "SW")]
                content = pv.card_rows(rows)
        self._tab_view.controls[i].content = ft.Column(
            [content], scroll=ft.ScrollMode.AUTO, expand=True)
        self._built.add(i)

    # The noise suppressor's rows, named for the AMP card it now shares.
    _NS_LABELS = {"Enable": "Noise Suppressor", "THRESHOLD": "NS Threshold",
                  "RELEASE": "NS Release"}

    # Blocks whose card width is set by hand instead of by how many controls they hold.
    _BLOCK_WIDTH = {"EQ_PEQ": {"wide": False}}

    _ASSIGN_WIDTH = {"ASSIGN_FS": {"wide": True}, "ASSIGN_GAFCFS": {"wide": False},
                     "PATCH_KNOB_READONLY": {"wide": False},
                     "PATCH_KNOB_SOLO_DELAY_READONLY": {"wide": False}}

    def _effect_card(self, title: str, rows: list) -> ft.Container:
        """One effect's Assign card: the knob and pedal rows, a gap, then the
        GA-FC rows (they would otherwise run on from the pedal's in one line)."""
        pedal = [r for r in rows if not r.label.startswith("GA-FC")]
        gafc = [r for r in rows if r.label.startswith("GA-FC")]
        body = pv.block_body(pedal, self._set_param)
        if body and gafc:
            body.append(ft.Container(height=_ASSIGN_GAP))
        body += pv.block_body(gafc, self._set_param)
        return pv.module_card(title, body, wide=pv.is_wide(rows))

    def _assign_cards(self, region, blocks: list[str]) -> list:
        """The general Assign cards: the footswitch first, then what the pedals
        do, the GA-FC footswitch and the read-only blocks (the per-effect
        settings have a sub-tab each)."""
        _, rest = model.assign_by_effect(region, self.app.ps, blocks)
        funcs, rest = model.assign_functions(region, self.app.ps, rest)
        # The footswitch block has a row to itself; the GA-FC footswitch and the
        # read-only knob blocks are small: half width, side by side.
        cards = {b: self._block_card(region, b, **self._ASSIGN_WIDTH.get(b.split("(")[0], {}))
                 for b in rest}
        first = [cards.pop("ASSIGN_FS", None), pv.block_card("Pedal functions", funcs,
                                                              self._set_param)]
        return [c for c in (*first, *cards.values()) if c is not None]

    def _assign_page(self, cards: list) -> ft.Control:
        """One Assign sub-tab's content: its cards, scrolling."""
        return ft.Container(
            ft.Column([pv.card_grid(cards)], scroll=ft.ScrollMode.AUTO, expand=True),
            padding=ft.Padding(left=0, right=0, top=_CONTENT_TOP, bottom=4), expand=True)

    def _assign_view(self, region, blocks: list[str]) -> ft.Control:
        """The Assign tab: sub-tabs of general settings, then one per effect, in
        two lines that together fill the width (a tab bar cannot wrap).  A page
        is built when first shown, so an effect is a click away, not a scroll."""
        effects, _ = model.assign_by_effect(region, self.app.ps, blocks)
        pages = [("General", lambda: self._assign_cards(region, blocks))]
        pages += [(title, lambda t=title, r=rows: [self._effect_card(t, r)])
                  for title, rows in effects]
        built: dict[int, ft.Control] = {}
        body = ft.Container(expand=True)
        chips: list[ft.Container] = []

        def show(i: int) -> None:
            if i not in built:
                with pv.collect_widgets(self._widgets):
                    built[i] = self._assign_page(pages[i][1]())
            for j, chip in enumerate(chips):
                self._style_chip(chip, j == i)
            body.content = built[i]

        def pick(i: int) -> None:
            ft.context.disable_auto_update()
            show(i)
            try:
                view.update()
            except RuntimeError:
                pass                        # not on the page yet

        for i, (title, _) in enumerate(pages):
            chips.append(ft.Container(
                ft.Text(title.upper(), size=10, weight=ft.FontWeight.BOLD,
                        no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
                        text_align=ft.TextAlign.CENTER),
                expand=True, height=_ASSIGN_CHIP_HEIGHT, border_radius=4,
                alignment=ft.Alignment.CENTER, tooltip=title,
                padding=ft.Padding(left=2, right=2, top=0, bottom=0),
                on_click=lambda e, i=i: pick(i)))
        half = (len(chips) + 1) // 2
        view = ft.Column([ft.Row(chips[:half], spacing=3), ft.Row(chips[half:], spacing=3),
                          body], spacing=3, expand=True)
        show(0)
        return view

    @staticmethod
    def _style_chip(chip: ft.Container, active: bool) -> None:
        """Highlight the sub-tab on show."""
        chip.bgcolor = theme.TAB_ACTIVE if active else None
        chip.content.color = theme.ACCENT if active else theme.MUTED
        chip.border = ft.Border(bottom=ft.BorderSide(2, theme.ACCENT if active else "transparent"))

    @staticmethod
    def _present(cell, blocks) -> bool:
        """Whether a layout cell (a block name, or a list of names) names
        any of the tab's blocks."""
        names = cell if isinstance(cell, list) else [cell]
        return any(n in blocks for n in names)

    # Explicit card grids (rows of equal-height cards).  "SW" is the Effects
    # card; a nested list stacks cards in one column.
    _TAB_LAYOUTS = {
        # OTHER shows the chain card; the AMP card holds OTHER's other settings and NS.
        # SW (the effects switches) is not a card: the chain diagram switches the effects.
        "Amp": [["AMP"], ["OTHER"], ["SENDRETURN"]],
        "Contour": [["CONTOUR_COM", "CONTOUR(1)"],
                    ["CONTOUR(2)", "CONTOUR(3)"]],
        "Pedal": [["PEDALFX_COM"], ["PEDALFX"]],
        # DELAY(1..3) / (4..6) are Delay 1 / Delay 2 in green, red, yellow:
        # one colour per line, the two delays side by side.
        "Delay": [["DELAY(1)", "DELAY(4)"], ["DELAY(2)", "DELAY(5)"],
                  ["DELAY(3)", "DELAY(6)"]],
    }

    @staticmethod
    def _body_style(bname: str) -> str:
        """Per-block layout: one line for compact effect modules, a separate
        switch line for the Solo blocks, selectors and then one line of knobs
        for the AMP and EQ blocks, the default three lines otherwise."""
        if re.fullmatch(r"(BOOSTER|CONTOUR)\(\d\)", bname) or bname in (
                "PEDALFX_COM", "NS", "SENDRETURN", "CONTOUR_COM", "SOLO_COM"):
            return "inline"
        if bname in ("SOLO_EQ", "AMP") or re.fullmatch(r"EQ_(PEQ|GE10)\(\d\)", bname):
            return "two_lines"           # AMP: the variations sit left of the amp type;
                                         # EQ: the selectors, then all the knobs on a line
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

    def _block_card(self, region, bname: str, **card_kw):
        """One block's module card: an `FX(n)` block the live FX card, `OTHER`
        the signal chain card, `AMP` the amp card; `SW` and `COLOR` have no card
        (the chain diagram switches the effects and sets their colours).

        Args:
            region: the amp region of the selected patch.
            bname: the block name.
            card_kw: passed on to the card (e.g. ``wide=False``).

        Returns:
            The card control, or `None` for blocks without their own card.
        """
        if bname == "COLOR":
            return None
        if bname == "SW":
            return None                     # the chain diagram switches the effects
        if bname == "OTHER":
            return self._chain_card(region)
        if bname == "AMP":
            return self._amp_card(region)
        if re.fullmatch(r"FX\(\d\)", bname):
            return self._fx_card(region, bname)
        rows = model.row_for_block(region, bname, self.app.ps)
        return pv.block_card(model.block_title(bname), rows, self._set_param,
                             style=self._body_style(bname),
                             **{**self._variation_kw(bname), **card_kw})

    # The settings that decide the chain: (block, label shown); the diagram follows them.
    _CHAIN_SETTINGS = (("OTHER", "Chain"), ("PEDALFX_COM", "Pedal FX position"),
                       ("EQ_EACH(1)", "EQ 1 position"), ("EQ_EACH(2)", "EQ 2 position"),
                       ("SENDRETURN", "Send/Return position"))

    def _chain_card(self, region) -> ft.Control:
        """The signal chain: the chain preset and the positions of the Pedal FX, the EQs and
        the Send/Return on one line, and below them the chain they give (see
        :mod:`katatone.gui.chain`), redrawn when one of them changes."""
        rows = []
        for block, label in self._CHAIN_SETTINGS:
            row = next(r for r in model.row_for_block(region, block, self.app.ps)
                       if r.param.name.endswith(("_CHAIN", "_POSITION")))
            rows.append(dataclasses.replace(row, label=label))
        values = {r.block: int(r.value) for r in rows}
        shown = ft.Container()

        ps = self.app.ps
        sw = {r.label: r for r in model.row_for_block(region, "SW", ps)}
        color = {r.label: r for r in model.row_for_block(region, "COLOR", ps)}

        def state(name: str):
            """``(on, colour value)`` of an effect of the chain, None for the other elements."""
            labels = chain.EFFECTS.get(name)
            if labels is None:
                return None
            c = color.get(labels[1] or "DELAY")
            return (bool(ps.get_param(region, "SW", sw[labels[0]].p_index)),
                    None if c is None else int(ps.get_param(region, "COLOR", c.p_index)))

        def draw() -> None:
            """Show the chain the five settings give."""
            order = chain.order(values["OTHER"], values["EQ_EACH(1)"], values["EQ_EACH(2)"],
                                values["SENDRETURN"], values["PEDALFX_COM"])
            shown.content = chain.diagram(order, state, click)

        def click(name: str) -> None:
            """A click on an effect: off -> green -> yellow -> red -> off."""
            sw_label, color_label = chain.EFFECTS[name]
            on, value = state(name)
            new_on, new_color = chain.next_state(on, value, color_label is not None)
            if color_label is not None and new_color != value:
                self._set_color(color[color_label], new_color)
                self.show_value("COLOR", color[color_label].p_index, new_color)
            if new_on != on:
                self._set_param(sw[sw_label], int(new_on))
                self.show_value("SW", sw[sw_label].p_index, int(new_on))
            draw()
            try:
                shown.update()
            except RuntimeError:
                pass                        # not on the page yet

        def on_set(r: model.RowDesc, v: int) -> None:
            """Selector callback: store the setting and redraw the diagram."""
            self._set_param(r, v)
            values[r.block] = int(v)
            draw()
            try:
                shown.update()
            except RuntimeError:
                pass                        # not on the page yet

        draw()
        _, selectors, _ = pv.block_widgets(rows, on_set)
        return pv.module_card("Signal chain", [pv.inline_row(selectors), shown],
                              wide=True, led=False)

    def _amp_card(self, region) -> ft.Control:
        """The AMP card: the variation switches and the amp type on a line with the OTHER
        block's cabinet resonance and master key, then the amp's knobs, then the noise
        suppressor (NS) with its labels spelled out."""
        ps = self.app.ps
        other = [r for r in model.row_for_block(region, "OTHER", ps)
                 if not r.param.name.endswith("_CHAIN")]
        switches, selectors, knobs = pv.block_widgets(
            model.row_for_block(region, "AMP", ps), self._set_param)
        _, other_selectors, _ = pv.block_widgets(other, self._set_param)
        body = [pv.inline_row([*switches, *selectors, *other_selectors]),
                pv.wrap_row(knobs, spacing=6)]
        ns = [dataclasses.replace(r, label=self._NS_LABELS.get(r.label, r.label))
              for r in model.row_for_block(region, "NS", ps)]
        body += pv.block_body(ns, self._set_param, "inline")
        return pv.module_card(model.block_title("AMP"), body, wide=True)

    def _fx_card(self, region, bname: str) -> ft.Control:
        """MOD / FX slot: TYPE selector + only the selected effect's params
        (from the matching FX_DETAIL block), swapped live on TYPE change."""
        detail = bname.replace("FX(", "FX_DETAIL(")
        type_row = model.row_for_block(region, bname, self.app.ps)[0]
        # Two lines: TYPE with the effect's switches and selectors, then its
        # knobs.  The TYPE dropdown stays put; only the widgets after it are swapped.
        type_dd = None
        top = pv.inline_row([])
        knobs_line = pv.wrap_row([], spacing=6)
        lines = ft.Column([top, knobs_line], spacing=10, tight=True)

        def fill(fx_type: int) -> None:
            """Rebuild the lines with the TYPE selector plus the params of
            effect type `fx_type`."""
            rows = model.fx_detail_rows(region, detail, fx_type, self.app.ps)
            switches, selectors, knobs = pv.block_widgets(rows, self._set_param)
            top.controls = [type_dd, *switches, *selectors]
            knobs_line.controls = knobs
            knobs_line.visible = bool(knobs)

        def on_type(row: model.RowDesc, v: int) -> None:
            """TYPE selector callback: store the new type and swap in that
            effect's widgets."""
            self._set_param(row, v)
            fill(v)
            lines.update()

        type_dd = pv.select(type_row, on_type)
        fill(int(type_row.value))
        return pv.module_card(model.block_title(bname), [lines], wide=True,
                              **self._variation_kw(bname))

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
        ps = self.app.ps
        self.app.history.record(
            ps, ("param", row.block, row.p_index),
            ps.get_param(region, row.block, row.p_index), val)
        ps.set_param(region, row.block, row.p_index, val)
        self.app.mark_dirty()
        self.app.param_changed(row.block, row.p_index, val)
