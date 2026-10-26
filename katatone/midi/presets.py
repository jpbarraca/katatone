"""Ready-made profiles for common kinds of controller.

A preset is a starting point: the controller numbers are the usual ones for that
kind of pedal (or the standard MIDI assignments), and the mapping dialog's Learn
button retargets any of them to the pedal in front of you.  No controller is
named, so the new profile opens none; enable yours in the Controllers section.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .mapping import (ChannelFromPc, ChannelStep, EffectAction, ExprTarget,
                      ExpressionAction, Mapping, Profile, SetlistStep, Trigger)
from .store import ProfileStore

# CC 80-83 are the "general purpose" controllers of the MIDI standard, which foot
# controllers commonly send for their switches; a footswitch sends 127 when pressed.
SWITCH_CCS = (80, 81, 82, 83)
EXPRESSION_CC = 11
VOLUME_CC = 7


def _switch(cc: int, action, label: str) -> Mapping:
    """A mapping fired by the press of a footswitch that sends control ``cc``."""
    return Mapping(trigger=Trigger(kind="cc", number=cc), action=action, label=label)


def _pc_pedal() -> list[Mapping]:
    return [Mapping(trigger=Trigger(kind="pc"), action=ChannelFromPc(),
                    label="Program change → channel")]


def _channel_switches() -> list[Mapping]:
    return [_switch(80, ChannelStep("next"), "Next channel"),
            _switch(81, ChannelStep("prev"), "Previous channel")]


def _effect_switches() -> list[Mapping]:
    effects = (("BOOST", "Booster"), ("MOD", "Mod"), ("DELAY", "Delay"), ("REVERB", "Reverb"))
    return [_switch(cc, EffectAction(effect, "toggle"), label)
            for cc, (effect, label) in zip(SWITCH_CCS, effects)]


def _wah_pedal() -> list[Mapping]:
    return [Mapping(trigger=Trigger(kind="cc", number=EXPRESSION_CC, mode="range"),
                    action=ExpressionAction(ExprTarget("pedal_fx")),
                    label="Expression pedal → pedal FX")]


def _volume_pedal() -> list[Mapping]:
    return [Mapping(trigger=Trigger(kind="cc", number=VOLUME_CC, mode="range"),
                    action=ExpressionAction(ExprTarget("volume")),
                    label="Volume pedal → amp volume")]


def _setlist_switches() -> list[Mapping]:
    return [_switch(80, SetlistStep("next"), "Next setlist patch"),
            _switch(81, SetlistStep("prev"), "Previous setlist patch")]


@dataclass(frozen=True)
class Preset:
    """A named set of mappings to start a profile from."""
    name: str
    description: str
    mappings: Callable[[], list[Mapping]]

    def profile(self) -> Profile:
        """A new profile with fresh copies of the preset's mappings."""
        return Profile(name=self.name, mappings=self.mappings())


PRESETS: tuple[Preset, ...] = (
    Preset("Program change pedal",
           "Any foot controller that sends program changes: PC 0 to 9 select the amp's "
           "channels as in the amp's own table.", _pc_pedal),
    Preset("Channel up / down",
           "Two footswitches sending CC 80 and CC 81: next and previous channel.",
           _channel_switches),
    Preset("Effect switches",
           "Four footswitches sending CC 80 to 83: booster, mod, delay and reverb on / off.",
           _effect_switches),
    Preset("Wah / expression pedal",
           "An expression pedal sending CC 11 moves the amp's pedal FX (wah, bend or 95E).",
           _wah_pedal),
    Preset("Volume pedal",
           "A volume pedal sending CC 7 sets the amp volume.", _volume_pedal),
    Preset("Setlist switches",
           "Two footswitches sending CC 80 and CC 81: next and previous patch of the "
           "open setlist.", _setlist_switches),
)


def create(store: ProfileStore, preset: Preset) -> Profile:
    """Store a new profile made from ``preset``, under a free name.

    Args:
        store: where the profiles are kept.
        preset: what to start from.
    Returns:
        The stored profile (named like the preset, or "<name> 2", "<name> 3" ...
        when that name is taken).
    """
    profile = preset.profile()
    base, n = preset.name, 2
    while store.exists(profile.name):
        profile.name, n = f"{base} {n}", n + 1
    store.save(profile)
    return profile
