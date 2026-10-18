"""Print what a USB MIDI controller sends (developer tool, not a user feature).

Read-only: opens the controller for *input* only, so nothing can be sent to it
or to the amp.

Run:  python3 dev/midi_capture.py                 # list input ports
      python3 dev/midi_capture.py "SINCO"         # capture from the port whose
                                                  # name contains "SINCO"
      python3 dev/midi_capture.py "SINCO" --raw   # also print the raw bytes
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from katatone.midi.events import describe  # noqa: E402
from katatone.midi.input import MidiInput  # noqa: E402
from katatone.protocol.alsa_seq import list_input_ports  # noqa: E402


def main() -> int:
    """Capture events from one controller until Ctrl-C.

    Returns:
        Process exit status (0 on a clean stop, 1 if no port matches).
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name", nargs="?", help="substring of the port name")
    ap.add_argument("--raw", action="store_true", help="also show raw bytes")
    args = ap.parse_args()

    ports = list_input_ports()
    if not args.name:
        for p in ports:
            print(f"hw:{p.spec}  {p.label}  (card {p.card})")
        return 0
    match = [p for p in ports if args.name.lower() in p.label.lower()]
    if not match:
        print(f"no input port matches {args.name!r}", file=sys.stderr)
        return 1

    t0 = time.monotonic()

    def on_event(label: str, ev) -> None:
        print(f"{time.monotonic() - t0:8.3f}  {label}  {describe(ev)}", flush=True)

    def on_state(label: str, state: str) -> None:
        print(f"{time.monotonic() - t0:8.3f}  {label}  [{state}]", flush=True)

    mi = MidiInput(match[0], on_event, on_state)
    if args.raw:
        # Raw bytes show what the controller really sends (running status etc.).
        # `MidiInput._open` replaces its parser, so wrap the class method.
        from katatone.midi import parser as _p
        _cls_feed = _p.MidiParser.feed

        def feed_cls(self, data: bytes):
            if data:
                print(f"          raw {data.hex(' ')}", flush=True)
            return _cls_feed(self, data)

        _p.MidiParser.feed = feed_cls
    mi.start()
    print(f"capturing {match[0].label} (hw:{match[0].spec}); Ctrl-C to stop")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    mi.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
