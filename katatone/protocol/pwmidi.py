"""PipeWire's MIDI sources (Bluetooth MIDI devices), linked with ``pw-link``.

PipeWire owns a Bluetooth MIDI device's connection and presents it as a MIDI
source node ``bluez_midi.<address>``.  To read it, KataTone links that source to
the sequencer port of its own input client (PipeWire shows the sequencer's ports
under ``Midi-Bridge``) and removes the link when the input is closed.  Needs
``pw-link`` (``pipewire-utils``); without it nothing is offered.
"""
from __future__ import annotations

import json
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


def sequencer_port(client: str, port: str) -> str:
    """How PipeWire names a sequencer port that receives MIDI (its ``(playback)``
    side): a port name that starts with its client's name is shortened, and then
    joined with ``": "`` instead of ``":"``."""
    if port.startswith(client):
        return f"Midi-Bridge:{client}: {port[len(client):].strip()} (playback)"
    return f"Midi-Bridge:{client}:{port} (playback)"


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
