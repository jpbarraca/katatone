"""Unit tests for the data-driven GUI model (pure logic, no Flet runtime).

These run headlessly.  A separate smoke test byte-compiles the Flet widget
modules so syntax errors are caught even when ``flet`` is not installed.
"""
from __future__ import annotations

import py_compile
from pathlib import Path

import pytest

from katatone.address.map import Param, load_map
from katatone.gui import model
from katatone.gui.model import (RowDesc, combo_display_value, control_kind,
                           friendly_block_label, group_blocks, param_label,
                           row_for_block, rows_for_region)
from katatone.model.paramset import ParamSet

GUI_DIR = Path(__file__).resolve().parent.parent / "katatone" / "gui"
FLET_MODULES = ["app.py", "statusbar.py", "patch_editor.py", "system_pane.py",
                "library_pane.py", "knob.py",
                "params_view.py", "midi_pane.py", "mapping_dialog.py",
                "dialogs.py", "analyzer.py"]


# ---------------------------------------------------------------------------
# control_kind
# ---------------------------------------------------------------------------

def _p(size=1, minv=0, maxv=0) -> Param:
    """Build a minimal Param for control_kind tests.

    Args:
        size: parameter size in bytes (1, 2 or 4).
        minv: minimum display value.
        maxv: maximum display value.

    Returns:
        a Param named "X" at offset 0 with the given size and range.
    """
    return Param(name="X", off=0, size=size, ofs=0, init=0, min=minv, max=maxv)


def test_control_kind_text() -> None:
    """A 16-byte param with a 32..125 range is a text field (the patch-name case)."""
    assert control_kind(_p(size=16, minv=32, maxv=125)) == "text"


def test_control_kind_constant() -> None:
    """A param whose min equals its max is a constant, not an editable control."""
    assert control_kind(_p(minv=5, maxv=5)) == "constant"


def test_control_kind_numeric_is_knob() -> None:
    """Numeric ranges of any byte size map to knobs — there are no sliders."""
    # Without display strings every numeric range is a knob (no sliders).
    assert control_kind(_p(minv=0, maxv=8)) == "knob"
    assert control_kind(_p(minv=0, maxv=100)) == "knob"
    assert control_kind(_p(size=2, minv=0, maxv=300)) == "knob"
    assert control_kind(_p(size=4, minv=0, maxv=300)) == "knob"


def test_control_kind_sw_is_checkbox() -> None:
    """A size-1 param with a 0..1 range whose name ends in _SW maps to a checkbox."""
    p = Param(name="PRMID_SYSTEM_COM_STEREOEXPAND_SW", off=0, size=1, ofs=0,
              init=0, min=0, max=1)
    assert control_kind(p) == "checkbox"


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("FX(1)", "FX 1"),
    ("EQ_PEQ(2)", "EQ PEQ 2"),
    ("ASSIGN_FS", "ASSIGN FS"),
    ("BOOSTER(3)", "BOOSTER 3"),
    ("COM", "COM"),
])
def test_friendly_block_label(raw: str, expected: str) -> None:
    """friendly_block_label rewrites raw block names into display labels (number separated, underscores as spaces)."""
    assert friendly_block_label(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("PRMID_PATCH_AMP_GAIN", "AMP GAIN"),
    ("PRMID_SYSTEM_MIDI_CC_1", "MIDI CC 1"),
    ("PRMID_PATCH_COM_NAME", "COM NAME"),
])
def test_param_label(raw: str, expected: str) -> None:
    """param_label strips the PRMID_ and block prefixes, turning raw param IDs into display labels."""
    assert param_label(raw) == expected


# ---------------------------------------------------------------------------
# block_order / rows
# ---------------------------------------------------------------------------

def test_block_order_matches_region() -> None:
    """model.block_order returns the blocks of a region in map order."""
    r = load_map()["PATCH(1)"]
    assert model.block_order(r) == [b.name for b in r.blocks]


def _seeded_paramset(region, fill: int = 3) -> ParamSet:
    """Seed every block with a small ramp so decoded values are deterministic.

    Uses the canonical block key (``ParamSet.block_key``): user-patch regions
    PATCH(1..10) all store under ``PATCH%<BLOCK>`` — exactly how
    ``amp.read.read_patch`` populates a ParamSet.
    """
    ps = ParamSet()
    for b in region.blocks:
        ps.set_block(ParamSet.block_key(region.name, b.name),
                     bytes((i + fill) & 0xFF for i in range(b.length)))
    return ps


def test_regression_rows_use_canonical_patch_key() -> None:
    """Regression: the editor must read values stored under 'PATCH%<BLOCK>'.

    ``read_patch`` keys blocks by the canonical 'PATCH' prefix (region
    PATCH(1) -> 'PATCH%AMP'), but the region object passed to the model is the
    per-patch 'PATCH(1)'.  If the model derived the lookup key from
    ``region.name`` it missed the block, every param decoded from b'' to 0,
    and the UI showed zeros/defaults right after a successful read.
    """
    r = load_map()["PATCH(1)"]
    ps = ParamSet()
    # Exactly what amp.read.read_patch stores for the AMP block (10 bytes).
    ps.set_block("PATCH%AMP", bytes([80, 90, 75, 60, 45, 30, 0, 2, 0, 0]))
    rows = row_for_block(r, "AMP", ps)
    by_label = {x.label: x.value for x in rows}
    assert by_label["GAIN"] == 80
    assert by_label["VOLUME"] == 90
    assert by_label["BASS"] == 75
    assert by_label["TYPE"] == 2
    # ...and get_param (used by every row) resolves the canonical key.
    assert ps.get_param(r, "AMP", 0) == 80


def test_regression_string_combo_maps_value_to_label() -> None:
    """String-labeled combos must preselect a label, not the index.

    A Flet Dropdown matches ``value`` to an option *label*, so the value set
    for a string combo must be the label itself (e.g. ``'PUSHED'``, never
    ``'2'``).  This locks the value->label mapping for the four string combos: Amp Type, Cabinet
    Resonance, Master Key and Send Return Position.
    """
    r = load_map()["PATCH(1)"]
    ps = ParamSet()
    # AMP: TYPE (index 7) = 2 -> 'PUSHED'
    ps.set_block("PATCH%AMP", bytes([80, 90, 75, 60, 45, 30, 0, 2, 0, 0]))
    # OTHER: CABINET_RESONANCE = 1 -> 'MODERN', MASTER_KEY = 4 -> 'E'
    other = bytearray(r.block("OTHER").length)
    for i, p in enumerate(r.block("OTHER").params):
        if p.name.endswith("CABINET_RESONANCE"):
            other[p.off] = 1
        elif p.name.endswith("MASTER_KEY"):
            other[p.off] = 4
    ps.set_block("PATCH%OTHER", bytes(other))
    # SENDRETURN: POSITION = 2 -> 'PRE FV'
    sr = bytearray(r.block("SENDRETURN").length)
    for i, p in enumerate(r.block("SENDRETURN").params):
        if p.name.endswith("POSITION"):
            sr[p.off] = 2
    ps.set_block("PATCH%SENDRETURN", bytes(sr))

    cases = [
        ("AMP", "AMP_TYPE", "PUSHED"),
        ("OTHER", "CABINET_RESONANCE", "MODERN"),
        ("OTHER", "MASTER_KEY", "E"),
        ("SENDRETURN", "SENDRETURN_POSITION", "PRE FV"),
    ]
    for bname, suffix, expected in cases:
        rows = row_for_block(r, bname, ps)
        row = next(x for x in rows if x.param.name.endswith(suffix))
        assert row.kind == "combo"
        assert row.options and isinstance(row.options[0], str), \
            f"{suffix} should be string-labeled"
        assert combo_display_value(row) == expected
        # The old buggy preselect (str of the raw int) is NOT a valid label:
        assert str(row.value) not in row.options


def test_row_for_block_amp() -> None:
    """row_for_block builds the AMP rows with TYPE reordered to the top and the expected mix of knobs, one combo and two radios."""
    r = load_map()["PATCH(1)"]
    ps = _seeded_paramset(r, fill=1)          # raw[i] = i+1 for i in 0..9
    rows = row_for_block(r, "AMP", ps)
    assert len(rows) == 10
    assert all(isinstance(x, RowDesc) for x in rows)
    # TYPE is reordered to the top (p_index=7, raw[7]=8)
    assert rows[0].label == "TYPE"
    assert rows[0].p_index == 7
    assert rows[0].value == 8
    # GAIN is now second (p_index=0, raw[0]=1)
    assert rows[1].label == "GAIN"
    assert rows[1].p_index == 0
    assert rows[1].value == 1
    # AMP has 7 knobs, 1 combo (TYPE), 2 radios (VARIATION)
    kinds = [x.kind for x in rows]
    assert kinds.count("knob") == 7
    assert kinds.count("combo") == 1
    assert kinds.count("radio") == 2
    # after TYPE, the next six are GAIN/VOLUME/BASS/MIDDLE/TREBLE/PRESENCE
    assert all(k == "knob" for k in kinds[1:7])
    assert rows[0].block == "AMP"


def test_row_for_block_name_is_text() -> None:
    """The COM block produces a single text row carrying the decoded 16-character patch name."""
    r = load_map()["PATCH(1)"]
    ps = ParamSet()
    # _value_for looks up via ParamSet.block_key(region.name, block_name)
    bid = ParamSet.block_key(r.name, "COM")    # "PATCH%COM" (canonical)
    ps.set_block(bid, ParamSet.encode_patch_name("Hello"))
    rows = row_for_block(r, "COM", ps)
    assert len(rows) == 1
    assert rows[0].kind == "text"
    assert rows[0].value == "Hello"


def test_rows_for_region_covers_all_blocks() -> None:
    """rows_for_region covers every block of a region, one row per parameter."""
    r = load_map()["PATCH(1)"]
    ps = _seeded_paramset(r)
    rows = rows_for_region(r, ps)
    assert set(rows) == {b.name for b in r.blocks}
    for name, block_rows in rows.items():
        assert len(block_rows) == len(r.block(name).params)


def test_combo_options_are_discrete_range() -> None:
    """Combo options span the param's discrete range (max - min + 1); string-labeled combos are indexed by int, integer ones hold the raw value."""
    r = load_map()["PATCH(1)"]
    ps = _seeded_paramset(r)
    # Re-seed OTHER with in-bounds values (default ramp exceeds max for
    # CABINET_RESONANCE which has max=2).
    other = r.block("OTHER")
    # CHAIN[0-8]=3, CABINET_RESONANCE[0-2]=1, MASTER_KEY[0-11]=5
    ps.set_block(ParamSet.block_key(r.name, "OTHER"), bytes([3, 1, 5]))
    rows = row_for_block(r, "OTHER", ps)
    combos = [x for x in rows if x.kind == "combo"]
    assert combos, "expected at least one combo in OTHER block"
    for c in combos:
        assert c.options is not None
        expected_count = c.param.max - c.param.min + 1
        assert len(c.options) == expected_count
        if isinstance(c.options[0], str):
            # String labels: value is an int index into the options tuple
            assert isinstance(c.value, int)
            assert c.param.min <= c.value <= c.param.max
        else:
            # Integer fallback
            assert c.options[0] == c.param.min and c.options[-1] == c.param.max
            assert c.value in c.options


# ---------------------------------------------------------------------------
# widget kinds across the real address map
# ---------------------------------------------------------------------------

def _row(region_name: str, block: str, suffix: str) -> RowDesc:
    """Fetch one row of a block by a suffix of its raw param name.

    Args:
        region_name: region key in the address map, e.g. "PATCH(1)".
        block: block name within the region, e.g. "AMP".
        suffix: suffix of the raw param name the row must match.

    Returns:
        the first RowDesc whose param name ends with `suffix`.
    """
    r = load_map()[region_name]
    return next(x for x in row_for_block(r, block, ParamSet())
                if x.param.name.endswith(suffix))


@pytest.mark.parametrize("region", ["PATCH(1)", "SYSTEM"])
def test_no_sliders_and_every_sw_is_a_checkbox(region: str) -> None:
    """Every row in a region is a supported control kind, and every size-1 0..1 _SW param is a checkbox — no sliders exist."""
    r = load_map()[region]
    for b in r.blocks:
        for row in row_for_block(r, b.name, ParamSet()):
            assert row.kind in ("text", "constant", "color", "checkbox",
                                "radio", "combo", "knob"), row
            p = row.param
            if p.name.endswith("_SW") and p.size == 1 and p.max - p.min == 1:
                assert row.kind == "checkbox", p.name


@pytest.mark.parametrize("block,suffix,count,first,last", [
    ("AMP", "AMP_TYPE", 6, "ACOUSTIC", "BROWN"),
    ("BOOSTER(1)", "BOOSTER_TYPE", 23, "MID BOOST", "CENTA OD"),
    ("FX(4)", "FX_TYPE", 31, "T.WAH", "PEDAL BEND"),
    ("DELAY(1)", "DELAY_TYPE", 8, "DIGITAL", "SDE-3000"),
    ("REVERB(1)", "REVERB_TYPE", 5, "PLATE", "MODULATE"),
    ("PEDALFX_COM", "PEDALFX_COM_TYPE", 3, "PEDAL WAH", "WAH 95E"),
])
def test_type_params_are_dropdowns_with_katatone_strings(block, suffix, count,
                                                    first, last) -> None:
    """The TYPE param of each effect block is a combo whose options match the KATANA type list exactly."""
    row = _row("PATCH(1)", block, suffix)
    assert row.kind == "combo"
    assert len(row.options) == count
    assert (row.options[0], row.options[-1]) == (first, last)


def test_assign_target_lists_only_on_exact_length() -> None:
    """ASSIGN_KNOBS target params are combos with the KATANA assign-target lists, matched by exact param name (AUTO_WAH keeps its underscore)."""
    row = _row("PATCH(1)", "ASSIGN_KNOBS", "ASSIGN_KNOBS_BOOSTER")
    assert row.kind == "combo"
    assert row.options[:3] == ("PRESET", "DRIVE", "TONE")
    # AUTO_WAH: the suffix contains an underscore.
    row = _row("PATCH(1)", "ASSIGN_KNOBS", "ASSIGN_KNOBS_AUTO_WAH")
    assert row.kind == "combo" and row.options[1] == "RATE"


def test_block_titles_name_effect_variations() -> None:
    """block_title names each effect slot with its variation, and block_variation is None for plain blocks."""
    assert model.block_title("BOOSTER(1)") == "BOOSTER · GREEN"
    assert model.block_title("FX(2)") == "MOD · RED"
    assert model.block_title("FX(6)") == "FX · YELLOW"
    assert model.block_title("DELAY(4)") == "DELAY 2 · GREEN"
    assert model.block_title("AMP") == "AMP"
    assert model.block_variation("NS") is None


def test_fx_detail_rows_follow_fx_type() -> None:
    """FX detail rows follow the selected FX type: one key per type option, type-specific params, empty for an out-of-range index."""
    r = load_map()["PATCH(1)"]
    keys = model.fx_detail_groups(r, "FX_DETAIL(1)")
    fx_type = _row("PATCH(1)", "FX(1)", "FX_TYPE")
    assert len(keys) == len(fx_type.options) == 31
    assert keys[0] == "TWAH" and keys[23] == "2X2CHORUS"
    twah = model.fx_detail_rows(r, "FX_DETAIL(1)", 0, ParamSet())
    assert twah and all("_TWAH_" in x.param.name for x in twah)
    assert {x.label for x in twah} >= {"MODE", "SENS", "EFFECT LEVEL"}
    chorus = model.fx_detail_rows(r, "FX_DETAIL(1)", 23, ParamSet())
    assert chorus and all("_2X2CHORUS_" in x.param.name for x in chorus)
    assert model.fx_detail_rows(r, "FX_DETAIL(1)", 99, ParamSet()) == []


# ---------------------------------------------------------------------------
# group_blocks
# ---------------------------------------------------------------------------

def test_group_blocks_membership() -> None:
    """group_blocks places each PATCH(1) block under its expected display group."""
    r = load_map()["PATCH(1)"]
    names = [b.name for b in r.blocks if b.name != "COM"]
    groups = group_blocks(names)
    by_label = dict(groups)
    assert "AMP" in by_label["Amp"]
    assert "FX(1)" in by_label["MOD"]
    assert "FX(4)" in by_label["FX"]
    assert "DELAY(6)" in by_label["Delay"]
    assert "ASSIGN_FS" in by_label["Assign"]
    assert "SOLO_EQ" in by_label["Solo"]


def test_group_blocks_is_partition_and_ordered() -> None:
    """group_blocks is an order-preserving partition: every input block appears once, no group is empty, and the display order is stable."""
    r = load_map()["PATCH(1)"]
    names = [b.name for b in r.blocks if b.name != "COM"]
    groups = group_blocks(names)
    flat = [n for _, bl in groups for n in bl]
    # every input block appears exactly once (order-preserving partition)
    assert sorted(flat) == sorted(names)
    assert len(flat) == len(names)
    # no empty groups
    assert all(bl for _, bl in groups)
    # stable, known display order (subset present in PATCH(1))
    labels = [g for g, _ in groups]
    assert labels.index("Amp") < labels.index("Booster") < labels.index("Delay")


# ---------------------------------------------------------------------------
# smoke: Flet modules byte-compile even when flet is not installed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod", FLET_MODULES)
def test_flet_module_byte_compiles(mod: str) -> None:
    """Each Flet widget module byte-compiles even when flet is not installed."""
    path = GUI_DIR / mod
    assert path.exists(), f"missing gui module: {mod}"
    py_compile.compile(str(path), doraise=True)
