"""Data-driven editor model — pure logic, no Flet dependency.

The patch / system editors are generated from the static address table
(:func:`katatone.address.map.load_map`).  Every parameter carries ``size, ofs, min,
max``; from that we decide what widget to render and what its current value is.

This module is deliberately Flet-free so it can be unit-tested headlessly and so
the same logic can later drive a non-Flet frontend (Tauri/web) unchanged.

Control kinds
-------------
- ``text``     16-byte ASCII block (the patch name)      -> text field
- ``constant`` ``min == max`` (read-only)                -> plain label
- ``checkbox`` ON/OFF switch (``*_SW`` or an ``OFF, ON`` label list)
- ``radio``    two named variations (``A, B``)
- ``color``    Red/Green/Blue effect colour
- ``combo``    any parameter with display strings (TYPE, ...) -> dropdown
- ``knob``     any other numeric value (1-byte or multi-byte) -> rotary knob
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..address.map import Block, Param, Region
from ..model.paramset import ParamSet

# ---------------------------------------------------------------------------
# Parameter label map (upgradable JSON file)
# ---------------------------------------------------------------------------

_LABELS_PATH = Path(__file__).parent / "param_labels.json"
_labels_cache: dict | None = None


def _load_labels() -> dict:
    """Load the param_labels.json map (cached after first read).

    Returns:
        The label map as a dict, or an empty dict when the file is missing
        or holds invalid JSON.
    """
    global _labels_cache
    if _labels_cache is None:
        try:
            with open(_LABELS_PATH, encoding="utf-8") as f:
                _labels_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _labels_cache = {}
    return _labels_cache


def _combo_labels(param_name: str, count: int) -> tuple[str, ...] | None:
    """Look up display strings for a combo parameter.

    Handles both direct keys and the ``_assign_detail_map`` suffix lookup.

    Args:
        param_name: static parameter name from the address table.
        count: number of display strings to return (max - min + 1).

    Returns:
        A tuple of `count` strings, or None if no mapping is found.
    """
    labels = _load_labels()
    # Direct lookup
    if param_name in labels:
        vals = labels[param_name]
        if isinstance(vals, list) and len(vals) >= count:
            return tuple(vals[:count])
    # Assign-target lookup by effect suffix (exact length only, so the
    # numeric MIN/MAX params sharing the suffix stay knobs):
    #   PRMID_PATCH_ASSIGN_KNOBS_CHORUS              -> "CHORUS"
    #   PRMID_PATCH_ASSIGN_EXPPDL_DETAIL_AUTO_WAH    -> "AUTO_WAH"
    #   PRMID_PATCH_ASSIGN_GAFCEXPPDL1_DETAIL_WAH95E -> "WAH95E"
    m = re.match(r"PRMID_PATCH_ASSIGN_(?:KNOBS|EXPPDL_DETAIL|"
                 r"GAFCEXPPDL\d_DETAIL)_(\w+)$", param_name)
    if m:
        vals = labels.get("_assign_detail_map", {}).get(m.group(1))
        if isinstance(vals, list) and len(vals) == count:
            return tuple(vals)
    return None


@dataclass(frozen=True)
class RowDesc:
    """A single editable row in the data-driven editor (framework-agnostic)."""
    block: str
    p_index: int
    param: Param
    kind: str
    label: str
    value: int | str
    options: tuple[int | str, ...] | None = None


def control_kind(p: Param) -> str:
    """Decide the widget kind for a parameter from its static metadata.

    Args:
        p: the static address-table entry (its ``size, min, max`` and name).

    Returns:
        One of ``text``, ``constant``, ``checkbox``, ``color``, ``knob``;
        ``row_for_block()`` later upgrades knobs with display strings to
        combo / checkbox / radio.
    """
    if p.size == 16:
        return "text"
    span = p.max - p.min
    if span <= 0:
        return "constant"
    # Color tri-state params (Red/Green/Blue) — rendered as 3 buttons
    if span == 2 and p.name.startswith("PRMID_PATCH_COLOR_") and p.name.endswith("_COLOR"):
        return "color"
    # SW toggles (0/1) render as checkboxes on every tab and subsection
    if p.size == 1 and span == 1 and p.name.endswith("_SW"):
        return "checkbox"
    # Everything else numeric is a knob; row_for_block() upgrades params that
    # have display strings to combo / checkbox / radio.
    return "knob"


def _is_on_off(labels: tuple[str, ...]) -> bool:
    """True when the display strings are exactly an ``OFF``/``ON`` switch pair."""
    return tuple(x.upper() for x in labels) == ("OFF", "ON")


def friendly_block_label(name: str) -> str:
    """'FX(1)' -> 'FX 1', 'EQ_PEQ(2)' -> 'EQ PEQ 2', 'ASSIGN_FS' -> 'ASSIGN FS'."""
    if name == "NS":
        return "Noise Suppressor"
    if name == "SENDRETURN":
        return "Send Return"
    s = name.replace("_", " ")
    s = re.sub(r"\((\d+)\)", r" \1", s)
    return s.strip()


def param_label(name: str) -> str:
    """'PRMID_PATCH_AMP_GAIN' -> 'AMP GAIN' (strip the PRMID_* prefix)."""
    n = name
    for pref in ("PRMID_PATCH_", "PRMID_SYSTEM_", "PRMID_"):
        if n.startswith(pref):
            n = n[len(pref):]
            break
    return n.replace("_", " ").strip()


# Block-aware label: strip the redundant block prefix from the param name.
# e.g. in AMP block: "AMP GAIN" -> "Gain", "AMP TYPE" -> "Type"
#      in SW block:  "SW BOOSTER SW" -> "Booster"
#      in COLOR block: "COLOR BOOSTER COLOR" -> "Booster"
# Any block works: the block's base name (number suffix removed) is stripped
# from the start of the param label, e.g. BOOSTER(1) + "BOOSTER TYPE" -> "Type".


def _display_label(name: str, block_name: str) -> str:
    """Generate a clean display label, stripping the redundant block prefix.

    e.g. block=AMP, name=PRMID_PATCH_AMP_GAIN -> "Gain"
         block=BOOSTER(1), name=PRMID_PATCH_BOOSTER_TYPE -> "Type"
         block=SW, name=PRMID_PATCH_SW_BOOSTER_SW -> "Booster"
         block=COLOR, name=PRMID_PATCH_COLOR_BOOSTER_COLOR -> "Booster"
    """
    raw = param_label(name)  # e.g. "AMP GAIN"
    # Strip the block's base name (number suffix removed) if it prefixes the
    # param label — e.g. "Booster Type" in the "Booster 1" section -> "Type".
    base = re.sub(r"\(\d+\)$", "", block_name).replace("_", " ").strip()
    if base:
        sp = base + " "
        if raw.startswith(sp):
            raw = raw[len(sp):]
        if not raw:
            raw = base
    # Trailing " SW" is redundant on a toggle (e.g. "Solo SW" -> "Solo")
    if name.endswith("_SW") and raw.endswith(" SW"):
        raw = raw[:-3]
    # For COLOR block, strip trailing " COLOR" (e.g. "Booster COLOR" -> "Booster")
    if block_name == "COLOR" and raw.endswith(" COLOR"):
        raw = raw[:-6]
    # A bare "SW" (NS / SENDRETURN) -> "Enable"
    if raw == "SW":
        raw = "Enable"
    return raw


def display_label(name: str, block_name: str) -> str:
    """Public spelling of :func:`_display_label` (for panes outside the editor).

    Args:
        name: the full parameter name (``PRMID_PATCH_AMP_GAIN``).
        block_name: the block it belongs to (``AMP``).

    Returns:
        The label the editor shows, e.g. "Gain".
    """
    return _display_label(name, block_name)


def block_order(region: Region) -> list[str]:
    """Block names in region order (drives the tab strip)."""
    return [b.name for b in region.blocks]


def _value_for(region: Region, block_name: str, p_index: int, p: Param,
               ps: ParamSet) -> int | str:
    """Current value of one parameter inside a block.

    Args:
        region: the Region the block belongs to.
        block_name: name of the block (e.g. ``AMP`` or ``SW``).
        p_index: 0-based position of the param within the block.
        p: the static param entry (its size decides text vs numeric).
        ps: the loaded ParamSet holding the current values.

    Returns:
        The patch name as a string for 16-byte params, else the numeric value.
    """
    if p.size == 16:
        return ParamSet.patch_name(
            ps.block(ParamSet.block_key(region.name, block_name)))
    return ps.get_param(region, block_name, p_index)


def combo_display_value(row: RowDesc) -> str:
    """The string a combo row's dropdown must be preselected to.

    A Flet Dropdown matches ``value`` against an option *label*.  For
    string-labeled combos that label is ``options[value - min]``; for integer
    combos it is ``str(value)``.  Preselecting with the numeric index (the old
    bug) made Amp Type / Cabinet Resonance / Master Key / Send Return Position
    render empty right after a read, since ``'2'`` never equals ``'Plexi'``.

    Args:
        row: the combo RowDesc with its current value and options.

    Returns:
        The option label the dropdown must be preselected to.
    """
    opts = row.options or ()
    if opts and isinstance(opts[0], str):
        idx = row.value - row.param.min if isinstance(row.value, int) else 0
        return opts[idx] if 0 <= idx < len(opts) else str(opts[0])
    return str(row.value)


def row_for_block(region: Region, block_name: str, ps: ParamSet) -> list[RowDesc]:
    """Build a :class:`RowDesc` for every parameter in one block.

    Args:
        region: the Region containing the block.
        block_name: name of the block to build rows for.
        ps: the loaded ParamSet supplying the current values.

    Returns:
        One RowDesc per param in block order, with the AMP block's TYPE row
        moved to the top.
    """
    b: Block = region.block(block_name)
    out: list[RowDesc] = []
    for i, p in enumerate(b.params):
        kind = control_kind(p)
        value = _value_for(region, block_name, i, p, ps)
        options = None
        if kind in ("knob", "checkbox", "color") and p.size == 1:
            str_labels = _combo_labels(p.name, p.max - p.min + 1)
            if str_labels is not None:
                options = str_labels
                if kind == "knob":
                    if str_labels == ("A", "B"):
                        kind = "radio"
                    elif _is_on_off(str_labels):
                        kind = "checkbox"
                    else:
                        kind = "combo"
        if (kind == "knob" and p.max - p.min == 1
                and block_name in ("SW", "NS", "SENDRETURN")):
            kind = "checkbox"
        label = _display_label(p.name, block_name)
        out.append(RowDesc(block=block_name, p_index=i, param=p, kind=kind,
                           label=label, value=value, options=options))
    # Reorder: in AMP block, move TYPE param to the top
    if block_name == "AMP" and len(out) > 1:
        type_idx = next((j for j, r in enumerate(out)
                         if r.param.name.endswith("AMP_TYPE")), None)
        if type_idx is not None and type_idx > 0:
            out.insert(0, out.pop(type_idx))
    return out


def rows_for_region(region: Region, ps: ParamSet) -> dict[str, list[RowDesc]]:
    """Row descriptors for every block of a region, keyed by block name.

    Args:
        region: the Region to render.
        ps: the loaded ParamSet supplying the current values.

    Returns:
        A dict mapping block name to its list of RowDesc.
    """
    return {b.name: row_for_block(region, b.name, ps) for b in region.blocks}


# ---------------------------------------------------------------------------
# Block -> editor-tab grouping (pure, so the tab layout is testable too)
# ---------------------------------------------------------------------------

_TAB_ORDER = ["Amp", "Booster", "MOD", "FX", "Delay", "Reverb",
              "EQ", "Solo", "Contour", "Pedal", "Assign", "Other"]


def _tab_for(name: str) -> str:
    """Map one block name to the editor tab it belongs to.

    Args:
        name: block name from the address table (case-insensitive).

    Returns:
        One of the tab labels in ``_TAB_ORDER``.
    """
    n = name.upper()
    if n in ("AMP", "OTHER", "COLOR", "SW", "NS", "SENDRETURN"):
        return "Amp"
    if n.startswith("BOOSTER"):
        return "Booster"
    if n.startswith("FX"):
        # FX(1..3) are the MOD slot's variations, FX(4..6) the FX slot's.
        m = re.search(r"\((\d+)\)", n)
        return "MOD" if (m and int(m.group(1)) <= 3) else "FX"
    if n.startswith("DELAY"):
        return "Delay"
    if n.startswith("REVERB"):
        return "Reverb"
    if n.startswith("EQ"):
        return "EQ"
    if n.startswith("SOLO"):
        return "Solo"
    if n.startswith("CONTOUR"):
        return "Contour"
    if n.startswith("PEDALFX"):
        return "Pedal"
    if n.startswith("ASSIGN") or n.startswith("PATCH_KNOB"):
        return "Assign"
    return "Other"


def group_blocks(block_names: list[str]) -> list[tuple[str, list[str]]]:
    """Group block names into logical editor tabs (order-preserving).

    Args:
        block_names: block names in region order.

    Returns:
        A list of ``(tab_label, [block names])`` in a stable display order,
        dropping empty groups.  This keeps an 80-block patch in ~11 usable tabs.
    """
    buckets: dict[str, list[str]] = {g: [] for g in _TAB_ORDER}
    for name in block_names:
        buckets[_tab_for(name)].append(name)
    return [(g, buckets[g]) for g in _TAB_ORDER if buckets[g]]



# ---------------------------------------------------------------------------
# Katana effect variations (GREEN / RED / YELLOW) and FX detail filtering
# ---------------------------------------------------------------------------

# Each effect slot has three variations selected by the panel colour button;
# block index (1)/(2)/(3) = green/red/yellow .  FX(1..3) are MOD, FX(4..6) FX, DELAY(4..6) DELAY 2.
VARIATIONS = ("GREEN", "RED", "YELLOW")

_SLOT_NAMES = {"BOOSTER": ("BOOSTER", "BOOSTER"), "FX": ("MOD", "FX"),
               "FX_DETAIL": ("MOD", "FX"), "DELAY": ("DELAY", "DELAY 2"),
               "REVERB": ("REVERB", "REVERB")}


def block_variation(name: str) -> tuple[str, int] | None:
    """'FX(5)' -> ('FX', 1): effect slot name and variation index (0..2).

    Args:
        name: numbered block name, e.g. ``FX(5)`` or ``BOOSTER(1)``.

    Returns:
        The (slot name, variation index) pair, or None for blocks that are
        not one of the numbered effect slots.
    """
    m = re.fullmatch(r"([A-Z_]+)\((\d)\)", name)
    if not m or m.group(1) not in _SLOT_NAMES:
        return None
    n = int(m.group(2)) - 1
    slot = _SLOT_NAMES[m.group(1)][n // 3]
    return slot, n % 3


def block_title(name: str) -> str:
    """Display title: 'FX(5)' -> 'FX · RED', 'AMP' -> 'AMP'."""
    v = block_variation(name)
    if v is None:
        return friendly_block_label(name)
    return f"{v[0]} · {VARIATIONS[v[1]]}"


_FX_DETAIL_RE = re.compile(r"PRMID_PATCH_FX_DETAIL_([A-Z0-9]+)_")


def fx_detail_groups(region: Region, detail_block: str) -> list[str]:
    """Effect keys of an FX_DETAIL block in FX TYPE order (TWAH, AWAH, ...).

    The detail block lists every effect's parameters back to back, in the
    same order as the FX TYPE values, so group *i* belongs to TYPE value *i*.

    Args:
        region: the Region containing the detail block.
        detail_block: name of the FX_DETAIL block to scan.

    Returns:
        The effect keys in order of appearance, one per effect.
    """
    keys: list[str] = []
    for p in region.block(detail_block).params:
        m = _FX_DETAIL_RE.match(p.name)
        if m and m.group(1) not in keys:
            keys.append(m.group(1))
    return keys


def fx_detail_rows(region: Region, detail_block: str, fx_type: int,
                   ps: ParamSet) -> list[RowDesc]:
    """Rows of ``detail_block`` that belong to the selected FX TYPE.

    Args:
        region: the Region containing the detail block.
        detail_block: name of the FX_DETAIL block to filter.
        fx_type: the selected FX TYPE value (index into the effect-key list).
        ps: the loaded ParamSet supplying the current values.

    Returns:
        The matching rows with the effect-key prefix stripped from the label;
        an empty list when fx_type is out of range.
    """
    keys = fx_detail_groups(region, detail_block)
    if not 0 <= fx_type < len(keys):
        return []
    key = keys[fx_type]
    out = []
    for row in row_for_block(region, detail_block, ps):
        m = _FX_DETAIL_RE.match(row.param.name)
        if m and m.group(1) == key:
            label = row.label
            if label.startswith(key + " "):
                label = label[len(key) + 1:]
            out.append(RowDesc(row.block, row.p_index, row.param, row.kind,
                               label, row.value, row.options))
    return out
