"""Transport tests: RQ1 read state machine, DT1 tx, pacing, timeouts."""
from __future__ import annotations

import time

import pytest

from conftest import FakeAmp, make_transport
from katatone.protocol import rc6
from katatone.protocol.transport import Transport, pace_delay_ms


def test_pace_delay_formula() -> None:
    """pace_delay_ms follows the reference formula (frame length in ms plus 20 ms)."""
    # 1-byte-ish: len*1000/3125 + 20 ms
    assert pace_delay_ms(b"\x00") == 1 * 1000 / 3125 + 20


def test_rq1_read_basic(transport, amp: FakeAmp) -> None:
    """A basic RQ1 read returns the bytes seeded at the address."""
    addr = 0x2000_0010
    amp.mem[rc6.to_nib(addr)] = bytearray(b"\x12\x34\x56\x78")
    assert transport.rq1(addr, 4) == b"\x12\x34\x56\x78"


def test_rq1_missing_returns_zeros(transport) -> None:
    """An RQ1 for a missing address returns zero bytes rather than failing."""
    assert transport.rq1(0x2000_0010, 4) == b"\x00\x00\x00\x00"


def test_rq1_chunked_across_three_chunks(transport, amp: FakeAmp) -> None:
    """A 300-byte read arrives as 128/128/44 chunks at the right nibble offsets and reassembles exactly."""
    # 300 bytes -> chunks 128 / 128 / 44 at byte offsets 0, 128(0x80), 256(0x100).
    # The transport converts the pre-nibble base to a nibble device-memory base
    # and accumulates chunk offsets in that domain, so seed each chunk window at
    # to_nib(base) + its byte offset.
    base = 0x2000_0000
    full = bytes((i * 7) % 256 for i in range(300))
    nbase = rc6.to_nib(base)
    for off, size in ((0, 128), (0x80, 128), (0x100, 44)):
        amp.mem[nbase + off] = bytearray(full[off:off + size])
    got = transport.rq1(base, 300)
    assert got == full


def test_dt1_tx_reaches_backend_and_records(transport, amp: FakeAmp) -> None:
    """A DT1 write reaches the amp (keyed by nibble index) and is logged as a tx frame."""
    addr = 0x2000_0100
    data = b"\x2A\x3B\x4C"
    transport.dt1(addr, data)
    # The amp records writes keyed by the nibble device-memory index.
    assert (rc6.to_nib(addr), data) in amp.writes
    # tx frame was logged
    assert any(kind == "tx" for kind, _ in transport.log)


def test_dt1_refuses_non_7bit_data(transport, amp: FakeAmp) -> None:
    """dt1 rejects data bytes >= 0x80 (which would be MIDI status bytes) and sends nothing."""
    # A data byte >= 0x80 would be a MIDI status byte inside the SysEx.
    with pytest.raises(ValueError):
        transport.dt1(0x2000_0100, b"\x01\x80")
    assert not amp.writes


def test_rq1_timeout_when_device_never_replies(amp) -> None:
    """An RQ1 to a silent device raises TimeoutError after the retry budget, within a bounded time."""
    class NoReply(FakeAmp):
        def reply_for(self, frame):
            """Never reply, simulating a dead device."""
            return None

    t = make_transport(NoReply(memory={rc6.CMD_EDITOR_LEVEL: b"\x01"}))
    try:
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            t.rq1(0x2000_0010, 4, timeout=0.2)
        # 3 retries × (0.2s timeout + ping recovery) ≈ 6–8 s
        assert time.monotonic() - start < 15
    finally:
        t.stop()


def test_dt1_too_long_is_rejected_mid_read(transport) -> None:
    """A DT1 larger than the max payload is refused with ValueError."""
    # A chunk larger than the max payload cannot be issued.
    with pytest.raises(ValueError):
        transport.dt1(0x2000_0000, b"\x00" * (rc6.SYSEX_MAXLEN + 1))


# --------------------------------------------------------------- on_unsolicited


def test_unsolicited_hook_not_called_for_rq1_reply(transport, amp: FakeAmp) -> None:
    """A DT1 that settles a pending RQ1 is a reply, not a notification."""
    seen: list = []
    transport.on_unsolicited = lambda nib, payload: seen.append((nib, payload))
    addr = 0x2000_0010
    amp.mem[rc6.to_nib(addr)] = bytearray(b"\x01\x02")
    assert transport.rq1(addr, 2) == b"\x01\x02"
    assert seen == []


def test_unsolicited_hook_gets_unmatched_dt1(transport) -> None:
    """A DT1 with no pending RQ1 reaches the hook with its nibble address and payload."""
    seen: list = []
    transport.on_unsolicited = lambda nib, payload: seen.append((nib, bytes(payload)))
    nib = rc6.to_nib(0x0000_0000)
    transport._dispatch(rc6.dt1(nib, b"\x00\x03"))
    assert seen == [(nib, b"\x00\x03")]


def test_unsolicited_hook_ignores_non_dt1(transport) -> None:
    """Only DT1 frames are notifications; an RQ1 echo is not."""
    seen: list = []
    transport.on_unsolicited = lambda nib, payload: seen.append(nib)
    transport._dispatch(rc6.rq1(rc6.to_nib(0x0000_0000), 2))
    assert seen == []


def test_unsolicited_hook_exception_keeps_rx_pump_alive(amp: FakeAmp) -> None:
    """An exception in the hook is logged and later reads still work."""
    t = make_transport(amp)
    try:
        def boom(nib, payload) -> None:
            raise RuntimeError("boom")

        t.on_unsolicited = boom
        t._dispatch(rc6.dt1(rc6.to_nib(0x0000_0000), b"\x00\x01"))
        amp.mem[rc6.to_nib(0x2000_0010)] = bytearray(b"\x09")
        assert t.rq1(0x2000_0010, 1) == b"\x09"
    finally:
        t.stop()


# --------------------------------------------------------------------- send_raw


def test_send_raw_sends_and_logs() -> None:
    """send_raw hands the bytes to the backend unchanged and logs them as tx."""
    sent: list[bytes] = []
    t = Transport(sent.append, lambda timeout: [])
    t.send_raw(b"\xc0\x05")
    t.send_raw(b"\xb0\x10\x7f")
    assert sent == [b"\xc0\x05", b"\xb0\x10\x7f"]
    assert t.log == [("tx", b"\xc0\x05"), ("tx", b"\xb0\x10\x7f")]


def test_send_raw_rejects_bad_input() -> None:
    """Sysex, real-time, data-first, oversize and 8-bit data bytes are refused."""
    sent: list[bytes] = []
    t = Transport(sent.append, lambda timeout: [])
    for bad in (b"", b"\xf0\x41\xf7", b"\xf8", b"\x05\x05", b"\xb0\x10\x7f\x00",
                b"\xb0\x80\x00"):
        with pytest.raises(ValueError):
            t.send_raw(bad)
    assert sent == []


def test_send_raw_waits_for_the_tx_lock() -> None:
    """send_raw cannot interleave with a SysEx frame that is mid-send."""
    import threading

    sent: list[bytes] = []
    t = Transport(sent.append, lambda timeout: [])
    done = threading.Event()
    with t._tx_lock:
        th = threading.Thread(target=lambda: (t.send_raw(b"\xc0\x01"), done.set()))
        th.start()
        assert not done.wait(0.1)       # blocked while the lock is held
    assert done.wait(1)
    th.join()
    assert sent == [b"\xc0\x01"]
