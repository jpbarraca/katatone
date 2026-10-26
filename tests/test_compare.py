"""Comparing two patches."""
from __future__ import annotations

from katatone.address.map import load_map
from katatone.model.compare import compare
from katatone.model.paramset import ParamSet

REGION = load_map()["PATCH"]


def patch(name: str = "Tone", **values: int) -> ParamSet:
    ps = ParamSet()
    ps.set_block("PATCH%COM", ParamSet.encode_patch_name(name).ljust(16))
    for key, v in values.items():
        block, i = key.split("_")
        ps.set_param(REGION, block, int(i), v)
    return ps


def test_equal_patches_have_no_differences() -> None:
    assert compare(patch(AMP_1=10), patch(AMP_1=10)) == []


def test_differences_are_listed_in_map_order_with_both_values() -> None:
    a, b = patch("One", AMP_1=10, AMP_3=7), patch("Two", AMP_1=20, AMP_3=7, AMP_2=5)
    got = [(d.block, d.p_index, d.a, d.b) for d in compare(a, b)]
    assert got == [("COM", 0, "One", "Two"), ("AMP", 1, 10, 20), ("AMP", 2, 0, 5)]
    assert compare(a, b)[0].is_name and not compare(a, b)[1].is_name


def test_blocks_neither_patch_has_are_skipped() -> None:
    assert compare(ParamSet(), ParamSet()) == []


def test_a_block_only_one_patch_has_counts_as_zeros() -> None:
    a = ParamSet()
    b = patch(AMP_1=4)
    assert [(d.block, d.a, d.b) for d in compare(a, b) if d.block == "AMP"] == [("AMP", 0, 4)]
