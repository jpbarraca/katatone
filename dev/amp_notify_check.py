"""Hardware checks for MIDI routing (developer tool).

1.  Does the amp announce its current patch with an unsolicited DT1 at
    0x00000000 (SETUP/COM patch number)?  Listen while the amp's own channel
    buttons are pressed, and (with --apply) after our own CMD_PATCH_SELECT.
2.  After CMD_PATCH_SELECT n, is the edit buffer (region PATCH) equal to the
    stored slot PATCH(n+1)?  If so a resync can read the slot and live writes
    after a switch land on the new channel.

Without --apply this is read-only: handshake (editor mode on/off, as the GUI
does), read the current patch number, and listen for notifications.  With
--apply it also selects another slot, compares, and reselects the original.
It never writes patch data and never sends PATCH_WRITE.

Run:  python3 dev/amp_notify_check.py                 # read-only, 15 s listen
      python3 dev/amp_notify_check.py --listen 30     # longer window to press buttons
      python3 dev/amp_notify_check.py --apply         # also the edit-buffer comparison + select notification
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from katatone.address.map import load_map  # noqa: E402
from katatone.amp import slots  # noqa: E402
from katatone.amp.connect import disconnect, handshake  # noqa: E402
from katatone.amp.write import select_patch  # noqa: E402
from katatone.model.paramset import ParamSet  # noqa: E402
from katatone.protocol import rc6  # noqa: E402
from katatone.protocol.transport import list_alsa_ports, make_alsa_transport  # noqa: E402

PATCH_NUMBER_NIB = rc6.to_nib(0x0000_0000)
# Small blocks are enough to tell two patches apart.
COMPARE_BLOCKS = ("COM", "AMP", "SW", "BOOSTER(1)", "NS", "PEDALFX_COM",
                  "SENDRETURN", "SOLO_COM")


def current_patch(t) -> int:
    """Read the amp's current patch number (SETUP/COM, 2 nibble bytes).

    Args:
        t: the connected transport.

    Returns:
        The patch number 0-9.
    """
    raw = t.rq1(0x0000_0000, 2)
    return ParamSet.raw_value(2, raw)


def compare_edit_buffer(t, n: int) -> list[str]:
    """Compare the edit buffer against slot `n` block by block.

    Args:
        t: the connected transport.
        n: the slot (0-based) the amp just selected.

    Returns:
        Names of the blocks that differ (empty means the edit buffer equals the slot).
    """
    mp = load_map()
    edit, slot = mp["PATCH"], mp[f"PATCH({n + 1})"]
    diff = []
    for name in COMPARE_BLOCKS:
        a = t.rq1(edit.block(name).base_addr, edit.block(name).length)
        b = t.rq1(slot.block(name).base_addr, slot.block(name).length)
        print(f"  {name:12} edit={a.hex(' ')}  slot={b.hex(' ')}  "
              f"{'same' if a == b else 'DIFFERENT'}")
        if a != b:
            diff.append(name)
    return diff


def main() -> int:
    """Run the checks.

    Returns:
        Process exit status (0 if every check that ran held).
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="also select another slot (edit-buffer comparison + notification after select)")
    ap.add_argument("--listen", type=float, default=15.0,
                    help="seconds to listen for notifications (default 15)")
    ap.add_argument("--port", default="KATANA", help="amp card name prefix")
    args = ap.parse_args()

    names = [p for p in list_alsa_ports()["in"] if p.startswith(args.port)]
    if not names:
        print("no amp port found", file=sys.stderr)
        return 1
    t = make_alsa_transport(names[0], names[0])
    t.start()
    ok = True
    t0 = time.monotonic()
    seen: list[tuple[float, int, bytes]] = []

    def on_unsolicited(nib: int, payload: bytes) -> None:
        seen.append((time.monotonic() - t0, nib, bytes(payload)))
        tag = "PATCH NUMBER" if nib == PATCH_NUMBER_NIB else "other"
        print(f"  [{seen[-1][0]:7.3f}s] unsolicited DT1 nib=0x{nib:08X} "
              f"payload={bytes(payload).hex(' ')}  ({tag})", flush=True)

    try:
        time.sleep(0.6)
        handshake(t)
        t.on_unsolicited = on_unsolicited
        model = slots.model_code(getattr(t, "identity_reply", None))
        valid = [s.index for s in slots.slots(model)]
        orig = current_patch(t)
        print(f"model={slots.model_name(model)}  current patch number={orig} "
              f"({slots.slot(orig, model).short})  slots={valid}")

        print(f"\nH1a: listening {args.listen:.0f} s — press the amp's channel "
              f"buttons now ...")
        time.sleep(args.listen)
        print(f"  {len(seen)} unsolicited DT1(s) in the window")

        if args.apply:
            target = next(i for i in valid if i != orig)
            print(f"\nselecting slot {target} "
                  f"({slots.slot(target, model).short}) ...")
            seen.clear()
            select_patch(t, target)
            time.sleep(1.0)
            print(f"  notifications after our select: {len(seen)}")
            print(f"  patch number now: {current_patch(t)}")
            diff = compare_edit_buffer(t, target)
            print("check:", "edit buffer == slot" if not diff else f"DIFFERS in {diff}")
            ok = not diff
            print(f"\nreselecting original slot {orig} ...")
            select_patch(t, orig)
            time.sleep(0.5)
            print(f"  patch number now: {current_patch(t)}")
    except Exception as e:                            # noqa: BLE001
        print("ERROR:", repr(e))
        ok = False
    finally:
        try:
            disconnect(t)
        finally:
            t.stop()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
