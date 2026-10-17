"""Patch slot <-> amp panel mapping (bank / channel per model)."""
from __future__ import annotations

from katatone.amp import slots


def test_katana50_has_panel_and_two_channels_per_bank() -> None:
    """The KATANA-50 model exposes the panel plus two channels per bank (five slots)."""
    got = [(s.index, s.short) for s in slots.slots(0x05)]
    assert got == [(0, "PN"), (1, "1A"), (2, "2A"), (5, "1B"), (6, "2B")]


def test_four_channel_models_use_all_nine_slots() -> None:
    """Four-channel models use all nine slots: panel plus four channels per bank."""
    got = [s.short for s in slots.slots(0x06)]
    assert got == ["PN", "1A", "2A", "3A", "4A", "1B", "2B", "3B", "4B"]
    assert [s.index for s in slots.slots(0x06)] == list(range(9))


def test_model_code_from_identity_reply() -> None:
    """model_code extracts the model from an identity reply, defaulting on unknown data, and model_name maps the code to a display string."""
    reply = bytes.fromhex("f07e100602410705000005000000f7")   # real KATANA-50
    assert slots.model_code(reply) == 0x05
    assert slots.model_name(0x05) == "KATANA-50 Gen 3"
    assert slots.model_code(None) == slots.DEFAULT_MODEL
    assert slots.model_code(bytes(11)) == slots.DEFAULT_MODEL   # unknown code


def test_slots_carry_bank_and_channel() -> None:
    """Each slot knows its bank and 1-based channel; the panel has no bank."""
    got = [(s.bank, s.ch) for s in slots.slots(0x05)]
    assert got == [(None, 0), ("A", 1), ("A", 2), ("B", 1), ("B", 2)]


def test_slot_lookup_and_fallback() -> None:
    """slot() finds a named slot for the model and falls back to a plain channel number when the slot is not on that model's panel."""
    assert slots.slot(5, 0x05).long == "Bank B · CH1"
    assert slots.slot(3, 0x05).short == "4"          # not on a KATANA-50


def test_bank_switch_keeps_channel_on_katana50() -> None:
    """Toggle/next/prev swap A and B keeping the channel; the panel goes to 1B."""
    assert slots.bank_switch(2, "toggle", 0x05) == 6      # 2A -> 2B
    assert slots.bank_switch(6, "toggle", 0x05) == 2      # 2B -> 2A
    assert slots.bank_switch(1, "next", 0x05) == 5        # 1A -> 1B
    assert slots.bank_switch(5, "next", 0x05) == 1        # wraps
    assert slots.bank_switch(1, "prev", 0x05) == 5        # wraps backwards
    assert slots.bank_switch(0, "toggle", 0x05) == 5      # Panel -> 1B
    assert slots.bank_switch(0, "prev", 0x05) == 5


def test_bank_switch_four_channel_model() -> None:
    """With four channels per bank, channel 4 maps 4A <-> 4B."""
    assert slots.bank_switch(4, "toggle", 0x06) == 8
    assert slots.bank_switch(7, "toggle", 0x06) == 3
    assert slots.bank_switch(0, "next", 0x06) == 5


def test_bank_switch_rejects_unknown_op() -> None:
    """A typo in the profile must not silently pick a channel."""
    import pytest
    with pytest.raises(ValueError):
        slots.bank_switch(1, "sideways")


def test_channel_step_walks_panel_order_and_wraps() -> None:
    """PN, 1A, 2A, 1B, 2B on a KATANA-50; wraps at both ends."""
    assert [slots.channel_step(i, "next", 0x05) for i in (0, 1, 2, 5, 6)] == \
        [1, 2, 5, 6, 0]
    assert [slots.channel_step(i, "prev", 0x05) for i in (0, 1, 2, 5, 6)] == \
        [6, 0, 1, 2, 5]


def test_channel_step_four_channel_model_and_unknown_start() -> None:
    """All nine slots on a 4-channel model; an unknown slot starts at an end."""
    assert slots.channel_step(4, "next", 0x06) == 5
    assert slots.channel_step(8, "next", 0x06) == 0
    assert slots.channel_step(3, "next", 0x05) == 0       # no 3A here
    assert slots.channel_step(3, "prev", 0x05) == 6


def test_amp_pc_table_and_has_slot() -> None:
    """The factory PC table names 3A on PC 2, which a KATANA-50 does not have."""
    assert slots.AMP_PC_TABLE[0] == 1 and slots.AMP_PC_TABLE[4] == 0
    assert slots.AMP_PC_TABLE[6] == 6
    assert slots.has_slot(slots.AMP_PC_TABLE[2], 0x06)
    assert not slots.has_slot(slots.AMP_PC_TABLE[2], 0x05)
