"""Probe: single RQ1(addr, size) reply at the FX_DETAIL wall.

Sends exactly ONE RQ1 frame at the FX_DETAIL(1) nibble base (0x04040e00)
requesting `size` bytes, and records how many DT1 data bytes come back. This
is how an FX_DETAIL block is read: a single rq1(addr, declared_size), then
addr += reply_len/2.

Also tests advancing single-byte RQ1s (128 at off 0, 97 at off 128, then a few
bytes past 225) to locate the exact amp wall.
"""
from __future__ import annotations

import sys
import time

from katatone.amp.connect import handshake
from katatone.protocol import rc6
from katatone.protocol.transport import _Pending, make_alsa_transport, list_alsa_ports

NIB = 0x04040E00  # to_nib(FX_DETAIL(1) base_addr 0x20101c00)


def single_rq1(t, nib: int, size: int, timeout: float = 5.0):
    """Send exactly ONE RQ1 frame and wait for its single DT1 reply.

    Args:
        t: open ALSA transport connected to the amp.
        nib: nibble address to request (to_nib of a byte address).
        size: number of bytes requested in the frame.
        timeout: seconds to wait for the reply before giving up.

    Returns:
        (reply bytes, wire address of the request) on success, or
        (None, None) if no reply arrives within the timeout.
    """
    w = rc6.wire_int(nib)
    p = _Pending(wire=w, nib=nib, want=size)
    t._pending[w] = p
    try:
        t.send_frame(rc6.rq1(nib, size))
        ok = p.event.wait(timeout)
    finally:
        t._pending.pop(w, None)
    if not ok:
        return None, None
    return bytes(p.got), w


def main() -> int:
    """Probe the FX_DETAIL wall with single and advancing single-byte RQ1s.

    Returns:
        0 when the probe finished, 2 if no KATANA3 ALSA port is found.
    """
    ports = list_alsa_ports()
    print("ports:", ports)
    if "KATANA3" not in (ports.get("in", []) + ports.get("out", [])):
        print("KATANA3 not found; exiting")
        return 2

    t = make_alsa_transport("KATANA3", "KATANA3")
    t.start()
    time.sleep(0.6)
    dev = handshake(t)
    print(f"connected deviceId=0x{dev:02X}")

    print(f"\n=== single RQ1 at FX_DETAIL(1) nib=0x{NIB:08X} ===")
    for size in (0, 1, 127, 128, 129, 200, 224, 225, 226, 256, 353):
        data, w = single_rq1(t, NIB, size, timeout=5.0)
        n = len(data) if data is not None else None
        tag = "TIMEOUT" if data is None else f"{n} bytes"
        print(f"  size={size:4d} -> {tag}")
        if data is not None and n <= 4:
            print(f"       payload: {data.hex()}")
        if data is not None and n > 4:
            print(f"       first4 : {data[:4].hex()}  last4: {data[-4:].hex()}")

    print("\n=== advancing single RQ1 (128@off0, then 128@off128, then off->232) ===")
    # Mimic chunked read to see where the wall is.
    offs = [0, 128, 129, 130, 224, 225, 226, 227, 232, 256]
    for off in offs:
        data, w = single_rq1(t, NIB + off, 128, timeout=2.0)
        n = len(data) if data is not None else None
        tag = "TIMEOUT" if data is None else f"{n} bytes"
        print(f"  off={off:4d} -> {tag}")

    t.stop()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
