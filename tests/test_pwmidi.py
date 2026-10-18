"""PipeWire Bluetooth MIDI sources: listing, naming and linking (pw-link faked)."""
from __future__ import annotations

import json

import pytest

from katatone.protocol import pwmidi


@pytest.fixture
def pw(monkeypatch):
    class PW:
        calls, links = [], set()
        listing = "alsa_input.x:capture_FL\nbluez_midi.server:out\nbluez_midi.E2_D0_02_A2_C0_6C:out\n"
        dump = json.dumps([{"info": {"props": {"node.name": "bluez_midi.E2_D0_02_A2_C0_6C",
                                               "node.description": "FootCtrlPlus"}}},
                           {"info": {"props": {"node.name": "other"}}}])
        fail_links = 0

    def run(args, timeout=5.0):
        PW.calls.append(args)
        if args[:2] == ["pw-link", "-o"]:
            return 0, PW.listing
        if args[0] == "pw-dump":
            return 0, PW.dump
        if args[:2] == ["pw-link", "-d"]:
            PW.links.discard(tuple(args[2:]))
            return 0, ""
        if PW.fail_links:
            PW.fail_links -= 1
            return 1, ""
        PW.links.add(tuple(args[1:]))
        return 0, ""
    monkeypatch.setattr(pwmidi, "_run", run)
    monkeypatch.setattr(pwmidi, "available", lambda: True)
    monkeypatch.setattr(pwmidi, "_names", (0.0, {}))
    monkeypatch.setattr(pwmidi.time, "sleep", lambda s: None)
    return PW


def test_bluetooth_midi_sources_are_listed_with_the_device_name(pw) -> None:
    (p,) = pwmidi.inports()
    assert p.spec == "pw:bluez_midi.E2_D0_02_A2_C0_6C:out"
    assert (p.card, p.label) == ("PipeWire", "FootCtrlPlus")        # not the server, not audio


def test_nothing_is_listed_without_pw_link(pw, monkeypatch) -> None:
    monkeypatch.setattr(pwmidi, "available", lambda: False)
    assert pwmidi.inports() == []


def test_a_device_without_a_description_is_labelled_by_its_node_name(pw) -> None:
    pw.dump = "not json"
    assert pwmidi.inports()[0].label == "bluez_midi.E2_D0_02_A2_C0_6C"


def test_pipewire_names_of_sequencer_ports() -> None:
    assert pwmidi.sequencer_port("Midi Through", "Midi Through Port-0") == \
        "Midi-Bridge:Midi Through: Port-0 (playback)"
    assert pwmidi.sequencer_port("KataTone 1-1", "input") == "Midi-Bridge:KataTone 1-1:input (playback)"


def test_link_retries_while_pipewire_notices_the_new_client(pw) -> None:
    pw.fail_links = 2
    assert pwmidi.link("a:out", "b:in", wait=5) and ("a:out", "b:in") in pw.links
    pw.fail_links = 99
    assert not pwmidi.link("a:out", "c:in", wait=0)


def test_unlink_and_presence(pw) -> None:
    pwmidi.link("a:out", "b:in")
    pwmidi.unlink("a:out", "b:in")
    assert ("a:out", "b:in") not in pw.links
    assert pwmidi.present("bluez_midi.E2_D0_02_A2_C0_6C:out")
    pw.listing = ""
    assert not pwmidi.present("bluez_midi.E2_D0_02_A2_C0_6C:out")


def test_opening_a_pipewire_port_links_it_to_our_own_client_and_close_removes_it(pw) -> None:
    from katatone.protocol import alsa_seqport as sp
    d = sp.CombinedDaemon(lambda: type("R", (), {"inports": lambda s: [], "close": lambda s: None})())
    d.openin("pw:bluez_midi.E2_D0_02_A2_C0_6C:out")
    (link,) = pw.links
    assert link[0] == "bluez_midi.E2_D0_02_A2_C0_6C:out"
    assert link[1].startswith("Midi-Bridge:KataTone ") and link[1].endswith(":input (playback)")
    d.close()
    assert pw.links == set()


def test_a_vanished_pipewire_device_reads_as_gone(pw) -> None:
    from katatone.protocol import alsa_seqport as sp
    from katatone.protocol.alsa_seq import PortGoneError
    d = sp.CombinedDaemon(lambda: type("R", (), {"inports": lambda s: [], "close": lambda s: None})())
    d.openin("pw:bluez_midi.E2_D0_02_A2_C0_6C:out")
    d._seq._pw_checked = 0.0
    pw.listing = ""
    with pytest.raises(PortGoneError):
        d.rawrecv(0.01)
    d.close()
