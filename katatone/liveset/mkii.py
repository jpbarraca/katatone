"""Import of KATANA MkII livesets: each patch is converted to the Gen 3 layout.

A MkII patch is a set of blocks (``UserPatch%Patch_0``, ``UserPatch%Fx(1)``, ...) with
its own parameter positions; a Gen 3 patch has 80 other blocks (``PATCH%AMP``,
``PATCH%BOOSTER(1)``, ...).  ``assets/mkii_map.json`` says, for every parameter, where
it is on each model and in which order to carry them over, plus the tables that turn
a MkII amp or effect type into the Gen 3 one:

* most values are copied as they are; a Gen 3 booster, effect, delay or reverb slot has
  three variants (green, red, yellow) where the MkII has one, and a value goes to
  all three;
* the type of each variant comes from the MkII's own per-colour assignment, through the
  type tables;
* amp types, the types the pedals and knobs are assigned to, and a few LED and contour
  values are renumbered.

What the Gen 3 does not have is left at its default, and a value with no equivalent is
reported (see :class:`Conversion`), never guessed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..address.map import load_map
from ..model.paramset import ParamSet
from .tsl import DEVICE, FORMAT_REV, Tsl

MKII_DEVICE = "KATANA MkII"
DATA = Path(__file__).resolve().parent.parent / "assets" / "mkii_map.json"
#: Length of a FX_DETAIL block in a Gen 3 liveset (the map declares more than the amp holds).
DETAIL_LENGTH = 225
_SIZE = {"1x7": 1, "2x7": 2, "2x4": 2, "4x4": 4}
#: What a MkII 2x7 value becomes on the Gen 3: four nibbles.
_SIZE_GEN3 = {"1x7": 1, "2x7": 4, "2x4": 2, "4x4": 4}


def is_mkii(device: str | None) -> bool:
    """Whether a liveset's ``device`` is a KATANA MkII."""
    return device == MKII_DEVICE


@lru_cache(maxsize=1)
def _data() -> dict:
    """The conversion tables (see the module docstring)."""
    return json.loads(DATA.read_text())


@lru_cache(maxsize=1)
def _entries() -> tuple[list[dict], dict[str, dict]]:
    """The parameters that have a MkII position, in the order they are carried over, and
    those (with the few that only the Gen 3 has) by key."""
    data = _data()
    entries = data["entries"]
    return entries, {e["key"]: e for e in entries + data["gen3_only"]}


@lru_cache(maxsize=1)
def _rules() -> dict:
    """The special cases, keyed by the parameter they apply to."""
    r = _data()["rules"]
    out: dict = {"variant": {v["source"]: v for v in r["variants"]},
                 "decrement": set(r["decrement"]), "enum": {}}
    for group in r["remap"]:
        for key in group["keys"]:
            out["enum"][key] = group["enum"]
    return out


@lru_cache(maxsize=1)
def _defaults() -> dict[str, bytes]:
    """A Gen 3 patch with every parameter at its default."""
    blocks = {}
    for b in load_map()["PATCH"].blocks:
        raw = bytearray(DETAIL_LENGTH if b.name.startswith("FX_DETAIL") else b.length)
        for i, p in enumerate(b.params):
            if isinstance(p.init, int) and p.off + p.size <= len(raw):
                ParamSet.encode(b, raw, i, p.init)
        blocks["PATCH%" + b.name] = bytes(raw)
    return blocks


def _read(blocks: dict[str, bytes], block: str, index: int, kind: str) -> int | None:
    """A MkII value, or None if the patch does not hold that block or byte."""
    data = blocks.get(block)
    n = _SIZE[kind]
    if data is None or index + n > len(data):
        return None
    chunk = data[index:index + n]
    if kind == "1x7":
        return chunk[0]
    if kind == "2x7":
        return chunk[0] * 128 + chunk[1]
    value = 0
    for nibble in chunk:                       # 2x4 and 4x4: one nibble per byte
        value = (value << 4) | (nibble & 15)
    return value


def _write(out: dict[str, bytearray], block: str, index: int, kind: str, value: int) -> bool:
    """Store a value in a Gen 3 block; False if the block has no room for it."""
    data = out.get(block)
    n = _SIZE_GEN3[kind]
    if data is None or index + n > len(data):
        return False
    if kind == "1x7":
        data[index] = value & 127
    else:
        for k in range(n):                     # nibbles, most significant first
            data[index + k] = (value >> (4 * (n - 1 - k))) & 15
    return True


#: How the notes call the things that are renumbered (a parameter's key means nothing to a user).
_TYPE_NAMES = {"OD_DS_TYPE": "booster type", "FX1_FX_TYPE": "mod type", "FX2_FX_TYPE": "FX type",
               "DELAY_TYPE": "delay type", "DELAY2_TYPE": "delay 2 type", "REVERB_TYPE": "reverb type"}
_COLOURS = ("green", "red", "yellow")


def _remap(table: str, value: int) -> tuple[int, str, str] | None:
    """The Gen 3 value for a MkII one: ``(value, MkII label, Gen 3 label)``, or None.

    The first row with that MkII value wins, as in the type tables' own order.
    """
    rows = _data()["enums"][table]
    for label, mk2, mk3 in rows:
        if mk2 == value:
            target = next((l for l, _, m in rows if m == mk3), label)
            return mk3, label, target
    return None


@dataclass
class Conversion:
    """The result of converting one patch.

    Attributes:
        patch: the Gen 3 patch.
        copied: how many values were carried over.
        notes: what to know about the result, e.g. amp types that were approximated or values
            with no Gen 3 equivalent.
    """
    patch: ParamSet
    copied: int = 0
    notes: list[str] = field(default_factory=list)


def convert_patch(blocks: dict[str, bytes], name: str = "") -> Conversion:
    """Convert one MkII patch.

    Args:
        blocks: the MkII patch's blocks by name (``UserPatch%...``), as bytes.
        name: what to call the patch in the notes.
    Returns:
        The Gen 3 patch and what to know about it.
    """
    entries, by_key = _entries()
    rules = _rules()
    out = {k: bytearray(v) for k, v in _defaults().items()}
    result = Conversion(ParamSet())
    label = f"{name}: " if name else ""

    def put(entry: dict, value: int, bank: int | None = None) -> None:
        """Write to every variant of a parameter, or to one."""
        if "mk3" not in entry:
            return
        targets = entry["mk3"] if bank is None else [entry["mk3"][bank]]
        for block in targets:
            if _write(out, block, entry["i3"], entry["type"], value):
                result.copied += 1

    def renumber(table: str, what: str, value: int) -> int | None:
        """The Gen 3 number for a MkII one; None (and a note) when there is none."""
        found = _remap(table, value)
        if found is None:
            result.notes.append(f"{label}{what} {value} of the MkII has no Gen 3 equivalent")
            return None
        if table == "amp" and found[1] != found[2]:
            result.notes.append(f"{label}amp type {found[1]} became {found[2]} on the Gen 3")
        return found[0]

    for entry in entries:
        key = entry["key"]
        value = _read(blocks, entry["mk2"][0], entry["mk2"][1], entry["type"])
        if value is None:
            continue
        if key == "PREAMP_A_TYPE":
            amp = renumber("amp", "amp type", value)
            if amp is not None:
                put(entry, amp, bank=0)
            continue
        if key == "PREAMP_A_GAIN":
            continue                           # the gain comes from the knob position below
        target = entry
        if key == "PRM_KNOB_POS_GAIN":
            target = by_key["PREAMP_A_GAIN"]
        elif key in rules["variant"]:
            rule = rules["variant"][key]
            what = f"{_TYPE_NAMES.get(rule['target'], 'type')} ({_COLOURS[rule['bank']]})"
            number = value if rule["enum"] is None else renumber(rule["enum"], what, value)
            if number is not None:
                put(by_key[rule["target"]], number, bank=rule["bank"])
            continue
        elif key in rules["decrement"]:
            value = max(0, value - 1)
        elif key == "PRM_CONTOUR_SW":
            if value > 0:
                select = by_key["PRM_CONTOUR_SELECT"]["mk2"]
                chosen = _read(blocks, select[0], select[1], "1x7")
                value = (chosen or 0) + 1
        elif key in rules["enum"]:
            number = renumber(rules["enum"][key], "pedal or knob assignment", value)
            if number is None:
                continue
            value = number
        put(target, value)

    result.patch = ParamSet()
    for block, data in out.items():
        result.patch.set_block(block, bytes(data))
    return result


def _memo(memo, notes: list[str]):
    """The patch's memo with a line saying it was converted (a Tone Central memo,
    which is an object and not text, is left as it is)."""
    if not isinstance(memo, str):
        return memo
    line = "Converted from KATANA MkII by KataTone." + "".join(f"  {n}." for n in notes)
    return f"{memo}\n{line}" if memo else line


@dataclass
class ConvertedLiveset:
    """A MkII liveset converted to the Gen 3.

    Attributes:
        tsl: the Gen 3 liveset.
        notes: what to know about the conversion, per patch.
    """
    tsl: Tsl
    notes: list[str]


def convert_liveset(obj: dict) -> ConvertedLiveset:
    """Convert a parsed MkII ``.tsl`` file.

    Args:
        obj: the file's JSON.
    Raises:
        ValueError: when it is not a MkII liveset.
    """
    if not is_mkii(obj.get("device")):
        raise ValueError(f"not a {MKII_DEVICE} liveset: {obj.get('device')!r}")
    tsl = Tsl(name=obj.get("name") or "MkII liveset", format_rev=FORMAT_REV, device=DEVICE)
    notes: list[str] = []
    for n, row in enumerate((obj.get("data") or [[]])[0], start=1):
        raw = {k: bytes(int(x, 16) for x in v) for k, v in (row.get("paramSet") or {}).items()}
        title = f"patch {n}"
        if "UserPatch%PatchName" in raw:
            title = ParamSet.patch_name(raw["UserPatch%PatchName"]) or title
        done = convert_patch(raw, title)
        tsl.patches.append(Tsl.encode(done.patch))
        tsl.memos.append(_memo(row.get("memo", ""), done.notes))
        notes += done.notes
    return ConvertedLiveset(tsl, notes)
