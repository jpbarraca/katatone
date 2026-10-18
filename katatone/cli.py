"""katatone CLI.

Usage examples:
  python -m katatone ports
  python -m katatone connect --in "Katana USB" --out "Katana USB"
  python -m katatone read --in IN --out OUT [--patch N] [--region SYSTEM] [--out-file x.tsl]
  python -m katatone write --in IN --out OUT --patch N --tsl file.tsl [--name NAME]
  python -m katatone clear --in IN --out OUT --patch N
  python -m katatone library list|save|import|remove
  python -m katatone wifi status|set|ensure|retry    (needs NetworkManager)
  python -m katatone passwd                     (the web GUI password)

Options:
  -v, --verbose   Enable debug logging to terminal
  --reduce-writes The file system is read-only: remount it rw only while writing
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from . import rofs
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


def _wifi(args) -> int:
    """The ``wifi`` command.

    Args:
        args: the parsed options.
    Returns:
        Exit code: 0 on success, 1 when NetworkManager refuses.
    """
    from .net.wifi import IpConfig, Wifi, WifiError
    w = Wifi()
    try:
        if args.action == "status":
            s, st = w.settings(), w.status()
            print(json.dumps({"network": s.ssid, "access_point": s.ap_ssid,
                              "access_point_enabled": s.ap_enabled,
                              "mode": st.mode, "connected_to": st.ssid,
                              "address": st.address, "hostname": s.hostname,
                              "dhcp": s.ip.dhcp, "static": vars(s.ip),
                              "mdns": s.mdns, "mdns_installed": s.mdns_installed}, indent=1))
        elif args.action == "set" and (args.hostname or args.mdns is not None) and not any(
                v is not None and v is not False and v != ""
                for k, v in vars(args).items() if k in (
                    "ssid", "key", "ap_ssid", "ap_key", "ap", "dhcp", "address")):
            if args.hostname:                     # only the device: no reconnect
                w.set_hostname(args.hostname)
                print(f"hostname: {args.hostname}")
            if args.mdns is not None:
                w.set_mdns(args.mdns)
                print(f"mdns: {'on' if args.mdns else 'off'}")
        elif args.action == "set":
            ip = (IpConfig(False, args.address, args.mask, args.gateway, args.dns)
                  if args.address else IpConfig() if args.dhcp else None)
            ssid = w.settings().ssid if args.ssid is None else args.ssid
            w.configure(ssid, args.key, args.ap_ssid, args.ap_key, args.ap, ip)
            if args.hostname:
                w.set_hostname(args.hostname)
            if args.mdns is not None:
                w.set_mdns(args.mdns)
            print(f"connected: {w.ensure(force=True)}")
        elif args.action == "retry":
            print(f"connected: {w.try_client()}")
        else:
            print(f"connected: {w.ensure()}")
    except WifiError as e:
        print(f"wifi: {e}", file=sys.stderr)
        return 1
    return 0


def _passwd() -> int:
    """The ``passwd`` command: ask for a new web GUI password.

    Returns:
        Exit code: 0 on success, 1 when it is refused or the prompts differ.
    """
    import getpass
    from .auth import Auth, AuthError
    pw = getpass.getpass("New password: ")
    if pw != getpass.getpass("Again: "):
        print("passwd: the passwords differ", file=sys.stderr)
        return 1
    try:
        Auth().set_password(pw)
    except (AuthError, OSError) as e:
        print(f"passwd: {e}", file=sys.stderr)
        return 1
    print("password changed")
    return 0


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
    ap = argparse.ArgumentParser(
        prog="katatone", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="KataTone command line for the BOSS Katana 50 Gen 3: read and write\n"
                    "patches, manage the local library, set up the Wi-Fi of a Raspberry Pi\n"
                    "and the web password.  The editor itself is katatone-gui.",
        epilog="examples:\n"
               "  katatone ports\n"
               "  katatone read --in KATANA3 --out KATANA3 --patch 0 --out-file tone.tsl\n"
               "  katatone write --in KATANA3 --out KATANA3 --patch 1 --tsl tone.tsl\n"
               "  katatone library list\n"
               "  katatone wifi set --ssid home --key 'my wifi key'\n"
               "\n'katatone COMMAND --help' lists the options of a command.")
    # Debug logging is OFF by default; pass -v/--verbose to trace execution.
    ap.add_argument("-v", "--verbose", action="store_true", dest="verbose",
                    help="enable debug logging to terminal")
    ap.add_argument("--reduce-writes", action="store_true", dest="reduce_writes",
                    help="the file system is read-only: remount it read-write "
                         "only while writing")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ports", help="list the MIDI cards that can be used as the amp")

    def need_midi(p):
        """Add the required ``--in`` / ``--out`` port options to a subparser.

        Args:
            p: the subparser to extend.
        """
        p.add_argument("--in", dest="in_port", required=True,
                       help="MIDI port to read from (a card name from 'katatone ports', e.g. KATANA3)")
        p.add_argument("--out", dest="out_port", required=True,
                       help="MIDI port to write to (usually the same card)")

    p = sub.add_parser("connect", help="open the link to the amp and run the handshake only")
    need_midi(p)

    p = sub.add_parser("read", help="read a patch or a region from the amp (JSON, or a .tsl file)")
    need_midi(p)
    p.add_argument("--patch", type=int, help="user patch to read, 0-9")
    p.add_argument("--region", choices=["SETUP", "SYSTEM", "TEMP", "COMMAND"],
                   help="read a whole region instead of a patch")
    p.add_argument("--out-file", help="save the patch as a .tsl file instead of printing JSON")

    p = sub.add_parser("write", help="write a patch from a .tsl file into an amp slot (overwrites it)")
    need_midi(p)
    p.add_argument("--patch", type=int, required=True, help="amp slot to overwrite, 0-9")
    p.add_argument("--tsl", required=True, help=".tsl liveset holding the patch")
    p.add_argument("--name", help="patch name to store (default: the file's)")

    p = sub.add_parser("clear", help="clear a user patch on the amp")
    need_midi(p)
    p.add_argument("--patch", type=int, required=True, help="amp slot to clear, 0-9")

    p = sub.add_parser("select", help="switch the amp to a user patch")
    need_midi(p)
    p.add_argument("--patch", type=int, required=True, help="amp slot to select, 0-9")

    p = sub.add_parser("library", help="the local library of livesets (.tsl files)")
    p.add_argument("action", choices=["list", "save", "import", "remove"],
                   help="list the livesets, import a .tsl file, or remove a liveset")
    p.add_argument("name", nargs="?", help="liveset name (for remove)")
    p.add_argument("--file", help=".tsl file to import")
    p.add_argument("--dir", help="use this library directory instead of the default")

    p = sub.add_parser("wifi", help="Wi-Fi: network, fallback access point, IP, host name (NetworkManager)")
    p.add_argument("action", choices=["status", "set", "ensure", "retry"],
                   help="status: show; set: store the options below and apply them; "
                        "ensure: join the network or else the access point; "
                        "retry: try the saved network now")
    p.add_argument("--ssid", default=None,
                   help="network to join ('' = access point only; omit to keep)")
    p.add_argument("--key", default="", help="its WPA-PSK key (blank: keep)")
    p.add_argument("--ap-ssid", default="", help="access point name (default KataTone)")
    p.add_argument("--ap-key", default="", help="access point key (blank: keep)")
    p.add_argument("--dhcp", action="store_true", help="get the address from the router")
    p.add_argument("--address", help="static IPv4 address (with --mask); implies no DHCP")
    p.add_argument("--mask", default="", help="subnet mask: 255.255.255.0 or 24")
    p.add_argument("--gateway", default="", help="static gateway")
    p.add_argument("--dns", default="", help="static DNS servers, space or comma separated")
    p.add_argument("--hostname", help="set the device's host name")
    p.add_argument("--mdns", action=argparse.BooleanOptionalAction, default=None,
                   help="answer to <host name>.local (avahi-daemon): on or off")
    p.add_argument("--ap", action=argparse.BooleanOptionalAction, default=None,
                   help="use the access point as the fallback (default: keep)")

    sub.add_parser("passwd", help="set the password of the web interface (asks for it)")

    args = ap.parse_args(argv)
    _setup_logging(args.verbose)
    rofs.enable(args.reduce_writes)
    log.debug("katatone %s %s", args.cmd,
              {k: v for k, v in vars(args).items() if k not in ("key", "ap_key")})

    if args.cmd == "wifi":
        return _wifi(args)
    if args.cmd == "passwd":
        return _passwd()

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
