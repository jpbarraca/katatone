"""Which parameters of the patch each key of the model's reply sets.

A reply has one object per section (``amp``, ``booster``, ``mod``, ``fx``, ``delay``,
``reverb``, ``eq``, ``noise_suppressor``).  A section maps its keys to the parameters of
the patch: the effects live in blocks per colour variation (green, red, yellow), and
the one the patch has selected is the one that sounds, so that is the one written.
``mod``, ``fx`` and ``eq`` also take ``params``: the settings of the effect (or EQ) type
chosen, which sit in a block that holds the parameters of every type.

The prompt's reference of names and ranges is generated from the same tables, so the
two cannot differ.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from ..address.map import Param, Region, load_map
from ..gui import model
from ..model.paramset import ParamSet

#: The sections in the order the editor shows them.
SECTIONS = ("amp", "booster", "mod", "fx", "delay", "reverb", "eq", "noise_suppressor")
VARIATIONS = model.VARIATIONS                       # ("GREEN", "RED", "YELLOW")

_COLOUR = {"booster": 0, "mod": 1, "fx": 2, "delay": 3, "reverb": 4}      # COLOR block index
_SWITCH = {"booster": "SW_BOOSTER_SW", "mod": "SW_MOD_SW", "fx": "SW_FX_SW",
           "delay": "SW_DELAY_SW", "reverb": "SW_REVERB_SW", "eq": "EQ_EACH_SW",
           "noise_suppressor": "NS_SW"}
_TITLE = {"amp": "Amp", "booster": "Booster", "mod": "Mod", "fx": "FX", "delay": "Delay",
          "reverb": "Reverb", "eq": "EQ 1", "noise_suppressor": "Noise suppressor"}
#: reply key -> parameter name after the block's prefix, for the keys the reply spells differently.
_ALIASES = {"time_ms": "time", "level": "effect_level"}
#: The plain keys of each section: reply key -> parameter name after the block's prefix.
_PLAIN = {
    "amp": ("AMP", "AMP_", ("type", "gain", "volume", "bass", "middle", "treble", "presence",
                            "resonance")),
    "booster": ("BOOSTER({n})", "BOOSTER_", ("type", "drive", "bottom", "tone", "level")),
    "mod": ("FX({n})", "FX_", ("type",)),
    "fx": ("FX({n})", "FX_", ("type",)),
    "delay": ("DELAY({n})", "DELAY_", ("type", "time_ms", "feedback", "level")),
    "reverb": ("REVERB({n})", "REVERB_", ("type", "time", "level")),
    "eq": ("EQ_EACH(1)", "EQ_EACH_", ("position", "type")),
    "noise_suppressor": ("NS", "NS_", ("threshold",)),
}
#: Where the effect settings of ``params`` live: FX_DETAIL block (per colour) of mod and fx.
_FX_FIRST_BLOCK = {"mod": 1, "fx": 4}
#: EQ type value -> its settings block and that block's prefix.
_EQ_BLOCKS = {0: ("EQ_PEQ(1)", "EQ_PEQ_"), 1: ("EQ_GE10(1)", "EQ_GE10_")}


@dataclass(frozen=True)
class Field:
    """One parameter of the patch a reply key sets."""
    key: str                            # the reply's key
    block: str
    p_index: int
    param: Param
    names: tuple[str, ...] | None       # the option names of a selector, else None

    @property
    def label(self) -> str:
        """The key as words ("Effect Level")."""
        return self.key.removesuffix("_ms").replace("_", " ").title()

    def text(self, value: int) -> str:
        """``value`` as shown: the option's name when the parameter has them."""
        i = value - self.param.min
        if self.names and 0 <= i < len(self.names):
            return self.names[i]
        return str(value)


#: The frequencies of the parametric EQs' FREQ parameters (value 0..27), a third of an octave apart.
_THIRD_OCTAVES = ("20.0Hz", "25.0Hz", "31.5Hz", "40.0Hz", "50.0Hz", "63.0Hz", "80.0Hz", "100Hz",
                  "125Hz", "160Hz", "200Hz", "250Hz", "315Hz", "400Hz", "500Hz", "630Hz", "800Hz",
                  "1.00kHz", "1.25kHz", "1.60kHz", "2.00kHz", "2.50kHz", "3.15kHz", "4.00kHz",
                  "5.00kHz", "6.30kHz", "8.00kHz", "10.0kHz")


def _region() -> Region:
    return load_map()["PATCH"]


def _names(p: Param) -> tuple[str, ...] | None:
    """The option names of a parameter that is a selector, else None."""
    if p.max <= p.min:
        return None
    names = model._combo_labels(p.name, p.max - p.min + 1)
    if names is None and p.name.endswith("_FREQ") and (p.min, p.max) == (0, 27):
        return _THIRD_OCTAVES
    return names


def _field(region: Region, block: str, name: str, key: str) -> Field | None:
    """The field of ``block`` whose parameter is called ``PRMID_PATCH_<name>`` (any case)."""
    wanted = ("PRMID_PATCH_" + name).lower()
    for i, p in enumerate(region.block(block).params):
        if p.name.lower() == wanted:
            return Field(key, block, i, p, _names(p))
    return None


def _fields(region: Region, block: str, prefix: str, keys) -> dict[str, Field]:
    """The fields of ``block`` for ``keys`` (a reply key, or the parameter's own name)."""
    out = {}
    for key in keys:
        f = _field(region, block, prefix + _ALIASES.get(key, key), key)
        if f is not None:
            out[key] = f
    return out


def colour_of(ps: ParamSet, section: str) -> int:
    """The selected variation (0 green, 1 red, 2 yellow) of an effect section."""
    return ps.get_param(_region(), "COLOR", _COLOUR[section])


@dataclass
class Resolved:
    """The parameters one section of a reply can set on one patch."""
    key: str
    title: str                          # "Delay · RED"
    switch: Field | None                # the section's on/off switch
    plain: dict[str, Field]             # reply key -> field (type first)
    detail: Callable[[int], dict[str, Field]] | None = None     # type value -> params of that type
    type_field: Field | None = None


def _group_keys(region: Region, block: str) -> list[str]:
    return model.fx_detail_groups(region, block)


def _block_fields(region: Region, block: str, prefix: str) -> dict[str, Field]:
    """The parameters of ``block`` whose names start with ``PRMID_PATCH_<prefix>``, keyed by the
    rest of the name in lower case."""
    full = "PRMID_PATCH_" + prefix
    out = {}
    for i, p in enumerate(region.block(block).params):
        if p.name.startswith(full):
            key = p.name[len(full):].lower()
            out[key] = Field(key, block, i, p, _names(p))
    return out


def _detail_fields(region: Region, block: str, group: str) -> dict[str, Field]:
    """The parameters of one effect (``group``) in an FX_DETAIL block, by short name."""
    return _block_fields(region, block, f"FX_DETAIL_{group}_")


def resolve(section: str, ps: ParamSet) -> Resolved:
    """Work out the fields of ``section`` for the patch ``ps`` (its selected variations).

    Args:
        section: one of :data:`SECTIONS`.
        ps: the patch the reply is for.
    """
    region = _region()
    block_fmt, prefix, keys = _PLAIN[section]
    colour = colour_of(ps, section) if section in _COLOUR else 0
    n = colour + 1 + (3 if section == "fx" else 0)
    block = block_fmt.format(n=n)
    title = _TITLE[section] + (f" · {VARIATIONS[colour]}" if section in _COLOUR else "")
    plain = _fields(region, block, prefix, keys)
    switch = None
    if section in _SWITCH:
        sw_block = "EQ_EACH(1)" if section == "eq" else "NS" if section == "noise_suppressor" else "SW"
        switch = _field(region, sw_block, _SWITCH[section], "on")
    res = Resolved(section, title, switch, plain, type_field=plain.get("type"))
    if section in ("mod", "fx"):
        detail = block.replace("FX(", "FX_DETAIL(")
        groups = _group_keys(region, detail)
        res.detail = lambda t: (_detail_fields(region, detail, groups[t])
                                if 0 <= t < len(groups) else {})
    elif section == "eq":
        res.detail = lambda t: _block_fields(region, *_EQ_BLOCKS[t]) if t in _EQ_BLOCKS else {}
    return res


# ----------------------------------------------------------------- prompt reference


def _range(f: Field) -> str:
    if f.names:
        return "{" + "|".join(f.names) + "}"
    return f"{f.param.min}..{f.param.max}"


def _line(label: str, fields) -> str:
    return f"{label}: " + ", ".join(f"{f.key} {_range(f)}" for f in fields)


def reference(ps: ParamSet) -> str:
    """The names and ranges of every plain key, one line per section (for the prompt)."""
    lines = []
    for s in SECTIONS:
        r = resolve(s, ps)
        lines.append("fx: type as in mod" if s == "fx" else _line(s, r.plain.values()))
    return "\n".join(lines)


def fx_catalogue(ps: ParamSet) -> str:
    """The settings of every mod/FX effect, one line per effect (for the prompt)."""
    region = _region()
    r = resolve("mod", ps)
    groups = _group_keys(region, "FX_DETAIL(1)")
    lines = []
    for t, name in enumerate(r.type_field.names):
        fields = _detail_fields(region, "FX_DETAIL(1)", groups[t]).values() if t < len(groups) else ()
        lines.append(_line(name, fields))
    return "\n".join(lines)


def eq_catalogue(ps: ParamSet) -> str:
    """The settings of both EQ types, one line each (for the prompt)."""
    r = resolve("eq", ps)
    names = r.plain["type"].names
    return "\n".join(_line(names[t], r.detail(t).values()) for t in sorted(_EQ_BLOCKS))


_NORM = re.compile(r"[^a-z0-9.]+")
_HZ = re.compile(r"^(\d+(?:\.\d+)?)(k?)(?:hz)?$")


def normal(text: str) -> str:
    """A name for comparing: lower case, no spaces or punctuation (``T-Scream`` -> ``tscream``)."""
    return _NORM.sub("", str(text).lower())


def number_of(text: str) -> float | None:
    """The value of "630Hz", "1.25kHz" or "2" (in Hz), None when ``text`` is not one."""
    m = _HZ.match(normal(text))
    return float(m.group(1)) * (1000 if m.group(2) else 1) if m else None
