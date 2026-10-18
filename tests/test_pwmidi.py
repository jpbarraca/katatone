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


def test_pw_mididump_lines_become_midi_bytes() -> None:
    p = pwmidi.parse_line
    assert p("  17: track: 0 sec:3.7 Program    (channel  1): program   2 (Electric Grand)") == bytes([0xC0, 2])
    assert p("track: 0 sec:0.0 Note On    (channel  1): note   C4, velocity 100") == bytes([0x90, 60, 100])
    assert p("track: 0 sec:0.0 Note Off   (channel 10): note  C#3, velocity   0") == bytes([0x89, 49, 0])
    assert p("track: 0 sec:0.0 Controller (channel  2): controller   7 (Volume (coarse)), value 127") == bytes([0xB1, 7, 127])
    assert p("track: 0 sec:0.0 Pitch Bend (channel  4): value 0") == bytes([0xE3, 0x00, 0x40])
    assert p("track: 0 sec:0.0 Pitch Bend (channel  4): value -8192") == bytes([0xE3, 0, 0])
    assert p("track: 0 sec:0.0 Channel Pressure (channel  5): pressure  33") == bytes([0xD4, 33])
    assert p("track: 0 sec:0.0 Aftertouch (channel  6): note   C4, pressure  50") == bytes([0xA5, 60, 50])
    assert p("opened  format:0") == b"" and p("track: 0 sec:0 Meta: End Of Track") == b""


def test_note_names_follow_pw_mididump() -> None:
    assert [pwmidi.note_number(n) for n in ("C4", "A4", "C-1", "G9", "Db4", "F#2", "61")] == \
        [60, 69, 0, 127, 61, 42, 61]


class FakeProc:
    def __init__(self, *a, **k): self.alive = True
    def poll(self): return None if self.alive else 0
    def terminate(self): self.alive = False
    def wait(self, t=None): return 0
    def kill(self): self.alive = False


@pytest.fixture
def dump(pw, monkeypatch):
    """A DumpReader whose pw-mididump is a fake and whose terminal is a pipe we write to."""
    import os
    r, w = os.pipe()
    keep = os.dup(w)
    monkeypatch.setattr(pwmidi.pty, "openpty", lambda: (r, w))
    monkeypatch.setattr(pwmidi.subprocess, "Popen", FakeProc)
    pw.dump_ids = "  57 midi-dump:input\n"
    orig = pw.listing
    base_run = pwmidi._run

    def run(args, timeout=5.0):
        if args[:3] == ["pw-link", "-I", "-i"]:
            return 0, pw.dump_ids
        return base_run(args, timeout)
    monkeypatch.setattr(pwmidi, "_run", run)
    yield keep
    os.close(keep)


def test_a_dump_reader_links_its_own_dump_and_turns_output_into_midi(pw, dump) -> None:
    import os
    reader = pwmidi.DumpReader("bluez_midi.E2_D0_02_A2_C0_6C:out")
    assert ("bluez_midi.E2_D0_02_A2_C0_6C:out", "57") in pw.links          # linked by port id
    os.write(dump, b"  1: track: 0 sec:1.0 Program (channel  1): program 4 (x)\n"
                   b"  2: track: 0 sec:1.1 Controller (channel  1): controller 7 (v), value 100\n"
                   b"  3: track: 0 sec:1.2 Program (chan")                  # a line cut in two
    assert reader.rawrecv(0.5) == bytes([0xC0, 4, 0xB0, 7, 100])
    os.write(dump, b"nel  1): program 6 (y)\n")
    assert reader.rawrecv(0.5) == bytes([0xC0, 6])
    assert reader.rawrecv(0.05) == b""
    reader.close()
    reader.close()                                                         # idempotent


def test_the_newest_dump_port_is_ours(pw, dump) -> None:
    pw.dump_ids = "  12 midi-dump:input\n  57 midi-dump:input\n  30 other:input\n"
    pwmidi.DumpReader("bluez_midi.E2_D0_02_A2_C0_6C:out")
    assert ("bluez_midi.E2_D0_02_A2_C0_6C:out", "57") in pw.links


def test_a_missing_source_is_an_error_and_cleans_up(pw, dump, monkeypatch) -> None:
    pw.fail_links = 99
    monkeypatch.setattr(pwmidi, "link", lambda *a, **k: False)
    with pytest.raises(RuntimeError, match="gone"):
        pwmidi.DumpReader("bluez_midi.E2_D0_02_A2_C0_6C:out", wait=0.2)


def test_a_vanished_device_reads_as_gone(pw, dump) -> None:
    from katatone.protocol.alsa_seq import PortGoneError
    reader = pwmidi.DumpReader("bluez_midi.E2_D0_02_A2_C0_6C:out")
    reader._last_check = 0.0
    pw.listing = ""
    with pytest.raises(PortGoneError):
        reader.rawrecv(0.01)
    reader.close()


def test_the_combined_daemon_reads_a_pipewire_device_without_the_sequencer(pw, dump) -> None:
    import os
    from katatone.protocol import alsa_seqport as sp
    raw = type("R", (), {"inports": lambda s: [], "close": lambda s: None})
    d = sp.CombinedDaemon(raw, lambda *a: (_ for _ in ()).throw(AssertionError("no sequencer client")))
    d.openin("pw:bluez_midi.E2_D0_02_A2_C0_6C:out")
    os.write(dump, b"track: 0 sec:1 Program (channel  1): program 2 (x)\n")
    assert d.rawrecv(0.5) == bytes([0xC0, 2])
    d.close()
