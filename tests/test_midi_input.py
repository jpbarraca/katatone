"""MidiInput tests with a fake daemon: events, unplug/replug under a new spec, busy."""
from __future__ import annotations

import threading
import time

from katatone.midi.events import ProgramChange
from katatone.midi.input import MidiInput, PortBusyError, PortGoneError
from katatone.protocol.alsa_seq import InPort


class FakeDaemon:
    """Stands in for `_Daemon`; `script` is a list of bytes / exceptions per rawrecv."""

    opened: list[str] = []

    def __init__(self, script: list, busy: bool = False) -> None:
        self.script = list(script)
        self.busy = busy
        self.closed = False

    def openin(self, spec: str) -> None:
        if self.busy:
            raise PortBusyError(spec)
        FakeDaemon.opened.append(spec)

    def rawrecv(self, timeout: float) -> bytes:
        time.sleep(0.005)
        if not self.script:
            return b""
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.closed = True


def wait_for(cond, timeout: float = 3.0) -> None:
    """Poll until `cond()` is true or fail the test."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.005)
    raise AssertionError("condition not reached")


def make(daemons: list, ports_fn) -> tuple[MidiInput, list, list]:
    """Build a MidiInput over the queued fake daemons; returns (input, events, states)."""
    events: list = []
    states: list = []
    it = iter(daemons)
    mi = MidiInput(InPort("4,0", "SINCO", "SINCO MIDI 1"),
                   lambda label, ev: events.append((label, ev)),
                   lambda label, st: states.append(st),
                   daemon_factory=lambda: next(it), list_ports=ports_fn,
                   gone_retry=0.01, busy_retry=0.01)
    return mi, events, states


def test_events_and_stop() -> None:
    """Bytes become events tagged with the label; stop reports closed and closes the daemon."""
    FakeDaemon.opened = []
    d = FakeDaemon([b"\xc0\x00", b"", b"\xc0\x02\xc0"])
    d2 = FakeDaemon([])
    mi, events, states = make([d, d2], lambda: [])
    mi.start()
    wait_for(lambda: len(events) == 2)
    assert events == [("SINCO MIDI 1", ProgramChange(0, 0)),
                      ("SINCO MIDI 1", ProgramChange(0, 2))]
    mi.stop()
    assert states == ["open", "closed"]
    assert d.closed


def test_unplug_then_reopen_under_new_spec() -> None:
    """After `gone`, the same label reappearing on another card is reopened."""
    FakeDaemon.opened = []
    d1 = FakeDaemon([b"\xc0\x01", PortGoneError("gone")])
    d2 = FakeDaemon([b"\xc0\x03"])
    scans = {"n": 0}

    def ports() -> list[InPort]:
        scans["n"] += 1
        if scans["n"] < 3:
            return []        # still unplugged
        return [InPort("5,0", "SINCO", "SINCO MIDI 1"),
                InPort("6,0", "Other", "Other MIDI 1")]

    mi, events, states = make([d1, d2], ports)
    mi.start()
    wait_for(lambda: len(events) == 2)
    mi.stop()
    assert [e.program for _, e in events] == [1, 3]
    assert FakeDaemon.opened == ["4,0", "5,0"]
    assert states[:3] == ["open", "gone", "open"]
    assert d1.closed


def test_busy_is_retried_until_free() -> None:
    """A busy device is reported, then opened once the other program lets go."""
    FakeDaemon.opened = []
    busy = FakeDaemon([], busy=True)
    ok = FakeDaemon([b"\xc0\x05"])
    mi, events, states = make([busy, ok], lambda: [])
    mi.start()
    wait_for(lambda: len(events) == 1)
    mi.stop()
    assert states[:2] == ["busy", "open"]
    assert busy.closed
    assert FakeDaemon.opened == ["4,0"]


def test_callback_exception_does_not_kill_reader() -> None:
    """A failing on_event is logged and the next event still arrives."""
    got: list = []
    calls = {"n": 0}

    def on_event(label, ev) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("boom")
        got.append(ev)

    mi = MidiInput(InPort("4,0", "SINCO", "SINCO MIDI 1"), on_event,
                   lambda *_: None,
                   daemon_factory=lambda: FakeDaemon([b"\xc0\x01\xc0\x02"]),
                   list_ports=lambda: [])
    mi.start()
    wait_for(lambda: got)
    mi.stop()
    assert got == [ProgramChange(0, 2)]


def test_empty_spec_is_resolved_by_label() -> None:
    """A saved controller that is not plugged in yet opens once it appears."""
    FakeDaemon.opened = []
    d = FakeDaemon([b"\xc0\x07"])
    scans = {"n": 0}

    def ports() -> list[InPort]:
        scans["n"] += 1
        return [] if scans["n"] < 2 else [InPort("7,0", "SINCO", "SINCO MIDI 1")]

    events: list = []
    mi = MidiInput(InPort("", "", "SINCO MIDI 1"),
                   lambda label, ev: events.append(ev), lambda *_: None,
                   daemon_factory=lambda: d, list_ports=ports,
                   gone_retry=0.01, busy_retry=0.01)
    mi.start()
    wait_for(lambda: events)
    mi.stop()
    assert FakeDaemon.opened == ["7,0"] and events == [ProgramChange(0, 7)]
