"""Back up the whole amp (its ten user patches and the SYSTEM settings) to one
file, and restore it.

The file is JSON.  Each patch and the system settings use the liveset file's
``paramSet`` shape (block id -> hex byte strings), so a patch can be copied out
into a ``.tsl`` by hand::

    {"format": "katatone-backup", "formatRev": 1, "device": "KATANA Gen3",
     "created": "...", "katatone": "0.5.0",
     "patches": [ {"PATCH%COM": ["4B", ...], ...}, ... ten of them ],
     "system": {"SYSTEM%...": [...], ...}}
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import __version__, rofs
from ..liveset.tsl import DEVICE, NUM_PATCHES, Tsl
from ..model.paramset import ParamSet
from ..paths import data_root
from ..protocol.transport import Transport
from .read import read_patch, read_region
from .write import put_back_edit_buffer, write_patch, write_system

log = logging.getLogger("katatone.backup")

FORMAT = "katatone-backup"
FORMAT_REV = 1
EXTENSION = "ktbackup"

# progress(step, steps, text)
Progress = Callable[[int, int, str], None]


class BackupError(ValueError):
    """The file is not a backup this version can restore."""


@dataclass
class Backup:
    """The contents of an amp: ``patches[n]`` is user patch ``n`` (0-9)."""

    patches: list[ParamSet]
    system: ParamSet
    created: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(
        timespec="seconds"))
    version: str = __version__

    def names(self) -> list[str]:
        """The patch names, one per slot."""
        return [ParamSet.patch_name(ps.block("PATCH%COM")) for ps in self.patches]

    def to_json(self) -> str:
        """The backup as the file's JSON text."""
        return json.dumps({
            "format": FORMAT, "formatRev": FORMAT_REV, "device": DEVICE,
            "created": self.created, "katatone": self.version,
            "patches": [Tsl.encode(ps) for ps in self.patches],
            "system": Tsl.encode(self.system),
        }, indent=1)

    def save(self, path: str | Path) -> None:
        """Write the backup to ``path``."""
        Path(path).write_text(self.to_json())

    @classmethod
    def from_json(cls, text: str) -> "Backup":
        """Parse the text of a backup file.

        Raises:
            BackupError: when it is not a backup of a Katana Gen 3 with all
                ten patches and the system settings.
        """
        try:
            obj = json.loads(text)
            if not isinstance(obj, dict) or obj.get("format") != FORMAT:
                raise BackupError("not a KataTone backup file")
            if obj.get("formatRev") != FORMAT_REV:
                raise BackupError(f"unsupported backup version {obj.get('formatRev')!r}")
            if obj.get("device") != DEVICE:
                raise BackupError(f"backup is for {obj.get('device')!r}, not {DEVICE!r}")
            raw_patches, raw_system = obj.get("patches"), obj.get("system")
            if not isinstance(raw_patches, list) or len(raw_patches) != NUM_PATCHES:
                raise BackupError(f"the backup must hold {NUM_PATCHES} patches")
            if not isinstance(raw_system, dict) or not raw_system:
                raise BackupError("the backup has no system settings")
            patches = [_paramset(p) for p in raw_patches]
            if not all(ps.block("PATCH%COM") for ps in patches):
                raise BackupError("a patch in the backup is empty")
            return cls(patches, _paramset(raw_system),
                       str(obj.get("created", "")), str(obj.get("katatone", "")))
        except (ValueError, TypeError, AttributeError) as e:
            if isinstance(e, BackupError):
                raise
            raise BackupError(f"damaged backup file: {e}") from e

    @classmethod
    def load(cls, path: str | Path) -> "Backup":
        """Read a backup file (see :meth:`from_json`)."""
        return cls.from_json(Path(path).read_text())


def _paramset(blocks: dict) -> ParamSet:
    """A ParamSet from the file's block id -> hex strings dict."""
    ps = ParamSet()
    for bid, hx in blocks.items():
        ps.set_block(bid, bytes(int(x, 16) for x in hx))
    return ps


def read_backup(t: Transport, progress: Progress | None = None) -> Backup:
    """Read the ten user patches and the SYSTEM settings from the amp.

    Args:
        t: the connected transport.
        progress: called as ``progress(step, steps, text)`` before each part.
    """
    steps = NUM_PATCHES + 1
    patches = []
    for n in range(NUM_PATCHES):
        if progress:
            progress(n, steps, f"Reading patch {n + 1}")
        patches.append(read_patch(t, n))
    if progress:
        progress(NUM_PATCHES, steps, "Reading system settings")
    system = read_region(t, "SYSTEM")
    if progress:
        progress(steps, steps, "Done")
    return Backup(patches, system)


def write_backup(t: Transport, backup: Backup, progress: Progress | None = None) -> None:
    """Write a backup to the amp: the ten user patches, then the SYSTEM settings.

    The amp plays what it played before (the edit buffer is put back), unsaved
    edits included.  Everything in the slots is overwritten.

    Args:
        t: the connected transport.
        backup: what to write.
        progress: called as ``progress(step, steps, text)`` before each part.
    """
    steps = NUM_PATCHES + 1
    keep = read_region(t, "PATCH")                  # the sound as it is now
    try:
        for n, ps in enumerate(backup.patches):
            if progress:
                progress(n, steps, f"Writing patch {n + 1}")
            write_patch(t, n, ps)
        if progress:
            progress(NUM_PATCHES, steps, "Writing system settings")
        write_system(t, backup.system)
    finally:
        put_back_edit_buffer(t, keep)
    if progress:
        progress(steps, steps, "Done")


def save_before_restore(current: Backup) -> Path:
    """Keep a copy of what the amp holds, in the data directory, before a
    restore overwrites it.

    Args:
        current: the amp as read just before the restore.
    Returns:
        The file written (``<data dir>/backups/before-restore-<time>.ktbackup``).
    """
    folder = data_root() / "backups"
    path = folder / f"before-restore-{datetime.now():%Y%m%d-%H%M%S}.{EXTENSION}"
    with rofs.writable(folder):
        folder.mkdir(parents=True, exist_ok=True)
        current.save(path)
    return path
