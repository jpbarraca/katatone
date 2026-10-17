"""MidiManager with fake inputs: enabling, persistence, exclusion, notifications."""
from __future__ import annotations

import threading
import time

import pytest

from katatone.midi.events import ProgramChange
from katatone.midi.mapping import ChannelSelect, Mapping, Profile, Trigger
from katatone.midi.manager import MidiManager, looks_like_amp
from katatone.midi.store import ProfileStore
from katatone.protocol.alsa_seq import InPort
from test_midi_router import FakeHost, wait_for

PORTS = [InPort("3,0", "KATANA3", "KATANA3 MIDI 1"),
         InPort("4,0", "SINCO", "SINCO MIDI 1"),
         InPort("5,0", "Keys", "Keys MIDI 1")]


class FakeInput:
    """Stands in for MidiInput: records start/stop; tests push events by hand."""

    instances: list["FakeInput"] = []

    def __init__(self, port, on_event, on_state) -> None:
        self.port, self.on_event, self.on_state = port, on_event, on_state
        self.running = False
        FakeInput.instances.append(self)

    def start(self) -> None:
        self.running = True
        self.on_state(self.port.label, "open")

    def stop(self) -> None:
        self.running = False
        self.on_state(self.port.label, "closed")


@pytest.fixture
def mgr(tmp_path):
    FakeInput.instances = []
    host = FakeHost()
    m = MidiManager(host, ProfileStore(tmp_path), input_factory=FakeInput,
                    list_ports=lambda: list(PORTS))
    m.host = host
    yield m
    m.stop()


def test_ports_exclude_the_amp(mgr) -> None:
    """A Katana is never offered as a controller, nor the connected amp's card."""
    assert [p.label for p in mgr.ports()] == ["SINCO MIDI 1", "Keys MIDI 1"]
    mgr._exclude = lambda: {"Keys"}
    assert [p.label for p in mgr.ports()] == ["SINCO MIDI 1"]
    assert looks_like_amp(PORTS[0]) and not looks_like_amp(PORTS[1])


def test_enable_opens_and_persists_in_the_profile(mgr, tmp_path) -> None:
    """Enabling writes the profile; a new manager reopens the controller on start."""
    mgr.start()
    mgr.enable("SINCO MIDI 1", True)
    assert [i.port.label for i in FakeInput.instances if i.running] == ["SINCO MIDI 1"]
    assert FakeInput.instances[0].port.spec == "4,0"
    assert ProfileStore(tmp_path).load("Default").devices == ["SINCO MIDI 1"]
    mgr.stop()

    FakeInput.instances = []
    m2 = MidiManager(FakeHost(), ProfileStore(tmp_path), input_factory=FakeInput,
                     list_ports=lambda: list(PORTS))
    m2.start()
    assert [i.port.label for i in FakeInput.instances if i.running] == ["SINCO MIDI 1"]
    m2.stop()


def test_saved_device_that_is_not_plugged_in_still_starts(tmp_path) -> None:
    """The input is created with an empty spec so it can find the device later."""
    store = ProfileStore(tmp_path)
    store.save(Profile(name="Default", devices=["Gone MIDI 1"]))
    FakeInput.instances = []
    m = MidiManager(FakeHost(), store, input_factory=FakeInput,
                    list_ports=lambda: list(PORTS))
    m.start()
    assert FakeInput.instances[0].port == InPort("", "", "Gone MIDI 1")
    m.stop()


def test_disable_closes_and_forgets(mgr, tmp_path) -> None:
    """Disabling stops the input and removes it from the saved profile."""
    mgr.start()
    mgr.enable("SINCO MIDI 1", True)
    mgr.enable("SINCO MIDI 1", False)
    assert not FakeInput.instances[0].running
    assert ProfileStore(tmp_path).load("Default").devices == []
    assert mgr.states["SINCO MIDI 1"] == "closed"


def test_enable_twice_opens_once(mgr) -> None:
    """A repeated enable does not spawn a second reader for the same port."""
    mgr.start()
    mgr.enable("SINCO MIDI 1", True)
    mgr.enable("SINCO MIDI 1", True)
    assert len(FakeInput.instances) == 1
    assert mgr.enabled() == ["SINCO MIDI 1"]


def test_events_reach_the_router_and_listeners(mgr) -> None:
    """A controller event runs its mapping and is announced with its label."""
    mgr.profile.mappings.append(
        Mapping(Trigger(kind="pc", number=0), ChannelSelect(2), label="To A2"))
    mgr.start()
    mgr.enable("SINCO MIDI 1", True)
    got = []
    unsub = mgr.subscribe(lambda *a: got.append(a))
    FakeInput.instances[0].on_event("SINCO MIDI 1", ProgramChange(0, 0))
    wait_for(lambda: any(a[0] == "activity" for a in got))
    assert mgr.host.selects == [(2, "pedal")]
    assert ("activity", "SINCO MIDI 1", ProgramChange(0, 0), ["To A2"]) in got
    unsub()
    got.clear()
    FakeInput.instances[0].on_event("SINCO MIDI 1", ProgramChange(0, 0))
    mgr.router.drain()
    assert got == []


def test_state_changes_are_announced_and_a_bad_listener_is_contained(mgr) -> None:
    """Listeners see state changes; one that raises does not block the others."""
    mgr.start()
    got = []
    mgr.subscribe(lambda *a: 1 / 0)
    mgr.subscribe(lambda *a: got.append(a))
    mgr.enable("Keys MIDI 1", True)
    assert ("state", "Keys MIDI 1", "open") in got


def test_set_profile_switches_mappings_and_controllers(mgr, tmp_path) -> None:
    """Another profile brings its own mappings and devices; the old ones close."""
    store = mgr.store
    store.save(Profile(name="Stage", devices=["Keys MIDI 1"], mappings=[
        Mapping(Trigger(kind="pc"), ChannelSelect(5))]))
    mgr.start()
    mgr.enable("SINCO MIDI 1", True)
    mgr.set_profile("Stage")
    assert store.active() == "Stage"
    running = sorted(i.port.label for i in FakeInput.instances if i.running)
    assert running == ["Keys MIDI 1"]
    FakeInput.instances[-1].on_event("Keys MIDI 1", ProgramChange(0, 3))
    mgr.router.drain()
    assert mgr.host.selects == [(5, "pedal")]
