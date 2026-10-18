"""Running with a read-only file system (``--reduce-writes``).

When enabled, the file system that holds KataTone's data (the patch library and
the configuration: the data directory) and the one that holds ``/etc`` (network,
host name and mDNS settings) are looked up at start-up, and they are the only
ones ever remounted (often they are the same).  Every write to them happens
inside :func:`writable`, which remounts the file system read-write and puts it
back to read-only when the last writer is done; a write that lands on any other
file system is left alone (that is for the administrator).  Disabled (the default),
:func:`writable` does nothing.  Remounting needs root: other users go through
``sudo -n``.
"""
from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Callable, Iterator

log = logging.getLogger("katatone.rofs")

MOUNTINFO = "/proc/self/mountinfo"

#: ``run(args) -> (returncode, stderr)``; tests replace it.
Runner = Callable[[list], "tuple[int, str]"]


def _run(args: list) -> "tuple[int, str]":
    """Run a command and return its exit code and error output."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return p.returncode, p.stderr.strip()


class RemountError(OSError):
    """The file system could not be made writable."""


class _State:
    """The switch, and which mounts we have opened for writing."""

    def __init__(self) -> None:
        self.enabled = False
        self.run: Runner = _run
        self.mountinfo = MOUNTINFO
        self.lock = threading.RLock()
        self.writers: dict[str, int] = {}       # mount point -> active writers
        self.reopened: set[str] = set()         # mounts to put back to read-only
        self.init_held: list[str] = []          # mounts held by begin_init
        self.scope: set[str] = set()            # mount points we may remount


_s = _State()


def enable(on: bool = True, data_path: "str | Path | None" = None,
           also: "tuple | list" = ("/etc",)) -> None:
    """Turn the remounting on (or off), and find the file systems to remount.

    Args:
        on: whether ``--reduce-writes`` is in effect.
        data_path: where KataTone keeps its data (default: the data directory).
        also: other places it writes to (``/etc``, for the network settings).
            The file systems holding ``data_path`` and these are the only ones
            that will be remounted.
    """
    _s.enabled = on
    _s.scope = set()
    if not on:
        return
    if data_path is None:
        from .paths import data_root
        data_path = data_root()
    for path in (data_path, *also):
        found = mount_of(path)
        if found is None:
            log.warning("cannot tell which file system holds %s: it will not be remounted",
                        path)
            continue
        _s.scope.add(found[0])
        log.debug("remountable: %s holds %s (%s)", found[0], path,
                  "ro" if found[1] else "rw")


def enabled() -> bool:
    """Whether ``--reduce-writes`` is in effect."""
    return _s.enabled


def _unescape(field: str) -> str:
    """Undo the octal escapes (``\\040`` for a space) of mountinfo."""
    out, i = [], 0
    while i < len(field):
        if field[i] == "\\" and field[i + 1:i + 4].isdigit():
            out.append(chr(int(field[i + 1:i + 4], 8)))
            i += 4
        else:
            out.append(field[i])
            i += 1
    return "".join(out)


def mount_of(path: str | Path) -> "tuple[str, bool] | None":
    """The mount holding ``path``.

    Args:
        path: a file or directory, which need not exist yet.
    Returns:
        ``(mount point, read_only)``, or None if it cannot be determined.
    """
    p = Path(os.path.realpath(path))
    while not p.exists() and p != p.parent:
        p = p.parent
    best = None
    try:
        lines = Path(_s.mountinfo).read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        left, _, right = line.partition(" - ")
        f, g = left.split(), right.split()
        if len(f) < 6 or len(g) < 3:
            continue
        mp = _unescape(f[4])
        if p == Path(mp) or Path(mp) in p.parents:
            if best is None or len(mp) >= len(best[0]):
                ro = "ro" in f[5].split(",") or "ro" in g[2].split(",")
                best = (mp, ro)
    return best


#: File systems that are RAM-backed, layered or inherently read-only: they are
#: managed by hand, and never remounted in either direction.
VOLATILE_FSTYPES = {"tmpfs", "ramfs", "devtmpfs", "overlay", "overlayfs", "squashfs"}


def _fstype_of(mp: str) -> str:
    """The file system type mounted at ``mp`` ('' if unknown)."""
    try:
        lines = Path(_s.mountinfo).read_text().splitlines()
    except OSError:
        return ""
    for line in lines:
        left, _, right = line.partition(" - ")
        f, g = left.split(), right.split()
        if len(f) >= 5 and g and _unescape(f[4]) == mp:
            return g[0]
    return ""


def begin_init() -> None:
    """Hold the data and ``/etc`` file systems read-write while the app starts up.

    Start-up writes in places nobody can list in advance, so rather than
    remounting around each write, keep them writable until :func:`end_init`,
    which makes them read-only again *even if they were writable before*.  A
    RAM-backed or layered file system (:data:`VOLATILE_FSTYPES`) is skipped.

    Raises:
        RemountError: if one is read-only and cannot be remounted.
    """
    if not _s.enabled:
        return
    for mp in sorted(_s.scope):
        if _acquire(mp) is not None:
            _s.init_held.append(mp)
            _s.reopened.add(mp)


def end_init() -> None:
    """Initialisation is over: release what :func:`begin_init` held, which
    puts the file systems back to read-only (idempotent)."""
    with _s.lock:
        held, _s.init_held = _s.init_held, []
    for mp in held:
        _release(mp)


def _remount(mp: str, mode: str) -> None:
    """``mount -o remount,<mode> mp``.

    Raises:
        RemountError: if it fails.
    """
    cmd = ["mount", "-o", f"remount,{mode}", mp]
    if os.geteuid() != 0:
        cmd = ["sudo", "-n", *cmd]
    rc, err = _s.run(cmd)
    if rc != 0:
        raise RemountError(f"cannot remount {mp} {mode}: {err or rc}")
    log.debug("remounted %s %s", mp, mode)


def _acquire(path: str | Path) -> str | None:
    """Open the mount of ``path`` for writing; returns its mount point."""
    found = mount_of(path)
    if found is None:
        return None
    mp, ro = found
    if mp not in _s.scope:
        return None             # not the data or /etc file system: not ours to remount
    if _fstype_of(mp) in VOLATILE_FSTYPES:
        return None             # RAM-backed or layered: managed by hand, never touched
    with _s.lock:
        if _s.writers.get(mp, 0) == 0 and ro:
            _remount(mp, "rw")
            _s.reopened.add(mp)
        _s.writers[mp] = _s.writers.get(mp, 0) + 1
    return mp


def _release(mp: str) -> None:
    """Drop a writer of ``mp``; the last one puts it back read-only."""
    with _s.lock:
        _s.writers[mp] -= 1
        if _s.writers[mp] or mp not in _s.reopened:
            return
        os.sync()
        try:
            _remount(mp, "ro")
        except RemountError:
            os.sync()
            try:
                _remount(mp, "ro")
            except RemountError as e:
                log.warning("%s", e)        # e.g. a file is still open for writing
                return
        _s.reopened.discard(mp)


@contextlib.contextmanager
def writable(*paths: str | Path) -> Iterator[None]:
    """Allow writing to ``paths`` for the duration of the block.

    Nested and concurrent blocks share one remount.  A no-op unless
    :func:`enable` was called.

    Raises:
        RemountError: if a file system cannot be made read-write (the block
            is not entered).
    """
    if not _s.enabled:
        yield
        return
    held: list[str] = []
    try:
        for p in paths:
            mp = _acquire(p)
            if mp is not None:
                held.append(mp)
        yield
    finally:
        for mp in reversed(held):
            _release(mp)
