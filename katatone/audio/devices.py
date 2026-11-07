"""Sound-card devices, as listed by ``arecord -L`` (inputs) and ``aplay -L`` (outputs)."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Device:
    """One capture or playback device.

    Attributes:
        name: the ALSA device name to pass to ``arecord -D`` / ``aplay -D``.
        label: a human-readable description for the dropdown.
    """

    name: str
    label: str


def parse_listing(text: str) -> list[Device]:
    """Parse the output of ``arecord -L``.

    Each device is a name at the start of a line followed by indented
    description lines.  The ``null`` device is skipped.

    Args:
        text: the command's output.
    Returns:
        The devices, in listing order.
    """
    devices: list[Device] = []
    name, desc = None, []

    def flush() -> None:
        """Store the device being read, if any."""
        if name and name != "null":
            label = ", ".join(d for d in desc if d)
            devices.append(Device(name, f"{name} — {label}" if label else name))

    for line in text.splitlines():
        if line and not line[0].isspace():
            flush()
            name, desc = line.strip(), []
        elif name:
            desc.append(line.strip())
    flush()
    return devices


def _list(tool: str) -> list[Device]:
    """The devices ``tool -L`` lists, or an empty list without the tool."""
    if shutil.which(tool) is None:
        return []
    try:
        out = subprocess.run([tool, "-L"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_listing(out)


def list_input_devices() -> list[Device]:
    """Return the capture devices, or an empty list without ``arecord``."""
    return _list("arecord")


def list_output_devices() -> list[Device]:
    """Return the playback devices, or an empty list without ``aplay``."""
    return _list("aplay")


def preferred(devices: list[Device], card: str | None) -> Device | None:
    """The device to preselect: the card's ``sysdefault`` entry if ``card``
    is given and present, else the first device.

    Args:
        devices: the listed devices.
        card: an ALSA card id such as ``KATANA3`` (or None).
    """
    if card:
        for d in devices:
            if d.name == f"sysdefault:CARD={card}":
                return d
        for d in devices:
            if f"CARD={card}" in d.name:
                return d
    return devices[0] if devices else None
