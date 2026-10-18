"""One controller's input: daemon, reader thread, parser, reconnect.

The reader thread only parses bytes and calls ``on_event``; it never touches
application state. Controllers are identified
by their port *label* (device name), because ALSA card numbers change between
boots and replugs; after an unplug the thread polls until a port with the same
label reappears, whatever its new ``hw:`` number.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from ..protocol.alsa_seq import InPort, PortBusyError, PortGoneError
from ..protocol.alsa_seqport import CombinedDaemon, list_all_input_ports
from .events import MidiEvent
from .parser import MidiParser

log = logging.getLogger("katatone.midi.input")

__all__ = ["MidiInput", "PortBusyError", "PortGoneError"]

# How long one rawrecv blocks; also the worst-case latency of stop().
_POLL_S = 0.05
_GONE_RETRY_S = 2.0
_BUSY_RETRY_S = 5.0


class MidiInput:
    """Reads one controller and reports parsed events."""

    def __init__(self, port: InPort,
                 on_event: Callable[[str, MidiEvent], None],
                 on_state: Callable[[str, str], None],
                 *,
                 daemon_factory: Callable[[], CombinedDaemon] = CombinedDaemon,
                 list_ports: Callable[[], list[InPort]] = list_all_input_ports,
                 gone_retry: float = _GONE_RETRY_S,
                 busy_retry: float = _BUSY_RETRY_S) -> None:
        """Prepare an input; nothing is opened until `start()`.

        Args:
            port: the controller to open; `port.label` is its stable identity.
            on_event: called as `on_event(label, event)` from the reader thread.
            on_state: called as `on_state(label, state)` on every change, with
                state one of "open", "busy", "gone", "closed" or "error: ...".
            daemon_factory: builds a daemon process (replaced in tests).
            list_ports: enumerates input ports (replaced in tests).
            gone_retry: seconds between scans while the device is unplugged.
            busy_retry: seconds between retries while another program holds it.
        """
        self.port = port
        self._on_event = on_event
        self._on_state = on_state
        self._factory = daemon_factory
        self._list_ports = list_ports
        self._gone_retry = gone_retry
        self._busy_retry = busy_retry
        self._parser = MidiParser()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._daemon: CombinedDaemon | None = None
        self._state = ""

    def start(self) -> None:
        """Spawn the reader thread (it opens the device and reconnects as needed)."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"midi-in-{self.port.label}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the thread, close the device and quit the daemon."""
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(timeout=3)
        self._close_daemon()
        self._set_state("closed")

    # -- internals ---------------------------------------------------------

    def _set_state(self, state: str) -> None:
        """Report a state change once (repeats are suppressed).

        Args:
            state: the new state string for `on_state`.
        """
        if state == self._state:
            return
        self._state = state
        log.info("%s: %s", self.port.label, state)
        try:
            self._on_state(self.port.label, state)
        except Exception:
            log.exception("on_state callback failed")

    def _close_daemon(self) -> None:
        """Quit the daemon process, ignoring errors."""
        d, self._daemon = self._daemon, None
        if d is not None:
            try:
                d.close()
            except Exception:
                pass

    def _resolve_spec(self) -> str | None:
        """Find the current `card,device` of this controller by label.

        Returns:
            The spec of the port whose label matches, or None if it is absent.
        """
        for p in self._list_ports():
            if p.label == self.port.label:
                return p.spec
        return None

    def _open(self, spec: str) -> bool:
        """Spawn a daemon and open `spec` for input.

        Args:
            spec: "<card>,<device>" to open.

        Returns:
            True when open; False after reporting why not (busy / error).
        """
        d = None
        try:
            d = self._factory()
            d.openin(spec)
        except PortBusyError:
            self._set_state("busy")
        except Exception as e:
            self._set_state(f"error: {e}")
        else:
            self._daemon = d
            self._parser = MidiParser()     # drop any half message from before
            self._set_state("open")
            return True
        if d is not None:
            try:
                d.close()
            except Exception:
                pass
        return False

    def _run(self) -> None:
        """Reader loop: open, read, and reopen after unplug / busy / errors."""
        # An empty spec means "find it by label" (a saved device not plugged in yet).
        spec: str | None = self.port.spec or None
        while not self._stop.is_set():
            if self._daemon is None:
                if spec is None:
                    spec = self._safe_resolve()
                if spec is None:
                    self._set_state("gone")
                    self._stop.wait(self._gone_retry)
                    continue
                if not self._open(spec):
                    # Busy keeps the same device; anything else may have moved.
                    wait = self._busy_retry if self._state == "busy" else self._gone_retry
                    if self._state != "busy":
                        spec = None
                    self._stop.wait(wait)
                    continue
            try:
                data = self._daemon.rawrecv(_POLL_S)
            except PortGoneError:
                self._close_daemon()
                self._set_state("gone")
                spec = None
                self._stop.wait(self._gone_retry)
                continue
            except Exception as e:
                # Daemon died or protocol error: restart it on the next pass.
                self._close_daemon()
                self._set_state(f"error: {e}")
                spec = None
                self._stop.wait(self._busy_retry)
                continue
            for ev in self._parser.feed(data):
                try:
                    self._on_event(self.port.label, ev)
                except Exception:
                    log.exception("on_event callback failed")

    def _safe_resolve(self) -> str | None:
        """`_resolve_spec` that treats an enumeration failure as "not there".

        Returns:
            The spec, or None.
        """
        try:
            return self._resolve_spec()
        except Exception:
            log.exception("port scan failed")
            return None
