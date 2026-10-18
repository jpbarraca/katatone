"""Controlled check: does ``write_patch`` store the editor's data in a slot?

Question: our Write DT1s the patch straight into the slot's memory
(PATCH(n)) and then sends CMDID_PATCH_WRITE n.  An alternative design edits
the *edit buffer* (PATCH) and Write only sends CMDID_PATCH_WRITE n, i.e.
"store the edit buffer into slot n".  If that is what the command does, our
Write stores the amp's current sound instead of the editor's patch.

Procedure (one slot, default 2B = patch 6):
  1. back up the slot (also saved as a liveset in the library), the edit
     buffer and the selected patch number;
  2. our write_patch with the slot's own data + AMP PRESENCE changed by one
     step, to a value different from the edit buffer's -> read the slot back;
  3. restore the slot the original way (backup -> edit buffer -> PATCH_WRITE)
     and verify it block by block against the backup (fallback: write_patch);
  4. reselect the original patch and put the edit buffer back.

Run:  python3 dev/write_check.py            # read-only: backups and dry run
      python3 dev/write_check.py --apply    # perform the check
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
from katatone.amp.read import PATCH_BLOCKS, read_patch, read_region  # noqa: E402
from katatone.amp.write import (_dt1_chunked, select_patch,  # noqa: E402
                           write_patch)
from katatone.library.store import Library  # noqa: E402
from katatone.liveset.tsl import Tsl  # noqa: E402
from katatone.model.paramset import ParamSet  # noqa: E402
from katatone.protocol import rc6  # noqa: E402
from katatone.protocol.transport import make_alsa_transport  # noqa: E402

MAP = load_map()
AMP_BLOCK = "AMP"
PRESENCE = 5                 # AMP param index


def log(msg: str) -> None:
    """Print a progress line immediately."""
    print(msg, flush=True)


def presence_of(ps: ParamSet) -> int:
    """Decode the AMP PRESENCE value out of a slot or edit-buffer ParamSet.

    Args:
        ps: ParamSet holding a patch slot or the edit buffer.

    Returns:
        The decoded PRESENCE value (0..100).
    """
    return ParamSet.decode(MAP["PATCH"].block(AMP_BLOCK),
                           ps.block(f"PATCH%{AMP_BLOCK}"), PRESENCE)


def with_presence(ps: ParamSet, value: int) -> ParamSet:
    """Copy a ParamSet with the AMP PRESENCE value replaced.

    Args:
        ps: source ParamSet (a patch slot or the edit buffer).
        value: new PRESENCE value, in the parameter's 0..100 range.

    Returns:
        A new ParamSet whose AMP block carries the new value.
    """
    out = ParamSet()
    out.data = dict(ps.data)
    raw = bytearray(out.block(f"PATCH%{AMP_BLOCK}"))
    ParamSet.encode(MAP["PATCH"].block(AMP_BLOCK), raw, PRESENCE, value)
    out.data[f"PATCH%{AMP_BLOCK}"] = bytes(raw)
    return out


def diff_blocks(a: ParamSet, b: ParamSet) -> list[str]:
    """List the patch blocks whose data differs between two ParamSets.

    Args:
        a: first ParamSet.
        b: second ParamSet.

    Returns:
        The names of the blocks with differing data.
    """
    return [n for n in PATCH_BLOCKS
            if a.block(f"PATCH%{n}") != b.block(f"PATCH%{n}")]


def read_slot_amp(t, n: int) -> bytes:
    """Read a patch slot's AMP block back from the amp.

    Args:
        t: open ALSA transport connected to the amp.
        n: patch slot number (region PATCH(n+1) in the address map).

    Returns:
        The raw DT1 data bytes of the slot's AMP block.
    """
    blk = MAP[f"PATCH({n + 1})"].block(AMP_BLOCK)
    return t.rq1(blk.base_addr, blk.length, timeout=3.0)


def current_patch(t) -> int:
    """Read the currently selected patch number from the amp.

    Args:
        t: open ALSA transport connected to the amp.

    Returns:
        The selected patch number, as stored in SETUP COM PATCH_NUMBER.
    """
    raw = t.rq1(0x00000000, 2, timeout=3.0)      # SETUP COM PATCH_NUMBER, 2x4
    return ParamSet.raw_value(2, raw)


def write_edit_buffer(t, ps: ParamSet) -> None:
    """Write a ParamSet's blocks into the edit buffer, one DT1 per block.

    Args:
        t: open ALSA transport connected to the amp.
        ps: ParamSet whose blocks go into the edit buffer.
    """
    region = MAP["PATCH"]
    for name in PATCH_BLOCKS:
        data = ps.block(f"PATCH%{name}")
        if data:
            _dt1_chunked(t, region.block(name).base_addr, data)


def commit(t, n: int) -> None:
    """Original Write: store the edit buffer into slot n.

    Sends CMDID_PATCH_WRITE n and then CMDID_EXPORT n, settling after each.

    Args:
        t: open ALSA transport connected to the amp.
        n: patch slot number (the --slot value).
    """
    t.dt1(rc6.CMD_PATCH_WRITE, rc6.int2x7(n))
    time.sleep(0.3)
    t.dt1(rc6.CMD_EXPORT, rc6.int2x7(n))
    time.sleep(0.5)


def main() -> int:
    """Run the write_patch storage check on one slot and restore everything.

    With --apply it backs up the slot, the edit buffer and the selected
    patch, writes the slot's own data with AMP PRESENCE changed by one
    step, judges what the write stored, then restores the slot via the
    original Write path and puts the selection and edit buffer back.

    Returns:
        0 on success or dry run, 2 if the slot is refused, 1 if a check
        or restore failed.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--slot", type=int, default=6, help="patch number (6 = 2B)")
    ap.add_argument("--port", default="KATANA3")
    args = ap.parse_args()
    n = args.slot

    t = make_alsa_transport(args.port, args.port)
    t.start()
    time.sleep(0.6)
    rc = 0
    try:
        handshake(t)
        model = slots.model_code(getattr(t, "identity_reply", None))
        label = slots.slot(n, model).short
        if n == 0 or n not in [s.index for s in slots.slots(model)]:
            log(f"Refusing: patch {n} is not a regular slot on "
                f"{slots.model_name(model)}")
            return 2
        sel = current_patch(t)
        log(f"{slots.model_name(model)}; selected patch {sel} "
            f"({slots.slot(sel, model).short}); test slot {label} (patch {n})")

        # 1. backups
        log("Reading slot backup…")
        slot_bak = read_patch(t, n)
        log("Reading edit buffer backup…")
        ebuf_bak = read_region(t, "PATCH")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        tsl = Tsl(name=f"Backup {label} {stamp}",
                  patches=[Tsl.encode(slot_bak), Tsl.encode(ebuf_bak)],
                  memos=[f"slot {label}", "edit buffer"])
        path = Library().save(tsl)
        log(f"Backups saved as liveset {tsl.name!r} ({path})")
        name = ParamSet.patch_name(slot_bak.block("PATCH%COM"))
        p0, e0 = presence_of(slot_bak), presence_of(ebuf_bak)
        v = next(x for x in (p0 + 1, p0 - 1, p0 + 2, p0 - 2)
                 if 0 <= x <= 100 and x not in (p0, e0))
        log(f"Slot {label} {name!r}: PRESENCE {p0}; edit buffer PRESENCE {e0}"
            f"; test value {v}")
        if not args.apply:
            log("Dry run: nothing written.")
            return 0

        # 2. current write_patch with a one-step change
        log(f"\n[test] write_patch({label}) with PRESENCE {v}…")
        write_patch(t, n, with_presence(slot_bak, v), name or None)
        time.sleep(0.5)
        a = ParamSet.decode(MAP["PATCH"].block(AMP_BLOCK),
                            read_slot_amp(t, n), PRESENCE)
        verdict = ("stores the editor's data" if a == v else
                   "COPIES THE EDIT BUFFER (editor data lost)" if a == e0 else
                   "unexpected result")
        log(f"[test] slot PRESENCE after write: {a} -> write_patch {verdict}")
        log(f"[test] selected patch after write: {current_patch(t)}")
        after = read_patch(t, n)
        log(f"[test] blocks differing from backup: {diff_blocks(after, slot_bak)}")
        log(f"[test] blocks differing from edit buffer: "
            f"{len(diff_blocks(after, ebuf_bak))} of {len(PATCH_BLOCKS)}")

        # 3. restore the slot (original method), verify, fallback
        log(f"\n[restore] backup -> edit buffer -> PATCH_WRITE {label}…")
        write_edit_buffer(t, slot_bak)
        commit(t, n)
        check = read_patch(t, n)
        bad = diff_blocks(check, slot_bak)
        if bad:
            log(f"[restore] mismatch {bad}; trying write_patch…")
            write_patch(t, n, slot_bak, name or None)
            time.sleep(0.5)
            check = read_patch(t, n)
            bad = diff_blocks(check, slot_bak)
        log(f"[restore] slot {label} "
            + ("identical to backup" if not bad else f"STILL DIFFERS: {bad}"))
        if bad:
            rc = 1

        # 4. amp state back
        log(f"\n[state] reselecting patch {sel}, restoring edit buffer…")
        select_patch(t, sel)
        time.sleep(0.5)
        write_edit_buffer(t, ebuf_bak)
        time.sleep(0.3)
        ebuf_now = read_region(t, "PATCH")
        bad = diff_blocks(ebuf_now, ebuf_bak)
        log("[state] edit buffer "
            + ("identical to backup" if not bad else f"differs: {bad}"))
        log(f"[state] selected patch: {current_patch(t)}")
        if bad:
            rc = 1
    except Exception as e:                            # noqa: BLE001
        log(f"ERROR: {e!r}")
        rc = 1
    finally:
        disconnect(t)
        t.stop()
    log("\nRESULT: " + ("OK" if rc == 0 else "CHECK OUTPUT"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
