"""Headless tests for the block layout styles in :mod:`katatone.gui.params_view`.

Widgets are constructed but never mounted, so no Flet runtime is needed.
"""
from __future__ import annotations

import types

import flet as ft

from katatone.address.map import load_map
from katatone.gui import params_view as pv
from katatone.gui.knob import Knob
from katatone.gui.model import row_for_block
from katatone.model.paramset import ParamSet


def _rows(block: str):
    """Build the model rows for one block of PATCH(1) against an empty ParamSet.

    Args:
        block: block name within PATCH(1), e.g. "BOOSTER(1)".

    Returns:
        the list of RowDesc for the block.
    """
    return row_for_block(load_map()["PATCH(1)"], block, ParamSet())


def _kinds(row: ft.Row) -> list[str]:
    """Classify the controls of a row as knob / switch / selector.

    Args:
        row: the ft.Row produced by a block_body style.

    Returns:
        one label per control, in row order.
    """
    out = []
    for c in row.controls:
        if isinstance(c, Knob):
            out.append("knob")
        elif isinstance(c, pv.ToggleButton):
            out.append("switch")
        else:
            out.append("selector")
    return out


def _noop(row, value) -> None:
    """A do-nothing change callback passed to the layout builders."""
    pass


def test_inline_booster_is_one_line_solo_type_dials() -> None:
    """The inline style lays a booster block out on one line: SOLO switch, TYPE selector, then the dials."""
    body = pv.block_body(_rows("BOOSTER(1)"), _noop, "inline")
    assert len(body) == 1
    kinds = _kinds(body[0])
    assert kinds[:2] == ["switch", "selector"]          # SOLO, TYPE
    assert set(kinds[2:]) == {"knob"}                     # then the dials


def test_switch_row_puts_switches_below_one_line() -> None:
    """The switch_row style keeps the main line switch-free and puts all switches on a second row."""
    body = pv.block_body(_rows("SOLO_DELAY"), _noop, "switch_row")
    assert len(body) == 2
    assert "switch" not in _kinds(body[0])
    assert set(_kinds(body[1])) == {"switch"}


def test_two_lines_puts_controls_first_and_dials_below() -> None:
    """The two_lines style puts switches and selectors on the first line and the knobs below."""
    body = pv.block_body(_rows("SOLO_EQ"), _noop, "two_lines")
    assert len(body) == 2
    assert set(_kinds(body[0])) == {"switch", "selector"}
    assert set(_kinds(body[1])) == {"knob"}


def test_default_rows_style_keeps_three_lines() -> None:
    """The default rows style keeps one line each for switches, selectors and knobs."""
    body = pv.block_body(_rows("SENDRETURN"), _noop)
    assert [set(_kinds(r)) for r in body] == [{"switch"}, {"selector"},
                                             {"knob"}]


def test_card_rows_stretches_shared_rows() -> None:
    """card_rows stretches a shared row so a stacked card fills it (STRETCH alignment, expand on the last card)."""
    a = pv.module_card("A", [ft.Text("a")])
    b = pv.module_card("B", [ft.Text("b")])
    c = pv.module_card("C", [ft.Text("c")])
    grid = pv.card_rows([[a], [b, [c, None]]])
    full, pair = grid.controls
    assert full is a
    assert pair.intrinsic_height
    assert pair.vertical_alignment == ft.CrossAxisAlignment.STRETCH
    assert b.expand == 1 and c.expand is True       # stacked last card fills


# ---------------------------------------------------------------- widget registry


def _row(block: str, p_index: int):
    """The RowDesc of one parameter."""
    return next(r for r in _rows(block) if r.p_index == p_index)


def _collect(build) -> tuple[dict, list]:
    """Run `build()` under collect_widgets; return the registry and `on_set` calls."""
    reg: dict = {}
    calls: list = []
    with pv.collect_widgets(reg):
        widget = build(lambda row, v: calls.append((row.label, v)))
    return reg, calls, widget


def test_nothing_is_registered_outside_collect_widgets() -> None:
    """The SYSTEM pane builds widgets without a collection and pays nothing."""
    pv.knob(_row("AMP", 1), _noop)
    assert pv._registry() is None


def test_knob_setter_moves_the_knob_without_firing_on_set() -> None:
    """The pedal moving the volume knob must not write back to the amp."""
    reg, calls, knob = _collect(lambda cb: pv.knob(_row("AMP", 1), cb))
    (setter,) = reg[("AMP", 1)]
    setter(73)
    assert knob.value == 73 and calls == []


def test_checkbox_setter_flips_the_button_and_its_dependents() -> None:
    """A footswitch toggling DELAY ticks the box and tells the colour picker."""
    toggled: list = []
    row = _row("SW", 3)
    reg, calls, box = _collect(
        lambda cb: pv.checkbox(row, cb, on_toggle=toggled.append))
    (setter,) = reg[("SW", 3)]
    setter(1)
    assert box.value is True and toggled == [True] and calls == []
    setter(0)
    assert box.value is False and toggled == [True, False]


def test_combo_setter_selects_the_option_label() -> None:
    """Dropdowns show the label of the new value, not its number."""
    row = _row("AMP", 7)
    reg, calls, col = _collect(lambda cb: pv.select(row, cb))
    (setter,) = reg[("AMP", 7)]
    setter(2)
    dd = col.controls[1]
    assert dd.value == row.options[2] and calls == []


def test_radio_and_color_setters_move_the_pill() -> None:
    """A/B variation and effect colour pills follow the value."""
    reg, calls, _ = _collect(lambda cb: pv.radio(_row("AMP", 6), cb))
    reg2, _, seg = _collect(lambda cb: pv.color_picker(_row("COLOR", 1), cb))
    reg[("AMP", 6)][0](1)
    reg2[("COLOR", 1)][0](2)
    assert seg.selected == 2 and calls == []
    reg2[("COLOR", 1)][0](9)                    # out of range is ignored
    assert seg.selected == 2


def test_block_body_registers_every_value_widget_of_a_block() -> None:
    """Building a whole card registers one setter per editable parameter."""
    reg: dict = {}
    with pv.collect_widgets(reg):
        pv.block_body(_rows("AMP"), _noop)
    assert {k for k in reg if k[0] == "AMP"} == {("AMP", i) for i in range(10)}


def test_a_parameter_past_the_first_128_bytes_is_found_by_its_data_index() -> None:
    """The map's offsets are address offsets (they carry at 128), the amp's data has no gaps:
    the 353 of an FX_DETAIL block are the 225 bytes the amp holds, and VIBRATO RATE at 0x127 is byte 167."""
    from katatone.address.map import load_map
    from katatone.gui import model
    region = load_map()["PATCH(1)"]
    detail = region.block("FX_DETAIL(1)")
    assert max(p.index + p.size for p in detail.params) == 225
    assert all(p.index == p.off for b in region.blocks if not b.name.startswith("FX_DETAIL") for p in b.params)
    rate = next(i for i, p in enumerate(detail.params) if p.name.endswith("VIBRATO_RATE"))
    assert detail.params[rate].off == 0x127 and detail.params[rate].index == 167

    ps = ParamSet()
    ps.set_block("PATCH%FX_DETAIL(1)", bytes(225))
    for i, name in enumerate(("RATE", "DEPTH", "LEVEL")):
        ps.set_param(region, "FX_DETAIL(1)", rate + i, 20 + i)
    raw = ps.block("PATCH%FX_DETAIL(1)")
    assert len(raw) == 225 and list(raw[167:170]) == [20, 21, 22]          # where the amp holds them
    keys = model.fx_detail_groups(region, "FX_DETAIL(1)")
    rows = model.fx_detail_rows(region, "FX_DETAIL(1)", keys.index("VIBRATO"), ps)
    assert [r.value for r in rows if r.label in ("RATE", "DEPTH", "LEVEL")] == [20, 21, 22]


def test_a_block_longer_than_one_message_is_sent_with_the_carry_in_its_address() -> None:
    from katatone.address.map import load_map
    from katatone.amp import write
    from katatone.protocol import rc6
    sent = []
    t = types.SimpleNamespace(dt1=lambda addr, data: sent.append((addr, len(data))))
    base = load_map()["PATCH"].block("FX_DETAIL(1)").base_addr
    write._dt1_chunked(t, base, bytes(225))
    assert sent == [(base, 128), (base + 0x100, 97)]                       # not base + 128: bit 7 is dropped
    assert rc6.to_nib(sent[1][0]) == rc6.to_nib(base) + 128
