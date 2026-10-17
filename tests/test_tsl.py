"""Tests for TSL liveset save/load and ParamSet conversion."""
from __future__ import annotations

import json

import pytest

from katatone.liveset.tsl import (DEVICE, MAX_PATCHES, NUM_PATCHES, LivesetFull,
                             Tsl)
from katatone.model.paramset import ParamSet


def test_new_liveset_is_an_empty_group() -> None:
    """A freshly built Tsl is an empty liveset group with no memos."""
    tsl = Tsl(name="demo")
    assert len(tsl) == 0 and tsl.memos == []


def test_from_paramset_keeps_amp_slot_layout() -> None:
    """Tsl.from_paramset produces a liveset with all NUM_PATCHES patch slots."""
    tsl = Tsl.from_paramset("demo", 3, ParamSet())
    assert len(tsl.patches) == NUM_PATCHES


def test_to_json_has_expected_shape() -> None:
    """to_json emits the TSL document shape: name, device, formatRev and one data row of NUM_PATCHES patches."""
    tsl = Tsl.from_paramset("demo", 0, ParamSet())
    obj = json.loads(tsl.to_json())
    assert obj["name"] == "demo"
    assert obj["device"] == DEVICE
    assert obj["formatRev"] == "0000"
    assert len(obj["data"]) == 1
    assert len(obj["data"][0]) == NUM_PATCHES
    assert "paramSet" in obj["data"][0][0]


def _named(name: str) -> dict:
    """Build a single-patch dict with the given COM name.

    Args:
        name: patch name stored in the PATCH%COM block.

    Returns:
        the Tsl.encode'd paramset dict for that patch.
    """
    ps = ParamSet()
    ps.set_block("PATCH%COM", ParamSet.encode_patch_name(name))
    return Tsl.encode(ps)


def test_patch_group_editing_and_memo_roundtrip(tmp_path) -> None:
    """Adding, moving, duplicating, renaming and removing patches keeps the group and deep-copied memos consistent, and save/load round-trips them."""
    tsl = Tsl(name="set")
    tsl.add(_named("Clean"))
    tsl.add(_named("Lead"), memo={"memo": "", "isToneCentralPatch": True})
    tsl.add(_named("Crunch"), index=1)
    assert [tsl.patch_name(i) for i in range(3)] == ["Clean", "Crunch", "Lead"]
    tsl.move(2, 0)
    tsl.duplicate(0)
    tsl.rename_patch(1, "Lead 2")
    tsl.remove(3)
    assert [tsl.patch_name(i) for i in range(3)] == ["Lead", "Lead 2", "Clean"]
    assert tsl.memos[1] == {"memo": "", "isToneCentralPatch": True}
    tsl.memos[1]["memo"] = "x"                   # duplicate is a deep copy
    assert tsl.memos[0]["memo"] == ""

    path = tmp_path / "set.tsl"
    tsl.save(path)
    loaded = Tsl.load(path)
    assert len(loaded) == 3 and loaded.patch_name(1) == "Lead 2"
    assert loaded.memos[0] == {"memo": "", "isToneCentralPatch": True}


def test_liveset_patch_limit() -> None:
    """Adding beyond MAX_PATCHES raises LivesetFull."""
    tsl = Tsl(name="full")
    for _ in range(MAX_PATCHES):
        tsl.add({})
    with pytest.raises(LivesetFull):
        tsl.add({})


def test_save_load_roundtrip(tmp_path) -> None:
    """A liveset built from a ParamSet saves and loads with the selected patch's blocks intact and the others empty."""
    ps = ParamSet()
    ps.set_block("PATCH%AMP", bytes([0x01, 0x02, 0x03]))
    ps.set_block("PATCH%DELAY(1)", bytes([0x0A]))
    tsl = Tsl.from_paramset("MySound", 3, ps)
    path = tmp_path / "mysound.tsl"
    tsl.save(path)

    loaded = Tsl.load(path)
    assert loaded.name == "MySound"
    out = loaded.to_paramset(3)
    assert out.block("PATCH%AMP") == bytes([0x01, 0x02, 0x03])
    assert out.block("PATCH%DELAY(1)") == bytes([0x0A])
    # other patches stay empty
    assert out.block("PATCH%AMP_HEX") == b""


def test_from_to_paramset_hex_is_uppercase() -> None:
    """Param bytes round-trip through the liveset as uppercase two-char hex strings."""
    ps = ParamSet()
    ps.set_block("PATCH%AMP", bytes([0xAB, 0xCD]))
    tsl = Tsl.from_paramset("n", 0, ps)
    # hex stored uppercase two-char strings
    assert tsl.patches[0]["PATCH%AMP"] == ["AB", "CD"]
    # back to identical bytes
    assert tsl.to_paramset(0).block("PATCH%AMP") == bytes([0xAB, 0xCD])


def test_load_rejects_wrong_device(tmp_path) -> None:
    """Tsl.load rejects a .tsl saved for a different device."""
    bad = {"name": "x", "device": "BOSS ME-80", "formatRev": "0000",
           "data": [[{"memo": "", "paramSet": {}}] + [{} for _ in range(NUM_PATCHES - 1)]]}
    path = tmp_path / "bad.tsl"
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        Tsl.load(path)
