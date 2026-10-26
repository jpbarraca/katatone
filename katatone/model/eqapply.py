"""Turn an EQ proposal of the analyzer into changes to a patch's EQ.

A patch has two EQ slots (``EQ_EACH(1)`` and ``EQ_EACH(2)``), each switched on or
off and either a parametric EQ or a 10-band graphic EQ, with their settings in
``EQ_PEQ(n)`` and ``EQ_GE10(n)``.  The proposal is what to add on top of the EQ the
amp is already using, so:

* a slot that is on and of the proposal's kind is adjusted by it;
* otherwise a slot that is off is switched on, set to the kind and filled with it;
* otherwise (both slots are on, with the other kind) nothing can be applied without
  changing the sound that was measured, and the reason is given.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..address.map import load_map
from .paramset import ParamSet

TYPE_PEQ, TYPE_GE10 = 0, 1
GE_LIMIT_DB, PEQ_LIMIT_DB = 24, 20
#: Parameter indexes in EQ_EACH, EQ_PEQ and EQ_GE10.
EACH_SW, EACH_TYPE = 1, 2
PEQ_LOW_GAIN, PEQ_HIGH_GAIN = 1, 8
PEQ_MIDS = ((2, 3, 4), (5, 6, 7))              # (freq, Q, gain) of the low-mid and high-mid band

Change = tuple[str, int, int]                  # (block, parameter index, display value)


@dataclass
class Plan:
    """What applying a proposal does."""
    changes: list[Change] = field(default_factory=list)
    slot: int | None = None            # 1 or 2, None when it cannot be applied
    note: str = ""                     # what happened, or why nothing can


def _clamp(v: int, limit: int) -> int:
    return max(-limit, min(limit, v))


def plan(ps: ParamSet, kind: str, values: dict) -> Plan:
    """Work out the parameter changes for a proposal.

    Args:
        ps: the patch the EQ is applied to.
        kind: ``"GE-10"`` or ``"PEQ"``.
        values: ``EqFit.values`` of the proposal.
    Returns:
        The plan: the changes (empty with a note when none can be made).
    """
    region = load_map()["PATCH"]
    want = TYPE_GE10 if kind == "GE-10" else TYPE_PEQ

    def get(block: str, i: int) -> int:
        return ps.get_param(region, block, i)

    slots = [(n, get(f"EQ_EACH({n})", EACH_SW), get(f"EQ_EACH({n})", EACH_TYPE)) for n in (1, 2)]
    same = next((n for n, sw, t in slots if sw and t == want), None)
    free = next((n for n, sw, _ in slots if not sw), None)
    if same is None and free is None:
        other = "parametric" if want == TYPE_GE10 else "graphic"
        label = "graphic (GE-10)" if want == TYPE_GE10 else "parametric (PEQ)"
        return Plan(note=f"Both EQ slots are on and {other}: switch one off, or make it {label}, to apply this.")
    slot = same if same is not None else free
    each = f"EQ_EACH({slot})"
    out: list[Change] = []
    if same is None:                                   # a free slot: switch it on as this kind
        out += [(each, EACH_TYPE, want), (each, EACH_SW, 1)]
    additive = same is not None
    if kind == "GE-10":
        block = f"EQ_GE10({slot})"
        for i, g in enumerate(values["gains"]):
            now = get(block, i) if additive else 0
            out.append((block, i, _clamp(now + g, GE_LIMIT_DB)))
        note = "adjusted" if additive else "set"
    else:
        block = f"EQ_PEQ({slot})"
        for idx, name in ((PEQ_LOW_GAIN, "low"), (PEQ_HIGH_GAIN, "high")):
            now = get(block, idx) if additive else 0
            out.append((block, idx, _clamp(now + values[name], PEQ_LIMIT_DB)))
        used: set[int] = set()
        skipped = 0
        for f, q, g in values["mids"]:
            if g == 0:
                continue
            band = next((b for b, (_, _, gi) in enumerate(PEQ_MIDS)
                         if b not in used and (not additive or get(block, gi) == 0)), None)
            if band is None:                           # both bands already carry a setting
                skipped += 1
                continue
            used.add(band)
            fi, qi, gi = PEQ_MIDS[band]
            out += [(block, fi, f), (block, qi, q), (block, gi, _clamp(g, PEQ_LIMIT_DB))]
        if not additive:                               # a fresh slot: the unused bands are flat
            for b, (_, _, gi) in enumerate(PEQ_MIDS):
                if b not in used:
                    out.append((block, gi, 0))
        note = ("adjusted" if additive else "set") + (
            f"; {skipped} mid band(s) skipped because both already have a gain" if skipped else "")
    changes = [c for c in out if c[2] != get(c[0], c[1])]
    if not changes:
        return Plan([], slot, "The EQ already has these settings.")
    return Plan(changes, slot, f"EQ {slot} ({kind}) {note}.")
