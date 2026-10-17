"""Mappings from controller messages to amp actions.

A `Profile` is a list of `Mapping`s; each pairs a `Trigger` (which message,
from which device) with an `Action` (switch channel, toggle an effect, drive
an expression target). Everything here is pure data and logic: no I/O, no
threads, so it is fully unit-testable.

Mode semantics (`Trigger.mode`):

* ``press``  - fires once on the *rising edge* (value goes from below to at or
  above ``threshold``; NoteOn is rising, NoteOff falling; a Program Change
  always fires). With ``repeat`` set it fires on *every* message at or above the
  threshold instead, for pedals that only ever send 127 and never a release.
* ``toggle`` - like ``press`` but keeps a boolean that flips on each fire; the
  action receives the new state.
* ``range``  - every event fires with the value 0-127; the router scales it to
  the target's range with `scale_range`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar, Union

from ..address.map import Param, load_map
from .events import (ChannelPressure, ControlChange, MidiEvent, NoteOff, NoteOn,
                     PitchBend, ProgramChange)

PROFILE_VERSION = 1

KINDS = ("note", "cc", "pc", "pitchbend", "pressure")
MODES = ("press", "toggle", "range")
CURVES = ("linear", "log")

# --------------------------------------------------------------------------
# Effects and expression targets (names resolved against the address map)
# --------------------------------------------------------------------------

# Parameter names in the address map carry this prefix; tables below omit it.
_PARAM_PREFIX = "PRMID_PATCH_"
# Writes always go to the edit buffer (region PATCH), never to a stored slot.
EDIT_REGION = "PATCH"


@dataclass(frozen=True)
class EffectDef:
    """Where an effect's on/off switch lives in the edit buffer."""

    block: str
    param: str
    label: str


EFFECTS: dict[str, EffectDef] = {
    "BOOST": EffectDef("SW", "SW_BOOSTER_SW", "Booster"),
    "MOD": EffectDef("SW", "SW_MOD_SW", "Mod"),
    "FX": EffectDef("SW", "SW_FX_SW", "FX"),
    "DELAY": EffectDef("SW", "SW_DELAY_SW", "Delay"),
    "DELAY2": EffectDef("SW", "SW_DELAY2_SW", "Delay 2"),
    "REVERB": EffectDef("SW", "SW_REVERB_SW", "Reverb"),
    "SEND_RETURN": EffectDef("SENDRETURN", "SENDRETURN_SW", "Send/Return"),
    "SOLO": EffectDef("SOLO_COM", "SOLO_COM_SW", "Solo"),
    "NS": EffectDef("NS", "NS_SW", "Noise suppressor"),
    "PEDAL_FX": EffectDef("PEDALFX_COM", "PEDALFX_COM_SW", "Pedal FX"),
}

EFFECT_OPS = ("toggle", "on", "off", "momentary")

# PEDALFX_COM_TYPE value -> the parameter holding that pedal's position.
PEDAL_TYPE = EffectDef("PEDALFX_COM", "PEDALFX_COM_TYPE", "Pedal FX type")
PEDAL_FX_POSITION_PARAMS: dict[int, EffectDef] = {
    0: EffectDef("PEDALFX", "PEDALFX_PEDALWAH_PEDAL_POSITION", "Pedal wah position"),
    1: EffectDef("PEDALFX", "PEDALFX_BEND_PEDAL_POS", "Pedal bend position"),
    2: EffectDef("PEDALFX", "PEDALFX_EWAH_PEDAL_POS", "Wah 95E position"),
}
VOLUME_PARAM = EffectDef("AMP", "AMP_VOLUME", "Amp volume")


def find_param(block: str, name: str) -> int:
    """Index of a parameter within a block of the edit buffer.

    Args:
        block: the block name in region PATCH (e.g. "SW").
        name: the parameter name, with or without the ``PRMID_PATCH_`` prefix.

    Returns:
        The index into ``Block.params`` (the ``p_index`` used by writes).

    Raises:
        KeyError with a clear message if the block or parameter is absent
        (the address map changed under us).
    """
    full = name if name.startswith("PRMID_") else _PARAM_PREFIX + name
    try:
        blk = load_map()[EDIT_REGION].block(block)
    except KeyError:
        raise KeyError(f"block {block!r} not found in region {EDIT_REGION}") from None
    for i, p in enumerate(blk.params):
        if p.name == full:
            return i
    raise KeyError(f"parameter {full!r} not found in block {block!r}")


def effect_param(effect: str) -> tuple[str, int, Param]:
    """Resolve an effect id to its on/off switch.

    Args:
        effect: a key of `EFFECTS`.

    Returns:
        ``(block, p_index, param)``.

    Raises:
        KeyError for an unknown effect id.
    """
    if effect not in EFFECTS:
        raise KeyError(f"unknown effect {effect!r}")
    d = EFFECTS[effect]
    i = find_param(d.block, d.param)
    return d.block, i, load_map()[EDIT_REGION].block(d.block).params[i]


@dataclass
class ExprTarget:
    """What an expression pedal drives.

    ``kind`` is ``pedal_fx`` (the position of whichever pedal effect is
    selected: wah, bend or 95E), ``volume`` (amp volume) or ``param`` (any
    numeric patch parameter, named by ``block`` and ``name``).
    """

    kind: str = "pedal_fx"
    block: str | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the profile JSON."""
        d: dict[str, Any] = {"kind": self.kind}
        if self.kind == "param":
            d.update(block=self.block, name=self.name)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExprTarget":
        """Rebuild from `to_dict` output.

        Args:
            d: the dict.

        Returns:
            The target.

        Raises:
            ValueError for an unknown kind or an incomplete ``param`` target.
        """
        kind = d.get("kind")
        if kind not in ("pedal_fx", "volume", "param"):
            raise ValueError(f"unknown expression target {kind!r}")
        if kind == "param" and not (d.get("block") and d.get("name")):
            raise ValueError("param target needs block and name")
        return cls(kind, d.get("block"), d.get("name"))

    def label(self) -> str:
        """Short text for the UI."""
        if self.kind == "pedal_fx":
            return "Pedal FX position"
        if self.kind == "volume":
            return "Volume"
        return f"{self.block}: {self.name}"


def pedal_fx_type(raw_type: int | None) -> int:
    """Clamp a PEDALFX_COM_TYPE value to a known pedal type (0 when unknown).

    Args:
        raw_type: the type read from the patch, or None if not known.

    Returns:
        0 (wah), 1 (bend) or 2 (95E).
    """
    return raw_type if raw_type in PEDAL_FX_POSITION_PARAMS else 0


def resolve_expression(target: ExprTarget,
                       pedal_type: int | None = None) -> tuple[str, int, Param]:
    """Resolve an expression target to the edit-buffer parameter it drives.

    Args:
        target: what the pedal drives.
        pedal_type: the patch's PEDALFX_COM_TYPE when known (only used for
            ``pedal_fx``; unknown means PEDAL WAH).

    Returns:
        ``(block, p_index, param)``; the Param carries the min/max.

    Raises:
        KeyError if the parameter does not exist in the address map.
    """
    if target.kind == "pedal_fx":
        d = PEDAL_FX_POSITION_PARAMS[pedal_fx_type(pedal_type)]
        block, name = d.block, d.param
    elif target.kind == "volume":
        block, name = VOLUME_PARAM.block, VOLUME_PARAM.param
    else:
        block, name = target.block or "", target.name or ""
    i = find_param(block, name)
    return block, i, load_map()[EDIT_REGION].block(block).params[i]


def numeric_params() -> list[tuple[str, str, Param]]:
    """Every numeric edit-buffer parameter a pedal could drive.

    Skips the patch name, constants, the FX_DETAIL blocks (their meaning
    depends on the effect type) and read-only blocks.

    Returns:
        ``(block, short_name, param)`` in editor order; ``short_name`` has no
        ``PRMID_PATCH_`` prefix, so it can go straight into an `ExprTarget`.
    """
    from ..amp.read import PATCH_BLOCKS
    region = load_map()[EDIT_REGION]
    out = []
    for bname in PATCH_BLOCKS:
        if bname == "COM" or bname.startswith(("FX_DETAIL", "PATCH_KNOB")):
            continue
        for p in region.block(bname).params:
            if p.size in (1, 2, 4) and p.max > p.min:
                out.append((bname, p.name.removeprefix(_PARAM_PREFIX), p))
    return out


def scale_range(value: int, lo: int, hi: int, invert: bool = False,
                curve: str = "linear") -> int:
    """Scale a controller value 0-127 onto ``lo..hi``.

    Args:
        value: the controller value, 0-127.
        lo: output at value 0.
        hi: output at value 127.
        invert: swap ``lo`` and ``hi`` (a pedal wired the other way round).
        curve: "linear", or "log" (``(v/127)**2``: finer control at the low end).

    Returns:
        The rounded output, clamped between ``lo`` and ``hi``.
    """
    if invert:
        lo, hi = hi, lo
    x = max(0, min(127, value)) / 127
    if curve == "log":
        x = x * x
    out = round(lo + (hi - lo) * x)
    return max(min(lo, hi), min(max(lo, hi), out))


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------


@dataclass
class ChannelSelect:
    """Switch the amp to one channel (a patch number)."""

    type: ClassVar[str] = "channel_select"
    slot: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the profile JSON."""
        return {"type": self.type, "slot": self.slot}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChannelSelect":
        """Rebuild from `to_dict` output."""
        return cls(slot=int(d["slot"]))

    def summary(self, slot_name=str) -> str:
        """Text for the mappings table.

        Args:
            slot_name: maps a patch number to its panel name.
        """
        return f"Channel → {slot_name(self.slot)}"


@dataclass
class ChannelFromPc:
    """Pick the channel from the program number (the amp's own PC table by default)."""

    type: ClassVar[str] = "channel_from_pc"
    table: dict[int, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the profile JSON (table keys become strings)."""
        d: dict[str, Any] = {"type": self.type}
        if self.table is not None:
            d["table"] = {str(k): v for k, v in self.table.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChannelFromPc":
        """Rebuild from `to_dict` output."""
        t = d.get("table")
        return cls(None if t is None else {int(k): int(v) for k, v in t.items()})

    def summary(self, slot_name=str) -> str:
        """Text for the mappings table."""
        return "Channel from PC" + (" (custom table)" if self.table else "")


@dataclass
class BankAction:
    """Change bank, keeping the channel (see `slots.bank_switch`)."""

    type: ClassVar[str] = "bank"
    op: str = "toggle"

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the profile JSON."""
        return {"type": self.type, "op": self.op}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BankAction":
        """Rebuild from `to_dict` output."""
        return cls(op=d["op"])

    def summary(self, slot_name=str) -> str:
        """Text for the mappings table."""
        return f"Bank {self.op}"


@dataclass
class ChannelStep:
    """Walk to the next/previous channel in panel order."""

    type: ClassVar[str] = "channel_step"
    op: str = "next"

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the profile JSON."""
        return {"type": self.type, "op": self.op}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChannelStep":
        """Rebuild from `to_dict` output."""
        return cls(op=d["op"])

    def summary(self, slot_name=str) -> str:
        """Text for the mappings table."""
        return f"Channel {self.op}"


@dataclass
class EffectAction:
    """Switch an effect on/off (see `EFFECTS`)."""

    type: ClassVar[str] = "effect"
    effect: str = "BOOST"
    op: str = "toggle"

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the profile JSON."""
        return {"type": self.type, "effect": self.effect, "op": self.op}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EffectAction":
        """Rebuild from `to_dict` output."""
        return cls(effect=d["effect"], op=d["op"])

    def summary(self, slot_name=str) -> str:
        """Text for the mappings table."""
        label = EFFECTS[self.effect].label if self.effect in EFFECTS else self.effect
        return f"{label} {self.op}"


@dataclass
class ExpressionAction:
    """Drive a parameter continuously from a controller value."""

    type: ClassVar[str] = "expression"
    target: ExprTarget = field(default_factory=ExprTarget)
    lo: int | None = None
    hi: int | None = None
    invert: bool = False
    curve: str = "linear"

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the profile JSON."""
        return {"type": self.type, "target": self.target.to_dict(),
                "lo": self.lo, "hi": self.hi, "invert": self.invert,
                "curve": self.curve}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExpressionAction":
        """Rebuild from `to_dict` output."""
        return cls(target=ExprTarget.from_dict(d["target"]), lo=d.get("lo"),
                   hi=d.get("hi"), invert=bool(d.get("invert", False)),
                   curve=d.get("curve", "linear"))

    def summary(self, slot_name=str) -> str:
        """Text for the mappings table."""
        return f"Expression → {self.target.label()}"

    def bounds(self, param: Param) -> tuple[int, int]:
        """The output range: explicit ``lo``/``hi``, else the parameter's own.

        Args:
            param: the target parameter.

        Returns:
            ``(lo, hi)`` clamped into the parameter's min..max.
        """
        lo = param.min if self.lo is None else self.lo
        hi = param.max if self.hi is None else self.hi
        return (max(param.min, min(param.max, lo)),
                max(param.min, min(param.max, hi)))


@dataclass
class AmpNative:
    """Forward a CC/PC to the amp's own MIDI map."""

    type: ClassVar[str] = "amp_native"
    kind: str = "cc"
    number: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the profile JSON."""
        return {"type": self.type, "kind": self.kind, "number": self.number}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AmpNative":
        """Rebuild from `to_dict` output."""
        return cls(kind=d["kind"], number=d.get("number"))

    def summary(self, slot_name=str) -> str:
        """Text for the mappings table."""
        return f"Amp native {self.kind.upper()}" + (
            f" {self.number}" if self.number is not None else "")


@dataclass
class UnknownAction:
    """An action type this version does not know; kept verbatim so saving the
    profile does not lose it (forward compatibility)."""

    type: ClassVar[str] = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the original dict unchanged."""
        return dict(self.raw)

    def summary(self, slot_name=str) -> str:
        """Text for the mappings table."""
        return f"Unsupported action ({self.raw.get('type', '?')})"


Action = Union[ChannelSelect, ChannelFromPc, BankAction, ChannelStep,
               EffectAction, ExpressionAction, AmpNative, UnknownAction]

ACTION_TYPES: dict[str, type] = {c.type: c for c in (
    ChannelSelect, ChannelFromPc, BankAction, ChannelStep, EffectAction,
    ExpressionAction, AmpNative)}


def action_from_dict(d: dict[str, Any]) -> Action:
    """Rebuild an action; unknown or malformed ones become `UnknownAction`.

    Args:
        d: the serialised action.

    Returns:
        The action object.
    """
    cls = ACTION_TYPES.get(d.get("type", ""))
    if cls is None:
        return UnknownAction(dict(d))
    try:
        return cls.from_dict(d)
    except (KeyError, TypeError, ValueError):
        return UnknownAction(dict(d))


# --------------------------------------------------------------------------
# Triggers and mappings
# --------------------------------------------------------------------------


def _scale_bend(value: int) -> int:
    """Pitch bend -8192..8191 -> 0..127."""
    return round((value + 8192) * 127 / 16383)


@dataclass
class Trigger:
    """Which controller message fires a mapping.

    ``None`` for ``device`` / ``channel`` / ``number`` means "any".
    ``device`` is a case-insensitive substring of the port name.
    """

    device: str | None = None
    channel: int | None = None      # 0-15 (the UI shows 1-16)
    kind: str = "pc"
    number: int | None = None       # note / control / program
    mode: str = "press"
    threshold: int = 64
    repeat: bool = False            # press/toggle: fire on every message >= threshold

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the profile JSON."""
        return {"device": self.device, "channel": self.channel,
                "kind": self.kind, "number": self.number, "mode": self.mode,
                "threshold": self.threshold, "repeat": self.repeat}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Trigger":
        """Rebuild from `to_dict` output.

        Raises:
            ValueError for an unknown kind or mode.
        """
        t = cls(device=d.get("device"), channel=d.get("channel"),
                kind=d.get("kind", "pc"), number=d.get("number"),
                mode=d.get("mode", "press"), threshold=int(d.get("threshold", 64)),
                repeat=bool(d.get("repeat", False)))
        if t.kind not in KINDS:
            raise ValueError(f"unknown trigger kind {t.kind!r}")
        if t.mode not in MODES:
            raise ValueError(f"unknown trigger mode {t.mode!r}")
        return t

    def matches(self, device: str, ev: MidiEvent) -> bool:
        """Whether `ev` from `device` is the message this trigger describes.

        Args:
            device: the port label the event came from.
            ev: the parsed event.

        Returns:
            True when device, channel, kind and number all agree.
        """
        if self.device and self.device.lower() not in device.lower():
            return False
        if self.channel is not None and ev.ch != self.channel:
            return False
        if self.kind == "note":
            if not isinstance(ev, (NoteOn, NoteOff)):
                return False
            num = ev.note
        elif self.kind == "cc":
            if not isinstance(ev, ControlChange):
                return False
            num = ev.control
        elif self.kind == "pc":
            if not isinstance(ev, ProgramChange):
                return False
            num = ev.program
        elif self.kind == "pitchbend":
            return isinstance(ev, PitchBend)
        else:
            return isinstance(ev, ChannelPressure)
        return self.number is None or self.number == num

    def summary(self) -> str:
        """Text for the mappings table, e.g. "SINCO · ch 1 · PC 2 · press"."""
        parts = [self.device or "any device",
                 "any ch" if self.channel is None else f"ch {self.channel + 1}"]
        names = {"note": "Note", "cc": "CC", "pc": "PC",
                 "pitchbend": "Pitch bend", "pressure": "Pressure"}
        what = names[self.kind]
        if self.kind in ("note", "cc", "pc"):
            what += " any" if self.number is None else f" {self.number}"
        parts += [what, self.mode]
        return " · ".join(parts)


def _new_id() -> str:
    """A fresh mapping id."""
    return uuid.uuid4().hex


@dataclass
class Mapping:
    """One trigger -> action rule."""

    trigger: Trigger
    action: Action
    label: str = ""
    enabled: bool = True
    id: str = field(default_factory=_new_id)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the profile JSON."""
        return {"id": self.id, "label": self.label, "enabled": self.enabled,
                "trigger": self.trigger.to_dict(), "action": self.action.to_dict()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Mapping":
        """Rebuild from `to_dict` output; an unknown action type loads disabled.

        Raises:
            ValueError / KeyError if the trigger itself is malformed.
        """
        action = action_from_dict(d.get("action", {}))
        enabled = bool(d.get("enabled", True)) and not isinstance(action, UnknownAction)
        return cls(trigger=Trigger.from_dict(d["trigger"]), action=action,
                   label=d.get("label", ""), enabled=enabled,
                   id=d.get("id") or _new_id())


def _both_edges(m: Mapping) -> bool:
    """Whether a mapping also needs the release (a momentary effect)."""
    return isinstance(m.action, EffectAction) and m.action.op == "momentary"


@dataclass
class Profile:
    """A named set of mappings plus the controllers to open."""

    name: str = "Default"
    devices: list[str] = field(default_factory=list)
    mappings: list[Mapping] = field(default_factory=list)
    version: int = PROFILE_VERSION
    # Per-mapping edge / toggle memory, keyed by mapping id (not saved).
    _down: dict[str, bool] = field(default_factory=dict, repr=False, compare=False)
    _toggle: dict[str, bool] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the profile JSON."""
        return {"version": self.version, "name": self.name,
                "devices": list(self.devices),
                "mappings": [m.to_dict() for m in self.mappings]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Profile":
        """Rebuild from `to_dict` output.

        Mappings whose trigger is unreadable are skipped (and so lost on the
        next save); unknown *actions* are kept, disabled.

        Args:
            d: the parsed JSON.

        Returns:
            The profile.

        Raises:
            ValueError if the file is from a newer, incompatible version.
        """
        version = int(d.get("version", PROFILE_VERSION))
        if version > PROFILE_VERSION:
            raise ValueError(f"profile version {version} is newer than supported")
        maps = []
        for md in d.get("mappings", []):
            try:
                maps.append(Mapping.from_dict(md))
            except (KeyError, TypeError, ValueError):
                continue
        return cls(name=d.get("name", "Default"),
                   devices=list(d.get("devices", [])), mappings=maps,
                   version=PROFILE_VERSION)

    def reset_state(self) -> None:
        """Forget edge and toggle memory (after editing mappings)."""
        self._down.clear()
        self._toggle.clear()

    def match(self, device: str, ev: MidiEvent) -> list[tuple[Mapping, int | bool]]:
        """The mappings `ev` fires, with the value each should act on.

        Stateful (edge and toggle memory per mapping id) but does no I/O.

        Args:
            device: the port label the event came from.
            ev: the parsed event.

        Returns:
            ``(mapping, value)`` in mapping order. ``value`` is the new on/off
            state for ``toggle`` mappings, ``True``/``False`` for ``press``
            (``False`` only for the release of a momentary effect), and the
            controller value 0-127 for ``range``.
        """
        out: list[tuple[Mapping, int | bool]] = []
        for m in self.mappings:
            if not m.enabled or not m.trigger.matches(device, ev):
                continue
            v = self._fire(m, ev)
            if v is not None:
                out.append((m, v))
        return out

    def _fire(self, m: Mapping, ev: MidiEvent) -> int | bool | None:
        """Apply one mapping's mode to an event it matches.

        Args:
            m: the mapping (already matched on device/channel/kind/number).
            ev: the event.

        Returns:
            The value to act on, or None when the mapping does not fire.
        """
        t = m.trigger
        value = _value_of(ev)
        if t.mode == "range":
            return value
        if isinstance(ev, ProgramChange):
            down, edge_only = True, False       # a PC is a press with no release
        elif isinstance(ev, NoteOn):
            down, edge_only = True, True
        elif isinstance(ev, NoteOff):
            down, edge_only = False, True
        else:
            down, edge_only = value >= t.threshold, True
        prev = self._down.get(m.id, False)
        if edge_only:
            self._down[m.id] = down
        rising = down and (t.repeat or not prev) if edge_only else down
        falling = prev and not down
        if t.mode == "toggle":
            if not rising:
                return None
            state = not self._toggle.get(m.id, False)
            self._toggle[m.id] = state
            return state
        if rising:
            return True
        if falling and _both_edges(m):
            return False
        return None


def _value_of(ev: MidiEvent) -> int:
    """The 0-127 value a controller message carries.

    Args:
        ev: any event.

    Returns:
        Velocity (NoteOn), 0 (NoteOff), the CC/pressure value, the program
        number, or the scaled pitch bend.
    """
    if isinstance(ev, NoteOn):
        return ev.velocity
    if isinstance(ev, NoteOff):
        return 0
    if isinstance(ev, ControlChange):
        return ev.value
    if isinstance(ev, ProgramChange):
        return ev.program
    if isinstance(ev, PitchBend):
        return _scale_bend(ev.value)
    return ev.value


def parse_pc_table(text: str, model: int) -> dict[int, int]:
    """Parse a program-to-channel table typed by the user.

    Args:
        text: comma-separated ``program=channel`` pairs where the channel is a
            panel name (PN, 1A, 2B, ... or "Panel") or a patch number,
            e.g. ``"0=1A, 2=2A, 4=PN"``.
        model: the model code (decides which panel names exist).

    Returns:
        ``{program: patch number}``.

    Raises:
        ValueError naming the offending entry.
    """
    from ..amp import slots as _slots
    names = {s.short.upper(): s.index for s in _slots.slots(model)}
    names["PANEL"] = 0
    out: dict[int, int] = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        prog, sep, chan = (x.strip() for x in part.partition("="))
        if not sep:
            raise ValueError(f"{part!r}: expected program=channel")
        try:
            program = int(prog)
        except ValueError:
            raise ValueError(f"{part!r}: program must be a number") from None
        if not 0 <= program <= 127:
            raise ValueError(f"{part!r}: program must be 0-127")
        if chan.upper() in names:
            out[program] = names[chan.upper()]
        else:
            raise ValueError(f"{part!r}: unknown channel {chan!r}")
    return out


def format_pc_table(table: dict[int, int], model: int) -> str:
    """Inverse of `parse_pc_table`.

    Args:
        table: ``{program: patch number}``.
        model: the model code.

    Returns:
        e.g. ``"0=1A, 2=2A"``.
    """
    from ..amp import slots as _slots
    return ", ".join(f"{k}={_slots.slot(v, model).short}" for k, v in sorted(table.items()))
