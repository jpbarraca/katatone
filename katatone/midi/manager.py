"""Owns the controller inputs, the router and the active profile.

The GUI talks to this class only: it lists ports, enables or disables them,
and subscribes to state and activity notifications. Callbacks arrive on
background threads; the GUI must marshal them itself.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from ..protocol.alsa_seq import InPort
from ..protocol.alsa_seqport import list_all_input_ports
from .events import MidiEvent
from .input import MidiInput
from .learn import LearnResult, LearnSession
from .mapping import Profile
from .router import Router, RouterHost
from .store import ProfileStore

log = logging.getLogger("katatone.midi.manager")


def looks_like_amp(port: InPort) -> bool:
    """Whether a port is a Katana itself, which must never be opened as a controller.

    Opening it would take the rawmidi device away from the amp link.

    Args:
        port: an input port.

    Returns:
        True for ports whose card or device name contains "katana".
    """
    return "katana" in f"{port.card} {port.name}".lower()


class MidiManager:
    """Controllers + router + profile, behind one small interface."""

    def __init__(self, host: RouterHost, store: ProfileStore | None = None, *,
                 exclude_cards: Callable[[], set[str]] = lambda: set(),
                 input_factory=MidiInput,
                 list_ports: Callable[[], list[InPort]] = list_all_input_ports) -> None:
        """Load the active profile and build the router (nothing runs yet).

        Args:
            host: the application, as seen by the router.
            store: profile storage (defaults to the user's data directory).
            exclude_cards: card names never offered as controllers (the
                connected amp's).
            input_factory: builds a `MidiInput` (replaced in tests).
            list_ports: enumerates input ports (replaced in tests).
        """
        self.store = store or ProfileStore()
        self.profile: Profile = self.store.load(self.store.active())
        self.router = Router(host, self.profile)
        self.router.on_activity = self._on_activity
        self.states: dict[str, str] = {}
        self._exclude = exclude_cards
        self._factory = input_factory
        self._list_ports = list_ports
        self._inputs: dict[str, MidiInput] = {}
        self._listeners: list[Callable[..., None]] = []
        self._lock = threading.Lock()
        self._started = False
        self._learn: LearnSession | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Start the router and open the controllers the profile enables."""
        if self._started:
            return
        self._started = True
        self.router.start()
        for label in list(self.profile.devices):
            self._open(label)

    def stop(self) -> None:
        """Close every controller and stop the router."""
        self._started = False
        with self._lock:
            inputs, self._inputs = list(self._inputs.values()), {}
        for mi in inputs:
            mi.stop()
        self.router.stop()

    # -- ports -------------------------------------------------------------

    def ports(self) -> list[InPort]:
        """Rescan and list the controllers that can be enabled.

        Returns:
            Input ports minus the amp (connected card, or anything called Katana).
        """
        excluded = self._exclude()
        return [p for p in self._list_ports()
                if p.card not in excluded and not looks_like_amp(p)]

    def enabled(self) -> list[str]:
        """Labels of the enabled controllers, in profile order."""
        return list(self.profile.devices)

    def enabled_cards(self) -> set[str]:
        """Card names of the enabled controllers (hidden from the amp's port lists).

        Returns:
            The `InPort.card` of every present port whose label is enabled.
        """
        wanted = set(self.profile.devices)
        return {p.card for p in self._list_ports() if p.label in wanted}

    def enable(self, label: str, on: bool) -> None:
        """Enable or disable one controller and remember it in the profile.

        Args:
            label: the port label (device name).
            on: True to open it, False to close it.
        """
        log.info("controller %s: %s", label, "enabled" if on else "disabled")
        if on and label not in self.profile.devices:
            self.profile.devices.append(label)
        elif not on and label in self.profile.devices:
            self.profile.devices.remove(label)
        self.save_profile()
        if on:
            self._open(label)
        else:
            self._close(label)

    def _open(self, label: str) -> None:
        """Start reading a controller by label (it may not be plugged in yet).

        Args:
            label: the port label.
        """
        with self._lock:
            if label in self._inputs:
                return
            spec = next((p.spec for p in self._list_ports() if p.label == label), "")
            port = InPort(spec=spec, card="", name=label)
            mi = self._factory(port, self._on_event, self._on_state)
            self._inputs[label] = mi
        mi.start()

    def _close(self, label: str) -> None:
        """Stop reading a controller.

        Args:
            label: the port label.
        """
        with self._lock:
            mi = self._inputs.pop(label, None)
        if mi is not None:
            mi.stop()

    # -- profile -----------------------------------------------------------

    def save_profile(self) -> None:
        """Write the active profile and make the router see edits to it."""
        self.store.save(self.profile)
        self.profile.reset_state()

    def set_profile(self, name: str) -> None:
        """Switch to another stored profile, reopening its controllers.

        Args:
            name: an existing profile.
        """
        new = self.store.load(name)
        log.info("active profile: %s (%d mappings)", name, len(new.mappings))
        self.store.set_active(name)
        old_devices = set(self.profile.devices)
        self.profile = new
        self.router.set_profile(new)
        for label in old_devices - set(new.devices):
            self._close(label)
        if self._started:
            for label in new.devices:
                self._open(label)

    # -- learn -------------------------------------------------------------

    def start_learn(self, on_done: Callable[[LearnResult | None], None],
                    timeout: float | None = None) -> LearnSession:
        """Capture the next controller message instead of acting on it.

        Args:
            on_done: called once with the result, or None on timeout.
            timeout: seconds to wait (default `learn.LEARN_TIMEOUT_S`).

        Returns:
            The running session (cancel it with `cancel_learn`).
        """
        self.cancel_learn()
        kw = {} if timeout is None else {"timeout": timeout}

        def done(result: LearnResult | None) -> None:
            if self._learn is session:
                self.router.capture = None
                self._learn = None
            on_done(result)

        session = LearnSession(done, **kw)
        self._learn = session
        self.router.capture = session.feed
        session.start()
        return session

    def cancel_learn(self) -> None:
        """Abandon a running Learn; mappings act on events again."""
        session, self._learn = self._learn, None
        self.router.capture = None
        if session is not None:
            session.cancel()

    # -- notifications -----------------------------------------------------

    def subscribe(self, cb: Callable[..., None]) -> Callable[[], None]:
        """Listen for ``cb("state", label, state)`` and
        ``cb("activity", device, event, matched_labels)``.

        Args:
            cb: called on a background thread.

        Returns:
            A function that removes the subscription.
        """
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb) if cb in self._listeners else None

    def _emit(self, *args) -> None:
        """Call every listener, containing their errors.

        Args:
            *args: the notification, starting with its kind.
        """
        for cb in list(self._listeners):
            try:
                cb(*args)
            except Exception:
                log.exception("MIDI listener failed")

    def _on_event(self, label: str, ev: MidiEvent) -> None:
        """Input thread: hand the event to the router."""
        self.router.submit(label, ev)

    def _on_state(self, label: str, state: str) -> None:
        """Input thread: remember and announce a controller state change."""
        self.states[label] = state
        self._emit("state", label, state)

    def _on_activity(self, device: str, ev: MidiEvent, labels: list[str]) -> None:
        """Router thread: announce a processed event (Monitor, activity LED)."""
        self._emit("activity", device, ev, labels)
