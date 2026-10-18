"""Hardware check: does the amp act on plain MIDI PC/CC sent over USB in editor mode?

Reads the amp's own MIDI settings (receive channel, PC for CH1B, CC for MOD),
then with --apply sends, via ``Transport.send_raw``:
  * a Program Change for channel 1B  -> does the amp switch and announce it?
  * CC (MOD) = 127 then 0           -> does the MOD switch in the edit buffer follow?
and finally restores the original channel and MOD state.  It sends no SysEx
beyond the normal handshake / select / single-parameter restore, and never a
patch write.

Run:  python3 dev/amp_native_check.py            # read-only: show the settings
      python3 dev/amp_native_check.py --apply    # perform the check
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
from katatone.amp.write import read_param, select_patch, write_param  # noqa: E402
from katatone.midi import mapping as mp  # noqa: E402
from katatone.model.paramset import ParamSet  # noqa: E402
from katatone.protocol.transport import list_alsa_ports, make_alsa_transport  # noqa: E402


def sys_param(t, block: str, name: str) -> int:
    """Read one SYSTEM parameter by name.

    Args:
        t: the connected transport.
        block: the SYSTEM block name.
        name: the full parameter name.

    Returns:
        Its display value.
    """
    blk = load_map()["SYSTEM"].block(block)
    for p in blk.params:
        if p.name == name:
            raw = t.rq1(blk.base_addr + p.off, p.size)
            return ParamSet.raw_value(p.size, raw) - p.ofs
    raise KeyError(name)


def patch_number(t) -> int:
    """The amp's current patch number."""
    return ParamSet.raw_value(2, t.rq1(0, 2))


def main() -> int:
    """Run the check.

    Returns:
        0 on success.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--port", default="KATANA")
    args = ap.parse_args()
    names = [p for p in list_alsa_ports()["in"] if p.startswith(args.port)]
    if not names:
        print("no amp port", file=sys.stderr)
        return 1
    t = make_alsa_transport(names[0], names[0])
    t.start()
    seen = []
    t0 = time.monotonic()
    try:
        time.sleep(0.6)
        handshake(t)
        t.on_unsolicited = lambda nib, pl: seen.append((time.monotonic() - t0, nib, bytes(pl)))
        model = slots.model_code(getattr(t, "identity_reply", None))
        ch = sys_param(t, "MIDI_COM", "PRMID_SYSTEM_MIDI_COM_CHANNEL")
        pc_1b = sys_param(t, "MIDI_PC", "PRMID_SYSTEM_MIDI_PC_CH1B")
        cc_mod = sys_param(t, "MIDI_CC", "PRMID_SYSTEM_MIDI_CC_MOD")
        orig = patch_number(t)
        mod_blk, mod_i, _ = mp.effect_param("MOD")
        mod0 = read_param(t, mod_blk, mod_i)
        print(f"model={slots.model_name(model)} rx channel={ch + 1} PC(1B)={pc_1b} "
              f"CC(MOD)={cc_mod} | current patch={orig} MOD={mod0}")
        if not args.apply:
            return 0
        target = 5 if slots.has_slot(5, model) else 1

        print(f"\nH2a: PC {pc_1b} on channel {ch + 1} (expect the amp to go to patch {target}) ...")
        seen.clear()
        t.send_raw(bytes([0xC0 | ch, pc_1b]))
        time.sleep(1.5)
        now = patch_number(t)
        print(f"  patch number now: {now}  -> {'AMP FOLLOWED THE PC' if now == target else 'no reaction'}")
        print(f"  notifications: {[(round(a, 2), hex(b), c.hex(' ')) for a, b, c in seen if b == 0]}")

        print(f"\nH2b: CC {cc_mod} = 127 then 0 (MOD switch in the edit buffer) ...")
        t.send_raw(bytes([0xB0 | ch, cc_mod, 127]))
        time.sleep(0.5)
        on = read_param(t, mod_blk, mod_i)
        t.send_raw(bytes([0xB0 | ch, cc_mod, 0]))
        time.sleep(0.5)
        off = read_param(t, mod_blk, mod_i)
        print(f"  MOD after 127: {on}, after 0: {off}  -> "
              f"{'AMP FOLLOWED THE CC' if (on, off) == (1, 0) else 'no reaction / different'}")
    finally:
        try:
            if args.apply:
                print("\nrestoring ...")
                select_patch(t, orig)
                time.sleep(0.5)
                if read_param(t, mod_blk, mod_i) != mod0:
                    write_param(t, mod_blk, mod_i, mod0)
                print(f"  patch={patch_number(t)} MOD={read_param(t, mod_blk, mod_i)} "
                      f"(was {orig}/{mod0})")
        except Exception as e:                          # noqa: BLE001
            print("restore failed:", e)
        disconnect(t)
        t.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
