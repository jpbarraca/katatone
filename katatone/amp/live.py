"""Live editing: send parameter changes to the amp's edit buffer as they happen.

Changes are queued and sent from one background thread.  Repeated changes of
the same parameter are coalesced (only the latest value is sent), so a fast
knob drag produces a handful of DT1 frames instead of one per pixel.  Only the
edit buffer is written (see :func:`katatone.amp.write.write_param`): nothing is
stored in a user patch until an explicit patch write.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from ..model.paramset import ParamSet
from ..protocol.transport import Transport
from .write import EDIT_REGION, write_param

log = logging.getLogger("katatone.live")

NAME = ("COM", -1)          # queue key for the 16-byte patch name


class LiveWriter:
    """Coalescing background sender for edit-buffer parameter writes.

    ``get_transport()`` returns the connected transport (or None);
    ``paused()`` holds sending while a patch read/write owns the wire;
    ``on_sent(key)`` / ``on_error(msg)`` report back (called on the worker
    thread).
    """

    def __init__(self, get_transport: Callable[[], Optional[Transport]],
                 paused: Callable[[], bool] = lambda: False,
                 on_sent: Callable[[tuple], None] = lambda key: None,
                 on_error: Callable[[str], None] = lambda msg: None,
                 interval: float = 0.03) -> None:
        """Wire up the transport accessor, pause gate and callbacks.

        The worker thread starts lazily on the first queued change.

        Args:
            get_transport: returns the connected transport (or None).
            paused: while true, holds sending (patch read/write owns the wire).
            on_sent: callback with the key of each sent change (worker thread).
            on_error: callback with an error message (worker thread).
            interval: coalescing delay in seconds before sending (default 0.03).
        """
        self._get_transport = get_transport
        self._paused = paused
        self._on_sent = on_sent
        self._on_error = on_error
        self._interval = interval
        self._pending: dict[tuple, object] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stop = False

    # ------------------------------------------------------------ public

    def queue_param(self, block: str, p_index: int, value: int) -> None:
        """Queue a live change of one edit-buffer parameter for sending.

        Args:
            block: the block name within the edit region (e.g. "AMP").
            p_index: index into the block's `params` list.
            value: the display value to write.
        """
        self._queue((block, p_index), value)

    def queue_name(self, name: str) -> None:
        """Queue a live change of the 16-byte patch name (COM block)."""
        self._queue(NAME, name)

    def clear(self) -> None:
        """Drop all queued changes without sending them."""
        with self._lock:
            self._pending.clear()

    def pending(self) -> int:
        """Number of changes currently queued for sending."""
        with self._lock:
            return len(self._pending)

    def stop(self) -> None:
        """Signal the worker thread to exit on its next wake."""
        self._stop = True
        self._wake.set()

    # ---------------------------------------------------------- internals

    def _queue(self, key: tuple, value) -> None:
        """Store `value` under `key`; coalescing means only the latest wins.

        Starts the worker thread if it is not running and wakes it so the
        change goes out after the coalescing interval.

        Args:
            key: queue key — (block, p_index) or the NAME key.
            value: the display value (int) or patch name (str).
        """
        with self._lock:
            self._pending.pop(key, None)       # keep send order = last change
            self._pending[key] = value
        if self._thread is None or not self._thread.is_alive():
            self._stop = False
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="katatone-live-edit")
            self._thread.start()
        self._wake.set()

    def _run(self) -> None:
        """Worker loop: after each wake, sleep the coalescing interval, then
        send queued changes one at a time until the queue drains.

        Sending is held while `paused()` is true; the loop exits when
        `stop()` is called.
        """
        while not self._stop:
            self._wake.wait()
            self._wake.clear()
            time.sleep(self._interval)          # let a drag coalesce
            while not self._stop:
                if self._paused():
                    time.sleep(0.1)
                    continue
                with self._lock:
                    if not self._pending:
                        break
                    key = next(iter(self._pending))
                    value = self._pending.pop(key)
                self._send(key, value)

    def _send(self, key: tuple, value) -> None:
        """Send one queued change over the transport.

        NAME keys write the encoded patch name into the edit region's COM
        block; (block, p_index) keys go through `write_param`.

        Args:
            key: queue key — the NAME key or (block, p_index).
            value: the patch name (str) or display value (int).
        """
        t = self._get_transport()
        if t is None:
            return
        try:
            if key == NAME:
                from ..address.map import load_map
                base = load_map()[EDIT_REGION].block("COM").base_addr
                t.dt1(base, ParamSet.encode_patch_name(str(value)))
            else:
                write_param(t, key[0], key[1], int(value))
            self._on_sent(key)
        except Exception as e:                        # noqa: BLE001
            log.warning("live edit %s failed: %s", key, e)
            self._on_error(f"Live edit failed for {key[0]}: {e}")
