"""MIDI transport: paced transmit queue + RX parser matching DT1 -> RQ1.

Wire rules:
  * pacing: delay before sending msg = len(msg)*1000/3125 + 20 ms
  * RQ1 reply: a DT1 frame with the same address completes the pending read
  * 15 s timeout on any pending request

Address model: read()/write() pass *pre-nibble* addresses.  This module is the
only place the nibble transform is applied: each request's pre-nibble base is
converted to a nibble device-memory index with ``rc6.to_nib`` and chunk
offsets then accumulate in that nibble domain, and the wire bytes become
``rc6.wire_addr(nib_base + off)``.  This is what makes a chunk that straddles a
0x7F -> 0x80 low-byte boundary read the correct bytes.

The MIDI backend is injectable so the state machine is unit-testable
without hardware (see tests/). The default backend uses `midiutil`/ALSA.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .rc6 import (DT1, EOX, STX, SYSEX_MAXLEN, SysexFrame, parse, rq1,
                  to_nib, wire_addr, nib_of_wire, wire_int)

log = logging.getLogger("katatone.transport")

PACE_BPS = 3125
PACE_INTERVAL_MS = 20
TIMEOUT_S = 15.0

# Max data bytes per chunk of a request.  RQ1/DT1 *data* is
# capped at SYSEX_MAXLEN = 128 (a 128-byte DT1 reply is a 141-byte
# sysex frame; USB-MIDI carries it and alsa_seq reassembles up to 2048 bytes).
RQ1_CHUNK = SYSEX_MAXLEN


def pace_delay_ms(msg: bytes) -> float:
    """Pacing delay before sending `msg`: `len(msg) * 1000 / 3125 + 20` ms.

    Returns:
        The delay to wait before transmitting, in milliseconds.
    """
    return len(msg) * 1000 / PACE_BPS + PACE_INTERVAL_MS


@dataclass
class _Pending:
    wire: int                  # wire address this chunk is keyed/answered by
    nib: int                   # nibble device-memory base of this chunk
    want: int                  # bytes requested in the RQ1 (not guaranteed)
    got: bytearray = field(default_factory=bytearray)
    event: threading.Event = field(default_factory=threading.Event)
    deadline: float = 0.0
    error: str | None = None
    n: int = 0                 # DT1 frames accumulated for this chunk


# How long one RX poll blocks the daemon.  A send has to wait for the poll in
# flight (they share the daemon pipe), so this is also the worst-case added
# latency of every write and read.  Measured on a KATANA-50: at
# 50 ms a live parameter write took 50 ms and a single-parameter read 101 ms; at
# 5 ms they take 27 ms and 36 ms, for about 1.6 % CPU while idle.
RX_POLL_S = 0.005


class Transport:
    """Thread-safe RC6 transport over an injected send/recv pair.

    send: Callable[[bytes], None]  — transmit one sysex frame
    recv: Callable[[float], list[bytes]] — block up to `timeout` s and return
          a list of *complete* incoming sysex frames (the MIDI driver already
          framed them; do not re-scan for an EOX terminator).
    """

    def __init__(self, send: Callable[[bytes], None],
                 recv: Callable[[float], list[bytes]]):
        """Wire the transport to a send/recv backend pair (see class docstring).

        Args:
            send: transmit one sysex frame.
            recv: block up to a timeout (seconds) and return the complete incoming frames.
        """
        self._send = send
        self._recv = recv
        self._pending: dict[int, _Pending] = {}
        self._tx_lock = threading.Lock()
        self.log: list[tuple[str, bytes]] = []   # ("tx"|"rx", frame) for capture/debug
        # Called as on_unsolicited(nib, payload) for a DT1 that matches no
        # pending RQ1 (the amp announcing a change, or echoing our own write).
        # Runs on the RX thread: it must return quickly.
        self.on_unsolicited: Callable[[int, bytes], None] | None = None
        # How long a run of DT1 frames answering one RQ1 must be silent before the
        # reply counts as complete; a slower link (Bluetooth) needs longer.
        self.quiet_s = 0.1

    # ------------------------------------------------------------------ tx

    def send_frame(self, frame: bytes) -> None:
        """Pace, log, and transmit one raw frame (serialized under the tx lock).

        Args:
            frame: the complete sysex frame to send.
        """
        log.debug("TX %d bytes: %s", len(frame), frame[:8].hex() + "…")
        with self._tx_lock:
            time.sleep(pace_delay_ms(frame) / 1000)
            self.log.append(("tx", frame))
            self._send(frame)

    def send_raw(self, data: bytes) -> None:
        """Transmit a short channel message (CC / PC) to the amp, unpaced.

        Takes the same lock as `send_frame` so the bytes can never land in the
        middle of a SysEx frame, but skips the SysEx pacing delay: a 2-3 byte
        message needs none.

        Args:
            data: one MIDI channel message (status byte plus its data bytes).

        Raises:
            ValueError if `data` is not a single channel message (0x80-0xEF
                status, at most 3 bytes, data bytes < 0x80).
        """
        if not 1 <= len(data) <= 3 or not 0x80 <= data[0] <= 0xEF \
                or any(x > 0x7F for x in data[1:]):
            raise ValueError(f"send_raw needs one channel message: {bytes(data).hex(' ')}")
        log.debug("TX raw %s", bytes(data).hex(" "))
        with self._tx_lock:
            self.log.append(("tx", bytes(data)))
            self._send(bytes(data))

    def dt1(self, addr: int, data: bytes) -> None:
        """DT1 write at pre-nibble `addr`.

        The caller (write_patch) is responsible for chunking blocks larger than
        the payload limit; a single DT1 frame carries at most ``SYSEX_MAXLEN``
        (128) data bytes.  `addr` is pre-nibble and
        is converted to the device memory index here.

        Args:
            addr: pre-nibble target address.
            data: payload bytes to write (must be 7-bit sysex data).

        Raises:
            ValueError if `data` exceeds the payload limit or has a byte > 0x7F.
        """
        from .rc6 import dt1 as _dt1
        if any(x > 0x7F for x in data):
            # SysEx data bytes are 7-bit; a byte >= 0x80 would reach the amp as
            # a MIDI status byte and corrupt the message.  Never send it.
            raise ValueError(f"dt1 data byte > 0x7F: {bytes(data).hex(' ')}")
        self.send_frame(_dt1(to_nib(addr), data))

    # ------------------------------------------------------------------ rx

    def _dispatch(self, frame: bytes) -> None:
        """Route an incoming DT1 reply to the matching pending RQ1 chunk.

        Match by WIRE address (the 4 raw nibble-encoded bytes), which is the
        device-memory index packed onto the wire.  Pending requests are keyed
        by wire hex and addresses are never decoded before matching, and —
        crucially — whatever the amp returns for each RQ1 is *accepted*
        (advancing the read pointer by ``dt1_data.length / 2``), rather than
        waiting for a fixed chunk size.

        The amp does NOT always answer a 128-byte RQ1 with 128 bytes: for a
        chunk whose range would run into a neighbouring (overlapping) block it
        replies with only the bytes up to that boundary.  So we settle the
        pending chunk on the *first* DT1 reply that matches its wire address
        and record exactly how many bytes came back — we never wait for the
        originally requested count.
        """
        self.log.append(("rx", frame))
        f = parse(frame)
        if f is None or f.command != DT1:
            return
        w = wire_int(f.nib)            # wire address (raw nibble bytes) as int
        p = self._pending.get(w)
        if p is None:
            hook = self.on_unsolicited
            if hook is not None:
                try:
                    hook(f.nib, f.payload)
                except Exception:
                    log.exception("on_unsolicited hook failed")
            return
        p.got.extend(f.payload)
        p.n += 1
        p.event.set()

    def _rx_pump(self) -> None:
        """Background reader: pulls from the backend until disconnected.

        The backend delivers *complete* sysex frames (the MIDI driver/stack
        handles framing); each is dispatched directly.  Note payloads may
        themselves contain 0xF7 bytes, so frames must NOT be reassembled by
        scanning for an EOX terminator.
        """
        while not self._stop_event.is_set():
            try:
                raw = self._recv(RX_POLL_S)
            except Exception:
                time.sleep(0.05)
                continue
            for frame in raw:
                self._dispatch(frame)

    # -------------------------------------------------------------- rq1/read

    def rq1(self, addr: int, size: int,
            timeout: float = TIMEOUT_S,
            stop_on_empty: bool = False,
            single_transaction: bool = False) -> bytes:
        """Read `size` bytes at pre-nibble `addr`, chunking automatically.

        The pre-nibble base is converted to a nibble device-memory index and
        chunk offsets accumulate in that domain, so chunk i sits at
        ``to_nib(addr) + cumulative_offset`` with wire bytes
        ``rc6.wire_addr`` of that nibble base.  `size` may exceed the chunk cap.

        When `stop_on_empty` is True a chunk the amp answers with no bytes
        (the KATANA FX_DETAIL wall) is treated as the end of reachable data
        and the read returns what has been accumulated so far, instead of
        raising ``TimeoutError``.

        When `single_transaction` is True the amp is asked once for the whole
        `size` and the reply is taken as-is: a single ``rq1(addr, declared_size)`` for a
        KATANA FX_DETAIL block, advancing by the reply length.  The KATANA amp hard-caps each
        FX_DETAIL block: one RQ1 with the declared size returns exactly the
        reachable bytes (min(size, 225)) in one transaction and answers
        nothing for the following gap, so no further request is made.  This is
        the follow-amp read path used by ``read_patch`` (``read.py``).
        """
        return self.rq1_nib(to_nib(addr), size, timeout, stop_on_empty,
                            single_transaction)

    def _ping(self, timeout: float = 3.0) -> bool:
        """Send a 1-byte RQ1 to the COMMAND block to check if the amp is alive.

        Args:
            timeout: how long to wait for the reply, in seconds.

        Returns:
            True if the amp answered within `timeout`, False on timeout.
        """
        from .rc6 import rq1 as _rq1
        cmd_nib = to_nib(0x7F000000)
        w = wire_int(cmd_nib)
        p = _Pending(wire=w, nib=cmd_nib, want=1,
                     deadline=time.monotonic() + timeout)
        self._pending[w] = p
        self.send_frame(_rq1(cmd_nib, 1))
        ok = p.event.wait(timeout)
        self._pending.pop(w, None)
        log.debug("ping %s", "OK" if ok else "FAILED")
        return ok

    def rq1_nib(self, nib_base: int, size: int,
                timeout: float = TIMEOUT_S,
                stop_on_empty: bool = False,
                single_transaction: bool = False) -> bytes:
        """Read `size` bytes at *nibble-space* address `nib_base`, chunking.

        Use this when the caller has already computed the nibble address
        (e.g. multi-session chunked reads that must accumulate offsets in
        nibble space without re-applying the non-linear to_nib transform).
        Retries each chunk up to 3 times on timeout, with a ping to verify
        the amp is responsive before each retry.

        Chunk advances follow the AMP's actual reply size (`addr += dt1_data.length / 2`): each chunk requests up to
        ``RQ1_CHUNK`` bytes and records exactly how many the amp returns, then
        moves on by that many nibbles.

        ``stop_on_empty`` turns an empty chunk response into a *soft stop*
        instead of a timeout.  The KATANA amp hard-caps each FX_DETAIL block:
        it answers the RQ1s up to its 225-byte window and then answers *nothing*
        for the following gap.  For a follow-amp read that empty response is not
        an error — it marks the end of the reachable data — so we return the
        bytes accumulated so far.  For ordinary blocks (``stop_on_empty=False``)
        the behaviour is unchanged: the chunk is retried, then raises
        ``TimeoutError``.

        ``single_transaction`` reads the whole `size` in ONE RQ1 and takes the
        reply as-is (no chunk loop, no advance).  This is the pattern
        for a KATANA FX_DETAIL block: one ``rq1(addr, declared_size)``,
        advance by the reply length.  The amp answers a single such RQ1 with
        exactly min(size, wall) bytes (225 for FX_DETAIL) and nothing past the
        wall, so one request captures the whole reachable block — never
        re-requesting the dead zone.
        """
        if size <= 0:
            return b""
        if single_transaction:
            log.debug("RQ1 (single) nib=0x%08X size=%d", nib_base, size)
            w = wire_int(nib_base)
            p = _Pending(wire=w, nib=nib_base, want=size,
                         deadline=time.monotonic() + timeout)
            self._pending[w] = p
            try:
                self.send_frame(rq1(nib_base, size))
                ok = p.event.wait(timeout)
            finally:
                self._pending.pop(w, None)
            if not ok:
                # The amp answered nothing for this (single) request.  For a
                # follow-amp FX_DETAIL block the wall is past this address, so
                # the reachable block is whatever came back (here, nothing).
                log.debug("RQ1 (single) timeout nib=0x%08X size=%d",
                          nib_base, size)
                return b""
            # A single RQ1 for a wall-limited block (e.g. KATANA FX_DETAIL,
            # hard-capped at 225 bytes) is answered by the amp as a run of
            # <=128-byte DT1 frames carrying the same address.  Drain any
            # frames still in flight (p.n stabilises) so the full reply is
            # accumulated, not just the first chunk.
            quiet_until = time.monotonic() + self.quiet_s
            last = p.n
            while True:
                if p.n != last:
                    last = p.n
                    quiet_until = time.monotonic() + self.quiet_s
                if time.monotonic() >= quiet_until:
                    break
                time.sleep(0.002)
            chunk_data = bytes(p.got)
            log.debug("  single nib=0x%08X -> %d bytes (requested %d)",
                      nib_base, len(chunk_data), size)
            return chunk_data
        log.debug("RQ1 nib=0x%08X size=%d", nib_base, size)
        out = bytearray()
        offset = 0
        max_retries = 3
        while offset < size:
            want = min(size - offset, RQ1_CHUNK)
            nib = nib_base + offset                 # nibble-space chunk base
            w = wire_int(nib)                      # wire address key
            chunk_data = None
            for attempt in range(1, max_retries + 1):
                p = _Pending(wire=w, nib=nib, want=want,
                             deadline=time.monotonic() + timeout)
                self._pending[w] = p
                self.send_frame(rq1(nib, want))
                if p.event.wait(timeout):
                    self._pending.pop(w, None)
                    chunk_data = bytes(p.got)       # amp's actual reply bytes
                    if not chunk_data:
                        # The amp answered but returned no bytes — this is the
                        # "amp answers nothing past this address" wall.  For a
                        # follow-amp read we stop here (all reachable bytes are
                        # already in hand); a strict read retries below.
                        if stop_on_empty:
                            break
                        continue
                    break
                # Timeout with no reply — the amp answered nothing at this
                # address.  For a follow-amp read this is the wall: stop after
                # the first attempt instead of burning retries that will all
                # fail against the same empty region.
                self._pending.pop(w, None)
                if stop_on_empty and attempt == 1:
                    break
                log.warning("RQ1 chunk timeout nib=0x%08X off=%d "
                            "want=%d attempt %d/%d",
                            nib_base, offset, want, attempt, max_retries)
                if attempt < max_retries:
                    # Ping to verify amp is alive; wait if not
                    log.info("sending ping to recover…")
                    time.sleep(0.5)
                    ping_to = min(3.0, timeout)
                    for ping_try in range(3):
                        if self._ping(timeout=ping_to):
                            break
                        time.sleep(min(3.0, timeout * 2))
                    time.sleep(0.5)
            if chunk_data is None:
                if stop_on_empty:
                    # Follow-amp read: the amp's reachable data ends here (a
                    # hard wall / gap).  Return the bytes accumulated so far
                    # rather than aborting the whole patch read.
                    log.debug("RQ1 stop_on_empty at nib=0x%08X off=%d "
                              "(%d bytes so far)",
                              nib_base, offset, len(out))
                    return bytes(out)
                raise TimeoutError(
                    f"RQ1 timeout nib=0x{nib_base:08X} off={offset} "
                    f"want={want} after {max_retries} attempts")
            out.extend(chunk_data)
            # ADVANCE BY THE AMP'S ACTUAL REPLY SIZE (follow-amp model).
            advance = len(chunk_data) or want
            offset += advance
            log.debug("  chunk nib=0x%08X -> %d bytes (requested %d)",
                      nib, len(chunk_data), want)
            # Small inter-chunk pacing to avoid flooding the amp
            if offset < size:
                time.sleep(0.02)
        return bytes(out)

    # -------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Start the background RX thread that dispatches incoming frames."""
        log.debug("starting RX thread")
        self._stop_event = threading.Event()
        self._rx_thread = threading.Thread(target=self._rx_pump, daemon=True)
        self._rx_thread.start()

    def stop(self) -> None:
        """Stop the RX thread and close the attached ALSA client, if any."""
        log.debug("stopping transport")
        if hasattr(self, "_stop_event"):
            self._stop_event.set()
        seq = getattr(self, "_seq", None)
        if seq is not None:
            seq.close()
            self._seq = None  # type: ignore[attr-defined]

    def pending_count(self) -> int:
        """Number of RQ1 chunks currently awaiting a DT1 reply."""
        return len(self._pending)


# ------------------------------------------------------------------ backend

_seq_client: "alsas.SeqClient | None" = None


def make_alsa_transport(in_name: str, out_name: str) -> "Transport":
    """Build a Transport over an ALSA rawmidi device (see alsa_seq).

    Args:
        in_name: ALSA input port name, as reported by ``list_alsa_ports``.
        out_name: ALSA output port name.

    Returns:
        A `Transport` wired to the new `SeqClient`.
    """
    from . import alsa_seq as alsas
    global _seq_client
    seq = alsas.SeqClient(in_name, out_name)
    _seq_client = seq

    def _send(frame: bytes) -> None:
        """Send one sysex frame over the ALSA client."""
        seq.send_sysex(frame)

    def _recv(timeout: float) -> list[bytes]:
        """Return the complete frames received within `timeout` seconds."""
        return seq.recv_frames(timeout)

    t = Transport(_send, _recv)
    t._seq = seq  # type: ignore[attr-defined]
    return t


def list_alsa_ports() -> dict[str, list[str]]:
    """Enumerate MIDI ports: {'in': [...], 'out': [...]}."""
    from . import alsa_seq as alsas
    return alsas.list_ports()
