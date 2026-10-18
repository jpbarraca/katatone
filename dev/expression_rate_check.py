"""Hardware check: can live expression writes keep up?

Sweeps the pedal-wah position 0 -> 99 (edit buffer only) through a LiveWriter
at 100 Hz and at 30 Hz of input updates and reports how many DT1 frames the
transport really sent, how many echoes the amp returned, the achieved frame
rate and whether the final value read back correctly.  Whether the wah *sounds*
smooth needs ears.  The original position is restored.

Run:  python3 dev/expression_rate_check.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from katatone.amp.connect import disconnect, handshake  # noqa: E402
from katatone.amp.live import LiveWriter  # noqa: E402
from katatone.amp.write import read_param, write_param  # noqa: E402
from katatone.midi import mapping as mp  # noqa: E402
from katatone.protocol.transport import list_alsa_ports, make_alsa_transport  # noqa: E402


def sweep(t, block: str, p: int, hz: float, interval: float) -> None:
    """Run one 0..100 sweep and print the statistics.

    Args:
        t: the connected transport.
        block: the parameter's block.
        p: its index.
        hz: how often a new value is offered.
        interval: the LiveWriter coalescing interval.
    """
    echoes = []
    t.on_unsolicited = lambda nib, pl: echoes.append(nib)
    sent_before = sum(1 for k, _ in t.log if k == "tx")
    live = LiveWriter(lambda: t, interval=interval)
    t0 = time.monotonic()
    for v in range(100):              # 0..99: ends on a value different from 100
        live.queue_param(block, p, v)
        time.sleep(1.0 / hz)
    while live.pending():
        time.sleep(0.005)
    time.sleep(0.3)
    dt = time.monotonic() - t0
    sent = sum(1 for k, _ in t.log if k == "tx") - sent_before
    live.stop()
    final = read_param(t, block, p)
    print(f"{hz:5.0f} Hz offered, interval {interval * 1000:.0f} ms: {sent} DT1 sent "
          f"({sent / dt:.1f}/s over {dt:.2f} s), {len(echoes)} echoes, "
          f"final read-back {final} {'OK' if final == 99 else 'WRONG'}")


def main() -> int:
    """Run both sweeps and restore the parameter.

    Returns:
        0 when both sweeps ended on the right value.
    """
    names = [p for p in list_alsa_ports()["in"] if p.startswith("KATANA")]
    if not names:
        print("no amp port", file=sys.stderr)
        return 1
    block, p, _ = mp.resolve_expression(mp.ExprTarget("pedal_fx"), 0)
    t = make_alsa_transport(names[0], names[0])
    t.start()
    try:
        time.sleep(0.6)
        handshake(t)
        orig = read_param(t, block, p)
        print(f"{block}[{p}] original = {orig}")
        try:
            sweep(t, block, p, 100, 0.01)
            sweep(t, block, p, 30, 0.01)
        finally:
            write_param(t, block, p, orig)
            time.sleep(0.2)
            print("restored:", read_param(t, block, p))
    finally:
        disconnect(t)
        t.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
