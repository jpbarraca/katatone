"""Follow the amp: show what it announces (a knob turned on the amp, a pedal moving an effect's
setting) in the editor, live.

The amp, in editor mode, sends a DT1 for each parameter that changes (see
:mod:`katatone.amp.notify`): about 20 a second while a pedal or a knob moves.  Each one is
mirrored in the editor's patch at once and shown on its widget at a gentler rate.

* Our own writes come back as announcements too; those of a parameter written a moment ago are
  ignored, so that a late echo cannot move a knob back while it is being dragged.
* A parameter a pedal of the patch drives is not an edit: the pedal moves it, the patch stays
  as it is.  Any other one is, like a knob turned on the amp.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Protocol

from . import notify

#: A parameter written by us this recently is not followed (the amp may echo it).
ECHO_WINDOW_S = 0.4
#: The widget of a parameter is moved at most this often.
SHOW_INTERVAL_S = 0.04


class FollowHost(Protocol):
    """What the follower needs of the application (``App``)."""

    live_ready: bool

    def mirror_announced(self, block: str, p_index: int, value: int, marks_dirty: bool) -> None:
        """Put the value in the editor's patch (``marks_dirty``: it counts as an edit)."""

    def show_param(self, block: str, p_index: int, value: int) -> None:
        """Move the widget of the parameter."""

    def pedal_targets(self) -> set[tuple[str, int]]:
        """The ``(block, parameter index)`` pairs the patch's pedals drive."""


class AmpFollower:
    """Turns what the amp announces into changes of the editor."""

    def __init__(self, host: FollowHost, clock: Callable[[], float] = time.monotonic,
                 timer: Callable[..., threading.Timer] = threading.Timer) -> None:
        """
        Args:
            host: the application.
            clock: the time source in seconds (tests pass a fake one).
            timer: ``threading.Timer`` (tests pass a fake one).
        """
        self.host, self._clock, self._timer = host, clock, timer
        self._own: dict[tuple[str, int], float] = {}        # parameter -> when we last wrote it
        self._last_show: dict[tuple[str, int], float] = {}
        self._pending: dict[tuple[str, int], int] = {}      # values not shown yet
        self._flush_timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def wrote(self, block: str, p_index: int) -> None:
        """We are sending this parameter: its echo is not the amp's own news."""
        self._own[(block, p_index)] = self._clock()

    def announced(self, nib: int, payload: bytes) -> bool:
        """An unsolicited DT1 arrived (the transport's RX thread: keep it quick).

        Returns:
            Whether it was a parameter change that was followed.
        """
        if not self.host.live_ready:
            return False                    # the editor does not mirror the amp now
        change = notify.decode(nib, payload)
        if change is None:
            return False
        key = (change.block, change.p_index)
        if self._clock() - self._own.get(key, float("-inf")) < ECHO_WINDOW_S:
            return False
        pedal = key in self.host.pedal_targets()
        self.host.mirror_announced(change.block, change.p_index, change.value, not pedal)
        self._show(key, change.value)
        return True

    def _show(self, key: tuple[str, int], value: int) -> None:
        """Move the widget now, or (when it moved a moment ago) with the latest value soon."""
        with self._lock:
            now = self._clock()
            if now - self._last_show.get(key, float("-inf")) >= SHOW_INTERVAL_S:
                self._last_show[key] = now
                self._pending.pop(key, None)
            else:
                self._pending[key] = value
                if self._flush_timer is None:
                    self._flush_timer = self._timer(SHOW_INTERVAL_S, self._flush)
                    self._flush_timer.daemon = True
                    self._flush_timer.start()
                return
        self.host.show_param(key[0], key[1], value)

    def _flush(self) -> None:
        """Show the latest value of every parameter that has one waiting."""
        with self._lock:
            pending, self._pending, self._flush_timer = self._pending, {}, None
            now = self._clock()
            for key in pending:
                self._last_show[key] = now
        for (block, p_index), value in pending.items():
            self.host.show_param(block, p_index, value)
