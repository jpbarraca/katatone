"""Sequencer input, against the real libasound with a second client standing in for a Bluetooth device."""
from __future__ import annotations

import time

import pytest

from katatone.midi.parser import MidiParser
from katatone.protocol import alsa_seqport as sp

try:
    probe = sp.SeqDaemon()
    probe.close()
except Exception:                                    # no libasound / no sequencer
    pytest.skip("ALSA sequencer not available", allow_module_level=True)


@pytest.fixture
def pair():
    pedal = sp.SeqDaemon("FootCtrlPlus", source="FootCtrlPlus Bluetooth")
    reader = sp.SeqDaemon()
    yield pedal, reader
    reader.close()
    pedal.close()


def listed(reader):
    return {p.name: p for p in reader.inports()}


def test_a_user_client_output_port_is_listed_with_a_stable_label(pair) -> None:
    pedal, reader = pair
    ports = listed(reader)
    p = ports["FootCtrlPlus Bluetooth"]
    assert p.spec == f"seq:{pedal.client}:{pedal.out_port}" and p.card == "FootCtrlPlus"
    assert all(q.spec.startswith("seq:") for q in ports.values())
    assert "KataTone input" not in ports and "KataTone" not in {q.card for q in ports.values()}


def test_a_program_change_arrives_as_midi_bytes(pair) -> None:
    pedal, reader = pair
    reader.openin(listed(reader)["FootCtrlPlus Bluetooth"].spec)
    pedal.send(sp.EV_PGMCHANGE, 0, 0, 4)
    data = reader.rawrecv(1.0)
    assert data == bytes([0xC0, 4])
    events = list(MidiParser().feed(data))
    assert len(events) == 1 and events[0].program == 4


def test_note_on_and_channels(pair) -> None:
    pedal, reader = pair
    reader.openin(listed(reader)["FootCtrlPlus Bluetooth"].spec)
    pedal.send(sp.EV_NOTEON, 2, 60, 100)
    assert reader.rawrecv(1.0) == bytes([0x92, 60, 100])


def test_nothing_arrives_when_nothing_is_sent_and_it_returns_after_the_timeout(pair) -> None:
    pedal, reader = pair
    reader.openin(listed(reader)["FootCtrlPlus Bluetooth"].spec)
    t = time.monotonic()
    assert reader.rawrecv(0.2) == b"" and 0.15 < time.monotonic() - t < 1.0


def test_other_sources_are_not_heard(pair) -> None:
    pedal, reader = pair
    other = sp.SeqDaemon("Other", source="Other port")
    try:
        reader.openin(listed(reader)["FootCtrlPlus Bluetooth"].spec)
        other.send(sp.EV_PGMCHANGE, 0, 0, 9)           # nobody subscribed to it
        assert reader.rawrecv(0.2) == b""
    finally:
        other.close()


def test_a_vanished_port_and_a_bad_spec_are_errors(pair) -> None:
    pedal, reader = pair
    with pytest.raises(RuntimeError, match="gone"):
        reader.openin("seq:250:0")
    with pytest.raises(RuntimeError, match="bad spec"):
        reader.openin("seq:abc")


def test_combined_daemon_routes_by_spec_and_survives_without_the_sequencer(monkeypatch) -> None:
    monkeypatch.setattr(sp.pwmidi, "inports", lambda: [])
    class Raw:
        opened = None
        def inports(self): return []
        def openin(self, spec): Raw.opened = spec
        def rawrecv(self, t): return b"\xb0\x01\x02"
        def close(self): pass

    def no_seq():
        raise sp.SeqError("libasound is not installed")
    c = sp.CombinedDaemon(Raw, no_seq)
    assert c.inports() == []                              # rawmidi only, no error
    c.openin("3,0")
    assert Raw.opened == "3,0" and c.rawrecv(0.1) == b"\xb0\x01\x02"
    c.close()
