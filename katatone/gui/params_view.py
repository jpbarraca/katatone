"""Shared, Katana-panel styled renderer for parameter blocks.

Both the patch editor and the SYSTEM pane turn a list of
:class:`katatone.gui.model.RowDesc` into one "module" card per block:

    ● BLOCK TITLE
    [x] switch   [x] switch                       <- checkboxes (SW params)
    TYPE ▾        VARIATION (A)(B)                 <- dropdowns / radios / colors
    (knob) (knob) (knob) (knob) (knob) ...         <- knobs, wrapped side by side

Widgets push only themselves (never a page-wide update) and report changes
through ``on_set(row, value)``; the caller owns the model write.
"""
from __future__ import annotations

import contextlib
import dataclasses
import threading
from typing import Callable, Optional

import flet as ft

from . import model, theme
from .knob import Knob

OnSet = Callable[[model.RowDesc, int], None]

KNOB_SIZE = 60
KNOB_TILE = 84            # fixed tile width so knob rows line up in columns
# Katana panel colour LEDs; value order of the COLOR params.
COLOR_HEX = ("#40c057", "#e62b0e", "#fcc419")   # green / red / yellow


def _option(text: str):
    """Build a dropdown option, using whichever spelling the installed Flet
    exposes (``ft.DropdownOption`` or ``ft.dropdown.Option``)."""
    opt = getattr(ft, "DropdownOption", None)
    return opt(text) if opt else ft.dropdown.Option(text)


# While `collect_widgets` is active, every value widget registers a setter here:
# {(block, p_index): [setter(value)]}. The setter moves the widget to a value
# without firing `on_set` (so a MIDI pedal can move a knob on screen).
# It is per thread: editors build their tabs on several worker threads, and a
# shared variable could be left set when two collections overlapped.
_state = threading.local()


def _registry() -> dict[tuple[str, int], list[Callable[[int], None]]] | None:
    """The collection active on this thread, or None."""
    return getattr(_state, "registry", None)


@contextlib.contextmanager
def collect_widgets(into: dict[tuple[str, int], list[Callable[[int], None]]]):
    """Record a setter for each widget built inside the ``with`` block.

    Args:
        into: the dict that receives ``{(block, p_index): [setter, ...]}``.
    """
    prev, _state.registry = _registry(), into
    try:
        yield
    finally:
        _state.registry = prev


def _register(row: model.RowDesc, setter: Callable[[int], None]) -> None:
    """Remember how to move the widget of `row`, if a collection is active.

    Args:
        row: the parameter row the widget shows.
        setter: moves the widget to a value without firing its callbacks.
    """
    reg = _registry()
    if reg is not None:
        reg.setdefault((row.block, row.p_index), []).append(setter)


def _push(ctrl: ft.Control) -> None:
    """Update one control, ignoring the RuntimeError raised before it is
    mounted on a page."""
    try:
        ctrl.update()
    except RuntimeError:
        pass                          # not mounted yet


def button(text: str, icon, on_click, primary: bool = False,
           expand: bool = False) -> ft.FilledButton:
    """Panel button: amber when primary, slate otherwise; text never wraps.

    Args:
        text: the button label (rendered with ``no_wrap``).
        icon: the Flet icon shown left of the label.
        on_click: called when the button is pressed.
        primary: amber background (``theme.ACCENT``) when true, slate
            otherwise.
        expand: only set when true: Flet treats any explicit value as a flex
            child, which breaks the button inside a wrapping Row.

    Returns:
        The styled ``ft.FilledButton``.
    """
    return ft.FilledButton(
        content=ft.Text(text, no_wrap=True, size=13), icon=icon,
        on_click=on_click, **({"expand": True} if expand else {}),
        style=ft.ButtonStyle(
            bgcolor=theme.ACCENT if primary else theme.TAB_ACTIVE,
            color=theme.BG if primary else theme.FOREGROUND,
            padding=ft.Padding(left=12, right=14, top=0, bottom=0),
            shape=ft.RoundedRectangleBorder(radius=6)))


def link_button(text: str, on_click, color: str = theme.ACCENT) -> ft.TextButton:
    """Text-only button for secondary actions such as "Cancel"."""
    return ft.TextButton(content=ft.Text(text, no_wrap=True, size=13),
                         on_click=on_click,
                         style=ft.ButtonStyle(color=color))


# Shared look for text fields / header dropdowns.
FIELD_STYLE = dict(dense=True, text_size=13, filled=True,
                   fill_color=theme.KNOB_LO, border_color=theme.PANEL_EDGE,
                   focused_border_color=theme.ACCENT, border_radius=6)


def caption(text: str) -> ft.Text:
    """Small silk-screen style caption (labels above dropdowns etc.)."""
    return ft.Text(text.upper(), size=10, color=theme.LABEL,
                   weight=ft.FontWeight.W_600, no_wrap=True,
                   style=ft.TextStyle(letter_spacing=0.8))


# ------------------------------------------------------------------ widgets

def knob(row: model.RowDesc, on_set: OnSet) -> Knob:
    """One knob for a ``RowDesc``: range from the param, a default tick when
    the factory init falls inside the range, and the row's current value
    (``param.min`` until the model holds an integer).

    Returns:
        The ``Knob``; its ``on_change`` forwards to ``on_set(row, value)``.
    """
    p = row.param
    value = row.value if isinstance(row.value, int) else p.min
    k = Knob(value=value, min=p.min, max=p.max, label=row.label,
             default=p.init if p.min <= p.init <= p.max else None,
             knob_size=KNOB_SIZE, tile_width=KNOB_TILE,
             on_change=lambda v, r=row: on_set(r, v))
    _register(row, k.set_value)
    return k


def checkbox(row: model.RowDesc, on_set: OnSet, label: Optional[str] = None,
             on_toggle: Optional[Callable[[bool], None]] = None) -> ft.Control:
    """Labelled checkbox in a fixed-width box (a bare Flet Checkbox with a
    label stretches to the full row, so several would never share a line).

    Args:
        label: silk-screen label override (defaults to ``row.label``).
        on_toggle: optional extra callback with the new ``bool`` state.
    """
    def changed(e) -> None:
        """Report the new state (``1``/``0``) through ``on_set`` and, if
        given, call ``on_toggle``."""
        ft.context.disable_auto_update()        # the client already shows it
        on = bool(e.control.value)
        on_set(row, 1 if on else 0)
        if on_toggle:
            on_toggle(on)

    def show(v: int) -> None:
        """Move the checkbox (and what hangs off it) without firing `on_set`."""
        cb.value = bool(v)
        _push(cb)
        if on_toggle:
            on_toggle(bool(v))

    text = (label or row.label).upper()
    cb = ft.Checkbox(
        value=bool(row.value), label=text,
        label_style=ft.TextStyle(size=11, color=theme.LABEL,
                                 weight=ft.FontWeight.W_600, letter_spacing=0.8),
        active_color=theme.ACCENT, check_color=theme.BG,
        border_side=ft.BorderSide(1.5, theme.KNOB_RIM),
        on_change=changed)
    _register(row, show)
    return ft.Container(cb, width=48 + 8 * len(text))


def select(row: model.RowDesc, on_set: OnSet) -> ft.Control:
    """Combo dropdown for a ``RowDesc``: one option per label, width sized
    to the longest one.

    Returns:
        A column with the caption on top; selecting an option reports
        ``param.min + option_index`` through ``on_set``.
    """
    opts = [str(o) for o in (row.options or ())]
    longest = max((len(o) for o in opts), default=4)
    width = max(130, min(240, 9 * longest + 76))

    def changed(e) -> None:
        """Map the chosen label back to a value (``param.min`` + option
        index) and report it through ``on_set``."""
        ft.context.disable_auto_update()
        if e.control.value in opts:
            on_set(row, row.param.min + opts.index(e.control.value))

    dd = ft.Dropdown(
        options=[_option(o) for o in opts],
        value=model.combo_display_value(row),
        width=width, dense=True, text_size=12,
        filled=True, fill_color=theme.KNOB_LO, bgcolor=theme.PANEL_TOP,
        border_color=theme.PANEL_EDGE, focused_border_color=theme.ACCENT,
        border_radius=6, menu_height=360,
        content_padding=ft.Padding(left=10, right=4, top=6, bottom=6),
        on_select=changed)

    def show(v: int) -> None:
        """Move the dropdown without firing `on_set`."""
        dd.value = model.combo_display_value(dataclasses.replace(row, value=v))
        _push(dd)

    _register(row, show)
    return ft.Column([caption(row.label), dd], spacing=4, tight=True)


class _Segmented:
    """Small pill group (A/B variation, effect colour); redraws only itself."""

    def __init__(self, row: model.RowDesc, on_set: OnSet, pills: list,
                 selected: int, disabled: bool = False) -> None:
        """Wire a group of pills to a parameter row: clicking one reports
        ``param.min + index`` through ``on_set``.

        Args:
            row: the parameter row the pills represent.
            on_set: callback for value changes.
            pills: the pill controls to lay out (one per option).
            selected: index of the initially active pill.
            disabled: render the group faded and non-interactive.
        """
        self.row, self.on_set = row, on_set
        self.selected, self.disabled = selected, disabled
        self.pills = pills
        for i, pill in enumerate(pills):
            pill.on_click = lambda e, i=i: self._click(i)
        self.root = ft.Row(pills, spacing=6, tight=True)
        self._paint()

    def _paint(self) -> None:
        """Redraw borders, opacity and labels: the active pill gets the
        accent border; a disabled group drops to 35 % opacity."""
        for i, pill in enumerate(self.pills):
            on = (i == self.selected) and not self.disabled
            pill.border = ft.Border.all(2 if on else 1,
                                        theme.ACCENT if on else theme.PANEL_EDGE)
            pill.opacity = 0.35 if self.disabled else 1.0
            if isinstance(pill.content, ft.Text):
                pill.content.color = theme.ACCENT if on else theme.MUTED

    def _click(self, i: int) -> None:
        """Handle a pill click: activate ``i`` (when enabled), repaint and
        report the new value through ``on_set``."""
        ft.context.disable_auto_update()
        if self.disabled or i == self.selected:
            return
        self.selected = i
        self._paint()
        _push(self.root)
        self.on_set(self.row, self.row.param.min + i)

    def show(self, value: int) -> None:
        """Select the pill for a parameter value without firing `on_set`.

        Args:
            value: the parameter value (``param.min`` + pill index).
        """
        i = value - self.row.param.min
        if 0 <= i < len(self.pills):
            self.selected = i
            self._paint()
            _push(self.root)

    def set_disabled(self, disabled: bool) -> None:
        """Enable or disable the group (e.g. when the owning effect switch
        is off) and repaint."""
        self.disabled = disabled
        self._paint()
        _push(self.root)


def radio(row: model.RowDesc, on_set: OnSet) -> ft.Control:
    """A/B variation as pill buttons, one per option label.

    Returns:
        A captioned column; picking a pill reports ``param.min + index``
        through ``on_set``.
    """
    opts = row.options or ("A", "B")
    pills = [ft.Container(
        content=ft.Text(str(o), size=12, weight=ft.FontWeight.BOLD),
        width=34, height=26, border_radius=13,
        alignment=ft.Alignment.CENTER, bgcolor=theme.KNOB_LO)
        for o in opts]
    seg = _Segmented(row, on_set, pills, int(row.value) - row.param.min)
    _register(row, seg.show)
    return ft.Column([caption(row.label), seg.root], spacing=4, tight=True)


def color_picker(row: model.RowDesc, on_set: OnSet,
                 disabled: bool = False) -> _Segmented:
    """Green/red/yellow effect-colour pills, one per ``COLOR_HEX`` entry.

    Args:
        disabled: render the pills faded and inert (effect switched off).

    Returns:
        The ``_Segmented`` group (attach its ``root`` control to a row).
    """
    pills = [ft.Container(
        content=ft.Container(width=12, height=12, border_radius=6, bgcolor=c),
        width=24, height=24, border_radius=12, padding=5)
        for c in COLOR_HEX]
    seg = _Segmented(row, on_set, pills, int(row.value) - row.param.min,
                     disabled=disabled)
    _register(row, seg.show)
    return seg


def constant(row: model.RowDesc) -> ft.Control:
    """Read-only caption + value for a constant (non-editable) row."""
    return ft.Column([caption(row.label),
                      ft.Text(str(row.value), size=13, color=theme.MUTED)],
                     spacing=4, tight=True)


# ------------------------------------------------------------------- layout

def module_card(title: str, body: list[ft.Control], *, wide: bool = True,
                led: bool = True, led_color: str = theme.ACCENT,
                badge: Optional[str] = None) -> ft.Container:
    """A Katana-style module: dark brushed panel, LED + engraved title.

    Args:
        title: the module name, engraved in spaced capitals.
        body: the controls below the title row.
        wide: full-width card (``lg: 12``) when true, half-width (``lg: 6``)
            when false.
        led: light the LED (e.g. the active colour variation of an effect).
        led_color: the LED (and badge) colour.
        badge: a small right-aligned note such as "ACTIVE".

    Returns:
        The styled ``ft.Container``.
    """
    header = ft.Row([
        ft.Container(width=9, height=9, border_radius=5,
                     bgcolor=led_color if led else theme.LED_OFF,
                     border=ft.Border.all(1, led_color),
                     shadow=ft.BoxShadow(blur_radius=8, spread_radius=0.5,
                                         color=led_color) if led else None),
        ft.Text(title.upper(), size=12, weight=ft.FontWeight.BOLD,
                color=theme.FOREGROUND, expand=True,
                style=ft.TextStyle(letter_spacing=2)),
        *([ft.Text(badge, size=9, color=led_color, weight=ft.FontWeight.BOLD,
                   style=ft.TextStyle(letter_spacing=1.5))] if badge else []),
    ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    return ft.Container(
        content=ft.Column([header,
                           ft.Container(height=1, bgcolor=theme.PANEL_EDGE),
                           *body], spacing=10, tight=True),
        padding=ft.Padding(left=14, right=14, top=10, bottom=12),
        border_radius=10,
        border=ft.Border.all(1, theme.PANEL_EDGE),
        gradient=ft.LinearGradient(begin=ft.Alignment.TOP_CENTER,
                                   end=ft.Alignment.BOTTOM_CENTER,
                                   colors=[theme.PANEL_TOP, theme.PANEL_BOTTOM]),
        shadow=ft.BoxShadow(blur_radius=10, color=theme.SHADOW,
                            offset=ft.Offset(0, 3)),
        col={"xs": 12, "lg": 12 if wide else 6},
    )


def wrap_row(controls: list[ft.Control], spacing: int = 14,
             align=ft.CrossAxisAlignment.START) -> ft.Row:
    """Wrapping row of controls (10 px between lines, vertical alignment
    ``align``)."""
    return ft.Row(controls, wrap=True, spacing=spacing, run_spacing=10,
                  vertical_alignment=align)


# Body styles for block_body():
#   "rows"       switches / selectors / knobs on separate lines (default)
#   "inline"     everything on one wrapping line: switches, selectors, knobs
#   "switch_row" everything but the switches on one line, switches below it
#   "two_lines"  switches + selectors on one line, knobs on the next
STYLES = ("rows", "inline", "switch_row", "two_lines")


def block_widgets(rows: list[model.RowDesc], on_set: OnSet
                  ) -> tuple[list, list, list]:
    """Build a block's widgets, grouped by kind.

    Returns:
        A ``(switches, selectors, knobs)`` tuple of control lists; constants
        count as selectors.
    """
    switches, selectors, knobs = [], [], []
    for row in rows:
        if row.kind == "checkbox":
            switches.append(checkbox(row, on_set))
        elif row.kind == "combo":
            selectors.append(select(row, on_set))
        elif row.kind == "radio":
            selectors.append(radio(row, on_set))
        elif row.kind == "color":
            selectors.append(ft.Column([caption(row.label),
                                        color_picker(row, on_set).root],
                                       spacing=4, tight=True))
        elif row.kind == "constant":
            selectors.append(constant(row))
        elif row.kind == "knob":
            knobs.append(knob(row, on_set))
    return switches, selectors, knobs


def inline_widgets(rows: list[model.RowDesc], on_set: OnSet) -> list:
    """One-line order: switches, selectors, then knobs.

    Returns:
        A flat list of controls, ready for an ``inline_row``.
    """
    switches, selectors, knobs = block_widgets(rows, on_set)
    return [*switches, *selectors, *knobs]


def inline_row(controls: list[ft.Control]) -> ft.Row:
    """One wrapping, vertically-centred line of controls."""
    return wrap_row(controls, spacing=14, align=ft.CrossAxisAlignment.CENTER)


def block_body(rows: list[model.RowDesc], on_set: OnSet,
               style: str = "rows") -> list[ft.Control]:
    """Lay out a block's rows in one of :data:`STYLES`.

    Args:
        rows: the block's parameter rows.
        on_set: callback each widget reports changes through.
        style: one of ``"rows"``, ``"inline"``, ``"switch_row"`` or
            ``"two_lines"``.

    Returns:
        The body controls (one list per line) for a ``module_card``.
    """
    if style == "inline":
        line = inline_widgets(rows, on_set)
        return [inline_row(line)] if line else []
    switches, selectors, knobs = block_widgets(rows, on_set)
    if style == "two_lines":
        body = [inline_row([*switches, *selectors])] if switches or selectors else []
        if knobs:
            body.append(wrap_row(knobs, spacing=6))
        return body
    if style == "switch_row":
        body = [inline_row([*selectors, *knobs])] if selectors or knobs else []
        if switches:
            body.append(wrap_row(switches, spacing=18))
        return body
    body = []
    if switches:
        body.append(wrap_row(switches, spacing=18))
    if selectors:
        body.append(wrap_row(selectors, spacing=18))
    if knobs:
        body.append(wrap_row(knobs, spacing=6))
    return body


def is_wide(rows: list[model.RowDesc]) -> bool:
    """Whether a block deserves a full-width card: more than four knobs or
    more than two selectors."""
    n_knobs = sum(r.kind == "knob" for r in rows)
    n_sel = sum(r.kind in ("combo", "radio", "color") for r in rows)
    return n_knobs > 4 or n_sel > 2


def block_card(title: str, rows: list[model.RowDesc], on_set: OnSet,
               style: str = "rows", **card_kw) -> Optional[ft.Container]:
    """Titled ``module_card`` for a block; ``wide`` defaults to
    :func:`is_wide` unless passed in ``card_kw``.

    Returns:
        The card, or ``None`` when the block renders no body at all.
    """
    body = block_body(rows, on_set, style)
    if not body:
        return None
    card_kw.setdefault("wide", is_wide(rows))
    return module_card(title, body, **card_kw)


def card_grid(cards: list[ft.Control]) -> ft.Control:
    """Cards flow two-up on wide windows (small modules side by side).

    Returns:
        The ``ft.ResponsiveRow`` of cards.
    """
    return ft.ResponsiveRow(cards, spacing=12, run_spacing=12)


Layout = list  # rows of cards; a nested list is a column of stacked cards


def card_rows(layout: Layout) -> ft.Control:
    """Explicit arrangement: each entry is one row of equal-width cards.

    Cards sharing a row are stretched to the same height (``intrinsic_height``
    gives the Row a bounded height inside the scrolling tab), so the panel
    reads as a tidy grid.  An entry that is itself a list stacks those cards
    in a column; its last card absorbs the leftover height.

    Returns:
        The ``ft.Column`` of card rows.
    """
    def cell(item) -> ft.Control:
        """One grid cell: a list stacks its cards in a column (the last one
        absorbs the leftover height); a bare card is expanded to fill its
        slot."""
        if isinstance(item, list):
            item = [c for c in item if c is not None]
            for c in item[:-1]:
                c.expand = False
            item[-1].expand = True
            return ft.Column(item, spacing=12, expand=1)
        item.expand = 1
        return item

    out = []
    for row in layout:
        row = [c for c in row if c is not None and c != []]
        if not row:
            continue
        if len(row) == 1 and not isinstance(row[0], list):
            out.append(row[0])
        else:
            out.append(ft.Row([cell(c) for c in row], spacing=12,
                              intrinsic_height=True,
                              vertical_alignment=ft.CrossAxisAlignment.STRETCH))
    return ft.Column(out, spacing=12)
