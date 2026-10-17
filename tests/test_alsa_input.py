"""Rawmidi backend tests against fake /proc/asound and /dev/snd trees."""
from __future__ import annotations

import errno
import os

import pytest

from katatone.protocol import alsa_seq
from katatone.protocol.alsa_seq import (InPort, PortBusyError, PortGoneError,
                                        SeqClient, _Daemon)

CARDS = """\
 0 [PCH            ]: HDA-Intel - HDA Intel PCH
                      HDA Intel PCH at 0x1000
 3 [KATANA3        ]: USB-Audio - KATANA3
                      Roland KATANA3 at usb-1
 4 [SINCO          ]: USB-Audio - SINCO
                      Jieli Technology SINCO at usb-2
"""


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A fake sound subsystem: cards 3 and 4 have rawmidi, card 0 has none."""
    proc, snd = tmp_path / "proc", tmp_path / "snd"
    proc.mkdir()
    snd.mkdir()
    (proc / "cards").write_text(CARDS)
    for n in (3, 4):
        (proc / f"card{n}").mkdir()
        (proc / f"card{n}" / "midi0").write_text("x")
        os.mkfifo(snd / f"midiC{n}D0")
    monkeypatch.setattr(alsa_seq, "PROC_ASOUND", proc)
    monkeypatch.setattr(alsa_seq, "SND_DIR", snd)
    monkeypatch.setattr(alsa_seq, "_subdevice_name",
                        lambda card, dev, fallback: {3: "KATANA3 MIDI 1",
                                                     4: "SINCO MIDI 1"}.get(card))
    return snd


def test_ports_only_lists_cards_with_rawmidi(tree) -> None:
    assert alsa_seq.list_ports() == {"in": ["KATANA3", "SINCO"],
                                     "out": ["KATANA3", "SINCO"]}


def test_inports_have_card_and_device_names(tree) -> None:
    assert alsa_seq.list_input_ports() == [
        InPort("3,0", "KATANA3", "KATANA3 MIDI 1"),
        InPort("4,0", "SINCO", "SINCO MIDI 1"),
    ]
    assert alsa_seq.list_input_ports()[1].label == "SINCO MIDI 1"


def test_no_sound_subsystem_lists_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(alsa_seq, "PROC_ASOUND", tmp_path / "missing")
    monkeypatch.setattr(alsa_seq, "SND_DIR", tmp_path / "missing")
    assert alsa_seq.list_ports() == {"in": [], "out": []}
    assert alsa_seq.list_input_ports() == []


def test_openin_busy_and_other_errors(tree, monkeypatch) -> None:
    """EBUSY becomes PortBusyError; other errors a plain RuntimeError."""
    d = _Daemon()
    real_open = os.open

    def failing(path, flags, *a):
        if str(path).endswith("midiC9D0"):
            raise OSError(errno.EBUSY, "busy")
        if str(path).endswith("midiC8D0"):
            raise OSError(errno.EINVAL, "bad")
        return real_open(path, flags, *a)

    monkeypatch.setattr(alsa_seq.os, "open", failing)
    d.openin("4,0")
    with pytest.raises(PortBusyError):
        d.openin("9,0")
    with pytest.raises(RuntimeError, match="-22") as ei:
        d.openin("8,0")
    assert not isinstance(ei.value, PortBusyError)
    d.close()


def test_rawrecv_returns_bytes_as_read(tree) -> None:
    d = _Daemon()
    d.openin("4,0")
    w = os.open(tree / "midiC4D0", os.O_WRONLY)
    os.write(w, b"\xc0\x00\xc0\x02")
    assert d.rawrecv(0.2) == b"\xc0\x00\xc0\x02"
    assert d.rawrecv(0.05) == b""
    os.close(w)
    d.close()


def test_rawrecv_unplugged_raises(tree) -> None:
    """The writer vanishing (POLLHUP) is reported as PortGoneError."""
    d = _Daemon()
    d.openin("4,0")
    w = os.open(tree / "midiC4D0", os.O_WRONLY)
    os.close(w)
    with pytest.raises(PortGoneError):
        d.rawrecv(0.2)
    assert d.rawrecv(0.05) == b""       # the device is closed now
    d.close()


def test_rawrecv_without_device_is_empty(tree) -> None:
    assert _Daemon().rawrecv(0.01) == b""


def test_client_sends_and_reassembles_frames(tree) -> None:
    """A duplex client writes frames out and gets reassembled frames back
    (a FIFO opened read-write loops the bytes back)."""
    frame = bytes.fromhex("f04110010507120000" "0001f7")
    c = SeqClient(out_name="KATANA3")
    assert c.card_id == 3
    c.send_sysex(frame)
    assert c.recv_frames(0.5) == [frame]
    assert c.recv_frames(0.05) == []
    c.close()


def test_open_matches_card_name_prefix(tree) -> None:
    d = _Daemon()
    assert d.open("KATANA3 MIDI 1") == 3
    d.close()


def test_open_unknown_card_fails(tree) -> None:
    with pytest.raises(RuntimeError, match="card not found"):
        _Daemon().open("NOPE")


def test_send_without_open_fails(tree) -> None:
    with pytest.raises(RuntimeError, match="not open"):
        _Daemon().send(b"\xf0\xf7")
