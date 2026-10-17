"""Mapping model: trigger matching, edge/toggle/range modes, tables, JSON round trip."""
from __future__ import annotations

import json

import pytest

from katatone.address.map import load_map
from katatone.midi import mapping as mp
from katatone.midi.events import (ChannelPressure, ControlChange, NoteOff, NoteOn,
                                  PitchBend, ProgramChange)
from katatone.midi.mapping import (BankAction, ChannelFromPc, ChannelSelect,
                                   EffectAction, ExprTarget, ExpressionAction,
                                   Mapping, Profile, Trigger, UnknownAction)

DEV = "SINCO MIDI 1"


def prof(*mappings: Mapping) -> Profile:
    return Profile(mappings=list(mappings))


def m(trigger: Trigger, action=None, **kw) -> Mapping:
    return Mapping(trigger=trigger, action=action or ChannelSelect(1), **kw)


def fired(p: Profile, ev, device: str = DEV) -> list:
    """Values of the mappings that fire, in order."""
    return [v for _, v in p.match(device, ev)]


# ------------------------------------------------------------------ matching


def test_device_substring_or_any() -> None:
    """A device filter is a case-insensitive substring of the port label."""
    ev = ProgramChange(0, 1)
    assert fired(prof(m(Trigger(device="sinco"))), ev) == [True]
    assert fired(prof(m(Trigger(device="Other"))), ev) == []
    assert fired(prof(m(Trigger(device=None))), ev, device="Anything") == [True]


def test_channel_and_number_filters() -> None:
    """Channel is 0-based; None means any; number must equal the program."""
    p = prof(m(Trigger(channel=2, number=5)))
    assert fired(p, ProgramChange(2, 5)) == [True]
    assert fired(p, ProgramChange(1, 5)) == []
    assert fired(p, ProgramChange(2, 6)) == []
    assert fired(prof(m(Trigger(number=None))), ProgramChange(9, 99)) == [True]


def test_kind_must_match() -> None:
    """A CC trigger ignores a PC with the same number, and vice versa."""
    assert fired(prof(m(Trigger(kind="cc", number=7))), ProgramChange(0, 7)) == []
    assert fired(prof(m(Trigger(kind="pc", number=7))), ControlChange(0, 7, 127)) == []
    assert fired(prof(m(Trigger(kind="note", number=60))), ControlChange(0, 60, 127)) == []


def test_disabled_mappings_are_ignored() -> None:
    """enabled=False never fires and does not disturb the others."""
    p = prof(m(Trigger(), enabled=False), m(Trigger()))
    assert len(p.match(DEV, ProgramChange(0, 0))) == 1


def test_several_mappings_on_one_trigger_fire_in_list_order() -> None:
    """Every matching mapping fires, in the profile's order."""
    a, b, c = (m(Trigger(), label=x) for x in "abc")
    got = [mm.label for mm, _ in prof(a, b, c).match(DEV, ProgramChange(0, 0))]
    assert got == ["a", "b", "c"]


# ------------------------------------------------------------------ press


def cc(v: int) -> ControlChange:
    return ControlChange(0, 20, v)


def test_press_fires_once_per_rising_edge() -> None:
    """127,127,127 fires once; 127,0,127 fires twice."""
    p = prof(m(Trigger(kind="cc", number=20)))
    assert [fired(p, cc(v)) for v in (127, 127, 127)] == [[True], [], []]
    p = prof(m(Trigger(kind="cc", number=20)))
    assert [fired(p, cc(v)) for v in (127, 0, 127)] == [[True], [], [True]]


def test_press_threshold() -> None:
    """Values below the threshold are 'up'; the threshold itself is 'down'."""
    p = prof(m(Trigger(kind="cc", number=20, threshold=100)))
    assert [fired(p, cc(v)) for v in (99, 100, 99, 127)] == [[], [True], [], [True]]


def test_press_repeat_fires_on_every_message_for_127_only_pedals() -> None:
    """A pedal that never sends a release needs repeat to fire on each press."""
    p = prof(m(Trigger(kind="cc", number=20, repeat=True)))
    assert [fired(p, cc(127)) for _ in range(3)] == [[True]] * 3
    assert fired(p, cc(0)) == []


def test_note_on_off_edges() -> None:
    """NoteOn is the rising edge regardless of velocity; NoteOff re-arms it."""
    p = prof(m(Trigger(kind="note", number=60)))
    assert fired(p, NoteOn(0, 60, 5)) == [True]
    assert fired(p, NoteOn(0, 60, 90)) == []          # no release in between
    assert fired(p, NoteOff(0, 60)) == []
    assert fired(p, NoteOn(0, 60, 90)) == [True]


def test_program_change_always_fires() -> None:
    """A pedal's PC has no release, so the same PC twice is two presses."""
    p = prof(m(Trigger(kind="pc", number=2)))
    assert [fired(p, ProgramChange(0, 2)) for _ in range(3)] == [[True]] * 3


def test_momentary_effect_gets_both_edges() -> None:
    """Held = on, released = off: only a momentary effect sees the release."""
    p = prof(m(Trigger(kind="cc", number=20), EffectAction("BOOST", "momentary")))
    assert [fired(p, cc(v)) for v in (127, 0, 127, 0)] == \
        [[True], [False], [True], [False]]
    q = prof(m(Trigger(kind="cc", number=20), EffectAction("BOOST", "toggle")))
    assert [fired(q, cc(v)) for v in (127, 0)] == [[True], []]


# ------------------------------------------------------------------ toggle


def test_toggle_flips_on_each_press() -> None:
    """Same value for every press (a pedal that always sends 127) alternates on/off."""
    p = prof(m(Trigger(kind="cc", number=20, mode="toggle", repeat=True)))
    assert [fired(p, cc(127)) for _ in range(4)] == [[True], [False], [True], [False]]


def test_toggle_state_is_per_mapping() -> None:
    """Two toggle mappings on the same message flip independently."""
    a = m(Trigger(kind="pc", mode="toggle"))
    b = m(Trigger(kind="pc", mode="toggle"))
    p = prof(a, b)
    p.match(DEV, ProgramChange(0, 1))
    b.enabled = False
    assert [v for _, v in p.match(DEV, ProgramChange(0, 1))] == [False]
    b.enabled = True
    # a flips back to on; b kept its own state (on) and flips to off.
    assert [v for _, v in p.match(DEV, ProgramChange(0, 1))] == [True, False]


def test_reset_state_forgets_edges_and_toggles() -> None:
    """After editing mappings the memory starts clean."""
    p = prof(m(Trigger(kind="cc", number=20, mode="toggle")))
    assert fired(p, cc(127)) == [True]
    p.reset_state()
    assert fired(p, cc(127)) == [True]


# ------------------------------------------------------------------ range


def test_range_fires_on_every_event_with_the_value() -> None:
    """A continuous controller hands every value through."""
    p = prof(m(Trigger(kind="cc", number=47, mode="range"), ExpressionAction()))
    assert [fired(p, ControlChange(0, 47, v)) for v in (0, 64, 127)] == \
        [[0], [64], [127]]


def test_pitch_bend_scales_to_0_127() -> None:
    """-8192 -> 0, centre -> 64 (63.5 rounds up), 8191 -> 127."""
    p = prof(m(Trigger(kind="pitchbend", mode="range"), ExpressionAction()))
    assert [fired(p, PitchBend(0, v)) for v in (-8192, 0, 8191)] == [[0], [64], [127]]


def test_channel_pressure_range() -> None:
    """Pressure is a plain 0-127 value."""
    p = prof(m(Trigger(kind="pressure", mode="range"), ExpressionAction()))
    assert fired(p, ChannelPressure(0, 33)) == [33]


def test_scale_range_linear_inverted_log() -> None:
    """Endpoints, inversion and the squared curve."""
    assert mp.scale_range(0, 0, 100) == 0
    assert mp.scale_range(127, 0, 100) == 100
    assert mp.scale_range(64, 0, 100) == 50
    assert mp.scale_range(0, 0, 100, invert=True) == 100
    assert mp.scale_range(127, 0, 100, invert=True) == 0
    assert mp.scale_range(64, 0, 100, curve="log") == 25      # (64/127)**2 * 100
    assert mp.scale_range(64, 20, 80) == 50
    assert mp.scale_range(200, 0, 100) == 100                 # out of range clamps
    assert mp.scale_range(0, -24, 24) == -24


def test_expression_bounds_default_to_the_parameter_range() -> None:
    """lo/hi default to the param's min/max and are clamped into it."""
    _, _, param = mp.resolve_expression(ExprTarget("volume"))
    assert ExpressionAction().bounds(param) == (0, 100)
    assert ExpressionAction(lo=20, hi=80).bounds(param) == (20, 80)
    assert ExpressionAction(lo=-5, hi=500).bounds(param) == (0, 100)


# ------------------------------------------------------------------ tables


def test_every_effect_resolves_to_a_boolean_switch() -> None:
    """All EFFECTS entries exist in the real address map as 0-1 switches."""
    for eid in mp.EFFECTS:
        block, i, p = mp.effect_param(eid)
        assert (p.min, p.max) == (0, 1), eid
        assert load_map()["PATCH"].block(block).params[i] is p


def test_every_pedal_type_resolves_to_its_position_param() -> None:
    """PEDALFX_COM_TYPE 0/1/2 picks wah / bend / 95E position; unknown means wah."""
    got = {t: mp.resolve_expression(ExprTarget("pedal_fx"), t)[2].name
           for t in (0, 1, 2)}
    assert got == {0: "PRMID_PATCH_PEDALFX_PEDALWAH_PEDAL_POSITION",
                   1: "PRMID_PATCH_PEDALFX_BEND_PEDAL_POS",
                   2: "PRMID_PATCH_PEDALFX_EWAH_PEDAL_POS"}
    assert mp.resolve_expression(ExprTarget("pedal_fx"), None)[2].name == got[0]
    assert mp.resolve_expression(ExprTarget("pedal_fx"), 7)[2].name == got[0]
    assert all((p.min, p.max) == (0, 100) for p in
               (mp.resolve_expression(ExprTarget("pedal_fx"), t)[2] for t in (0, 1, 2)))


def test_volume_and_arbitrary_param_targets() -> None:
    """VOLUME is AMP_VOLUME; a param target is looked up by block and name."""
    block, i, p = mp.resolve_expression(ExprTarget("volume"))
    assert (block, p.name) == ("AMP", "PRMID_PATCH_AMP_VOLUME")
    block, i, p = mp.resolve_expression(ExprTarget("param", "AMP", "AMP_GAIN"))
    assert p.name == "PRMID_PATCH_AMP_GAIN" and i == 0
    block, i, p = mp.resolve_expression(
        ExprTarget("param", "NS", "PRMID_PATCH_NS_THRESHOLD"))
    assert p.name == "PRMID_PATCH_NS_THRESHOLD"


def test_find_param_errors_are_clear() -> None:
    """A renamed block or parameter raises KeyError naming it."""
    with pytest.raises(KeyError, match="NOPE"):
        mp.find_param("NOPE", "X")
    with pytest.raises(KeyError, match="SW_NOPE"):
        mp.find_param("SW", "SW_NOPE")
    with pytest.raises(KeyError, match="unknown effect"):
        mp.effect_param("CHORUS")


# ------------------------------------------------------------------ JSON


def sample_profile() -> Profile:
    return Profile(name="Stage", devices=["SINCO MIDI 1"], mappings=[
        m(Trigger("SINCO", 0, "pc", 2), ChannelSelect(5), label="B1"),
        m(Trigger("SINCO", 0, "pc", None), ChannelFromPc({0: 1, 4: 0}), label="pc"),
        m(Trigger(None, None, "cc", 20, "toggle", 100, True),
          EffectAction("DELAY", "on")),
        m(Trigger(None, 1, "cc", 47, "range"),
          ExpressionAction(ExprTarget("param", "AMP", "AMP_GAIN"), 10, 90, True, "log")),
        m(Trigger(kind="note", number=60), BankAction("next")),
    ])


def test_profile_round_trips_through_json() -> None:
    """to_dict -> JSON -> from_dict reproduces every field."""
    p = sample_profile()
    q = Profile.from_dict(json.loads(json.dumps(p.to_dict())))
    assert q == p
    assert q.mappings[1].action.table == {0: 1, 4: 0}      # int keys restored
    assert q.mappings[3].action.target == ExprTarget("param", "AMP", "AMP_GAIN")


def test_unknown_action_loads_disabled_and_survives_a_save() -> None:
    """A newer action type is kept verbatim but never fires; it is not an error."""
    d = sample_profile().to_dict()
    d["mappings"][0]["action"] = {"type": "teleport", "where": "moon"}
    q = Profile.from_dict(d)
    assert isinstance(q.mappings[0].action, UnknownAction)
    assert not q.mappings[0].enabled
    assert q.to_dict()["mappings"][0]["action"] == {"type": "teleport", "where": "moon"}
    only = Profile(mappings=[q.mappings[0]])
    assert fired(only, ProgramChange(0, 2)) == []


def test_malformed_action_or_trigger_is_contained() -> None:
    """A broken action disables that mapping; a broken trigger drops only it."""
    d = sample_profile().to_dict()
    d["mappings"][0]["action"] = {"type": "channel_select"}          # no slot
    d["mappings"][1]["trigger"]["kind"] = "sysex"
    q = Profile.from_dict(d)
    assert isinstance(q.mappings[0].action, UnknownAction) and not q.mappings[0].enabled
    assert len(q.mappings) == 4


def test_newer_profile_version_is_rejected() -> None:
    """Better to refuse than to silently drop fields we do not understand."""
    with pytest.raises(ValueError):
        Profile.from_dict({"version": 99, "name": "x"})


def test_mapping_ids_are_unique_and_kept() -> None:
    """Fresh ids are distinct; a saved id is preserved on load."""
    a, b = m(Trigger()), m(Trigger())
    assert a.id != b.id
    assert Mapping.from_dict(a.to_dict()).id == a.id


def test_summaries_for_the_table() -> None:
    """Human-readable trigger and action text."""
    assert Trigger("SINCO", 0, "pc", 2).summary() == "SINCO · ch 1 · PC 2 · press"
    assert Trigger(kind="cc", number=None, mode="range").summary() == \
        "any device · any ch · CC any · range"
    assert ChannelSelect(5).summary(lambda n: "1B") == "Channel → 1B"
    assert EffectAction("DELAY", "toggle").summary() == "Delay toggle"
    assert BankAction("next").summary() == "Bank next"


def test_numeric_params_are_editor_params_without_names_or_fx_detail() -> None:
    got = mp.numeric_params()
    names = {(b, n) for b, n, _ in got}
    assert ("AMP", "AMP_GAIN") in names and ("NS", "NS_THRESHOLD") in names
    assert not any(b == "COM" or b.startswith("FX_DETAIL") for b, _, _ in got)
    assert all(p.max > p.min for _, _, p in got)
    for b, n, _ in got[:50] + got[-50:]:
        mp.find_param(b, n)                 # every entry resolves


def test_pc_table_round_trip_and_errors() -> None:
    t = mp.parse_pc_table("0=1A, 2 = 2a, 4=PN, 6=Panel", 0x05)
    assert t == {0: 1, 2: 2, 4: 0, 6: 0}
    assert mp.format_pc_table({0: 1, 4: 0}, 0x05) == "0=1A, 4=PN"
    assert mp.parse_pc_table("", 0x05) == {}
    for bad in ("1", "x=1A", "200=1A", "1=3A"):      # no 3A on a KATANA-50
        with pytest.raises(ValueError):
            mp.parse_pc_table(bad, 0x05)
    assert mp.parse_pc_table("1=3A", 0x06) == {1: 3}
