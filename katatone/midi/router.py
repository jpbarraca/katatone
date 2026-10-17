"""Routes controller events to amp actions on one worker thread.

``MidiInput`` reader threads only parse bytes and call :meth:`Router.submit`;
this module matches events against the active `Profile` and carries out the
actions through a `RouterHost` (implemented by the GUI app). One worker keeps
events in order, so three quick footswitch presses become three channel
switches in the right sequence.

Safety: the router can only (a) select a channel, (b) write single parameters
of the edit buffer through ``LiveWriter``. It never sends a patch write,
export, clear or init, and never touches stored slots or SYSTEM/SETUP.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Protocol

from ..address.map import load_map
from ..amp import slots
from ..amp.live import LiveWriter
from .events import (ControlChange, MidiEvent, NoteOn, ProgramChange, describe)
from .mapping import (EDIT_REGION, PEDAL_TYPE, Action, AmpNative, BankAction, ChannelFromPc,
                      ChannelSelect, ChannelStep, EffectAction, ExpressionAction,
                      Mapping, Profile, UnknownAction, effect_param, find_param,
                      pedal_fx_type, resolve_expression, scale_range)

log = logging.getLogger("katatone.midi.router")

# Forwarding CC/PC to the amp's own MIDI map: a Program Change over USB in editor mode is acted on (and announced), but a
# switch CC is not a simple on/off: use `effect` actions for switches.
AMP_NATIVE_ENABLED = True
# The amp's default CC for its expression pedal (SYSTEM MIDI_CC_EXPPEDAL).
DEFAULT_EXP_CC = 47

# Coalescing delay of the expression LiveWriter: small, so a wah sweep is smooth.
EXPRESSION_INTERVAL_S = 0.01
# On-screen knobs follow expression at most this often per parameter.
SHOW_INTERVAL_S = 0.05
# A router-initiated switch is "expected" this long, so a bank/step press that
# arrives before the amp's channel is confirmed builds on it, not on a stale one.
EXPECT_WINDOW_S = 1.0


class RouterHost(Protocol):
    """What the router needs from the application (implemented by ``App``)."""

    connected: bool
    model: int
    amp_patch: int | None
    live_ready: bool
    ps: object                      # ParamSet mirroring the edit buffer when live_ready
    state_lock: threading.Lock
    busy_writing: bool

    def get_transport(self): ...
    def select_channel(self, n: int, source: str) -> None: ...
    def apply_live_value(self, block: str, p_index: int, value: int,
                         mark_dirty: bool) -> None: ...
    def show_param(self, block: str, p_index: int, value: int) -> None: ...
    def status(self, msg: str) -> None: ...


class Router:
    """Event queue + worker that applies a profile's mappings."""

    def __init__(self, host: RouterHost, profile: Profile) -> None:
        """Create the router; call `start()` to run the worker.

        Args:
            host: the application (see `RouterHost`).
            profile: the mappings to apply.
        """
        self.host = host
        self.profile = profile
        # on_activity(device, event, matched_labels): every event, matched or not
        # (the Monitor). Called on the worker thread.
        self.on_activity: Callable[[str, MidiEvent, list[str]], None] | None = None
        # While set (MIDI learn), events go here instead of the mappings, so
        # pressing a pedal to learn it does not also switch the amp's channel.
        self.capture: Callable[[str, MidiEvent], None] | None = None
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._profile_lock = threading.Lock()
        # (block, p_index) -> last value the router wrote; cleared on a switch.
        self._written: dict[tuple[str, int], int] = {}
        self._pedal_types: dict[int, int] = {}      # slot -> PEDALFX_COM_TYPE seen
        self._expected: tuple[int, float] | None = None
        self._last_show: dict[tuple[str, int], float] = {}
        self._pending_show: dict[tuple[str, int], int] = {}
        # Dedicated, faster writer so wah sweeps are smooth; only Write/Clear
        # (not resync reads) hold it back.
        self.writer = LiveWriter(
            get_transport=lambda: host.get_transport() if host.connected else None,
            paused=lambda: host.busy_writing,
            on_error=host.status, interval=EXPRESSION_INTERVAL_S)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Start the worker thread (idempotent)."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="midi-router",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the worker and the expression writer."""
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(timeout=2)
        self.writer.stop()

    def set_profile(self, profile: Profile) -> None:
        """Swap the active profile (edge/toggle memory starts clean).

        Args:
            profile: the new profile.
        """
        profile.reset_state()
        with self._profile_lock:
            self.profile = profile

    def submit(self, device: str, ev: MidiEvent) -> None:
        """Queue an event from an input thread (never blocks).

        Args:
            device: the port label the event came from.
            ev: the parsed event.
        """
        self._q.put((device, ev))

    def test(self, m: Mapping) -> None:
        """Run a mapping's action once, as if its trigger had just fired.

        For a range mapping the controller value is 127 (the target's maximum).
        Queued behind real events so it never races them.

        Args:
            m: the mapping to try (it need not be in the profile).
        """
        self._q.put((None, m))

    def drain(self, timeout: float = 2.0) -> bool:
        """Wait until every queued event has been processed (for tests).

        Args:
            timeout: seconds to wait.

        Returns:
            True if the queue emptied in time.
        """
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._q.unfinished_tasks == 0:
                return True
            time.sleep(0.002)
        return False

    # -- worker ------------------------------------------------------------

    def _run(self) -> None:
        """Worker loop: process events in order; flush throttled screen updates."""
        while not self._stop.is_set():
            try:
                device, ev = self._q.get(timeout=SHOW_INTERVAL_S)
            except queue.Empty:
                self._flush_shows()
                continue
            try:
                if device is None:
                    self._run_test(ev)
                else:
                    self._process(device, ev)
            except Exception:
                log.exception("router failed on %s",
                              "a test action" if device is None else describe(ev))
            finally:
                self._q.task_done()
            self._flush_shows()

    def _run_test(self, m: Mapping) -> None:
        """Worker side of `test`.

        Args:
            m: the mapping to try.
        """
        t = m.trigger
        ch = t.channel or 0
        if t.kind == "pc":
            ev: MidiEvent = ProgramChange(ch, t.number or 0)
        elif t.kind == "cc":
            ev = ControlChange(ch, t.number or 0, 127)
        else:
            ev = ProgramChange(ch, 0)
        self._run_action(m, 127 if t.mode == "range" else True, ev)

    def _process(self, device: str, ev: MidiEvent) -> None:
        """Match one event and run the actions it fires.

        Args:
            device: the port label.
            ev: the event.
        """
        with self._profile_lock:
            profile = self.profile
        log.debug("event %s: %s", device, describe(ev))
        capture = self.capture
        if capture is not None:
            capture(device, ev)
            hits, labels = [], ["learning"]
        else:
            hits, labels = profile.match(device, ev), []
        for m, value in hits:
            labels.append(m.label or m.action.summary(self._slot_name))
            try:
                self._run_action(m, value, ev)
            except Exception as e:                   # noqa: BLE001
                log.exception("action %s failed", m.action.type)
                self.host.status(f"MIDI action failed: {e}")
        cb = self.on_activity
        if cb is not None:
            try:
                cb(device, ev, labels)
            except Exception:
                log.exception("on_activity callback failed")

    # -- helpers -----------------------------------------------------------

    def _slot_name(self, n: int) -> str:
        """Panel name of slot `n` on the connected model."""
        return slots.slot(n, self.host.model).short

    def _edit_region(self):
        """The edit-buffer region (block layouts for reading `ps`)."""
        return load_map()[EDIT_REGION]

    def _current_channel(self) -> int | None:
        """The channel to build a relative switch on.

        Returns:
            The channel the router last asked for (within a second), else the
            one the amp is known to be on, else None.
        """
        if self._expected is not None:
            n, t = self._expected
            if time.monotonic() - t < EXPECT_WINDOW_S:
                return n
        return self.host.amp_patch

    def _select(self, n: int, source: str = "pedal") -> None:
        """Switch the amp to slot `n` (or say why not).

        Args:
            n: the patch number.
            source: where the request came from, for the status line.
        """
        if not slots.has_slot(n, self.host.model):
            self.host.status(f"{self._slot_name(n)}: not on this amp")
            return
        log.debug("select slot %d (%s) from %s", n, self._slot_name(n), source)
        self._expected = (n, time.monotonic())
        self._written.clear()               # switch values belonged to the old channel
        self.host.select_channel(n, source)

    def _value_in_ps(self, block: str, p_index: int) -> int | None:
        """The parameter's value in the editor's patch, if it mirrors the amp.

        Args:
            block: the block name.
            p_index: the parameter index.

        Returns:
            The value, or None when the editor does not mirror the amp.
        """
        h = self.host
        with h.state_lock:
            if not h.live_ready:
                return None
            return h.ps.get_param(self._edit_region(), block, p_index)

    def _write(self, block: str, p_index: int, value: int, mark_dirty: bool) -> None:
        """Send one edit-buffer parameter and update the editor and caches.

        Args:
            block: the block name.
            p_index: the parameter index.
            value: the display value.
            mark_dirty: whether this counts as an edit of the patch.
        """
        key = (block, p_index)
        log.debug("write %s[%d] = %d%s", block, p_index, value,
                  " (edit)" if mark_dirty else "")
        self._written[key] = value
        self.host.apply_live_value(block, p_index, value, mark_dirty)
        self.writer.queue_param(block, p_index, value)
        self._show(key, value)

    def _show(self, key: tuple[str, int], value: int) -> None:
        """Move the on-screen widget, at most every `SHOW_INTERVAL_S` per param.

        Args:
            key: ``(block, p_index)``.
            value: the value to show; a throttled one is shown by the next flush.
        """
        now = time.monotonic()
        if now - self._last_show.get(key, 0.0) >= SHOW_INTERVAL_S:
            self._last_show[key] = now
            self._pending_show.pop(key, None)
            self.host.show_param(key[0], key[1], value)
        else:
            self._pending_show[key] = value

    def _flush_shows(self) -> None:
        """Show throttled values that are now due (so the last one always lands)."""
        if not self._pending_show:
            return
        now = time.monotonic()
        for key, value in list(self._pending_show.items()):
            if now - self._last_show.get(key, 0.0) >= SHOW_INTERVAL_S:
                self._last_show[key] = now
                del self._pending_show[key]
                self.host.show_param(key[0], key[1], value)

    # -- actions -----------------------------------------------------------

    def _run_action(self, m: Mapping, value: int | bool, ev: MidiEvent) -> None:
        """Carry out one fired mapping.

        Args:
            m: the mapping.
            value: what `Profile.match` returned for it.
            ev: the event that fired it.
        """
        a: Action = m.action
        log.debug("action %s -> %r", a.type, value)
        if isinstance(a, UnknownAction):
            return
        if isinstance(a, ExpressionAction):
            if not self._need_amp():
                return
            self._expression(a, int(value))
            return
        if isinstance(a, EffectAction):
            if not self._need_amp():
                return
            self._effect(a, m, value)
            return
        if isinstance(a, (ChannelSelect, ChannelFromPc, BankAction, ChannelStep)):
            if not self._need_amp():
                return
            self._channel(a, ev)
            return
        if isinstance(a, AmpNative):
            if not AMP_NATIVE_ENABLED:
                self.host.status("MIDI: forwarding to the amp is not enabled")
            elif self._need_amp():
                self._amp_native(a, value, ev)
            return
        self.host.status(f"MIDI: {a.type} is not available yet")

    def _need_amp(self) -> bool:
        """True if the amp is connected; otherwise says so in the status line."""
        if self.host.connected:
            return True
        self.host.status("Amp not connected")
        return False

    def _channel(self, a: Action, ev: MidiEvent) -> None:
        """Channel actions: select / from PC / bank / step.

        Args:
            a: the action.
            ev: the event (the program number for ``channel_from_pc``).
        """
        model = self.host.model
        if isinstance(a, ChannelSelect):
            self._select(a.slot)
        elif isinstance(a, ChannelFromPc):
            if isinstance(ev, ProgramChange):
                number = ev.program
            elif isinstance(ev, ControlChange):
                number = ev.value
            elif isinstance(ev, NoteOn):
                number = ev.note
            else:
                return
            table = a.table if a.table is not None else slots.AMP_PC_TABLE
            n = table.get(number)
            if n is None:
                self.host.status(f"PC {number}: no channel assigned")
            elif not slots.has_slot(n, model):
                # Name it as a four-channel model would ("3A") so the message is clear.
                self.host.status(f"PC {number} → "
                                 f"{slots.slot(n, 0x06).short}: not on this amp")
            else:
                self._select(n)
        else:
            cur = self._current_channel()
            cur = -1 if cur is None else cur
            if isinstance(a, BankAction):
                self._select(slots.bank_switch(cur, a.op, model))
            else:
                self._select(slots.channel_step(cur, a.op, model))

    def _effect(self, a: EffectAction, m: Mapping, value: int | bool) -> None:
        """Switch an effect on/off.

        ``toggle`` flips the known state (the editor's patch when it mirrors the
        amp, else what the router last wrote; unknown counts as off). With a
        ``toggle``-mode trigger the controller's own state is used instead.

        Args:
            a: the action.
            m: its mapping (for the trigger mode).
            value: the matched value (bool for toggle/momentary).
        """
        block, p, _ = effect_param(a.effect)
        if a.op == "on":
            new = 1
        elif a.op == "off":
            new = 0
        elif a.op == "momentary":
            new = int(bool(value))
        elif m.trigger.mode == "toggle":
            new = int(bool(value))
        else:
            cur = self._value_in_ps(block, p)
            if cur is None:
                cur = self._written.get((block, p), 0)
            new = 0 if cur else 1
        self._write(block, p, new, mark_dirty=True)

    def _amp_channel(self) -> int:
        """The amp's MIDI receive channel (0-based): from the SYSTEM settings if
        they have been read, else the factory default 0."""
        ps = getattr(self.host, "system_ps", None)
        if ps is None:
            return 0
        try:
            return max(0, min(15, ps.get_param(load_map()["SYSTEM"], "MIDI_COM", 0)))
        except (KeyError, IndexError):
            return 0

    def _amp_exp_cc(self) -> int:
        """The CC the amp listens to for its expression pedal (SYSTEM settings
        if read, else 47)."""
        ps = getattr(self.host, "system_ps", None)
        if ps is None:
            return DEFAULT_EXP_CC
        try:
            return ps.get_param(load_map()["SYSTEM"], "MIDI_CC", 6)
        except (KeyError, IndexError):
            return DEFAULT_EXP_CC

    def _amp_native(self, a: AmpNative, value: int | bool, ev: MidiEvent) -> None:
        """Send a plain PC / CC to the amp on its receive channel.

        The amp acts on it through its own MIDI map, which is the only way to
        reach per-patch assignments such as FOOT VOLUME. Only these two message
        types can be sent (`Transport.send_raw` refuses anything else).

        Args:
            a: the action.
            value: the matched value (bool for press/toggle, 0-127 for range).
            ev: the event (its program number is used for a PC without a number).
        """
        t = self.host.get_transport()
        if t is None:
            return
        ch = self._amp_channel()
        if a.kind == "pc":
            number = a.number if a.number is not None else (
                ev.program if isinstance(ev, ProgramChange) else None)
            if number is None:
                self.host.status("Amp-native PC needs a program number")
                return
            msg = bytes([0xC0 | ch, number & 0x7F])
        else:
            number = a.number if a.number is not None else self._amp_exp_cc()
            val = (127 if value else 0) if isinstance(value, bool) else int(value)
            msg = bytes([0xB0 | ch, number & 0x7F, max(0, min(127, val))])
        log.debug("amp native %s", msg.hex(" "))
        t.send_raw(msg)

    def _pedal_type(self) -> int:
        """PEDALFX_COM_TYPE of the current patch (cached per slot; 0 if unknown)."""
        slot = self.host.amp_patch
        idx = find_param(PEDAL_TYPE.block, PEDAL_TYPE.param)
        cur = self._value_in_ps(PEDAL_TYPE.block, idx)
        if cur is not None:
            cur = pedal_fx_type(cur)
            if slot is not None:
                self._pedal_types[slot] = cur
            return cur
        return self._pedal_types.get(slot, 0) if slot is not None else 0

    def _expression(self, a: ExpressionAction, value: int) -> None:
        """Drive a parameter from a 0-127 controller value.

        Args:
            a: the action.
            value: the controller value.
        """
        pedal = self._pedal_type() if a.target.kind == "pedal_fx" else None
        block, p, param = resolve_expression(a.target, pedal)
        lo, hi = a.bounds(param)
        out = scale_range(value, lo, hi, a.invert, a.curve)
        if self._written.get((block, p)) == out:
            return                          # nothing new to send
        self._write(block, p, out, mark_dirty=False)
