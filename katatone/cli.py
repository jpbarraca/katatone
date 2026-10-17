"""katatone CLI.

Usage examples:
  python -m katatone ports
  python -m katatone connect --in "Katana USB" --out "Katana USB"
  python -m katatone read --in IN --out OUT [--patch N] [--region SYSTEM] [--out-file x.tsl]
  python -m katatone write --in IN --out OUT --patch N --tsl file.tsl [--name NAME]
  python -m katatone clear --in IN --out OUT --patch N
  python -m katatone library list|save|import|remove

Options:
  -v, --verbose   Enable debug logging to terminal
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from .amp.connect import HandshakeError, disconnect, handshake
from .amp.read import read_patch, read_region
from .amp.write import clear_patch, select_patch, write_patch
from .library.store import Library
from .liveset.tsl import Tsl
from .protocol import rc6
from .protocol.transport import Transport, list_alsa_ports, make_alsa_transport

log = logging.getLogger("katatone")


def _setup_logging(verbose: bool) -> None:
    """Configure root logger. -v enables DEBUG, default is WARNING."""
    level = logging.DEBUG if verbose else logging.WARNING
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"))
    root = logging.getLogger("katatone")
    root.setLevel(level)
    root.addHandler(handler)


def _connect(args) -> Transport:
    """Open the transport to the amp and run the identity handshake.

    Args:
        args: parsed CLI args (uses ``in_port`` and ``out_port``).
    Returns:
        A started ``Transport`` connected to the amp.
    """
    log.debug("listing ALSA ports…")
    ports = list_alsa_ports()
    log.debug("ALSA in : %s", ports['in'])
    log.debug("ALSA out: %s", ports['out'])
    print(f"MIDI in : {ports['in']}")
    print(f"MIDI out: {ports['out']}")
    log.debug("opening transport in=%r out=%r", args.in_port, args.out_port)
    t = make_alsa_transport(args.in_port, args.out_port)
    t.start()
    # Amp needs a moment for its USB-MIDI rawmidi port to settle after open
    # before it will answer the identity handshake; without this the identity
    # reply is flaky.  (~0.5–0.6 s observed in probes.)
    time.sleep(0.6)
    log.debug("transport started, sending handshake…")
    dev = handshake(t)
    log.debug("handshake OK deviceId=0x%02X", dev)
    print(f"Connected (deviceId=0x{dev:02X})")
    return t


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: run the command, then exit via ``os._exit`` so
    alsa-lib's atexit destructors are skipped (they segfault on PipeWire).

    Args:
        argv: command-line arguments (defaults to ``sys.argv[1:]``).
    Returns:
        The process exit code.
    """
    code = _run(argv)
    sys.stdout.flush()
    sys.stderr.flush()
    # alsa-lib static atexit cleanup segfaults on PipeWire hosts after the
    # seq client is closed; skip destructors once work is done.
    os._exit(code)


def _run(argv: list[str] | None = None) -> int:
    """Parse the command line and dispatch to the selected command.

    Args:
        argv: command-line arguments (defaults to ``sys.argv[1:]``).
    Returns:
        Exit code: 0 on success, 2 on usage errors.
    """
    ap = argparse.ArgumentParser(prog="katatone", description=__doc__)
    # Debug logging is OFF by default; pass -v/--verbose to trace execution.
    ap.add_argument("-v", "--verbose", action="store_true", dest="verbose",
                    help="enable debug logging to terminal")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ports", help="list MIDI ports")

    def need_midi(p):
        """Add the required ``--in`` / ``--out`` port options to a subparser.

        Args:
            p: the subparser to extend.
        """
        p.add_argument("--in", dest="in_port", required=True)
        p.add_argument("--out", dest="out_port", required=True)

    p = sub.add_parser("connect", help="handshake only")
    need_midi(p)

    p = sub.add_parser("read", help="read region or patch")
    need_midi(p)
    p.add_argument("--patch", type=int, help="user patch 0-9")
    p.add_argument("--region", choices=["SETUP", "SYSTEM", "TEMP", "COMMAND"])
    p.add_argument("--out-file", help="save as .tsl (patch mode)")

    p = sub.add_parser("write", help="write patch from .tsl")
    need_midi(p)
    p.add_argument("--patch", type=int, required=True)
    p.add_argument("--tsl", required=True)
    p.add_argument("--name")

    p = sub.add_parser("clear", help="clear user patch")
    need_midi(p)
    p.add_argument("--patch", type=int, required=True)

    p = sub.add_parser("select", help="select user patch on amp")
    need_midi(p)
    p.add_argument("--patch", type=int, required=True)

    p = sub.add_parser("library", help="local library")
    p.add_argument("action", choices=["list", "save", "import", "remove"])
    p.add_argument("name", nargs="?")
    p.add_argument("--file", help=".tsl file for import")
    p.add_argument("--dir", help="library dir override")

    args = ap.parse_args(argv)
    _setup_logging(args.verbose)
    log.debug("katatone %s %s", args.cmd, vars(args))

    if args.cmd == "ports":
        print(json.dumps(list_alsa_ports(), indent=1))
        return 0

    if args.cmd in ("connect", "read", "write", "clear", "select"):
        t = _connect(args)
        try:
            if args.cmd == "connect":
                log.debug("connect-only, nothing to do")
            elif args.cmd == "read":
                if args.patch is not None:
                    log.debug("reading patch %d…", args.patch)
                    ps = read_patch(t, args.patch,
                                    progress=lambda d, n: (log.debug("block %d/%d", d, n), print(f"  block {d}/{n}")))
                    if args.out_file:
                        from .model.paramset import ParamSet
                        Tsl.from_paramset(
                            ParamSet.patch_name(ps.block("PATCH%COM")) or
                            f"Patch{args.patch + 1}", args.patch, ps).save(args.out_file)
                        log.debug("saved TSL to %s", args.out_file)
                        print(f"saved {args.out_file}")
                    else:
                        print(json.dumps(
                            {k: v.hex() for k, v in ps.data.items()}, indent=0))
                elif args.region:
                    log.debug("reading region %s…", args.region)
                    ps = read_region(t, args.region,
                                     progress=lambda d, n: (log.debug("block %d/%d", d, n), print(f"  block {d}/{n}")))
                    print(json.dumps({k: v.hex() for k, v in ps.data.items()}, indent=0))
                else:
                    print("need --patch N or --region NAME", file=sys.stderr)
                    return 2
            elif args.cmd == "write":
                log.debug("writing patch %d from %s…", args.patch, args.tsl)
                tsl = Tsl.load(args.tsl)
                if args.patch >= len(tsl):
                    print(f"{args.tsl} has only {len(tsl)} patches",
                          file=sys.stderr)
                    return 2
                write_patch(t, args.patch, tsl.to_paramset(args.patch), args.name)
                print(f"wrote patch {args.patch + 1}")
            elif args.cmd == "clear":
                log.debug("clearing patch %d…", args.patch)
                clear_patch(t, args.patch)
                print(f"cleared patch {args.patch + 1}")
            elif args.cmd == "select":
                log.debug("selecting patch %d…", args.patch)
                select_patch(t, args.patch)
                print(f"selected patch {args.patch + 1}")
        except Exception as e:
            log.error("%s failed: %s", args.cmd, e, exc_info=args.verbose)
            raise
        finally:
            log.debug("disconnecting…")
            disconnect(t)
            t.stop()
            log.debug("transport stopped")
        return 0

    if args.cmd == "library":
        lib = Library(args.dir)
        if args.action == "list":
            for e in lib.list():
                print(f"{e.name:30s} {e.path}")
        elif args.action == "import":
            p = lib.import_file(args.file)
            print(f"imported -> {p}")
        elif args.action == "remove":
            lib.remove(args.name)
        else:
            print("save requires the GUI/amp path (read then library save)",
                  file=sys.stderr)
            return 2
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
