"""Learn: turning the first controller message into a trigger."""
from __future__ import annotations

import threading
import time

from katatone.midi.events import (ChannelPressure, ControlChange, NoteOff, NoteOn,
                                  PitchBend, ProgramChange)
from katatone.midi.learn import LearnSession, decide
from katatone.midi.mapping import ChannelSelect, Mapping, Profile, Trigger
from katatone.midi.manager import MidiManager
from katatone.midi.store import ProfileStore
from test_midi_manager import FakeInput, PORTS
from test_midi_router import FakeHost

DEV = "SINCO MIDI 1"


def run(events, timeout=1.0):
    """Feed events to a session; return what on_done received (once)."""
    got, done = [], threading.Event()
    s = LearnSession(lambda r: (got.append(r), done.set()), timeout)
    s.start()
    for dev, ev in events:
        s.feed(dev, ev)
    assert done.wait(2)
    time.sleep(0.05)
    return got


def test_pc_becomes_a_press_suggesting_channel_select() -> None:
    (r,) = run([(DEV, ProgramChange(0, 2))])
    assert r.trigger == Trigger(DEV, 0, "pc", 2, "press")
    assert r.suggested_action == "channel_select"
    assert r.description == "SINCO MIDI 1 · ch 1 · PC 2"


def test_note_on_press_ignores_a_leading_release() -> None:
    (r,) = run([(DEV, NoteOff(1, 60)), (DEV, NoteOn(1, 62, 90))])
    assert (r.trigger.kind, r.trigger.number, r.trigger.channel) == ("note", 62, 1)


def test_cc_switch_values_are_a_press_that_fires_on_every_message() -> None:
    """127 or 0 as the first value: a switch; repeat suits pedals with and without release."""
    for v in (127, 0):
        (r,) = run([(DEV, ControlChange(0, 20, v))])
        assert r.trigger.mode == "press" and r.trigger.repeat
        assert r.suggested_action == "effect"


def test_cc_with_any_other_first_value_is_a_pedal() -> None:
    (r,) = run([(DEV, ControlChange(0, 47, 37))])
    assert r.trigger.mode == "range" and r.suggested_action == "expression"
    assert r.description.endswith("CC 47 (pedal)")


def test_learn_stops_at_the_first_message_and_ignores_the_rest() -> None:
    """A sweep that follows must not be reported again or change the result."""
    got = run([(DEV, ControlChange(0, 47, 40)), (DEV, ControlChange(0, 47, 90)),
               (DEV, ProgramChange(0, 3))])
    assert len(got) == 1 and got[0].trigger.number == 47


def test_reports_without_waiting() -> None:
    """No settle window: the result arrives right after the first message."""
    done = threading.Event()
    s = LearnSession(lambda r: done.set(), timeout=5)
    s.start()
    t0 = time.monotonic()
    s.feed(DEV, ControlChange(0, 20, 127))
    assert done.wait(1) and time.monotonic() - t0 < 0.5


def test_pitch_bend_and_pressure_are_ranges() -> None:
    (a,) = run([(DEV, PitchBend(0, 100))])
    (b,) = run([(DEV, ChannelPressure(0, 5))])
    assert (a.trigger.kind, a.trigger.mode) == ("pitchbend", "range")
    assert (b.trigger.kind, b.trigger.mode) == ("pressure", "range")


def test_timeout_reports_none() -> None:
    assert run([], timeout=0.05) == [None]


def test_cancel_reports_nothing() -> None:
    got = []
    s = LearnSession(got.append, timeout=0.05)
    s.start()
    s.cancel()
    threading.Event().wait(0.15)
    assert got == []


def test_decide_trigger_matches_what_it_learned() -> None:
    ev = ProgramChange(0, 4)
    assert decide(DEV, ev).trigger.matches(DEV, ev)


def test_manager_learn_captures_without_firing_mappings(tmp_path) -> None:
    """Pressing the pedal to learn it must not also switch the amp."""
    host = FakeHost()
    store = ProfileStore(tmp_path)
    store.save(Profile(name="Default", devices=[DEV], mappings=[
        Mapping(Trigger(kind="pc", number=0), ChannelSelect(5))]))
    FakeInput.instances = []
    m = MidiManager(host, store, input_factory=FakeInput, list_ports=lambda: list(PORTS))
    m.start()
    got, done = [], threading.Event()
    m.start_learn(lambda r: (got.append(r), done.set()))
    FakeInput.instances[0].on_event(DEV, ProgramChange(0, 0))
    assert done.wait(2) and m.router.drain()
    assert host.selects == [] and got[0].trigger.number == 0
    assert m.router.capture is None
    FakeInput.instances[0].on_event(DEV, ProgramChange(0, 0))   # normal again
    m.router.drain()
    assert host.selects == [(5, "pedal")]
    m.stop()
