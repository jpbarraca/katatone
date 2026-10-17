"""Tests for the connection handshake (no hardware)."""
from __future__ import annotations

import pytest

from conftest import FakeAmp, make_transport
from katatone.protocol import rc6
from katatone.protocol.rc6 import CMD_EDITOR_LEVEL, CMD_EDITOR_MODE
from katatone.amp.connect import (handshake, disconnect, wait_for_identity,
                             WrongDeviceError, FirmwareMismatchError)


def test_handshake_success(transport, amp: FakeAmp) -> None:
    """handshake returns the KATANA device ID and turns editor mode on."""
    dev = handshake(transport)
    assert dev == rc6.DEVICE_ID
    # MODE = ON recorded as a write (keyed by nibble address)
    assert (rc6.to_nib(CMD_EDITOR_MODE), b"\x01") in amp.writes


def test_handshake_rejects_wrong_firmware(amp) -> None:
    """handshake raises FirmwareMismatchError when the amp reports a communication level other than COMM_LEVEL."""
    amp.mem[rc6.to_nib(CMD_EDITOR_LEVEL)] = b"\x05"   # COMM_LEVEL is 1
    t = make_transport(amp)
    try:
        with pytest.raises(FirmwareMismatchError):
            handshake(t)
    finally:
        t.stop()


def test_handshake_fail_without_identity(amp) -> None:
    """handshake raises WrongDeviceError when the device never answers the identity request."""
    class NoIdentity(FakeAmp):
        def reply_for(self, frame):
            """Reply to every frame except the identity request, which stays silent."""
            if frame == rc6.identity_request():
                return None        # no identity reply
            return super().reply_for(frame)

    t = make_transport(NoIdentity(memory={rc6.to_nib(CMD_EDITOR_LEVEL): b"\x01"}))
    try:
        with pytest.raises(WrongDeviceError):
            handshake(t)
    finally:
        t.stop()


def test_disconnect_writes_mode_off(transport, amp: FakeAmp) -> None:
    """disconnect turns editor mode off on the amp."""
    disconnect(transport)
    assert (rc6.to_nib(CMD_EDITOR_MODE), b"\x00") in amp.writes


def test_wait_for_identity_returns_prefix_frame(transport, amp: FakeAmp) -> None:
    """wait_for_identity returns the identity reply prefix frame with the device ID in byte 2."""
    frame = wait_for_identity(transport, timeout=2)
    assert frame.startswith(rc6.identity_reply_prefix())
    assert frame[2] == rc6.DEVICE_ID
