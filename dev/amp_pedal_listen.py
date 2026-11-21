"""Does the amp announce a parameter that a pedal (or a knob) moves? (developer tool)

The amp's MIDI port can be opened by one program at a time, so KataTone must be closed.  The amp
announces changes only in editor mode, so by default the tool does what KataTone does when it
connects: the handshake (identity, then editor mode on) and, at the end, editor mode off.  It
writes no patch data and sends no request after that: it then only *listens*.  (``--no-handshake``
listens without sending anything, for an amp that another program has already put in editor mode.)
Every DT1 the transport cannot match to a request is printed, with the parameter it is when
it is exactly one numeric parameter of the edit buffer (see ``katatone.amp.notify``).  The DT1
replies to the other window's own reads show up too (they come in runs of one address).

Run:  python3 dev/amp_pedal_listen.py                 # 60 s, with the handshake
      python3 dev/amp_pedal_listen.py --seconds 120   # longer, to move the pedal and turn knobs
      python3 dev/amp_pedal_listen.py --phases ready:15,pedal:50,knob:50
                                                      # told what to do when, aloud and on screen
"""
from __future__ import annotations

import argparse
import collections
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from katatone.amp import notify  # noqa: E402
from katatone.amp.connect import disconnect, handshake  # noqa: E402
from katatone.protocol import rc6  # noqa: E402
from katatone.protocol.transport import list_alsa_ports, make_alsa_transport  # noqa: E402

PATCH_NUMBER_NIB = rc6.to_nib(0x0000_0000)

#: What to tell the person at the amp for each named phase of ``--phases``.
CUES = {
    "ready": "Get ready at the pedal. The test starts in a few seconds.",
    "pedal": "Now move the pedal slowly through its full range, again and again.",
    "knob": "Now turn the gain knob on the amp, slowly, both ways.",
    "rest": "Stop. Leave everything still.",
}


def cue(text: str) -> None:
    """Say ``text`` aloud and show it on the desktop (whatever of that is available)."""
    print(f"--- {text}", flush=True)
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-u", "critical", "KataTone amp test", text],
                         stderr=subprocess.DEVNULL)
    if shutil.which("spd-say"):
        subprocess.Popen(["spd-say", text], stderr=subprocess.DEVNULL)


def run_phases(spec: str) -> None:
    """Go through ``name:seconds,name:seconds,...``, with a cue at the start of each, a warning
    ten seconds before the end of the long ones and a last cue at the end."""
    for item in spec.split(","):
        name, _, secs = item.partition(":")
        seconds = float(secs)
        cue(CUES.get(name, name))
        if seconds > 25:
            time.sleep(seconds - 10)
            cue("Ten seconds left.")
            time.sleep(10)
        else:
            time.sleep(seconds)
    cue("Finished. Thank you. You can stop.")


def main() -> int:
    """Listen and summarise.

    Returns:
        0, or 1 when no amp port is found.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seconds", type=float, default=60.0, help="how long to listen")
    ap.add_argument("--port", default="KATANA", help="amp card name prefix")
    ap.add_argument("--phases", help="e.g. ready:15,pedal:50,knob:50 (replaces --seconds): each "
                    "phase is announced aloud and on screen")
    ap.add_argument("--no-handshake", action="store_true",
                    help="send nothing at all (the amp must already be in editor mode)")
    args = ap.parse_args()

    names = [p for p in list_alsa_ports()["in"] if p.startswith(args.port)]
    if not names:
        print("no amp port found", file=sys.stderr)
        return 1
    t = make_alsa_transport(names[0], names[0])
    t0 = time.monotonic()
    by_param: dict[str, list[float]] = collections.defaultdict(list)
    other = collections.Counter()

    def on_unsolicited(nib: int, payload: bytes) -> None:
        now = time.monotonic() - t0
        c = notify.decode(nib, bytes(payload))
        if c is not None:
            by_param[c.name].append(now)
            print(f"{now:8.3f}s  {c.name} = {c.value}", flush=True)
        else:
            tag = "patch number" if nib == PATCH_NUMBER_NIB else f"nib 0x{nib:08X}"
            other[tag] += 1
            print(f"{now:8.3f}s  ({tag}, {len(payload)} bytes) {bytes(payload).hex(' ')[:60]}",
                  flush=True)

    t.on_unsolicited = on_unsolicited
    t.start()
    if not args.no_handshake:
        time.sleep(0.6)
        handshake(t)
    t0 = time.monotonic()
    print(f"listening on {names[0]} for {args.seconds:.0f} s: nothing is sent.  "
          "Move the pedal, then turn a knob of the amp.", flush=True)
    try:
        if args.phases:
            run_phases(args.phases)
        else:
            time.sleep(args.seconds)
    finally:
        if not args.no_handshake:
            disconnect(t)
        t.stop()
    print("\n--- summary")
    for name, times in sorted(by_param.items(), key=lambda kv: -len(kv[1])):
        span = times[-1] - times[0]
        rate = f"{(len(times) - 1) / span:.1f}/s" if len(times) > 1 and span > 0 else "once"
        print(f"{name:45} {len(times):4} messages  {rate}  first {times[0]:.1f}s last {times[-1]:.1f}s")
    for tag, n in other.items():
        print(f"{tag:45} {n:4} messages (not a single parameter)")
    if not by_param and not other:
        print("nothing was announced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
