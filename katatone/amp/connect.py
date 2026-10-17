"""Amp connection: identity handshake + editor comm-mode setup."""
from __future__ import annotations

import logging
import time

from ..protocol import rc6
from ..protocol.rc6 import (CMD_EDITOR_LEVEL, CMD_EDITOR_MODE, identity_reply_prefix,
                            identity_request)
from ..protocol.transport import Transport

log = logging.getLogger("katatone.connect")

COMM_LEVEL = 1  # ProductSetting.communicationLevel for KATANA Gen 3


class HandshakeError(Exception):
    pass


class WrongDeviceError(HandshakeError):
    pass


class FirmwareMismatchError(HandshakeError):
    pass


def wait_for_identity(t: Transport, timeout: float = 5.0) -> bytes:
    """Send identity request; return the verified inquiry reply frame.

    Args:
        t: the connected transport to poll for the reply.
        timeout: seconds to wait for the reply (default 5.0).
    Returns:
        The identity reply frame (bytes).
    Raises:
        WrongDeviceError: if no reply arrives within `timeout`.
    """
    deadline = time.monotonic() + timeout
    prefix = identity_reply_prefix()
    t.send_frame(identity_request())
    while time.monotonic() < deadline:
        for kind, frame in t.log:
            if kind == "rx" and frame.startswith(prefix):
                return frame
        time.sleep(0.05)
    raise WrongDeviceError("no identity reply (wrong device or no connection)")


def handshake(t: Transport, timeout: float = 5.0) -> int:
    """Full connection sequence; returns the device's actual deviceId byte.

    Verifies the identity reply, gates on the editor communication level
    (must equal COMM_LEVEL), then turns editor MODE on.

    Args:
        t: the connected transport to talk to.
        timeout: seconds for the identity wait and the comm-level read.
    Returns:
        The deviceId byte from the identity reply.
    Raises:
        WrongDeviceError: if no identity reply arrives.
        FirmwareMismatchError: if the comm level != COMM_LEVEL.
    """
    log.debug("sending identity request…")
    reply = wait_for_identity(t, timeout)
    t.identity_reply = reply          # model code (byte 10): see amp.slots
    device_id = reply[2]
    log.debug("identity OK device=0x%02X", device_id)
    # Firmware gate: 1-byte RQ1 read of CMD_EDITOR_LEVEL must equal COMM_LEVEL
    level = t.rq1(CMD_EDITOR_LEVEL, 1, timeout=timeout)[0]
    log.debug("comm level = %d (expected %d)", level, COMM_LEVEL)
    if level != COMM_LEVEL:
        raise FirmwareMismatchError(
            f"editor communication level {level} != {COMM_LEVEL} "
            f"(device firmware {'older' if level < COMM_LEVEL else 'newer'} than supported)")
    # MODE = ON
    log.debug("enabling editor mode")
    t.dt1(CMD_EDITOR_MODE, b"\x01")
    return device_id


def disconnect(t: Transport) -> None:
    """MODE = OFF (best effort)."""
    log.debug("disabling editor mode")
    try:
        t.dt1(CMD_EDITOR_MODE, b"\x00")
        time.sleep(0.2)
    except Exception as e:
        log.warning("disconnect error (ignored): %s", e)
