"""The amp link over Bluetooth LE MIDI, straight through BlueZ (D-Bus).

BOSS's Bluetooth adapter for the Katana is a plain BLE-MIDI device: Roland's
sysex travels in standard BLE-MIDI packets, and no initialisation packet is
needed.  This module frames and unframes those packets (:func:`encode`,
:class:`Decoder`, pure and tested) and drives the GATT link with ``dbus-fast``
(:class:`BleLink`), producing a :class:`~katatone.protocol.transport.Transport`
like the USB one.  Pairing the adapter is the system's business (KDE's Bluetooth
settings, ``bluetoothctl``); KataTone connects to a paired one.

The adapter's characteristic UUIDs are reported mangled by some BlueZ versions,
so the MIDI I/O characteristic is found by its properties (read, write without
response, notify) inside the BLE-MIDI service.
"""
from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass

log = logging.getLogger("katatone.ble")

SERVICE = "03b80e5a-ede8-4b33-a751-6ce34ec4c700"
CHARACTERISTIC = "7772e5db-3868-4112-a1a9-f2669d106bf3"
NEEDED_FLAGS = {"read", "write-without-response", "notify"}
SPEC_PREFIX = "ble:"
#: Largest packet used, whatever the MTU.
MAX_PACKET = 67
SYSEX_START, SYSEX_END = 0xF0, 0xF7
#: Over Bluetooth the frames of one long reply arrive further apart than over USB.
QUIET_S = 0.6


class BleError(RuntimeError):
    """The Bluetooth link could not be opened or used."""


# --------------------------------------------------------------- packets

def _timestamp(now_ms: int | None = None) -> tuple[int, int]:
    """(header byte, timestamp-low byte) for a 13-bit millisecond timestamp."""
    ms = (int(time.monotonic() * 1000) if now_ms is None else now_ms) & 0x1FFF
    return 0x80 | (ms >> 7), 0x80 | (ms & 0x7F)


def encode(message: bytes, mtu: int = 23, now_ms: int | None = None) -> list[bytes]:
    """Split one MIDI message into BLE-MIDI packets.

    A sysex message is spread over as many packets as needed: the first has the
    header, a timestamp and ``F0``; the following ones a header and data; the
    closing ``F7`` is preceded by a timestamp byte.

    Args:
        message: a complete channel message or sysex frame (``F0 … F7``).
        mtu: the connection's ATT MTU.
        now_ms: a millisecond clock (tests fix it).
    Returns:
        The packets, in order.
    """
    size = max(8, min(mtu, MAX_PACKET) - 3)
    head, ts = _timestamp(now_ms)
    if not message or message[0] != SYSEX_START:
        return [bytes([head, ts]) + bytes(message)]
    if message[-1] != SYSEX_END:
        raise ValueError("a sysex message must end with F7")
    body = message[:-1]
    packets, first = [], True
    pos = 0
    while pos < len(body):
        take = size - (2 if first else 1)
        chunk = body[pos:pos + take]
        packets.append(bytes([head]) + (bytes([ts]) if first else b"") + chunk)
        pos += len(chunk)
        first = False
    if len(packets[-1]) + 2 <= size:
        packets[-1] += bytes([ts, SYSEX_END])
    else:
        packets.append(bytes([head, ts, SYSEX_END]))
    return packets


class Decoder:
    """Turns BLE-MIDI packets into complete MIDI messages.

    Sysex is reassembled across packets.  A channel message must carry its
    status byte (running status is not supported); realtime bytes are dropped.
    """

    def __init__(self) -> None:
        self._sysex: bytearray | None = None

    def feed(self, packet: bytes) -> list[bytes]:
        """Decode one notification.

        Returns:
            The messages completed by it (possibly none).
        """
        out: list[bytes] = []
        n = len(packet)
        i = 1                                   # packet[0] is the header
        msg: bytearray | None = None
        while i < n:
            b = packet[i]
            if self._sysex is not None:                     # inside a sysex message
                if b == SYSEX_END:
                    self._sysex.append(b)
                    out.append(bytes(self._sysex))
                    self._sysex = None
                elif b & 0x80:
                    pass                                    # a timestamp (or realtime) byte
                else:
                    self._sysex.append(b)
            elif b & 0x80:
                if i + 1 < n and packet[i + 1] & 0x80 and b < 0xF8:
                    pass                                    # timestamp before a status byte
                elif b == SYSEX_START:
                    self._sysex = bytearray([b])
                elif b < 0xF8:
                    if msg:
                        out.append(bytes(msg))
                    msg = bytearray([b])
            elif msg is not None:
                msg.append(b)
            i += 1
        if msg:
            out.append(bytes(msg))
        return out


# ----------------------------------------------------------------- BlueZ

@dataclass(frozen=True)
class BleDevice:
    """A paired Bluetooth MIDI device."""
    address: str
    name: str
    connected: bool

    @property
    def spec(self) -> str:
        return f"{SPEC_PREFIX}{self.address}"


def _dbus():
    try:
        import dbus_fast.aio as aio
        import dbus_fast
    except ImportError as e:
        raise BleError("Bluetooth needs the dbus-fast package (pip install 'katatone[bluetooth]')") from e
    return aio, dbus_fast


#: Why the Bluetooth amp is not offered, when the machine has a Bluetooth adapter
#: but listing failed (None otherwise); the main window shows it once.
problem: str | None = None


def _has_adapter() -> bool:
    try:
        return bool(os.listdir("/sys/class/bluetooth"))
    except OSError:
        return False


def _run_in_thread(coro_factory, timeout: float = 15.0):
    """Run ``coro_factory()`` to completion on a fresh event loop in its own thread.

    The caller may itself be on a running event loop (the GUI builds the window on
    one), where ``asyncio.run`` is not allowed.
    """
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="ble-list") as pool:
        return pool.submit(lambda: asyncio.run(coro_factory())).result(timeout)


def list_devices(name_filter: str = "katana") -> list[BleDevice]:
    """The paired BLE-MIDI devices whose name contains ``name_filter``.

    Returns:
        The devices; empty when BlueZ or ``dbus-fast`` is not available, or when
        ``KATATONE_NO_BLUETOOTH`` is set (a package without Bluetooth support, such
        as the Flatpak, sets it so that no "needs dbus-fast" message is shown).
    """
    global problem
    if os.environ.get("KATATONE_NO_BLUETOOTH"):
        problem = None
        return []
    try:
        aio, fast = _dbus()
    except BleError as e:
        problem = str(e) if _has_adapter() else None
        return []
    problem = None

    async def go():
        bus = await aio.MessageBus(bus_type=fast.BusType.SYSTEM).connect()
        try:
            r = await bus.call(fast.Message(
                destination="org.bluez", path="/", interface="org.freedesktop.DBus.ObjectManager",
                member="GetManagedObjects"))
            out = []
            for path, ifs in r.body[0].items():
                d = ifs.get("org.bluez.Device1")
                if not d or SERVICE not in [u.lower() for u in d["UUIDs"].value]:
                    continue
                name = d.get("Name", d.get("Alias")).value
                if name_filter.lower() in name.lower() and d["Paired"].value:
                    out.append(BleDevice(d["Address"].value, name, bool(d["Connected"].value)))
            return out
        finally:
            bus.disconnect()
    try:
        return _run_in_thread(go)
    except Exception as e:                             # noqa: BLE001
        log.debug("listing Bluetooth MIDI devices failed", exc_info=True)
        problem = f"Bluetooth amp unavailable: {e}"
        return []


class BleLink:
    """One open BLE-MIDI connection with its own event-loop thread."""

    def __init__(self, address: str, connect_timeout: float = 25.0) -> None:
        """Connect and start listening.

        Args:
            address: the device's Bluetooth address.
            connect_timeout: seconds to wait for the connection.
        Raises:
            BleError: if it cannot be opened.
        """
        self.address = address
        self.decoder = Decoder()
        self.frames: "queue.Queue[bytes]" = queue.Queue()
        self.mtu = 23
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="ble", daemon=True)
        self._thread.start()
        self._fd = -1
        try:
            self._call(self._open(connect_timeout), connect_timeout + 10)
        except Exception as e:                         # noqa: BLE001
            self.close()
            raise e if isinstance(e, BleError) else BleError(str(e)) from e

    def _call(self, coro, timeout: float):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def _open(self, timeout: float) -> None:
        aio, fast = _dbus()
        self._fast = fast
        self._bus = await aio.MessageBus(bus_type=fast.BusType.SYSTEM, negotiate_unix_fd=True).connect()
        dev = "/org/bluez/hci0/dev_" + self.address.replace(":", "_")
        props = await self._get_all(dev, "org.bluez.Device1")
        if not props.get("Paired", fast.Variant("b", False)).value:
            raise BleError(f"{self.address} is not paired: add it in the system's Bluetooth settings")
        if not props["Connected"].value:
            await self._call_method(dev, "org.bluez.Device1", "Connect", timeout=timeout)
        end = time.monotonic() + timeout
        while not (await self._get_all(dev, "org.bluez.Device1"))["ServicesResolved"].value:
            if time.monotonic() > end:
                raise BleError("the device did not finish connecting")
            await asyncio.sleep(0.2)
        self._char = await self._find_characteristic(dev)
        reply = await self._call_method(self._char, "org.bluez.GattCharacteristic1", "AcquireNotify",
                                        signature="a{sv}", body=[{}])
        self._fd, self.mtu = reply.unix_fds[0], reply.body[1]
        os.set_blocking(self._fd, False)
        self._loop.add_reader(self._fd, self._on_readable)
        await self._call_method(self._char, "org.bluez.GattCharacteristic1", "ReadValue",
                                signature="a{sv}", body=[{}])        # as the BLE-MIDI spec asks
        log.debug("BLE MIDI open: %s mtu %s", self._char, self.mtu)

    async def _get_all(self, path: str, interface: str) -> dict:
        r = await self._call_method(path, "org.freedesktop.DBus.Properties", "GetAll",
                                    signature="s", body=[interface])
        return r.body[0]

    async def _call_method(self, path, interface, member, signature="", body=(), timeout=20.0):
        fast = self._fast
        reply = await asyncio.wait_for(self._bus.call(fast.Message(
            destination="org.bluez", path=path, interface=interface, member=member,
            signature=signature, body=list(body))), timeout)
        if reply.message_type == fast.MessageType.ERROR:
            raise BleError(f"{member}: {reply.body[0] if reply.body else reply.error_name}")
        return reply

    async def _find_characteristic(self, dev: str) -> str:
        r = await self._call_method("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
        services = {p for p, i in r.body[0].items() if p.startswith(dev)
                    and "org.bluez.GattService1" in i
                    and i["org.bluez.GattService1"]["UUID"].value.lower() == SERVICE}
        for p, i in sorted(r.body[0].items()):
            c = i.get("org.bluez.GattCharacteristic1")
            if not c or p.rsplit("/", 1)[0] not in services:
                continue
            if c["UUID"].value.lower() == CHARACTERISTIC or NEEDED_FLAGS <= set(c["Flags"].value):
                return p
        raise BleError("the device has no BLE-MIDI characteristic")

    def _on_readable(self) -> None:
        """Loop thread: take the notifications and queue the finished messages."""
        while True:
            try:
                data = os.read(self._fd, 512)
            except BlockingIOError:
                return
            except OSError:
                return
            if not data:
                return
            for msg in self.decoder.feed(data):
                self.frames.put(msg)

    # -- used by the transport ------------------------------------------------
    def send(self, message: bytes) -> None:
        """Write one MIDI message, packet by packet, waiting for each write."""
        for packet in encode(message, self.mtu):
            self._call(self._write(packet), 10)

    async def _write(self, packet: bytes) -> None:
        await self._call_method(self._char, "org.bluez.GattCharacteristic1", "WriteValue",
                                signature="aya{sv}",
                                body=[packet, {"type": self._fast.Variant("s", "command")}])

    def recv(self, timeout: float) -> list[bytes]:
        """Wait up to ``timeout`` seconds for messages; returns those that arrived."""
        out: list[bytes] = []
        try:
            out.append(self.frames.get(timeout=timeout))
            while True:
                out.append(self.frames.get_nowait())
        except queue.Empty:
            return out

    def close(self) -> None:
        """Release the notification stream and stop the thread (idempotent)."""
        loop, self._loop = self._loop, None
        if loop is None:
            return
        if self._fd >= 0:
            try:
                loop.call_soon_threadsafe(loop.remove_reader, self._fd)
                time.sleep(0.05)
                os.close(self._fd)
            except OSError:
                pass
            self._fd = -1
        loop.call_soon_threadsafe(loop.stop)
        self._thread.join(2)


def make_ble_transport(address: str, allow_writes: bool = True):
    """Open the amp over Bluetooth and wrap it as a :class:`Transport`.

    Args:
        address: the paired adapter's address.
        allow_writes: let Roland data writes through (the default).  Off
            (``--no-ble-writes``), only identity and read requests, the patch
            select, editor mode and preview mute are sent.
    Raises:
        BleError: if the link cannot be opened.
    """
    from .rc6 import (CMD_EDITOR_MODE, CMD_PATCH_SELECT, CMD_PREVIEW_MUTE, DT1, parse,
                      to_nib)
    from .transport import Transport
    link = BleLink(address)
    # Without --ble-writes only these may be written: switching the amp to a patch and
    # in and out of editor mode, and the preview mute; never patch or system data.
    harmless = {to_nib(a) for a in (0x0000_0000, CMD_EDITOR_MODE, CMD_PATCH_SELECT,
                                    CMD_PREVIEW_MUTE)}

    def send(frame: bytes) -> None:
        if not allow_writes and frame and frame[0] == SYSEX_START:
            f = parse(frame)
            if f is not None and f.command == DT1 and f.nib not in harmless:
                raise BleError("writes over Bluetooth are turned off (--no-ble-writes)")
        link.send(frame)

    t = Transport(send, link.recv)
    t._seq = link                                      # Transport.stop() closes it
    t.quiet_s = QUIET_S
    return t
