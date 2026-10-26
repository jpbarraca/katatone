"""Importing a KATANA MkII liveset: the patches are converted to the Gen 3 layout."""
from __future__ import annotations

import json

import pytest

from katatone.address.map import load_map
from katatone.library.store import Library
from katatone.liveset import mkii
from katatone.liveset.tsl import Tsl
from katatone.model.paramset import ParamSet

DATA = mkii._data()
ENTRIES, BY_KEY = mkii._entries()
REGION = load_map()["PATCH"]


def blank_mkii() -> dict[str, bytearray]:
    """A MkII patch with every block present and zeroed."""
    return {name: bytearray(size) for name, size in DATA["mk2_blocks"].items()}


def set_mkii(blocks: dict[str, bytearray], key: str, value: int) -> None:
    """Set a MkII parameter by key (1x7 and 2x7 values)."""
    e = BY_KEY[key]
    block, index = e["mk2"]
    if e["type"] == "2x7":
        blocks[block][index], blocks[block][index + 1] = value >> 7, value & 127
    else:
        blocks[block][index] = value


def convert(blocks: dict[str, bytearray], name: str = "T") -> mkii.Conversion:
    return mkii.convert_patch({k: bytes(v) for k, v in blocks.items()}, name)


def gen3(done: mkii.Conversion, key: str, bank: int = 0) -> int:
    """A converted value, read raw from the Gen 3 patch (1x7 or nibbles)."""
    e = BY_KEY[key]
    raw = done.patch.block(e["mk3"][bank])
    n = mkii._SIZE_GEN3[e["type"]]
    chunk = raw[e["i3"]:e["i3"] + n]
    if e["type"] == "1x7":
        return chunk[0]
    value = 0
    for nibble in chunk:
        value = (value << 4) | nibble
    return value


def mkii_liveset(*patches: dict, name: str = "MkII set", memo="") -> dict:
    rows = [{"memo": memo, "paramSet": {k: [f"{b:02X}" for b in v] for k, v in p.items()}} for p in patches]
    return {"name": name, "formatRev": "0002", "device": "KATANA MkII", "data": [rows]}


# ---- the conversion tables ----

def test_the_tables_agree_with_the_gen_3_address_map() -> None:
    sizes = {"PATCH%" + b.name: (mkii.DETAIL_LENGTH if b.name.startswith("FX_DETAIL") else b.length)
             for b in REGION.blocks}
    assert len(sizes) == 80
    keys = [e["key"] for e in ENTRIES + DATA["gen3_only"]]
    assert len(keys) == len(set(keys))
    for e in ENTRIES + DATA["gen3_only"]:
        for block in e.get("mk3", []):
            assert block in sizes, (e["key"], block)
            assert e["i3"] + mkii._SIZE_GEN3[e["type"]] <= sizes[block], (e["key"], block, e["i3"])
        if "mk2" in e:
            block, index = e["mk2"]
            assert index + mkii._SIZE[e["type"]] <= DATA["mk2_blocks"][block], (e["key"], block, index)


def test_the_rules_name_parameters_and_tables_that_exist() -> None:
    rules = DATA["rules"]
    for v in rules["variants"]:
        assert v["source"] in BY_KEY and v["target"] in BY_KEY and len(BY_KEY[v["target"]]["mk3"]) == 3
        assert v["enum"] is None or v["enum"] in DATA["enums"]
    for g in rules["remap"]:
        assert g["enum"] in DATA["enums"] and all(k in BY_KEY for k in g["keys"])
    assert all(k in BY_KEY for k in rules["decrement"])
    assert all(len(rows) > 3 and all(len(r) == 3 for r in rows) for rows in DATA["enums"].values())


def test_every_gen_3_position_is_a_real_parameter_of_the_address_map() -> None:
    """Cross-check of the tables against the map made from Tone Studio: a position either is the start
    of a parameter there or lies in the part of a detail block the map does not describe."""
    starts = {"PATCH%" + b.name: {p.off for p in b.params} for b in REGION.blocks}
    off = [(e["key"], blk, e["i3"]) for e in ENTRIES for blk in e.get("mk3", [])
           if e["i3"] not in starts[blk] and blk != "PATCH%COM" and "FX_DETAIL" not in blk]
    assert off == []


# ---- converting patches ----

def test_a_patch_has_all_80_blocks_at_the_right_sizes() -> None:
    done = convert(blank_mkii())
    assert len(done.patch.blocks()) == 80
    for b in REGION.blocks:
        want = mkii.DETAIL_LENGTH if b.name.startswith("FX_DETAIL") else b.length
        assert len(done.patch.block("PATCH%" + b.name)) == want, b.name


def test_the_name_is_carried_over() -> None:
    blocks = blank_mkii()
    blocks["UserPatch%PatchName"][:] = ParamSet.encode_patch_name("My Tone").ljust(16)
    assert ParamSet.patch_name(convert(blocks).patch.block("PATCH%COM")) == "My Tone"


def test_values_are_copied_to_all_three_variants() -> None:
    blocks = blank_mkii()
    set_mkii(blocks, "OD_DS_DRIVE", 77)
    done = convert(blocks)
    assert [gen3(done, "OD_DS_DRIVE", b) for b in range(3)] == [77, 77, 77]


def test_a_value_with_a_single_place_goes_there() -> None:
    single = next(e for e in ENTRIES if e["type"] == "1x7" and len(e.get("mk3", [])) == 1
                  and e["key"] not in DATA["rules"]["decrement"] and e["key"] != "PREAMP_A_TYPE"
                  and e["mk2"][0] == "UserPatch%Patch_0")
    blocks = blank_mkii()
    set_mkii(blocks, single["key"], 45)
    done, base = convert(blocks), convert(blank_mkii())
    assert gen3(done, single["key"]) == 45
    changed = [(b, i) for b in done.patch.blocks()
               for i, (x, y) in enumerate(zip(done.patch.block(b), base.patch.block(b))) if x != y]
    assert changed == [(single["mk3"][0], single["i3"])]


def test_the_gain_is_the_knob_position_not_the_stored_value() -> None:
    blocks = blank_mkii()
    set_mkii(blocks, "PREAMP_A_GAIN", 91)         # stored on the MkII as 20 + 0.8 * knob
    set_mkii(blocks, "PRM_KNOB_POS_GAIN", 89)
    assert gen3(convert(blocks), "PREAMP_A_GAIN") == 89


@pytest.mark.parametrize("mkii_value,label,gen3_value", [
    (8, "Clean", 1), (11, "Crunch", 3), (24, "Lead", 4), (23, "Brown", 5), (1, "Acoustic", 0),
    (0, "Natural Clean", 1), (2, "Combo Crunch", 3), (4, "Hi-Gain Stack", 4), (5, "Power Drive", 5),
])
def test_amp_types_become_the_gen_3_ones(mkii_value, label, gen3_value) -> None:
    blocks = blank_mkii()
    set_mkii(blocks, "PREAMP_A_TYPE", mkii_value)
    done = convert(blocks, "Tone")
    assert gen3(done, "PREAMP_A_TYPE") == gen3_value
    if label not in ("Clean", "Crunch", "Lead", "Brown", "Acoustic"):          # an approximation: say so
        assert any(f"amp type {label} became" in n and n.startswith("Tone: ") for n in done.notes)
    else:
        assert not any("amp type" in n for n in done.notes)


def test_an_amp_type_without_an_equivalent_is_reported_and_not_guessed() -> None:
    blocks = blank_mkii()
    set_mkii(blocks, "PREAMP_A_TYPE", 99)
    done = convert(blocks)
    assert any("amp type 99 of the MkII has no Gen 3 equivalent" in n for n in done.notes)
    assert gen3(done, "PREAMP_A_TYPE") == mkii._defaults()["PATCH%AMP"][BY_KEY["PREAMP_A_TYPE"]["i3"]]


def test_the_type_of_each_colour_comes_from_its_own_assignment() -> None:
    blocks = blank_mkii()
    set_mkii(blocks, "OD_DS_TYPE", 0)
    set_mkii(blocks, "FXBOX_ASGN_FX1A_G", 0)      # booster: MkII Mid Boost
    set_mkii(blocks, "FXBOX_ASGN_FX1A_R", 1)      # Clean Boost
    set_mkii(blocks, "FXBOX_ASGN_FX1A_Y", 2)      # Treble Boost
    done = convert(blocks)
    table = {mk2: mk3 for _, mk2, mk3 in reversed(DATA["enums"]["booster"])}
    assert [gen3(done, "OD_DS_TYPE", b) for b in range(3)] == [table[0], table[1], table[2]]


def test_effect_types_of_the_slots_are_renumbered_per_colour() -> None:
    blocks = blank_mkii()
    rows = {mk2: mk3 for _, mk2, mk3 in reversed(DATA["enums"]["mod"])}
    for key, value in (("FXBOX_ASGN_FX1B_G", 0), ("FXBOX_ASGN_FX1B_R", 1), ("FXBOX_ASGN_FX1B_Y", 2)):
        set_mkii(blocks, key, value)
    done = convert(blocks)
    assert [gen3(done, "FX1_FX_TYPE", b) for b in range(3)] == [rows[0], rows[1], rows[2]]


def test_delay_and_reverb_types_follow_their_assignments_and_the_layer_mode_is_copied() -> None:
    blocks = blank_mkii()
    set_mkii(blocks, "FXBOX_ASGN_FX2A_Y", 2)
    set_mkii(blocks, "FXBOX_ASGN_FX3_R", 3)
    set_mkii(blocks, "FXBOX_LAYER_FX3_G", 1)
    done = convert(blocks)
    delay = {mk2: mk3 for _, mk2, mk3 in reversed(DATA["enums"]["delay"])}
    reverb = {mk2: mk3 for _, mk2, mk3 in reversed(DATA["enums"]["reverb"])}
    assert gen3(done, "DELAY_TYPE", 2) == delay[2]
    assert gen3(done, "REVERB_TYPE", 1) == reverb[3]
    assert gen3(done, "PRMID_PATCH_REVERB_LAYER_MODE", 0) == 1


def test_values_that_only_the_mkii_has_are_not_carried_over() -> None:
    led = BY_KEY["PRM_LED_STATE_DELAY"]
    assert "mk3" not in led                                       # the LEDs have no place on the Gen 3
    blocks = blank_mkii()
    set_mkii(blocks, "PRM_LED_STATE_DELAY", 3)
    assert convert(blocks).patch.blocks() == convert(blank_mkii()).patch.blocks()
    a, b = convert(blocks), convert(blank_mkii())
    assert all(a.patch.block(k) == b.patch.block(k) for k in a.patch.blocks())


def test_the_contour_switch_becomes_the_selected_contour_plus_one() -> None:
    blocks = blank_mkii()
    set_mkii(blocks, "PRM_CONTOUR_SW", 1)
    set_mkii(blocks, "PRM_CONTOUR_SELECT", 2)
    assert gen3(convert(blocks), "PRM_CONTOUR_SW") == 3
    off = blank_mkii()
    set_mkii(off, "PRM_CONTOUR_SELECT", 2)                        # switched off: stays off
    assert gen3(convert(off), "PRM_CONTOUR_SW") == 0


def test_pedal_and_knob_assignments_are_renumbered() -> None:
    blocks = blank_mkii()
    set_mkii(blocks, "KNOB_ASSIGN_REVERB", 1)
    table = {mk2: mk3 for _, mk2, mk3 in reversed(DATA["enums"]["assign_reverb"])}
    assert gen3(convert(blocks), "KNOB_ASSIGN_REVERB") == table[1]


def test_a_two_byte_value_becomes_four_nibbles() -> None:
    two = next(e for e in ENTRIES if e["type"] == "2x7" and "mk3" in e)
    blocks = blank_mkii()
    set_mkii(blocks, two["key"], 300)
    done = convert(blocks)
    entry = BY_KEY[two["key"]]
    raw = done.patch.block(entry["mk3"][0])[entry["i3"]:entry["i3"] + 4]
    assert list(raw) == [0, 1, 2, 12]                           # 0x012C


def test_a_patch_with_missing_blocks_still_converts() -> None:
    done = mkii.convert_patch({"UserPatch%PatchName": ParamSet.encode_patch_name("Only").ljust(16)})
    assert ParamSet.patch_name(done.patch.block("PATCH%COM")) == "Only" and len(done.patch.blocks()) == 80


def test_the_default_patch_the_conversion_starts_from_is_inside_every_range() -> None:
    ps = ParamSet()
    for name, data in mkii._defaults().items():
        ps.set_block(name, data)
    for b in REGION.blocks:
        raw = ps.block("PATCH%" + b.name)
        for j, p in enumerate(b.params):
            if p.size != 16 and p.off + p.size <= len(raw) and isinstance(p.init, int):
                assert ps.get_param(REGION, b.name, j) == p.init, (b.name, p.name)


# ---- livesets and the library ----

def test_a_liveset_is_converted_with_its_name_memos_and_a_note_in_the_memo() -> None:
    a, b = blank_mkii(), blank_mkii()
    a["UserPatch%PatchName"][:] = ParamSet.encode_patch_name("One").ljust(16)
    set_mkii(a, "PREAMP_A_TYPE", 2)
    done = mkii.convert_liveset(mkii_liveset(a, b, name="Gig", memo="my memo"))
    assert (done.tsl.name, done.tsl.device, done.tsl.format_rev) == ("Gig", "KATANA Gen3", "0000")
    assert len(done.tsl) == 2 and done.tsl.patch_name(0) == "One"
    assert done.tsl.memos[0].startswith("my memo\nConverted from KATANA MkII by KataTone.")
    assert "Combo Crunch became Crunch" in done.tsl.memos[0] and "Combo Crunch became Crunch" in done.notes[0]
    assert done.tsl.memos[1].startswith("my memo\nConverted from KATANA MkII by KataTone.")


def test_a_tone_central_memo_object_is_left_alone() -> None:
    memo = {"isToneCentralPatch": True}
    ls = mkii_liveset(blank_mkii())
    ls["data"][0][0]["memo"] = memo
    assert mkii.convert_liveset(ls).tsl.memos[0] == memo


def test_only_a_mkii_liveset_is_converted() -> None:
    assert mkii.is_mkii("KATANA MkII") and not mkii.is_mkii("KATANA Gen3") and not mkii.is_mkii(None)
    with pytest.raises(ValueError):
        mkii.convert_liveset({"device": "KATANA Gen3", "data": [[]]})


def test_the_library_imports_a_mkii_file_as_a_gen_3_liveset(tmp_path) -> None:
    src = tmp_path / "from_mkii.tsl"
    blocks = blank_mkii()
    blocks["UserPatch%PatchName"][:] = ParamSet.encode_patch_name("Crunchy").ljust(16)
    set_mkii(blocks, "PREAMP_A_TYPE", 3)
    src.write_text(json.dumps(mkii_liveset(blocks, name="Mk2 set")))
    lib = Library(tmp_path / "lib")
    path, notes = lib.import_file_with_notes(src)
    assert notes[0] == "Converted from a KATANA MkII liveset" and any("Stack Crunch" in n for n in notes)
    stored = Tsl.load(path)                                     # a plain Gen 3 file now
    assert stored.device == "KATANA Gen3" and stored.patch_name(0) == "Crunchy"
    assert lib.import_file(src).name != path.name              # a second import gets a free name


def test_a_gen_3_file_is_imported_as_before(tmp_path) -> None:
    lib = Library(tmp_path / "lib")
    tsl = Tsl.from_paramset("Mine", 0, convert(blank_mkii()).patch)
    src = tmp_path / "gen3.tsl"
    tsl.save(src)
    path, notes = lib.import_file_with_notes(src)
    assert notes == [] and Tsl.load(path).device == "KATANA Gen3"


def test_other_devices_and_junk_are_refused(tmp_path) -> None:
    lib = Library(tmp_path / "lib")
    other = tmp_path / "other.tsl"
    other.write_text(json.dumps({"name": "x", "device": "KATANA 100", "data": [[]]}))
    junk = tmp_path / "junk.tsl"
    junk.write_text("not json")
    for bad in (other, junk, tmp_path / "missing.tsl"):
        with pytest.raises(ValueError):
            lib.import_file(bad)
    assert lib.list() == []


# ---- the command line and the window ----

def write_mkii(tmp_path, amp: int = 2):
    blocks = blank_mkii()
    blocks["UserPatch%PatchName"][:] = ParamSet.encode_patch_name("Cmd").ljust(16)
    set_mkii(blocks, "PREAMP_A_TYPE", amp)
    src = tmp_path / "m.tsl"
    src.write_text(json.dumps(mkii_liveset(blocks, name="CLI set")))
    return src


def test_the_command_line_imports_a_mkii_file_and_says_what_changed(tmp_path, capsys) -> None:
    from katatone import cli
    lib = tmp_path / "lib"
    assert cli._run(["library", "import", "--file", str(write_mkii(tmp_path)), "--dir", str(lib)]) == 0
    out = capsys.readouterr().out
    assert "imported ->" in out and "Converted from a KATANA MkII liveset" in out
    assert "amp type Combo Crunch became Crunch" in out
    assert Tsl.load(lib / "CLI set.tsl").patch_name(0) == "Cmd"


def test_the_window_imports_a_mkii_file_and_tells_the_user(monkeypatch, tmp_path) -> None:
    import flet.controls.base_control as bc
    from katatone.gui import app as app_mod
    from test_gui_resync import StubPage
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("KATATONE_NO_BLUETOOTH", "1")
    monkeypatch.setattr(bc.BaseControl, "update", lambda self: None)
    app = app_mod.App(StubPage(), start_midi=False)
    app._import_thread(str(write_mkii(tmp_path)))
    assert "Imported ->" in app.last_status
    assert "Converted from a KATANA MkII liveset: check the patches before using them" in app.last_status
    assert "(4 notes in the patch memos)" in app.last_status
    app._import_thread(str(tmp_path / "nope.tsl"))
    assert app.last_status.startswith("Import failed:")
