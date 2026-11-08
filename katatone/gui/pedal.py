"""Which parameter a pedal drives, from the patch's pedal assignments.

An expression pedal (the amp's own, or one of the three of each GA-FC) is assigned to an
effect slot (Booster, Mod, FX, Delay, Delay 2 or Reverb) by its FUNC block, and to one
parameter of that effect by its DETAIL block, which holds one choice per effect type.  The
amp moves the parameter with the pedal, so what is stored in the patch can differ from what
the amp plays.  :func:`targets` names the parameters the pedals drive, so that their live
values can be read from the amp when the patch is written.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from ..address.map import Region
from ..model.paramset import ParamSet
from . import model

#: FUNC value -> the effect slot the pedal controls (0 to 3 are volume and Pedal FX: no effect).
_FUNC_SLOT = {4: "BOOSTER", 5: "MOD", 6: "FX", 7: "DELAY", 8: "DELAY2", 9: "REVERB"}
#: Slot -> its index in the COLOR block, and its (block name, number of its first variation).
_COLOR_INDEX = {"BOOSTER": 0, "MOD": 1, "FX": 2, "DELAY": 3, "DELAY2": 3, "REVERB": 4}
_VARIATIONS = {"BOOSTER": ("BOOSTER", 1), "MOD": ("FX", 1), "FX": ("FX", 4),
               "DELAY": ("DELAY", 1), "DELAY2": ("DELAY", 4), "REVERB": ("REVERB", 1)}
#: (FUNC block, DETAIL block) of every pedal: the amp's own, then GA-FC 1 and 2 with three each.
PEDALS = [("ASSIGN_EXPPDL_FUNC", "ASSIGN_EXPPDL_DETAIL")] + [
    (f"ASSIGN_GAFCEXPPDL{k}_FUNC({u})", f"ASSIGN_GAFCEXPPDL{k}_DETAIL({u})")
    for u in (1, 2) for k in (1, 2, 3)]
#: An effect's name in the FX_DETAIL blocks -> its name in the DETAIL choices.
_TYPE_ALIASES = {"PITCHSHIFT": "PITCHSHIFTER", "ACPROCESS": "ACPROCESSOR", "AWAH": "AUTO_WAH",
                 "PEDALWAH": "PEDAL_WAH", "ACSIM": "ACGUITARSIM", "EPHASER": "PHASE90E",
                 "EFLANGER": "FLANGER117E", "EWAH": "WAH95E", "HEAVYOCTAVE": "HEAVYOCT",
                 "BEND": "PEDALBEND", "2X2CHORUS": "CHORUS"}
#: A choice's wording -> the label of the parameter in the editor.
_LABEL_ALIASES = {"DELAY TIME": "TIME", "REVERB TIME": "TIME", "DIRECT MIX": "DIRECT LEVEL",
                  "SOLO SW": "SOLO"}


def _norm(text: str) -> str:
    """Letters and digits only, in capitals: names compare equal whatever their separators."""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


@lru_cache(maxsize=1)
def _choices() -> dict[str, list[str]]:
    """Per effect, the parameters a pedal can be set to drive (the first is "PRESET": none)."""
    path = Path(__file__).with_name("param_labels.json")
    return json.loads(path.read_text()).get("_assign_detail_map", {})


def targets(ps: ParamSet, region: Region) -> list[tuple[str, int]]:
    """The parameters the patch's pedals drive.

    Args:
        ps: the patch.
        region: a user patch region of the address map (for the block layout).
    Returns:
        ``(block name, parameter index)`` pairs, each once, in pedal order.  A pedal assigned
        to volume or to nothing, or to a choice that is not a parameter of the effect, adds
        none.
    """
    found: list[tuple[str, int]] = []
    for func_block, detail_block in PEDALS:
        slot = _FUNC_SLOT.get(ps.get_param(region, func_block, 0))
        if slot is None:
            continue
        base, first = _VARIATIONS[slot]
        number = first + min(2, max(0, ps.get_param(region, "COLOR", _COLOR_INDEX[slot])))
        block = f"{base}({number})"
        if base == "FX":
            detail = f"FX_DETAIL({number})"
            kind = ps.get_param(region, block, 0)
            keys = model.fx_detail_groups(region, detail)
            if not 0 <= kind < len(keys):
                continue
            key = _TYPE_ALIASES.get(keys[kind], keys[kind])
            rows = model.fx_detail_rows(region, detail, kind, ps)
        else:
            key, rows = base, model.row_for_block(region, block, ps)
        key = next((k for k in _choices() if _norm(k) == _norm(key)), None)
        if key is None:
            continue
        chosen = next((i for i, p in enumerate(region.block(detail_block).params)
                       if p.name.endswith("_DETAIL_" + key)), None)
        if chosen is None:
            continue
        index = ps.get_param(region, detail_block, chosen)
        names = _choices()[key]
        if not 0 < index < len(names):                  # 0 is PRESET: the pedal drives none
            continue
        wanted = _norm(_LABEL_ALIASES.get(names[index], names[index]))
        row = next((r for r in rows if _norm(r.label) == wanted), None)
        if row is not None and (row.block, row.p_index) not in found:
            found.append((row.block, row.p_index))
    return found
