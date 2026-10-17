"""MIDI learn: capture the first controller message and turn it into a `Trigger`.

The user presses the footswitch or moves the pedal; the first event from any
enabled controller identifies the control and ends Learn at once. For a CC the
single value decides between a switch and a pedal (see `decide`); the mode can
still be changed by hand in the dialog.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from .events import (ChannelPressure, ControlChange, MidiEvent, NoteOff, NoteOn,
                     PitchBend, ProgramChange)
from .mapping import Trigger

LEARN_TIMEOUT_S = 10.0


@dataclass
class LearnResult:
    """What Learn captured.

    Attributes:
        trigger: the trigger to start the mapping from.
        description: the captured control, e.g. "SINCO MIDI 1 · ch 1 · PC 2".
        suggested_action: the action type to preselect: ``channel_select``,
            ``effect`` or ``expression``.
    """

    trigger: Trigger
    description: str
    suggested_action: str


def decide(device: str, first: MidiEvent) -> LearnResult:
    """Build a trigger from the first message a control sent.

    A PC or note is a press. A CC whose value is 0 or 127 is taken for a switch
    (``press`` with ``repeat``, which suits pedals that send only 127 as well as
    ones that send 127 then 0); any other CC value is a pedal (``range``).
    Pitch bend and pressure are always continuous. A pedal that happens to
    start at an end stop is learned as a switch; change its mode in the dialog.

    Args:
        device: the port label the event came from.
        first: the first event received.

    Returns:
        The captured trigger with a default mode and suggested action.
    """
    where = f"{device} · ch {first.ch + 1}"
    if isinstance(first, ProgramChange):
        return LearnResult(Trigger(device, first.ch, "pc", first.program),
                           f"{where} · PC {first.program}", "channel_select")
    if isinstance(first, (NoteOn, NoteOff)):
        return LearnResult(Trigger(device, first.ch, "note", first.note),
                           f"{where} · Note {first.note}", "channel_select")
    if isinstance(first, ControlChange):
        if first.value not in (0, 127):
            return LearnResult(
                Trigger(device, first.ch, "cc", first.control, "range"),
                f"{where} · CC {first.control} (pedal)", "expression")
        return LearnResult(
            Trigger(device, first.ch, "cc", first.control, "press", repeat=True),
            f"{where} · CC {first.control} (switch)", "effect")
    if isinstance(first, PitchBend):
        return LearnResult(Trigger(device, first.ch, "pitchbend", None, "range"),
                           f"{where} · Pitch bend", "expression")
    assert isinstance(first, ChannelPressure)
    return LearnResult(Trigger(device, first.ch, "pressure", None, "range"),
                       f"{where} · Pressure", "expression")


class LearnSession:
    """Waits for a controller message; thread-safe, reports once."""

    def __init__(self, on_done: Callable[[LearnResult | None], None],
                 timeout: float = LEARN_TIMEOUT_S) -> None:
        """Prepare a session; call `start()` to begin the countdown.

        Args:
            on_done: called once, on a background thread, with the result, or
                with None when nothing arrived within `timeout`.
            timeout: seconds to wait for the first message.
        """
        self._on_done = on_done
        self._timeout = timeout
        self._lock = threading.Lock()
        self._finished = False
        self._timer: threading.Timer | None = None

    def start(self) -> None:
        """Begin waiting for the first message."""
        with self._lock:
            self._arm(self._timeout, self._expire)

    def cancel(self) -> None:
        """Stop without reporting."""
        with self._lock:
            self._finished = True
            if self._timer is not None:
                self._timer.cancel()

    def feed(self, device: str, ev: MidiEvent) -> None:
        """Offer an event (router thread); the first usable one ends Learn.

        Args:
            device: the port label.
            ev: the parsed event.
        """
        with self._lock:
            if self._finished or isinstance(ev, NoteOff):
                return              # a NoteOff is the release of an earlier press
            self._finished = True
            if self._timer is not None:
                self._timer.cancel()
            result = decide(device, ev)
        threading.Thread(target=self._on_done, args=(result,), daemon=True).start()

    def _arm(self, seconds: float, fn: Callable[[], None]) -> None:
        """(Re)start the single countdown timer; the lock must be held.

        Args:
            seconds: delay.
            fn: what to run when it fires.
        """
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(seconds, fn)
        self._timer.daemon = True
        self._timer.start()

    def _expire(self) -> None:
        """Timer: nothing arrived in time."""
        with self._lock:
            if self._finished:
                return
            self._finished = True
        self._on_done(None)
