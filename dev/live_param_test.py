"""Controlled hardware test: write individual parameters to the amp.

Safety rules (enforced by ``katatone.amp.write.write_param`` and this script):

* Only the amp's *edit buffer* (region ``PATCH`` @ 0x20000000) is written.
  That changes the current sound live but is never stored in a user patch:
  no CMDID_PATCH_WRITE / EXPORT / CLEAR is sent, SYSTEM is never touched.
* Each test changes one parameter by a single step, reads it back, and
  restores the original value; afterwards the whole block is compared with
  the backup taken before any write.  On any mismatch the backup is written
  back immediately.
* Without ``--apply`` the script only connects and reads (no DT1 writes
  apart from the editor-mode handshake the GUI also sends).

Run:   python3 dev/live_param_test.py            # read-only dry run
       python3 dev/live_param_test.py --apply    # controlled writes
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from katatone.address.map import load_map  # noqa: E402
from katatone.amp.connect import disconnect, handshake  # noqa: E402
from katatone.amp.write import (EDIT_REGION, _dt1_chunked,  # noqa: E402
                           read_param, write_param)
from katatone.amp.live import LiveWriter  # noqa: E402
from katatone.model.paramset import ParamSet  # noqa: E402
from katatone.protocol.transport import (list_alsa_ports,  # noqa: E402
                                    make_alsa_transport)

# (block, parameter-name suffix): one 7-bit knob and one 4x4 multi-byte value.
CASES = [("AMP", "AMP_PRESENCE"), ("DELAY(1)", "DELAY_TIME")]
SETTLE = 0.15          # s between a DT1 and the read-back


def find(block_name: str, suffix: str) -> tuple[int, object]:
    """Find the parameter index and model object by name suffix in a block.

    Args:
        block_name: edit-buffer block name as in the address map.
        suffix: name suffix matched against the end of each param name.

    Returns:
        (index, param) of the first param whose name ends with the suffix.

    Raises:
        KeyError if no parameter in the block matches the suffix.
    """
    blk = load_map()[EDIT_REGION].block(block_name)
    for i, p in enumerate(blk.params):
        if p.name.endswith(suffix):
            return i, p
    raise KeyError(suffix)


def read_block(t, block_name: str) -> bytes:
    """Read a whole edit-buffer block back from the amp with one RQ1.

    Args:
        t: open ALSA transport connected to the amp.
        block_name: block name as in the address map, e.g. "AMP".

    Returns:
        The raw DT1 data bytes for the block.
    """
    blk = load_map()[EDIT_REGION].block(block_name)
    return t.rq1(blk.base_addr, blk.length, timeout=3.0)


def main() -> int:
    """Run the controlled live-parameter test and report OK or FAILED.

    With --apply each case parameter is stepped by one, read back and
    restored; afterwards every touched block is compared with its backup
    and the backup is written back on any mismatch.

    Returns:
        0 if no failures were recorded, 1 otherwise.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually write (default: read-only dry run)")
    ap.add_argument("--port", default=None, help="ALSA port name")
    ap.add_argument("--via-live", action="store_true",
                    help="send the writes through the GUI's LiveWriter queue")
    args = ap.parse_args()

    ports = list_alsa_ports()
    port = args.port or next((p for p in ports.get("out", [])
                              if "KATANA" in p.upper()), None)
    if not port:
        print("No KATANA MIDI port found:", ports)
        return 2
    print(f"Port {port!r}  mode={'APPLY' if args.apply else 'DRY RUN'}"
          f"{' via LiveWriter' if args.via_live else ''}")

    t = make_alsa_transport(port, port)
    t.start()
    live = LiveWriter(lambda: t, interval=0.03) if args.via_live else None

    def send(block_name: str, i: int, value: int) -> bytes:
        """Write one edit-buffer parameter and return the raw bytes sent.

        Args:
            block_name: edit-buffer block name as in the address map.
            i: parameter index within the block.
            value: decoded parameter value to write.

        Returns:
            The raw on-wire bytes the amp should hold for the parameter.
        """
        if live is None:
            return write_param(t, block_name, i, value)
        live.queue_param(block_name, i, value)
        while live.pending():
            time.sleep(0.01)
        time.sleep(0.05)                    # worker finishing the last DT1
        p = load_map()[EDIT_REGION].block(block_name).params[i]
        return ParamSet.raw_bytes(p.size, value + p.ofs)

    time.sleep(0.6)
    failures: list[str] = []
    backups: dict[str, bytes] = {}
    try:
        dev = handshake(t)
        print(f"Connected, deviceId=0x{dev:02X}")
        name = ParamSet.patch_name(read_block(t, "COM"))
        print(f"Edit buffer patch name: {name!r}")

        for block_name, suffix in CASES:
            backups[block_name] = read_block(t, block_name)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        bak = f"/tmp/katana_editbuffer_backup_{stamp}.json"
        with open(bak, "w") as f:
            json.dump({k: v.hex() for k, v in backups.items()}, f)
        print(f"Backup of touched blocks -> {bak}")

        for block_name, suffix in CASES:
            i, p = find(block_name, suffix)
            orig = read_param(t, block_name, i)
            blk = load_map()[EDIT_REGION].block(block_name)
            from_block = ParamSet.decode(blk, backups[block_name], i)
            new = orig + 1 if orig < p.max else orig - 1
            print(f"\n{p.name}: current={orig} (block decode {from_block}), "
                  f"range {p.min}..{p.max}, size {p.size}")
            if orig != from_block:
                failures.append(f"{p.name}: single read {orig} != block "
                                f"decode {from_block}")
            if not args.apply:
                data = ParamSet.raw_bytes(p.size, new + p.ofs)
                print(f"  dry run: would write {new} as {data.hex(' ')} "
                      f"@ {blk.base_addr + p.off:#010x}, then restore {orig}")
                continue
            sent = send(block_name, i, new)
            time.sleep(SETTLE)
            got = read_param(t, block_name, i)
            ok = got == new
            print(f"  write {new} ({sent.hex(' ')}) -> read back {got}  "
                  f"{'OK' if ok else 'MISMATCH'}")
            if not ok:
                failures.append(f"{p.name}: wrote {new}, read {got}")
            send(block_name, i, orig)
            time.sleep(SETTLE)
            back = read_param(t, block_name, i)
            ok = back == orig
            print(f"  restore {orig} -> read back {back}  "
                  f"{'OK' if ok else 'MISMATCH'}")
            if not ok:
                failures.append(f"{p.name}: restore {orig}, read {back}")

        # Whole-block comparison: nothing else may have changed.
        for block_name, before in backups.items():
            after = read_block(t, block_name)
            same = after == before
            print(f"\nBlock {block_name}: {'unchanged' if same else 'CHANGED'}"
                  f" vs backup")
            if not same:
                failures.append(f"block {block_name} differs from backup")
    except Exception as e:                            # noqa: BLE001
        failures.append(f"error: {e!r}")
        print("ERROR:", repr(e))
    finally:
        if args.apply and failures and backups:
            print("\nRestoring backed-up blocks…")
            blks = load_map()[EDIT_REGION]
            for block_name, data in backups.items():
                try:
                    _dt1_chunked(t, blks.block(block_name).base_addr, data)
                except Exception as e:                # noqa: BLE001
                    print("  restore failed:", block_name, e)
        disconnect(t)
        t.stop()
    print("\nRESULT:", "OK" if not failures else "FAILED")
    for f in failures:
        print("  -", f)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
