"""Live edit sender: coalescing, pausing and edit-buffer-only writes."""
from __future__ import annotations

import time

from conftest import FakeAmp
from katatone.address.map import load_map
from katatone.amp.live import NAME, LiveWriter
from katatone.model.paramset import ParamSet
from katatone.protocol import rc6

EDIT = load_map()["PATCH"]
PRESENCE = (EDIT.block("AMP").base_addr + 5)


def _wait(cond, timeout=2.0) -> bool:
    """Poll a condition until it holds or the timeout expires.

    Args:
        cond: zero-arg callable polled every 10 ms.
        timeout: maximum seconds to wait.

    Returns:
        True if the condition held, False on timeout.
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def _presence_writes(amp: FakeAmp) -> list[bytes]:
    """Collect the payloads written to the PRESENCE param of the fake amp.

    Args:
        amp: the fake amp whose write log is scanned.

    Returns:
        the list of payload bytes written at the PRESENCE nibble address.
    """
    return [d for nib, d in amp.writes if nib == rc6.to_nib(PRESENCE)]


def test_fast_changes_are_coalesced_to_the_last_value(transport,
                                                      amp: FakeAmp) -> None:
    """Rapid param updates coalesce: only a handful of writes go out and the last one carries the final value."""
    sent = []
    live = LiveWriter(lambda: transport, on_sent=sent.append, interval=0.05)
    for v in range(10, 60):                         # a fast knob drag
        live.queue_param("AMP", 5, v)
    assert _wait(lambda: live.pending() == 0 and sent)
    writes = _presence_writes(amp)
    assert writes[-1] == bytes([59])
    assert len(writes) < 10                         # not one per step
    live.stop()


def test_paused_holds_sends_until_released(transport, amp: FakeAmp) -> None:
    """While paused the LiveWriter holds queued sends and flushes the final value on release."""
    busy = [True]
    live = LiveWriter(lambda: transport, paused=lambda: busy[0], interval=0.01)
    live.queue_param("AMP", 5, 42)
    time.sleep(0.2)
    assert not _presence_writes(amp)                # held while busy
    busy[0] = False
    assert _wait(lambda: _presence_writes(amp) == [bytes([42])])
    live.stop()


def test_name_goes_to_edit_buffer_and_nothing_without_transport(
        transport, amp: FakeAmp) -> None:
    """Names go to the COM block of the edit buffer, and a writer without a transport drops updates silently."""
    errors = []
    live = LiveWriter(lambda: None, on_error=errors.append, interval=0.01)
    live.queue_param("AMP", 5, 1)
    time.sleep(0.1)
    assert not amp.writes and not errors            # not connected: dropped

    sent = []
    live = LiveWriter(lambda: transport, on_sent=sent.append, interval=0.01)
    live.queue_name("Live Tone")
    assert _wait(lambda: sent == [NAME])
    com = EDIT.block("COM").base_addr
    assert (rc6.to_nib(com), ParamSet.encode_patch_name("Live Tone")) \
        in amp.writes
    # edit buffer only: nothing at or above the first user patch slot
    assert all(nib < rc6.to_nib(0x20100000) for nib, _ in amp.writes)
    live.stop()


def test_bad_value_reports_error_and_sends_nothing(transport,
                                                   amp: FakeAmp) -> None:
    """A value outside the param's range triggers on_error and sends nothing."""
    errors = []
    live = LiveWriter(lambda: transport, on_error=errors.append, interval=0.01)
    live.queue_param("AMP", 5, 500)                 # outside 0..100
    assert _wait(lambda: errors)
    assert not _presence_writes(amp)
    live.stop()
