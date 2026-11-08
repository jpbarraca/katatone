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
    assert by_label["AMP"] == 2
    # ...and get_param (used by every row) resolves the canonical key.
    assert ps.get_param(r, "AMP", 0) == 80


def test_regression_string_combo_maps_value_to_label() -> None:
    """String-labeled combos must preselect a label, not the index.

    A Flet Dropdown matches ``value`` to an option *label*, so the value set
    for a string combo must be the label itself (e.g. ``'Pushed'``, never
    ``'2'``).  This locks the value->label mapping for the four string combos: Amp Type, Cabinet
    Resonance, Master Key and Send Return Position.
    """
    r = load_map()["PATCH(1)"]
    ps = ParamSet()
    # AMP: TYPE (index 7) = 2 -> 'Pushed'
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
        ("AMP", "AMP_TYPE", "Pushed"),
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
    # The variations are checkboxes first, then the amp type (p_index=7, raw[7]=8)
    assert [r.label for r in rows[:3]] == ["PRE VARIATION", "PWR VARIATION", "AMP"]
    assert [r.kind for r in rows[:3]] == ["checkbox", "checkbox", "combo"]
    assert rows[2].p_index == 7
    assert rows[2].value == 8
    # GAIN is the first knob (p_index=0, raw[0]=1)
    assert rows[3].label == "GAIN"
    assert rows[3].p_index == 0
    assert rows[3].value == 1
    # AMP has 7 knobs, 1 combo (the amp), 2 checkboxes (the variations)
    kinds = [x.kind for x in rows]
    assert kinds.count("knob") == 7
    assert kinds.count("combo") == 1
    assert kinds.count("checkbox") == 2 and kinds.count("radio") == 0
    # after the amp, the next six are GAIN/VOLUME/BASS/MIDDLE/TREBLE/PRESENCE
    assert all(k == "knob" for k in kinds[3:9])
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
    ("AMP", "AMP_TYPE", 6, "Acoustic", "Brown"),
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


def test_the_amp_is_a_dropdown_of_named_amps_labelled_amp() -> None:
    row = _row("PATCH(1)", "AMP", "AMP_TYPE")
    assert row.kind == "combo" and row.label == "AMP"
    assert row.options == ("Acoustic", "Clean", "Pushed", "Crunch", "Lead", "Brown")
    assert model.display_label("PRMID_PATCH_AMP_TYPE", "AMP") == "AMP"
    assert model.display_label("PRMID_PATCH_BOOSTER_TYPE", "BOOSTER(1)") == "TYPE"   # others unchanged


def test_amp_variations_are_checkboxes_left_of_the_amp_type() -> None:
    """Checked means B; the row values stay 0 (A) / 1 (B), as the radios had."""
    from katatone.model.paramset import ParamSet
    r = load_map()["PATCH(1)"]
    rows = row_for_block(r, "AMP", ParamSet())
    pre = next(x for x in rows if x.param.name.endswith("PREAMP_VARIATION"))
    pwr = next(x for x in rows if x.param.name.endswith("POWERAMP_VARIATION"))
    assert pre.kind == pwr.kind == "checkbox" and pre.options == pwr.options == ("A", "B")
    assert (pre.param.min, pre.param.max) == (0, 1)
    assert rows.index(pre) < rows.index(pwr) < next(i for i, x in enumerate(rows) if x.kind == "combo")


def test_the_amp_card_puts_its_checkboxes_and_the_amp_type_on_one_line() -> None:
    from katatone.gui.patch_editor import PatchEditor
    assert PatchEditor._body_style("AMP") == "two_lines"


def test_assign_blocks_are_regrouped_by_effect() -> None:
    from katatone.address.map import load_map
    from katatone.model.paramset import ParamSet
    region = load_map()["PATCH(1)"]
    names = [b.name for b in region.blocks if b.name.startswith(("ASSIGN", "PATCH_KNOB"))]
    effects, rest = model.assign_by_effect(region, ParamSet(), names)
    titles = [t for t, _ in effects]
    assert titles[:3] == ["Booster", "Delay", "Reverb"] and len(titles) == 34
    booster = dict(effects)["Booster"]
    labels = [r.label for r in booster]
    assert labels[:4] == ["Knob assign", "Exp pedal detail", "Exp pedal min", "Exp pedal max"]
    assert "GA-FC 2 · EXP 3 max" in labels
    assert all(r.param.name.endswith("_BOOSTER") for r in booster)
    assert len(booster) == 4 + 2 * 3 * 3                   # pedal + 2 GA-FC x 3 pedals x 3
    # nothing is lost: what is not per effect stays as its own blocks
    assert "ASSIGN_FS" in rest and "ASSIGN_KNOBS" not in rest
    per_effect = sum(len(rows) for _, rows in effects)
    assert per_effect == 34 * len(booster)


def test_the_pedal_function_blocks_share_one_card() -> None:
    from katatone.address.map import load_map
    from katatone.model.paramset import ParamSet
    region = load_map()["PATCH(1)"]
    names = [b.name for b in region.blocks if b.name.startswith(("ASSIGN", "PATCH_KNOB"))]
    _, rest = model.assign_by_effect(region, ParamSet(), names)
    rows, rest = model.assign_functions(region, ParamSet(), rest)
    assert [r.label for r in rows] == [
        "Exp pedal function", "GA-FC 1 · EXP 1 function", "GA-FC 2 · EXP 1 function",
        "GA-FC 1 · EXP 2 function", "GA-FC 2 · EXP 2 function",
        "GA-FC 1 · EXP 3 function", "GA-FC 2 · EXP 3 function"]
    assert not any(n.endswith(("_FUNC", "_FUNC(1)", "_FUNC(2)")) for n in rest)
    assert "ASSIGN_FS" in rest


def test_the_gafc_footswitch_cards_are_half_width() -> None:
    import types
    from katatone.address.map import load_map
    from katatone.gui import patch_editor as pe
    from katatone.model.paramset import ParamSet
    region = load_map()["PATCH(1)"]
    ed = object.__new__(pe.PatchEditor)
    ed.app = types.SimpleNamespace(ps=ParamSet())
    ed._active_colors = {}
    asked = {}
    real = pe.PatchEditor._block_card
    ed._block_card = lambda r, b, **kw: asked.setdefault(b, kw) and real(ed, r, b, **kw)
    blocks = [b.name for b in region.blocks if b.name.startswith(("ASSIGN", "PATCH_KNOB"))]
    ed._assign_cards(region, blocks)
    assert asked["ASSIGN_GAFCFS(1)"] == asked["ASSIGN_GAFCFS(2)"] == {"wide": False}
    assert asked["PATCH_KNOB_READONLY"] == asked["PATCH_KNOB_SOLO_DELAY_READONLY"] == {"wide": False}
    assert asked["ASSIGN_FS"] == {"wide": True}              # a row of its own
    card = real(ed, region, "ASSIGN_GAFCFS(1)", wide=False)
    assert card.col == {"xs": 12, "lg": 6}


def test_the_pitch_shifter_shows_direct_mix_then_voice_1_then_voice_2() -> None:
    from katatone.address.map import load_map
    from katatone.model.paramset import ParamSet
    region = load_map()["PATCH(1)"]
    keys = model.fx_detail_groups(region, "FX_DETAIL(1)")
    rows = model.fx_detail_rows(region, "FX_DETAIL(1)", keys.index("PITCHSHIFT"), ParamSet())
    knobs = [r.label for r in rows if r.kind == "knob"]
    assert knobs == ["DIRECT MIX", "PITCH1", "FINE1", "PRE DELAY1", "LEVEL1", "FEEDBACK1",
                     "PITCH2", "FINE2", "PRE DELAY2", "LEVEL2"]
    assert rows[0].label == "VOICE"                            # the selector stays first
    other = model.fx_detail_rows(region, "FX_DETAIL(1)", 0, ParamSet())
    assert [r.p_index for r in other] == sorted(r.p_index for r in other)   # others untouched


def test_an_effects_assign_card_has_a_gap_between_pedal_and_gafc_rows() -> None:
    import types
    from katatone.address.map import load_map
    from katatone.gui import patch_editor as pe
    from katatone.model.paramset import ParamSet
    region = load_map()["PATCH(1)"]
    names = [b.name for b in region.blocks if b.name.startswith("ASSIGN")]
    effects, _ = model.assign_by_effect(region, ParamSet(), names)
    ed = object.__new__(pe.PatchEditor)
    ed._set_param = lambda *a: None
    card = ed._effect_card(*effects[0])
    gaps = [c for c in _flatten(card) if getattr(c, "height", None) == pe._ASSIGN_GAP]
    assert len(gaps) == 1


def _flatten(c):
    yield c
    for attr in ("content", "controls"):
        sub = getattr(c, attr, None)
        for x in (sub if isinstance(sub, list) else [sub] if sub is not None else []):
            yield from _flatten(x)


def test_the_general_assign_cards_start_with_the_footswitch() -> None:
    import types
    from katatone.address.map import load_map
    from katatone.gui import patch_editor as pe
    from katatone.model.paramset import ParamSet
    region = load_map()["PATCH(1)"]
    ed = object.__new__(pe.PatchEditor)
    ed.app = types.SimpleNamespace(ps=ParamSet())
    ed._active_colors = {}
    ed._set_param = lambda *a: None
    names = [b.name for b in region.blocks if b.name.startswith(("ASSIGN", "PATCH_KNOB"))]
    titles = [_titles(c)[0] for c in ed._assign_cards(region, names)]
    assert titles == ["ASSIGN FS", "PEDAL FUNCTIONS", "ASSIGN GAFCFS 1", "ASSIGN GAFCFS 2",
                      "PATCH KNOB READONLY", "PATCH KNOB SOLO DELAY READONLY"]


def _titles(c):
    out = [c.value] if type(c).__name__ == "Text" else []
    for attr in ("content", "controls"):
        sub = getattr(c, attr, None)
        for x in (sub if isinstance(sub, list) else [sub] if sub is not None else []):
            out += _titles(x)
    return out


def test_the_eq_blocks_put_their_knobs_on_one_line() -> None:
    from katatone.gui import patch_editor as pe
    for name in ("EQ_PEQ(1)", "EQ_PEQ(2)", "EQ_GE10(1)", "EQ_GE10(2)"):
        assert pe.PatchEditor._body_style(name) == "two_lines"
    assert pe.PatchEditor._body_style("EQ_EACH(1)") == "rows"       # no knobs: unchanged



def test_the_delay_cards_pair_the_two_delays_colour_by_colour() -> None:
    import types
    from katatone.address.map import load_map
    from katatone.gui import patch_editor as pe
    from katatone.model.paramset import ParamSet
    region = load_map()["PATCH(1)"]
    ed = object.__new__(pe.PatchEditor)
    ed.app = types.SimpleNamespace(ps=ParamSet(), region_for=lambda n: region, patch_number=0)
    ed._active_colors = {}
    ed._set_param = lambda *a: None
    ed._widgets = {}
    names = [b.name for b in region.blocks if b.name.startswith("DELAY")]
    ed._tab_specs = [("Delay", names)]
    ed._tab_view = types.SimpleNamespace(controls=[types.SimpleNamespace(content=None)])
    ed._built = set()
    ed._build_tab(0)
    layout = pe.PatchEditor._TAB_LAYOUTS["Delay"]
    assert len(layout) == 3 and all(len(row) == 2 for row in layout)
    for first, second in layout:                            # the same colour on a line
        assert model.block_variation(first)[1] == model.block_variation(second)[1]
        assert model.block_variation(first)[0] == "DELAY" and model.block_variation(second)[0] == "DELAY 2"
    rows = ed._tab_view.controls[0].content.controls[0].controls
    assert len(rows) == 3 and all(len(r.controls) == 2 for r in rows)


def test_an_fx_card_has_the_type_on_one_line_and_the_knobs_on_the_next() -> None:
    import types
    import flet as ft
    from katatone.address.map import load_map
    from katatone.gui import patch_editor as pe
    from katatone.model.paramset import ParamSet
    region = load_map()["PATCH(1)"]
    ps = ParamSet()
    ed = object.__new__(pe.PatchEditor)
    ed.app = types.SimpleNamespace(ps=ps, region_for=lambda n: region, patch_number=0)
    ed._active_colors = {}
    ed._set_param = lambda *a: None
    ed._widgets = {}
    keys = model.fx_detail_groups(region, "FX_DETAIL(1)")
    ps.set_param(region, "FX(4)", 0, keys.index("PITCHSHIFT"))
    card = ed._fx_card(region, "FX(4)")
    column = next(c for c in _flatten(card)
                  if isinstance(c, ft.Column) and len(c.controls) == 2
                  and all(isinstance(x, ft.Row) for x in c.controls))
    top, knobs = column.controls
    kinds = lambda row: {type(c).__name__ for c in _flatten(row)}      # noqa: E731
    assert "Dropdown" in kinds(top) and "Knob" not in kinds(top)
    assert "Knob" in kinds(knobs) and "Dropdown" not in kinds(knobs)
    assert len([c for c in _flatten(knobs) if type(c).__name__ == "Knob"]) == 10


def test_the_two_parametric_eq_cards_are_half_width() -> None:
    import types
    from katatone.address.map import load_map
    from katatone.gui import patch_editor as pe
    from katatone.model.paramset import ParamSet
    region = load_map()["PATCH(1)"]
    ed = object.__new__(pe.PatchEditor)
    ed.app = types.SimpleNamespace(ps=ParamSet(), region_for=lambda n: region, patch_number=0)
    ed._active_colors = {}
    ed._set_param = lambda *a: None
    ed._widgets = {}
    names = [b.name for b in region.blocks if b.name.startswith("EQ")]
    ed._tab_specs = [("EQ", names)]
    ed._tab_view = types.SimpleNamespace(controls=[types.SimpleNamespace(content=None)])
    ed._built = set()
    ed._build_tab(0)
    grid = ed._tab_view.controls[0].content.controls[0]
    cols = [c.col["lg"] for c in grid.controls]
    assert [cols[i] for i, n in enumerate(names) if n.startswith("EQ_PEQ")] == [6, 6]
    assert [cols[i] for i, n in enumerate(names) if n.startswith("EQ_GE10")] == [12, 12]


def _amp_tab():
    import types
    from katatone.address.map import load_map
    from katatone.gui import patch_editor as pe
    from katatone.model.paramset import ParamSet
    region = load_map()["PATCH(1)"]
    ed = object.__new__(pe.PatchEditor)
    ed.app = types.SimpleNamespace(ps=ParamSet(), region_for=lambda n: region, patch_number=0)
    ed._active_colors = {}
    ed._set_param = lambda *a: None
    ed._widgets = {}
    names = [b.name for b in region.blocks if model._tab_for(b.name) == "Amp"]
    ed._tab_specs = [("Amp", names)]
    ed._tab_view = types.SimpleNamespace(controls=[types.SimpleNamespace(content=None)])
    ed._built = set()
    ed._build_tab(0)
    return ed, ed._tab_view.controls[0].content


def test_the_amp_tab_has_the_chain_above_an_amp_card_holding_other_and_ns() -> None:
    ed, content = _amp_tab()
    rows = content.controls[0].controls                   # the card rows, top to bottom
    first = [t for t in _titles(rows[0])]
    second = [t for t in _titles(rows[1])][:1]
    assert second == ["SIGNAL CHAIN"] and first[0] == "AMP"
    texts = " ".join(t.upper() for t in first)
    assert all(w in texts for w in ("CABINET RESONANCE", "MASTER KEY", "THRESHOLD", "RELEASE"))
    all_titles = [_titles(r)[0] for r in rows]
    assert all_titles.count("AMP") == 1 and "OTHER" not in all_titles and "NS" not in all_titles
    assert all_titles[0] == "AMP" and "EFFECTS" not in all_titles       # the amp on top, no Effects card


def test_the_chain_order_follows_the_preset_and_the_positions() -> None:
    from katatone.gui import chain
    default = chain.order(2, 0, 0, 0, 0)
    assert default == ["Pedal FX", "Booster", "Mod", "FX", "EQ 1", "EQ 2", "AMP",
                       "Foot Volume", "Send/Return", "Delay", "Delay 2", "Reverb"]
    assert chain.order(0, 0, 1, 2, 1) == ["Booster", "EQ 1", "AMP", "EQ 2", "Pedal FX", "Send/Return",
                                          "Mod", "FX", "Foot Volume", "Delay", "Delay 2", "Reverb"]
    assert chain.order(8, 1, 1, 1, 0)[:5] == ["Pedal FX", "Mod", "FX", "Booster", "Delay"]
    assert chain.order(8, 1, 1, 1, 0)[-2:] == ["Reverb", "Send/Return"]
    for preset in range(9):                              # every preset plays each effect once
        names = chain.order(preset, 0, 1, 0, 1)
        assert sorted(names) == sorted(set(names)) and "AMP" in names and len(names) == 12


def test_the_chain_diagram_shows_numbered_square_blocks_with_the_amp_highlighted() -> None:
    from katatone.gui import chain, theme
    ed, content = _amp_tab()
    card = content.controls[0].controls[1]
    titles = _titles(card)
    assert titles.count("AMP") == 1 and titles.index("BOOSTER") < titles.index("AMP")
    blocks = [c for c in _flatten(card) if getattr(c, "width", None) in (chain.BLOCK_SIZE[0], chain.AMP_SIZE[0])]
    assert len(blocks) == 12 and sum(b.width == chain.AMP_SIZE[0] for b in blocks) == 1
    amp = next(b for b in blocks if b.width == chain.AMP_SIZE[0])
    assert amp.bgcolor == theme.ACCENT and amp.height == chain.AMP_SIZE[1]
    assert all(b.bgcolor != theme.ACCENT and b.border_radius < 6 for b in blocks if b is not amp)


def test_changing_a_chain_setting_redraws_the_diagram() -> None:
    import types
    import flet as ft
    ed, content = _amp_tab()
    card = content.controls[0].controls[1]
    names = lambda: [t for t in _titles(card) if t.isupper() and t != "SIGNAL CHAIN"]   # noqa: E731
    dropdowns = [c for c in _flatten(card) if type(c).__name__ == "Dropdown"]
    assert len(dropdowns) == 5
    before = names()
    chain_dd = dropdowns[0]                                   # the chain preset: 1 in an empty patch
    assert before.index("AMP") < before.index("MOD")          # chain 1: Booster, amp, Mod, ...
    chain_dd.value = "4"
    chain_dd.on_select(types.SimpleNamespace(control=chain_dd))
    after = names()
    assert after.index("BOOSTER") < after.index("MOD") < after.index("FX") < after.index("DELAY") < after.index("AMP")



def test_the_amp_card_lines_and_the_noise_suppressor_names() -> None:
    import flet as ft
    ed, content = _amp_tab()
    amp = content.controls[0].controls[0]
    rows = [c for c in _flatten(amp) if isinstance(c, ft.Row)]
    kinds = [[type(x).__name__ for x in r.controls] for r in rows]
    # line 1: the two variation switches, then amp type, cabinet resonance and master key
    assert ["ToggleButton", "ToggleButton", "Column", "Column", "Column"] in kinds
    # line 2: the amp's seven knobs, a gap, the noise suppressor's switch and its two knobs
    assert ["Knob"] * 7 + ["Container", "ToggleButton", "Knob", "Knob"] in kinds
    shown = _titles(amp)
    switches = [c.label for c in _flatten(amp) if type(c).__name__ == "ToggleButton"]
    assert "NOISE SUPPRESSOR" in switches                     # the NS enable switch
    assert "NS THRESHOLD" in shown and "NS RELEASE" in shown
    assert not any(t.startswith("Chain ") for t in _titles(content.controls[0].controls[1]))


def test_a_click_goes_off_green_yellow_red_off() -> None:
    from katatone.gui import chain
    state = (False, 1)                                      # off, red kept from before
    seen = []
    for _ in range(5):
        state = chain.next_state(*state)
        seen.append(state)
    assert seen == [(True, 0), (True, 2), (True, 1), (False, 1), (True, 0)]      # green, yellow, red
    assert chain.next_state(True, 0, has_color=False) == (False, 0)       # no colour: on and off
    assert chain.next_state(False, None, has_color=False) == (True, None)


def test_clicking_an_effect_in_the_diagram_switches_it_and_cycles_its_colour() -> None:
    import types
    from katatone.gui import chain, theme
    ed, content = _amp_tab()
    ps, region = ed.app.ps, ed.app.region_for(0)
    ed._set_param = lambda row, v: ps.set_param(region, row.block, row.p_index, v)
    ed.tabs = types.SimpleNamespace(selected_index=0)
    card = content.controls[0].controls[1]

    def block(name):
        return next(c for c in _flatten(card) if getattr(c, "on_click", None) and name in _titles(c))

    def now(label_index):
        return ps.get_param(region, "SW", label_index), ps.get_param(region, "COLOR", label_index)

    assert now(0)[0] == 0 and block("BOOSTER").opacity < 1            # booster starts off, dimmed
    seen = []
    for _ in range(4):
        block("BOOSTER").on_click(None)
        seen.append(now(0))
    assert seen == [(1, 0), (1, 2), (1, 1), (0, 1)]                    # green, yellow, red, off
    block("BOOSTER").on_click(None)
    assert block("BOOSTER").border.top.color == "#40c057" and block("BOOSTER").opacity == 1
    block("DELAY 2").on_click(None)
    assert ps.get_param(region, "SW", 4) == 1                          # Delay 2: on / off only
    assert not [c for c in _flatten(card) if "AMP" in _titles(c) and getattr(c, "on_click", None)]


def test_each_chain_dropdown_moves_only_its_own_element() -> None:
    import types
    from katatone.gui import chain
    ed, content = _amp_tab()
    card = content.controls[0].controls[1]
    dds = {n: d for n, d in zip(("chain", "pedal", "eq1", "eq2", "sr"),
                                [c for c in _flatten(card) if type(c).__name__ == "Dropdown"])}
    names = lambda: [t for t in _titles(card) if t in {n.upper() for n in chain.order(0, 0, 0, 0, 0)}]   # noqa: E731

    def pick(key, value):
        dds[key].value = value
        dds[key].on_select(types.SimpleNamespace(control=dds[key]))

    assert names() == [n.upper() for n in chain.order(0, 0, 0, 0, 0)]
    pick("pedal", "POST AMP")
    assert names() == [n.upper() for n in chain.order(0, 0, 0, 0, 1)]
    assert [d.value for d in dds.values()] == ["1", "POST AMP", "AMP IN", "AMP IN", "POST FV"]   # EQ 1 untouched
    pick("eq1", "AMP OUT")
    assert names() == [n.upper() for n in chain.order(0, 1, 0, 0, 1)]
    pick("eq2", "AMP OUT")
    pick("sr", "POST REV")
    assert names() == [n.upper() for n in chain.order(0, 1, 1, 1, 1)]
