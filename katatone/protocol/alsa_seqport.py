"""Controller input from ALSA *sequencer* ports (libasound through ``ctypes``).

Bluetooth MIDI devices are not rawmidi cards: the operating system (BlueZ's MIDI
bridge, or PipeWire / WirePlumber with a link) presents them as ports of
user-space sequencer clients.  KataTone creates a client of its own with one
input port, lists the output ports of user clients and of kernel clients that are no sound card (the others are the
rawmidi devices :mod:`alsa_seq` already offers), and subscribes its port to the
one chosen.  How the device is connected to the sequencer is the system's
business: nothing here pairs, links or scans.

The classes mirror :class:`~katatone.protocol.alsa_seq._Daemon` (``inports``,
``openin``, ``rawrecv``, ``close``) so :class:`~katatone.midi.input.MidiInput`
uses them unchanged; ``rawrecv`` returns plain MIDI bytes.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import select
import time
from ctypes import POINTER, byref, c_char_p, c_int, c_size_t, c_uint, c_ubyte, c_void_p

from . import pwmidi
from .alsa_seq import InPort, PortGoneError

SPEC_PREFIX = "seq:"

SND_SEQ_OPEN_DUPLEX = 3
SND_SEQ_NONBLOCK = 1
CAP_READ, CAP_WRITE, CAP_SUBS_READ, CAP_SUBS_WRITE = 0x01, 0x02, 0x20, 0x40
SND_SEQ_USER_CLIENT = 1
SYSTEM_CLIENT = 0
SND_SEQ_PORT_TYPE_MIDI_GENERIC, SND_SEQ_PORT_TYPE_APPLICATION = 0x2, 1 << 20
SND_SEQ_QUEUE_DIRECT = 253
SND_SEQ_ADDRESS_SUBSCRIBERS = 254
EV_NOTEON, EV_PGMCHANGE = 6, 11
_RECV_MAX = 256


class Event(ctypes.Structure):
    """``snd_seq_event_t`` (28 bytes; every field is a byte, so no padding)."""
    _fields_ = [("type", c_ubyte), ("flags", c_ubyte), ("tag", c_ubyte), ("queue", c_ubyte),
                ("time", c_ubyte * 8), ("src_client", c_ubyte), ("src_port", c_ubyte),
                ("dst_client", c_ubyte), ("dst_port", c_ubyte), ("data", c_ubyte * 12)]


class SeqError(RuntimeError):
    """libasound is missing or refused a call."""


def _lib() -> ctypes.CDLL:
    """Load libasound and declare the calls used.

    Raises:
        SeqError: if it is not installed.
    """
    name = ctypes.util.find_library("asound")
    if not name:
        raise SeqError("libasound is not installed")
    lib = ctypes.CDLL(name)
    P, S = c_void_p, c_char_p
    sig = {
        "snd_seq_open": (c_int, [POINTER(P), S, c_int, c_int]),
        "snd_seq_close": (c_int, [P]),
        "snd_seq_set_client_name": (c_int, [P, S]),
        "snd_seq_client_id": (c_int, [P]),
        "snd_seq_create_simple_port": (c_int, [P, S, c_uint, c_uint]),
        "snd_seq_connect_from": (c_int, [P, c_int, c_int, c_int]),
        "snd_seq_disconnect_from": (c_int, [P, c_int, c_int, c_int]),
        "snd_seq_poll_descriptors_count": (c_int, [P, c_int]),
        "snd_seq_event_input": (c_int, [P, POINTER(POINTER(Event))]),
        "snd_seq_event_output_direct": (c_int, [P, POINTER(Event)]),
        "snd_seq_client_info_malloc": (c_int, [POINTER(P)]),
        "snd_seq_client_info_free": (None, [P]),
        "snd_seq_client_info_set_client": (None, [P, c_int]),
        "snd_seq_client_info_get_client": (c_int, [P]),
        "snd_seq_client_info_get_name": (S, [P]),
        "snd_seq_client_info_get_type": (c_int, [P]),
        "snd_seq_client_info_get_card": (c_int, [P]),
        "snd_seq_query_next_client": (c_int, [P, P]),
        "snd_seq_port_info_malloc": (c_int, [POINTER(P)]),
        "snd_seq_port_info_free": (None, [P]),
        "snd_seq_port_info_set_client": (None, [P, c_int]),
        "snd_seq_port_info_set_port": (None, [P, c_int]),
        "snd_seq_port_info_get_port": (c_int, [P]),
        "snd_seq_port_info_get_name": (S, [P]),
        "snd_seq_port_info_get_capability": (c_uint, [P]),
        "snd_seq_query_next_port": (c_int, [P, P]),
        "snd_midi_event_new": (c_int, [c_size_t, POINTER(P)]),
        "snd_midi_event_free": (None, [P]),
        "snd_midi_event_init": (None, [P]),
        "snd_midi_event_no_status": (None, [P, c_int]),
        "snd_midi_event_decode": (ctypes.c_long, [P, POINTER(c_ubyte), ctypes.c_long, POINTER(Event)]),
    }
    for fn, (res, args) in sig.items():
        f = getattr(lib, fn)
        f.restype, f.argtypes = res, args
    lib.snd_seq_poll_descriptors.restype = c_int
    lib.snd_seq_poll_descriptors.argtypes = [c_void_p, c_void_p, c_uint, ctypes.c_short]
    return lib


def _check(rc: int, what: str) -> int:
    if rc < 0:
        raise SeqError(f"{what} failed (err {rc})")
    return rc


class SeqDaemon:
    """A sequencer client with one input port that follows one source port."""

    def __init__(self, name: str = "KataTone", source: str | None = None) -> None:
        """
        Args:
            name: this client's name.
            source: also create an output port of this name (tests use it as a
                stand-in for a device the system presents).
        """
        self._lib = _lib()
        h = c_void_p()
        self._port_name = b"input" if name != "KataTone" else b"KataTone input"
        _check(self._lib.snd_seq_open(byref(h), b"default", SND_SEQ_OPEN_DUPLEX, SND_SEQ_NONBLOCK),
               "snd_seq_open")
        self._h = h
        self._lib.snd_seq_set_client_name(h, name.encode())
        self.client = self._lib.snd_seq_client_id(h)
        self.port = _check(self._lib.snd_seq_create_simple_port(
            h, self._port_name, CAP_WRITE | CAP_SUBS_WRITE,
            SND_SEQ_PORT_TYPE_MIDI_GENERIC | SND_SEQ_PORT_TYPE_APPLICATION), "create port")
        self.out_port = (_check(self._lib.snd_seq_create_simple_port(
            h, source.encode(), CAP_READ | CAP_SUBS_READ,
            SND_SEQ_PORT_TYPE_MIDI_GENERIC | SND_SEQ_PORT_TYPE_APPLICATION), "create port")
            if source else None)
        dec = c_void_p()
        _check(self._lib.snd_midi_event_new(_RECV_MAX, byref(dec)), "snd_midi_event_new")
        self._lib.snd_midi_event_no_status(dec, 1)      # every message with its status byte
        self._dec = dec
        n = self._lib.snd_seq_poll_descriptors_count(h, select.POLLIN)
        buf = (ctypes.c_char * (8 * max(1, n)))()       # struct pollfd { int fd; short events, revents }
        self._lib.snd_seq_poll_descriptors(h, buf, n, select.POLLIN)
        self._fds = [int.from_bytes(buf[i * 8:i * 8 + 4], "little", signed=True) for i in range(n)]
        self._src: tuple[int, int] | None = None
        self._name = name

    # -- listing ------------------------------------------------------------
    def inports(self) -> list[InPort]:
        """Output ports of the clients through which the system presents devices:
        user-space ones and kernel ones that are no sound card."""
        lib, out = self._lib, []
        ci, pi = c_void_p(), c_void_p()
        lib.snd_seq_client_info_malloc(byref(ci))
        lib.snd_seq_port_info_malloc(byref(pi))
        try:
            lib.snd_seq_client_info_set_client(ci, -1)
            while lib.snd_seq_query_next_client(self._h, ci) >= 0:
                cid = lib.snd_seq_client_info_get_client(ci)
                if cid == self.client or cid == SYSTEM_CLIENT:
                    continue
                # A user client (BlueZ, PipeWire, ...) or a kernel one that is no sound
                # card ("Midi Through"): cards' own clients are the rawmidi devices.
                if (lib.snd_seq_client_info_get_type(ci) != SND_SEQ_USER_CLIENT
                        and lib.snd_seq_client_info_get_card(ci) >= 0):
                    continue
                cname = lib.snd_seq_client_info_get_name(ci).decode(errors="replace")
                lib.snd_seq_port_info_set_client(pi, cid)
                lib.snd_seq_port_info_set_port(pi, -1)
                while lib.snd_seq_query_next_port(self._h, pi) >= 0:
                    cap = lib.snd_seq_port_info_get_capability(pi)
                    if cap & (CAP_READ | CAP_SUBS_READ) != (CAP_READ | CAP_SUBS_READ):
                        continue
                    port = lib.snd_seq_port_info_get_port(pi)
                    pname = lib.snd_seq_port_info_get_name(pi).decode(errors="replace")
                    label = pname if pname.startswith(cname) else f"{cname} {pname}".strip()
                    out.append(InPort(f"{SPEC_PREFIX}{cid}:{port}", cname, label))
        finally:
            lib.snd_seq_client_info_free(ci)
            lib.snd_seq_port_info_free(pi)
        return out

    # -- input --------------------------------------------------------------
    def openin(self, spec: str) -> None:
        """Subscribe to the port named by ``spec`` (``seq:<client>:<port>``).

        Raises:
            RuntimeError: if the spec is malformed or the port is gone.
        """
        try:
            client, port = (int(x) for x in spec[len(SPEC_PREFIX):].split(":"))
        except ValueError:
            raise RuntimeError("openin failed: err bad spec") from None
        self._unsubscribe()
        if self._lib.snd_seq_connect_from(self._h, self.port, client, port) < 0:
            raise RuntimeError("openin failed: the sequencer port is gone")
        self._src = (client, port)

    def _unsubscribe(self) -> None:
        if self._src is not None:
            self._lib.snd_seq_disconnect_from(self._h, self.port, *self._src)
            self._src = None

    def rawrecv(self, timeout: float) -> bytes:
        """Wait up to ``timeout`` seconds and return the MIDI bytes that arrived."""
        out = bytearray()
        end = time.monotonic() + timeout
        while True:
            self._drain(out)
            left = end - time.monotonic()
            if out or left <= 0:
                return bytes(out)
            select.select(self._fds, [], [], left)

    def _drain(self, out: bytearray) -> None:
        """Decode every queued event into ``out``."""
        lib = self._lib
        ev = POINTER(Event)()
        buf = (c_ubyte * 16)()
        while lib.snd_seq_event_input(self._h, byref(ev)) >= 0 and ev:
            if (ev.contents.src_client, ev.contents.src_port) != self._src:
                continue
            lib.snd_midi_event_init(self._dec)
            n = lib.snd_midi_event_decode(self._dec, buf, len(buf), ev)
            if n > 0:
                out += bytes(buf[:n])

    def send(self, event_type: int, channel: int, a: int, b: int) -> None:
        """Send one event to the port's subscribers (used by tests)."""
        ev = Event()
        ev.type, ev.queue = event_type, SND_SEQ_QUEUE_DIRECT
        ev.src_client, ev.src_port = self.client, self.out_port
        ev.dst_client, ev.dst_port = SND_SEQ_ADDRESS_SUBSCRIBERS, 0
        if event_type == EV_PGMCHANGE:                 # control: channel, param, value
            ev.data[0] = channel
            ev.data[8:12] = b.to_bytes(4, "little")
        else:                                          # note: channel, note, velocity
            ev.data[0], ev.data[1], ev.data[2] = channel, a, b
        _check(self._lib.snd_seq_event_output_direct(self._h, byref(ev)), "send")

    def close(self) -> None:
        """Unsubscribe and close the client (idempotent)."""
        if self._h is None:
            return
        self._unsubscribe()
        self._lib.snd_midi_event_free(self._dec)
        self._lib.snd_seq_close(self._h)
        self._h = None


def list_all_input_ports() -> list[InPort]:
    """Every controller input: the rawmidi devices and the sequencer's user ports.

    Returns:
        The ports; the sequencer ones are left out when libasound or the
        sequencer is not available.
    """
    from .alsa_seq import list_input_ports
    ports = list(list_input_ports()) + pwmidi.inports()
    try:
        seq = SeqDaemon()
    except (SeqError, OSError):
        return ports
    try:
        return ports + seq.inports()
    finally:
        seq.close()


class CombinedDaemon:
    """The rawmidi daemon and the sequencer reader behind one interface.

    A spec starting with ``seq:`` goes to the sequencer, anything else to rawmidi.
    A system without libasound still lists and opens the rawmidi devices.
    """

    def __init__(self, raw_factory=None, seq_factory=SeqDaemon) -> None:
        from .alsa_seq import _Daemon
        self._raw = (raw_factory or _Daemon)()
        self._seq_factory = seq_factory
        self._seq: SeqDaemon | None = None
        self._pw = None                 # the reader of a PipeWire source, while open
        self._active = None

    def _sequencer(self):
        if self._seq is None:
            self._seq = self._seq_factory()
        return self._seq

    def inports(self) -> list[InPort]:
        """Rawmidi inputs, then the sequencer's user-client ports."""
        ports = list(self._raw.inports())
        try:
            ports += self._sequencer().inports()
        except (SeqError, OSError):
            pass
        return ports + pwmidi.inports()

    def openin(self, spec: str) -> None:
        """Open a rawmidi device or a sequencer port for input."""
        if self._pw is not None:
            self._pw.close()
            self._pw = None
        if spec.startswith(pwmidi.SPEC_PREFIX):
            self._pw = pwmidi.DumpReader(spec[len(pwmidi.SPEC_PREFIX):])
            self._active = self._pw
            return
        target = self._sequencer() if spec.startswith(SPEC_PREFIX) else self._raw
        target.openin(spec)
        self._active = target

    def rawrecv(self, timeout: float) -> bytes:
        """Bytes from the opened device."""
        if self._active is None:
            raise PortGoneError("not open")
        return self._active.rawrecv(timeout)

    def close(self) -> None:
        """Close both."""
        for d in (self._pw, self._seq, self._raw):
            if d is not None:
                d.close()
        self._seq = self._pw = None
