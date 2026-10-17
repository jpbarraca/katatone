"""ALSA MIDI backend: plain file I/O on the kernel's rawmidi device nodes.

A USB-MIDI device such as the amp shows up as ``/dev/snd/midiC<card>D<dev>``.
Those nodes are ordinary character devices, so reading and writing them needs
no library, no compiler and no helper process: the same Python code works on
any Linux with ALSA (the user needs access to ``/dev/snd``, normally the
``audio`` group or a logind ACL).  Cards are listed from
``/proc/asound/cards``, and device names come from the control device's
``SNDRV_CTL_IOCTL_RAWMIDI_INFO`` ioctl (the same names ``amidi -l`` prints).

Two kinds of use, never on the same device at once:

* the amp link (:class:`SeqClient`): duplex, sysex frames are reassembled from
  the USB packets they arrive in;
* controller input (``openin`` / ``rawrecv``): input only, bytes returned as
  read, since note/CC/PC messages never end in ``F7``.
"""
from __future__ import annotations

import atexit
import errno
import fcntl
import os
import re
import select
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path

#: Device nodes and proc files; module-level so tests can point them at fakes.
SND_DIR = Path("/dev/snd")
PROC_ASOUND = Path("/proc/asound")

# Reads are done in slices this long so close() is noticed promptly.
_SLICE_S = 0.2
_READ_MAX = 512
_SYSEX_MAX = 2048

# Sysex clients created by SeqClient, for atexit cleanup.
_live: set["SeqClient"] = set()
_live_lock = threading.Lock()


class PortBusyError(RuntimeError):
    """The rawmidi device is held by another program (`EBUSY`)."""


class PortGoneError(RuntimeError):
    """The rawmidi device disappeared (controller unplugged)."""


@dataclass(frozen=True)
class InPort:
    """One rawmidi device that can deliver input (a controller or the amp).

    Attributes:
        spec: "<card>,<device>" as understood by `openin`; valid only until the
            device is replugged, since card numbers change.
        card: the ALSA card name ("SINCO").
        name: the device name ("SINCO MIDI 1").
    """

    spec: str
    card: str
    name: str

    @property
    def label(self) -> str:
        """The stable identifier stored in profiles (the device name, never `hw:N`)."""
        return self.name


# ----------------------------------------------------------------- cards

_CARD_LINE = re.compile(r"^\s*(\d+) \[(.*?)\s*\]:\s*(.*)$")


@dataclass(frozen=True)
class _Card:
    """One line of ``/proc/asound/cards``: number, id ("KATANA3") and name."""

    num: int
    id: str
    name: str


def _cards() -> list[_Card]:
    """Return every sound card the kernel knows, in card-number order."""
    try:
        text = (PROC_ASOUND / "cards").read_text()
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        m = _CARD_LINE.match(line)
        if m:
            # "USB-Audio - KATANA3": the driver, then the card's name.
            _, _, name = m.group(3).partition(" - ")
            out.append(_Card(int(m.group(1)), m.group(2), name or m.group(2)))
    return out


def _midi_cards() -> list[_Card]:
    """The cards that have a rawmidi endpoint (device 0)."""
    return [c for c in _cards() if (PROC_ASOUND / f"card{c.num}" / "midi0").exists()]


def _device_nodes(card: int) -> list[int]:
    """Rawmidi device numbers present for ``card``, ascending."""
    devs = []
    for p in SND_DIR.glob(f"midiC{card}D*"):
        m = re.fullmatch(rf"midiC{card}D(\d+)", p.name)
        if m:
            devs.append(int(m.group(1)))
    return sorted(devs)


# struct snd_rawmidi_info (sound/asound.h); ioctl number = _IOWR('U', 0x41, ...).
_INFO_SIZE = 268
_INFO_IOCTL = (3 << 30) | (_INFO_SIZE << 16) | (ord("U") << 8) | 0x41
_STREAM_INPUT = 1


def _subdevice_name(card: int, dev: int, fallback: str) -> str | None:
    """The input subdevice's name ("SINCO MIDI 1"), or None if it has no input.

    Args:
        card: the card number.
        dev: the rawmidi device number.
        fallback: name used when the subdevice name is empty.
    """
    try:
        fd = os.open(SND_DIR / f"controlC{card}", os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        buf = bytearray(_INFO_SIZE)
        struct.pack_into("IIi", buf, 0, dev, 0, _STREAM_INPUT)
        fcntl.ioctl(fd, _INFO_IOCTL, buf)
    except OSError:
        return None     # no input stream on this device
    finally:
        os.close(fd)
    name = bytes(buf[20 + 64 + 80:20 + 64 + 80 + 32]).split(b"\0")[0].decode(errors="replace")
    if not name:
        name = bytes(buf[20 + 64:20 + 64 + 80]).split(b"\0")[0].decode(errors="replace")
    return name or fallback


# ------------------------------------------------------------ reassembly


class SysexReassembler:
    """Turn the bytes read from a rawmidi device into complete sysex frames.

    USB-MIDI splits a long sysex over several 64-byte packets and rawmidi
    delivers each as a separate read, so bytes are buffered until the ``F7``
    terminator arrives.  While reassembling, so stray bytes cannot corrupt the
    link:

    * real-time bytes (``F8``..``FF``: clock, active sensing) are dropped, even
      inside a sysex;
    * bytes outside a sysex (before an ``F0``) are discarded;
    * a new ``F0`` abandons an unterminated earlier frame;
    * one frame is emitted per ``F7``, even when several arrive in one read.
    """

    def __init__(self) -> None:
        """Start with no frame in progress."""
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        """Add received bytes and return the frames they completed.

        Args:
            chunk: bytes as read from the device.
        Returns:
            The complete ``F0 .. F7`` frames, in arrival order.
        """
        frames = []
        buf = self._buf
        for b in chunk:
            if b >= 0xF8:
                continue
            if b == 0xF0:
                buf.clear()
            elif not buf:
                continue
            if len(buf) >= _SYSEX_MAX:      # overlong: drop it
                buf.clear()
                continue
            buf.append(b)
            if b == 0xF7:
                frames.append(bytes(buf))
                buf.clear()
        return frames


# ----------------------------------------------------------------- device


class _Daemon:
    """Rawmidi access for one purpose: the amp link or one controller.

    (The name is kept from the former ``kt_alsa`` helper process this class
    replaced; everything now happens in-process.)
    """

    def __init__(self) -> None:
        """Create an idle instance; nothing is opened yet."""
        self._fd: int | None = None        # duplex amp link (open)
        self._in_fd: int | None = None     # controller input (openin)
        self._reasm = SysexReassembler()
        self._rx_lock = threading.Lock()
        self._tx_lock = threading.Lock()
        self._closed = False

    # -- discovery --------------------------------------------------------

    def ports(self) -> list[str]:
        """Names of the cards that have a rawmidi endpoint ("KATANA3")."""
        return [c.id for c in _midi_cards()]

    def inports(self) -> list[InPort]:
        """Every rawmidi device with an input stream, per device."""
        out = []
        for c in _cards():
            for dev in _device_nodes(c.num):
                name = _subdevice_name(c.num, dev, f"{c.name} MIDI {dev + 1}")
                if name is not None:
                    out.append(InPort(f"{c.num},{dev}", c.name, name))
        return out

    # -- amp link ---------------------------------------------------------

    def open(self, name: str) -> int:
        """Open the card named ``name`` for duplex sysex I/O.

        Args:
            name: a card id, or any string starting with one ("KATANA3 MIDI 1").
        Returns:
            The card number.
        Raises:
            RuntimeError if there is no such card or it cannot be opened.
        """
        card = next((c for c in _midi_cards()
                     if name == c.id or name.startswith(c.id)), None)
        if card is None:
            raise RuntimeError(f"open failed: err card not found: {name}")
        self._close_fd("_fd")
        try:
            self._fd = os.open(SND_DIR / f"midiC{card.num}D0",
                               os.O_RDWR | os.O_NONBLOCK)
        except OSError as e:
            raise RuntimeError(f"open failed: err open hw:{card.num},0 "
                               f"rc={-e.errno} ({e.strerror})") from e
        self._reasm = SysexReassembler()
        return card.num

    def send(self, frame: bytes) -> None:
        """Write one frame to the amp.

        Args:
            frame: the complete MIDI bytes to transmit.
        Raises:
            RuntimeError if the link is not open or the write fails.
        """
        fd = self._fd
        if fd is None:
            raise RuntimeError("send failed: err not open")
        view = memoryview(frame)
        with self._tx_lock:
            try:
                while view:
                    try:
                        n = os.write(fd, view)
                    except BlockingIOError:
                        select.select([], [fd], [], 1.0)
                        continue
                    view = view[n:]
            except OSError as e:
                raise RuntimeError(f"send failed: err send {-e.errno}") from e

    def recv(self, timeout: float) -> list[bytes]:
        """Wait up to ``timeout`` seconds for complete sysex frames.

        Returns as soon as the data available has completed at least one
        frame.

        Args:
            timeout: how long to wait, in seconds.
        Returns:
            The frames received (possibly none).
        """
        fd = self._fd
        if fd is None:
            return []
        deadline = time.monotonic() + timeout
        with self._rx_lock:
            frames: list[bytes] = []
            while not frames and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if not select.select([fd], [], [], min(remaining, _SLICE_S))[0]:
                    continue
                try:
                    chunk = os.read(fd, _READ_MAX)
                except BlockingIOError:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                frames += self._reasm.feed(chunk)
            return frames

    # -- controller input -------------------------------------------------

    def openin(self, spec: str) -> None:
        """Open one device for input only (nothing can be sent to it).

        Args:
            spec: "<card>,<device>" from `InPort.spec`.
        Raises:
            PortBusyError if another program holds the device.
            RuntimeError for any other failure.
        """
        m = re.fullmatch(r"(\d+),(\d+)", spec)
        if not m:
            raise RuntimeError("openin failed: err bad spec")
        self._close_fd("_in_fd")
        try:
            self._in_fd = os.open(SND_DIR / f"midiC{m.group(1)}D{m.group(2)}",
                                  os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            if e.errno == errno.EBUSY:
                raise PortBusyError(spec) from e
            raise RuntimeError(f"openin failed: err {-e.errno}") from e

    def rawrecv(self, timeout: float) -> bytes:
        """Wait up to ``timeout`` seconds and return the bytes that arrived.

        Args:
            timeout: how long to wait, in seconds.
        Returns:
            The bytes exactly as read (no message framing).
        Raises:
            PortGoneError if the device was unplugged.
        """
        fd = self._in_fd
        if fd is None:
            return b""
        poller = select.poll()
        poller.register(fd, select.POLLIN)
        out = bytearray()
        with self._rx_lock:
            try:
                events = poller.poll(max(1, int(timeout * 1000)))
            except OSError:
                events = []
            if any(ev & (select.POLLERR | select.POLLHUP | select.POLLNVAL)
                   for _, ev in events):
                self._gone()
            while events:
                try:
                    chunk = os.read(fd, _READ_MAX)
                except BlockingIOError:
                    break
                except OSError as e:
                    if e.errno in (errno.ENODEV, errno.EIO):
                        self._gone()
                    break
                if not chunk:
                    break
                out += chunk
        return bytes(out)

    def _gone(self) -> None:
        """Close the vanished input device and report it."""
        self._close_fd("_in_fd")
        raise PortGoneError("device gone")

    # -- shutdown ---------------------------------------------------------

    def _close_fd(self, attr: str) -> None:
        """Close and forget the descriptor held in ``attr``, if any."""
        fd = getattr(self, attr)
        setattr(self, attr, None)
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def close(self) -> None:
        """Close every open device."""
        self._closed = True
        self._close_fd("_fd")
        self._close_fd("_in_fd")


# ------------------------------------------------------------------ ports


def list_ports() -> dict[str, list[str]]:
    """Enumerate rawmidi MIDI cards: {'in': [names], 'out': [names]}.

    A USB-MIDI card exposes both directions on one rawmidi endpoint, so the
    same name list is returned for in and out.
    """
    names = [c.id for c in _midi_cards()]
    return {"in": names, "out": list(names)}


def list_input_ports() -> list[InPort]:
    """Enumerate every rawmidi device that can deliver input.

    Unlike `list_ports` this includes the device name and is per device rather
    than per card. Callers filter out the amp themselves.

    Returns:
        The input ports, or an empty list if none can be read.
    """
    try:
        return _Daemon().inports()
    except Exception:
        return []


# ------------------------------------------------------------------ client


class SeqClient:
    """Duplex ALSA rawmidi client bound to a named card (e.g. "KATANA3")."""

    def __init__(self, in_name: str | None = None,
                 out_name: str | None = None):
        """Open a duplex rawmidi client on the named card.

        Args:
            in_name: ALSA port name to use when out_name is not given.
            out_name: ALSA port name to open (takes precedence over in_name).

        Raises:
            ValueError if neither name is given.
        """
        name = out_name or in_name
        if not name:
            raise ValueError("SeqClient requires a port/card name")
        self._d = _Daemon()
        self.card_id: int = self._d.open(name)
        with _live_lock:
            _live.add(self)
        atexit.register(self._atexit_close)

    def _atexit_close(self) -> None:
        """atexit hook: unregister the client and close it, ignoring errors."""
        with _live_lock:
            _live.discard(self)
        try:
            self.close()
        except Exception:
            pass

    def send_sysex(self, frame: bytes) -> None:
        """Transmit one sysex frame to the amp."""
        self._d.send(frame)

    def recv_frames(self, timeout: float) -> list[bytes]:
        """Block up to `timeout` seconds and return the complete frames received."""
        return self._d.recv(timeout)

    def close(self) -> None:
        """Unregister the client and close the device."""
        with _live_lock:
            _live.discard(self)
        self._d.close()
