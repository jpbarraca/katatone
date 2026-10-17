"""Physical meaning of the amp's patch slots (bank / channel per model).

Patch numbers 0..9 (CMDID_PATCH_SELECT value, region ``PATCH(n+1)``) map to
the amp's front panel:

    0       PANEL
    1..4    bank A, channel 1..4
    5..8    bank B, channel 1..4
    9       unused

Only the channels a model actually has exist: the KATANA-50 Gen 3 has two
per bank (PN, 1A, 2A, 1B, 2B), the other Gen 3 models four.  The model is
reported in the identity reply (byte 10).
"""
from __future__ import annotations

from dataclasses import dataclass

# identity-reply model code -> (name, channels per bank)
MODELS = {
    0x05: ("KATANA-50 Gen 3", 2),
    0x06: ("KATANA-100 Gen 3", 4),
    0x07: ("KATANA-100/212 Gen 3", 4),
    0x08: ("KATANA-HEAD Gen 3", 4),
    0x09: ("KATANA Artist Gen 3", 4),
    0x0A: ("KATANA-50 EX Gen 3", 4),
    0x0B: ("KATANA Artist HEAD Gen 3", 4),
}
DEFAULT_MODEL = 0x05        # the amp this editor targets (used until connected)
FIXED_CH = 4                # slots reserved per bank in patch numbering
BANKS = "AB"


@dataclass(frozen=True)
class Slot:
    index: int          # patch number 0..9
    short: str          # "PN", "1A", "2B", ...
    long: str           # "Panel", "Bank A · CH1", ...
    bank: str | None = None     # "A" / "B"; None for the panel
    ch: int = 0                 # channel within the bank, 1-based


def model_code(identity_reply: bytes | None) -> int:
    """Model code from a device-inquiry reply (DEFAULT_MODEL if unknown).

    Args:
        identity_reply: the identity reply frame, or None.
    Returns:
        The model code (byte 10) if known, else DEFAULT_MODEL.
    """
    if identity_reply and len(identity_reply) > 10 \
            and identity_reply[10] in MODELS:
        return identity_reply[10]
    return DEFAULT_MODEL


def model_name(code: int) -> str:
    """Display name for a model code (the default model's name if unknown)."""
    return MODELS.get(code, MODELS[DEFAULT_MODEL])[0]


def slots(code: int = DEFAULT_MODEL) -> list[Slot]:
    """The slots that exist on this model, in patch-number order.

    Args:
        code: the model code to use (default model if unknown).
    Returns:
        The Slot list: PANEL first, then bank A and bank B channels.
    """
    channels = MODELS.get(code, MODELS[DEFAULT_MODEL])[1]
    out = [Slot(0, "PN", "Panel")]
    for b, bank in enumerate(BANKS):
        for ch in range(channels):
            out.append(Slot(1 + b * FIXED_CH + ch, f"{ch + 1}{bank}",
                            f"Bank {bank} · CH{ch + 1}", bank, ch + 1))
    return out


def slot(index: int, code: int = DEFAULT_MODEL) -> Slot:
    """Slot for a patch number (a numeric fallback if the model lacks it).

    Args:
        index: the patch number 0..9.
        code: the model code to look up.
    Returns:
        The matching Slot, or a numeric fallback Slot.
    """
    for s in slots(code):
        if s.index == index:
            return s
    return Slot(index, str(index + 1), f"Patch {index + 1}")


# The amp's factory PC -> channel map (SYSTEM MIDI_PC_*: CH1A..CH4A = 0..3,
# PANEL = 4, CH1B..CH4B = 5..8), as {program: patch number}.  A PC may name a
# channel the connected model lacks (a KATANA-50 has no 3A/4A/3B/4B).
AMP_PC_TABLE: dict[int, int] = {
    0: 1, 1: 2, 2: 3, 3: 4,
    4: 0,
    5: 5, 6: 6, 7: 7, 8: 8,
}


def bank_switch(index: int, op: str, code: int = DEFAULT_MODEL) -> int:
    """The slot reached by changing bank while keeping the channel.

    The panel counts as bank A for navigation, and lands on channel 1 of the
    target bank. Banks wrap, so with two banks next, prev and toggle agree.

    Args:
        index: the current patch number.
        op: "toggle", "next" or "prev".
        code: the model code (decides how many channels a bank has).

    Returns:
        The patch number to select.

    Raises:
        ValueError for an unknown `op`.
    """
    if op not in ("toggle", "next", "prev"):
        raise ValueError(f"unknown bank op {op!r}")
    cur = slot(index, code)
    bank = BANKS.index(cur.bank) if cur.bank else 0
    step = -1 if op == "prev" else 1
    target = (bank + step) % len(BANKS)
    ch = cur.ch or 1
    channels = MODELS.get(code, MODELS[DEFAULT_MODEL])[1]
    return 1 + target * FIXED_CH + (min(ch, channels) - 1)


def channel_step(index: int, op: str, code: int = DEFAULT_MODEL) -> int:
    """The next or previous slot in panel order (PN, 1A, 2A, ..., wrapping).

    Args:
        index: the current patch number; one the model lacks starts at the
            first slot going forward and the last going back.
        op: "next" or "prev".
        code: the model code.

    Returns:
        The patch number to select.

    Raises:
        ValueError for an unknown `op`.
    """
    if op not in ("next", "prev"):
        raise ValueError(f"unknown step op {op!r}")
    order = [s.index for s in slots(code)]
    if index not in order:
        return order[0] if op == "next" else order[-1]
    i = order.index(index)
    return order[(i + (1 if op == "next" else -1)) % len(order)]


def has_slot(index: int, code: int = DEFAULT_MODEL) -> bool:
    """Whether the model has a slot with this patch number.

    Args:
        index: a patch number.
        code: the model code.

    Returns:
        True if `index` is one of `slots(code)`.
    """
    return any(s.index == index for s in slots(code))
