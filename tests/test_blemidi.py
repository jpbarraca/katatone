"""BLE-MIDI framing (including the amp's real identity reply), the write guard and the Bluetooth amp option."""
from __future__ import annotations

import types

import pytest

from katatone.protocol import blemidi as ble
from katatone.protocol import rc6
from katatone.protocol.transport import Transport


def feed_all(packets):
    d, out = ble.Decoder(), []
    for p in packets:
        out += d.feed(p)
    return out


def test_the_amps_real_identity_reply_is_reassembled() -> None:
    packets = [bytes.fromhex(h) for h in ("8f db f0 7e 10", "8f 06 02 41", "8f 07 05 00",
                                          "8f 00 05 00", "8f 00 00 db f7")]
    assert feed_all(packets) == [bytes.fromhex("f0 7e 10 06 02 41 07 05 00 00 05 00 00 00 f7")]


def test_a_short_sysex_is_one_packet_with_timestamps() -> None:
    (p,) = ble.encode(bytes([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7]), 23, now_ms=0)
    assert p == bytes([0x80, 0x80, 0xF0, 0x7E, 0x7F, 0x06, 0x01, 0x80, 0xF7])


@pytest.mark.parametrize("mtu", [23, 96, 185])
@pytest.mark.parametrize("length", [0, 1, 20, 61, 62, 63, 64, 130, 400])
def test_any_sysex_survives_a_roundtrip_at_any_size(mtu, length) -> None:
    frame = bytes([0xF0, 0x41]) + bytes((i * 7) % 128 for i in range(length)) + bytes([0xF7])
    packets = ble.encode(frame, mtu, now_ms=4321)
    assert all(len(p) <= max(8, min(mtu, ble.MAX_PACKET) - 3) for p in packets)
    assert feed_all(packets) == [frame]


def test_channel_messages_roundtrip_and_two_messages_in_one_packet() -> None:
    assert feed_all(ble.encode(bytes([0xC0, 5]), 23, now_ms=9)) == [bytes([0xC0, 5])]
    assert feed_all(ble.encode(bytes([0xB0, 7, 100]), 23, now_ms=9)) == [bytes([0xB0, 7, 100])]
    packet = bytes([0x81, 0x90, 0xC0, 3, 0x91, 0xB1, 7, 64])
    assert feed_all([packet]) == [bytes([0xC0, 3]), bytes([0xB1, 7, 64])]


def test_a_sysex_split_across_packets_waits_for_its_end() -> None:
    d = ble.Decoder()
    assert d.feed(bytes([0x80, 0x80, 0xF0, 1, 2])) == []
    assert d.feed(bytes([0x80, 3, 4, 0x80, 0xF7])) == [bytes([0xF0, 1, 2, 3, 4, 0xF7])]


def test_an_unterminated_sysex_cannot_be_encoded() -> None:
    with pytest.raises(ValueError):
        ble.encode(bytes([0xF0, 1, 2]), 23)


def test_timestamps_are_13_bit() -> None:
    head, ts = ble._timestamp(0x1FFF)
    assert (head, ts) == (0x80 | 0x3F, 0x80 | 0x7F)
    assert ble._timestamp(0x2000) == ble._timestamp(0)


class FakeLink:
    def __init__(self, address):
        self.sent = []

    def send(self, frame): self.sent.append(frame)
    def recv(self, timeout): return []
    def close(self): pass


@pytest.fixture
def sent(monkeypatch):
    monkeypatch.setattr(ble, "BleLink", FakeLink)


def test_with_writes_turned_off_only_harmless_commands_go_out(sent) -> None:
    t = ble.make_ble_transport("AA:BB", allow_writes=False)
    t.send_frame = None
    link = t._seq
    send = t._send
    send(rc6.rq1(rc6.to_nib(0x1000), 4))                          # reads are fine
    send(rc6.dt1(rc6.to_nib(rc6.CMD_EDITOR_MODE), b"\x01"))       # editor mode
    send(rc6.dt1(rc6.to_nib(0x0000_0000), b"\x00\x02"))           # patch select
    send(rc6.dt1(rc6.to_nib(rc6.CMD_PREVIEW_MUTE), b"\x01"))      # preview mute
    assert len(link.sent) == 4
    for addr in (0x6000_0000, rc6.CMD_PATCH_WRITE, rc6.CMD_PATCH_CLEAR):
        with pytest.raises(ble.BleError, match="turned off"):
            send(rc6.dt1(rc6.to_nib(addr), b"\x01"))
    assert len(link.sent) == 4


def test_by_default_everything_goes_out(sent) -> None:
    t = ble.make_ble_transport("AA:BB")
    t._send(rc6.dt1(rc6.to_nib(0x6000_0000), b"\x01"))
    assert len(t._seq.sent) == 1


def test_the_transport_waits_longer_for_the_slower_link_and_closes_it(sent) -> None:
    t = ble.make_ble_transport("AA:BB")
    assert t.quiet_s == ble.QUIET_S > Transport(lambda f: None, lambda t: []).quiet_s
    closed = []
    t._seq.close = lambda: closed.append(1)
    t.stop()
    assert closed == [1]


def test_a_bluetooth_amp_is_listed_by_its_device_name(monkeypatch) -> None:
    from katatone.gui import statusbar
    dev = ble.BleDevice("E7:47:8F:07:BF:63", "KATANA 3 MIDI", True)
    monkeypatch.setattr(ble, "list_devices", lambda name_filter="katana": [dev])
    monkeypatch.setattr("katatone.protocol.transport.list_alsa_ports", lambda: {"out": ["KATANA3"]})
    ports = statusbar._ports()
    assert ports == ["KATANA3", "ble:E7:47:8F:07:BF:63"]
    assert statusbar._default_port(ports) == "KATANA3"             # USB preferred
    opt = statusbar._option("ble:E7:47:8F:07:BF:63")
    assert opt.key == "ble:E7:47:8F:07:BF:63" and opt.text == "KATANA 3 MIDI (Bluetooth)"
    assert statusbar._option("KATANA3").text == "KATANA3"


def test_a_bluetooth_amp_is_not_dropped_because_it_is_not_an_alsa_port() -> None:
    from katatone.gui import app as gui_app
    app = object.__new__(gui_app.App)
    app.connected, app.amp_port, app._connecting, app.busy = True, "ble:AA:BB", False, False
    dropped = []
    app._disconnect_thread = lambda **kw: dropped.append(kw)
    app._auto_connect_step(["KATANA3"], 100.0)
    assert dropped == []
    app.amp_port = "KATANA3"
    app._auto_connect_step([], 100.0)                              # a USB amp that vanished
    assert len(dropped) == 1


def test_without_dbus_fast_nothing_is_listed(monkeypatch) -> None:
    monkeypatch.setattr(ble, "_dbus", lambda: (_ for _ in ()).throw(ble.BleError("no")))
    assert ble.list_devices() == []


def test_a_missing_dbus_fast_is_reported_when_the_machine_has_bluetooth(monkeypatch) -> None:
    def no_dbus():
        raise ble.BleError("Bluetooth needs the dbus-fast package (pip install 'katatone[bluetooth]')")
    monkeypatch.setattr(ble, "_dbus", no_dbus)
    monkeypatch.setattr(ble, "_has_adapter", lambda: True)
    assert ble.list_devices() == [] and "dbus-fast" in ble.problem
    monkeypatch.setattr(ble, "_has_adapter", lambda: False)      # no adapter: nothing to say
    ble.list_devices()
    assert ble.problem is None


def test_listing_works_from_inside_a_running_event_loop() -> None:
    """The window is built on an event loop, where asyncio.run() is forbidden."""
    import asyncio

    async def work():
        return 42

    async def on_loop():
        return ble._run_in_thread(work)
    assert asyncio.run(on_loop()) == 42
