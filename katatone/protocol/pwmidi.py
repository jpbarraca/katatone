"""PipeWire's MIDI sources (Bluetooth MIDI devices), linked with ``pw-link``.

PipeWire owns a Bluetooth MIDI device's connection and presents it as a MIDI
source node ``bluez_midi.<address>``.  To read it KataTone starts ``pw-mididump``
(``pipewire-utils``), links the source to its input with ``pw-link`` and reads the
messages it prints; the link goes when the input is closed.  Deliberately not
through the ALSA sequencer: linking into a sequencer client makes PipeWire's
sequencer bridge subscribe to *every* sequencer port, which takes the amp's USB MIDI
device away from KataTone.  Without ``pw-link`` nothing is offered.
"""
from __future__ import annotations

import json
import os
import pty
import re
import select
import shutil
import subprocess
import time

from .alsa_seq import InPort

SPEC_PREFIX = "pw:"
SOURCE_PREFIX = "bluez_midi."
#: The BlueZ client itself, which is not a device.
SKIP = ("bluez_midi.server",)
_NAMES_TTL_S = 30.0
_names: tuple[float, dict[str, str]] = (0.0, {})


def available() -> bool:
    """Whether ``pw-link`` is installed."""
    return shutil.which("pw-link") is not None


def _run(args: list, timeout: float = 5.0) -> "tuple[int, str]":
    """Run a command; returns (exit code, stdout)."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return p.returncode, p.stdout


def source_ports() -> list[str]:
    """Names of PipeWire's Bluetooth MIDI output ports (``bluez_midi.X:out``)."""
    if not available():
        return []
    rc, out = _run(["pw-link", "-o"])
    ports = [l.strip() for l in out.splitlines() if l.strip().startswith(SOURCE_PREFIX)]
    return [p for p in ports if p.split(":")[0] not in SKIP]


def _descriptions() -> dict[str, str]:
    """``node.name`` -> ``node.description`` of the Bluetooth MIDI nodes (cached)."""
    global _names
    when, names = _names
    if time.monotonic() - when < _NAMES_TTL_S:
        return names
    names = {}
    rc, out = _run(["pw-dump"], timeout=10)
    try:
        for obj in json.loads(out or "[]"):
            props = (obj.get("info") or {}).get("props") or {}
            n = props.get("node.name", "")
            if n.startswith(SOURCE_PREFIX):
                names[n] = props.get("node.description") or props.get("node.nick") or n
    except ValueError:
        pass
    _names = (time.monotonic(), names)
    return names


def inports() -> list[InPort]:
    """The Bluetooth MIDI sources as input ports, labelled with the device's name."""
    ports = source_ports()
    if not ports:
        return []
    desc = _descriptions()
    return [InPort(f"{SPEC_PREFIX}{p}", "PipeWire", desc.get(p.split(":")[0], p.split(":")[0]))
            for p in ports]


def link(source: str, dest: str, wait: float = 3.0) -> bool:
    """Link a source port to a destination, retrying while PipeWire notices a
    sequencer client that was only just created.

    Returns:
        True once linked (or already linked).
    """
    end = time.monotonic() + wait
    while True:
        rc, _ = _run(["pw-link", source, dest])
        if rc == 0:
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.2)


def unlink(source: str, dest: str) -> None:
    """Remove the link (no error if there is none)."""
    _run(["pw-link", "-d", source, dest])


def present(source: str) -> bool:
    """Whether the source port still exists (the device is still connected)."""
    return source in source_ports()


# ----------------------------------------------------------- pw-mididump

_NOTE = re.compile(r"([A-G])([#b]?)(-?\d+)")
_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def note_number(name: str) -> int:
    """MIDI note number of a name as ``pw-mididump`` prints it (``C4`` = 60)."""
    m = _NOTE.fullmatch(name.strip())
    if not m:
        return int(name)
    letter, accidental, octave = m.groups()
    return (int(octave) + 1) * 12 + _SEMITONE[letter] + (1 if accidental == "#" else -1 if accidental == "b" else 0)


_LINES = (
    (re.compile(r"Note On\s+\(channel\s+(\d+)\): note\s+(\S+), velocity\s+(\d+)"),
     lambda g: bytes([0x90 | int(g[0]) - 1, note_number(g[1]), int(g[2])])),
    (re.compile(r"Note Off\s+\(channel\s+(\d+)\): note\s+(\S+), velocity\s+(\d+)"),
     lambda g: bytes([0x80 | int(g[0]) - 1, note_number(g[1]), int(g[2])])),
    (re.compile(r"Controller\s+\(channel\s+(\d+)\): controller\s+(\d+).*?value\s+(\d+)"),
     lambda g: bytes([0xB0 | int(g[0]) - 1, int(g[1]), int(g[2])])),
    (re.compile(r"Program\s+\(channel\s+(\d+)\): program\s+(\d+)"),
     lambda g: bytes([0xC0 | int(g[0]) - 1, int(g[1])])),
    (re.compile(r"Pitch Bend\s+\(channel\s+(\d+)\): value\s+(-?\d+)"),
     lambda g: bytes([0xE0 | int(g[0]) - 1, (int(g[1]) + 8192) & 0x7F, ((int(g[1]) + 8192) >> 7) & 0x7F])),
    (re.compile(r"Channel Pressure\s+\(channel\s+(\d+)\): pressure\s+(\d+)"),
     lambda g: bytes([0xD0 | int(g[0]) - 1, int(g[1])])),
    (re.compile(r"Aftertouch\s+\(channel\s+(\d+)\): note\s+(\S+), pressure\s+(\d+)"),
     lambda g: bytes([0xA0 | int(g[0]) - 1, note_number(g[1]), int(g[2])])),
)


def parse_line(line: str) -> bytes:
    """The MIDI message a ``pw-mididump`` line describes (b'' for anything else:
    system messages and sysex are not used by controllers)."""
    for pattern, build in _LINES:
        m = pattern.search(line)
        if m:
            try:
                return build(m.groups())
            except (ValueError, KeyError):
                return b""
    return b""


class DumpReader:
    """Reads one PipeWire MIDI source through its own ``pw-mididump`` process."""

    def __init__(self, source: str, wait: float = 3.0) -> None:
        """Start the dump and link ``source`` (``bluez_midi.<addr>:out``) to it.

        Raises:
            RuntimeError: if it cannot be started or linked.
        """
        self.source = source
        self._buf = b""
        self._last_check = time.monotonic()
        master, slave = pty.openpty()               # a terminal: the dump prints line by line
        try:
            self._proc = subprocess.Popen(["pw-mididump"], stdout=slave, stderr=subprocess.DEVNULL,
                                          stdin=subprocess.DEVNULL, close_fds=True)
        except OSError as e:
            os.close(master)
            os.close(slave)
            raise RuntimeError(f"openin failed: {e}") from e
        os.close(slave)
        self._fd = master
        os.set_blocking(master, False)
        if not self._link(wait):
            self.close()
            raise RuntimeError("openin failed: the PipeWire source is gone")

    def _link(self, wait: float) -> bool:
        end = time.monotonic() + wait
        while time.monotonic() < end:
            rc, out = _run(["pw-link", "-I", "-i"])
            ids = [int(m.group(1)) for m in re.finditer(r"^\s*(\d+)\s+midi-dump:input", out, re.M)]
            if ids and link(self.source, str(max(ids)), wait=0):    # ours is the newest
                return True
            time.sleep(0.15)
        return False

    def rawrecv(self, timeout: float) -> bytes:
        """Wait up to ``timeout`` seconds and return the MIDI bytes that arrived.

        Raises:
            PortGoneError: if the device has gone away.
        """
        from .alsa_seq import PortGoneError
        if time.monotonic() - self._last_check > 2.0:
            self._last_check = time.monotonic()
            if not present(self.source) or self._proc.poll() is not None:
                raise PortGoneError(self.source)
        ready, _, _ = select.select([self._fd], [], [], timeout)
        out = bytearray()
        if ready:
            try:
                self._buf += os.read(self._fd, 4096)
            except OSError:
                return b""
            *lines, self._buf = self._buf.split(b"\n")
            for line in lines:
                out += parse_line(line.decode(errors="replace"))
        return bytes(out)

    def close(self) -> None:
        """Stop the dump (which ends the link) and close the terminal (idempotent)."""
        proc, self._proc = getattr(self, "_proc", None), None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(2)
            except subprocess.TimeoutExpired:
                proc.kill()
        fd, self._fd = getattr(self, "_fd", -1), -1
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
