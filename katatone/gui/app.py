"""Flet application shell: state controller + background-op runner + entrypoint.

The GUI is a *pure consumer* of the core APIs (``katatone.amp``, ``katatone.library``,
``katatone.liveset``, ``katatone.remote``).  All blocking MIDI / network work runs in
``page.run_thread`` threads; they mutate the shared :class:`App` state and then
call :meth:`App._ui` to refresh the widgets (Flet marshals ``page.update()`` to
the main event loop, so it is safe to call from a worker thread).

Run with:  ``python -m katatone.gui.app``
"""
from __future__ import annotations

import logging
import threading
import time
import traceback

import flet as ft

from ..address.map import load_map
from ..amp import slots
from ..amp.live import LiveWriter
from ..library.store import Library
from ..midi.manager import MidiManager
from ..model.paramset import ParamSet
from ..protocol import rc6
from . import theme
from . import (library_pane, midi_pane, patch_editor, statusbar, system_pane)

TAB_PATCH = "PATCH"
TAB_SYSTEM = "SYSTEM"
TAB_MIDI = midi_pane.TAB_MIDI
TAB_LIBRARY = "LIBRARY"
TAB_ORDER = (TAB_PATCH, TAB_SYSTEM, TAB_MIDI, TAB_LIBRARY)

log = logging.getLogger("katatone.gui")

# The amp announces its current patch with a DT1 at this (nibble) address
# (SETUP/COM, PRMID_SETUP_COM_PATCH_NUMBER).
PATCH_NUMBER_NIB = rc6.to_nib(0x0000_0000)
# Notifications for a patch we selected ourselves arrive within ~100 ms (the amp
# sends two); anything this close after our own select is an echo, not news.
SELF_SELECT_WINDOW_S = 0.5
RESYNC_DELAY_S = 0.15


class _StaleRead(Exception):
    """Raised from a read's progress callback when a newer read superseded it."""


class App:
    """Holds all GUI state and orchestrates background operations."""

    def __init__(self, page: ft.Page, *, start_midi: bool = True) -> None:
        """Create the shared GUI state and build the whole window.

        Args:
            page: the Flet page this app runs on (window, event loop and
                ``run_thread`` workers).
            start_midi: open the saved MIDI controllers and start routing
                (tests turn this off, or call :meth:`start_midi` with fakes).
        """
        self.page = page
        # ---- state ----
        self.transport = None
        self.connected = False
        self.patch_number = 0
        self.ps = ParamSet()            # working patch
        self.dirty = False
        self.system_ps: ParamSet | None = None
        self.lib = Library()
        # Popen of the confined Tone Exchange webview (see tonecentral_open).
        self.tc_proc: "subprocess.Popen | None" = None
        # Popen of the analyzer window (see open_analyzer); fully independent.
        self.an_proc: "subprocess.Popen | None" = None
        self.busy = False
        self._map = load_map()
        self.model = slots.DEFAULT_MODEL    # until the amp identifies itself
        # The slot the amp plays (last one selected on it); None until then.
        self.amp_patch: int | None = None
        # Live edit: each change goes straight to the
        # amp's edit buffer, so the sound follows the editor; Write stores it
        # in a slot.  Only while the editor mirrors the amp's current sound,
        # i.e. after a slot was selected on the amp and read from it.
        self.live_ready = False
        self.live = LiveWriter(
            get_transport=lambda: self.transport if self.connected else None,
            paused=lambda: self.busy,
            on_error=self.set_status)
        # ---- following the amp (channel switches from a pedal or the amp) ----
        # Guards `ps` swaps, `dirty` and `recent_writes`, which both the read
        # thread and the MIDI router touch.
        self.state_lock = threading.Lock()
        self._read_gen = 0              # bumped by every new read/resync request
        self._read_lock = threading.Lock()      # one slot read on the wire at a time
        self._resync_timer: threading.Timer | None = None
        self._read_pending = False      # a read is scheduled or running
        # Live writes made while a read is pending, re-applied to the fresh
        # `ps`: {(block, p_index): (value, marks_dirty)}.
        self.recent_writes: dict[tuple[str, int], tuple[int, bool]] = {}
        # True only during Write / Clear / System save (not reads), so MIDI
        # live writes keep flowing while a resync read is in flight.
        self.busy_writing = False
        self._self_select: tuple[int, float] = (-1, 0.0)   # (patch, monotonic time)
        # Card names of the connected amp (never offered as MIDI controllers).
        self._amp_ports: set[str] = set()
        self.midi: MidiManager | None = None
        self._build()
        if start_midi:
            self.start_midi()

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        """Lay out the window: status bar, the five tabbed panes, the progress
        bar and the status line."""
        p = self.page
        p.theme_mode = "dark"
        p.bgcolor = theme.BG
        p.title = "KataTone"
        p.padding = 12
        p.window.width = 1320
        p.window.height = 860
        p.window.min_width = 960
        p.window.min_height = 640
        p.theme = ft.Theme(color_scheme_seed=theme.ACCENT)
        p.dark_theme = ft.Theme(color_scheme_seed=theme.ACCENT)

        self.status_text = ft.Text("Ready.", color=theme.MUTED, size=13)
        self.progress = ft.ProgressBar(visible=False, height=4,
                                          bgcolor=theme.ACCENT)

        top = statusbar.build(self)
        self.editor = patch_editor.PatchEditor(self)
        tabs = ft.Tabs(
            length=4,
            expand=True,
            on_change=self._on_tab_change,
            content=ft.Column(
                expand=True,
                controls=[
                    ft.TabBar([
                        ft.Tab(label=TAB_PATCH, icon=ft.Icons.TUNE),
                        ft.Tab(label=TAB_SYSTEM, icon=ft.Icons.SETTINGS),
                        ft.Tab(label=TAB_MIDI, icon=ft.Icons.PIANO),
                        ft.Tab(label=TAB_LIBRARY, icon=ft.Icons.LIBRARY_MUSIC),
                    ], indicator_color=theme.ACCENT, label_color=theme.ACCENT,
                        unselected_label_color=theme.MUTED,
                        divider_color=theme.PANEL_EDGE,
                        tab_alignment=ft.TabAlignment.START, scrollable=True,
                        label_text_style=ft.TextStyle(
                            size=13, weight=ft.FontWeight.BOLD,
                            letter_spacing=2)),
                    ft.TabBarView([
                        self.editor.root,
                        system_pane.build(self),
                        midi_pane.build(self),
                        library_pane.build(self),
                    ], expand=True),
                ],
            ),
        )
        p.add(ft.Column([
            top,
            tabs,
            self.progress,
            self.status_text,
        ], spacing=8, expand=True))
        self.refresh()

    # ------------------------------------------------------------------ state

    def slots(self) -> list:
        """The amp's patch slots (PN, 1A, 2A, ...) for the connected model.

        Returns:
            ``list[Slot]`` in patch-number order; each slot has an ``index``
            (0..9) and a ``short`` name.
        """
        return slots.slots(self.model)

    def slot_name(self, n: int) -> str:
        """The short display name of amp slot ``n`` (e.g. ``"1A"``)."""
        return slots.slot(n, self.model).short

    def region_for(self, patch_number: int):
        """The address-map region of user patch ``patch_number`` (1-based).

        Returns:
            the ``Region`` (with its ``blocks``) from the loaded address map.
        """
        return self._map[f"PATCH({patch_number + 1})"]

    def _ui(self, fn) -> None:
        """Run a UI mutation (typically from a worker thread), then repaint.

        Args:
            fn: the widget-mutating callable, run before the repaint.
        """
        fn()
        self.page.update()

    def set_status(self, msg: str) -> None:
        """Set the status-line text with a targeted push (no full repaint)."""
        # Targeted push: status text only.  A full page.update() here would
        # re-serialize the entire editor tree on every call.
        self.status_text.value = msg
        self.status_text.update()

    def set_busy(self, busy: bool, msg: str | None = None) -> None:
        """Enter/leave the busy state: show the progress bar, and set the
        status text if given.

        Args:
            busy: True while an amp operation is running.
            msg: the status text to show (skipped if ``None``).
        """
        self.busy = busy
        self.progress.visible = busy
        self.progress.update()
        if msg:
            self.status_text.value = msg
            self.status_text.update()

    def patch_name(self) -> str:
        """The working patch's name, or ``""`` when it has none."""
        return ParamSet.patch_name(self.ps.block("PATCH%COM"))

    # ------------------------------------------------------------ connection

    def connect(self, in_port: str, out_port: str) -> None:
        """Connect to the amp in a background thread.

        Args:
            in_port: the ALSA port name for incoming MIDI (as listed by
                ``list_alsa_ports``).
            out_port: the ALSA port name for outgoing MIDI.
        """
        self._amp_ports = {in_port, out_port}
        self.page.run_thread(self._connect_thread, in_port, out_port)

    def _connect_thread(self, in_port: str, out_port: str) -> None:
        """Worker for :meth:`connect`: open the transport, run the identity
        handshake, then auto-read the current slot (unless the editor has
        unsaved edits).

        Args:
            in_port: the ALSA port name for incoming MIDI.
            out_port: the ALSA port name for outgoing MIDI.
        """
        from ..amp.connect import disconnect, handshake
        from ..protocol.transport import make_alsa_transport
        self.set_busy(True, "Connecting…")
        t = None
        try:
            t = make_alsa_transport(in_port, out_port)
            t.start()
            import time
            # Amp USB-MIDI rawmidi port needs to settle after open before it
            # answers the identity handshake (matches the CLI connect path).
            time.sleep(0.6)
            dev = handshake(t)
            self.transport = t
            self.connected = True
            self.model = slots.model_code(getattr(t, "identity_reply", None))
            t.on_unsolicited = self._on_unsolicited
            self._adopt_amp_patch(t)
            if self.patch_number not in [x.index for x in self.slots()]:
                self.patch_number = 0
            self.set_status(f"Connected to {slots.model_name(self.model)} "
                            f"(deviceId=0x{dev:02X})")
            self.refresh()
            if self.dirty:
                # Offline edits: keep them (Write stores them; Read would
                # replace them).  Live edit starts once a slot is read.
                self.set_status(f"Connected to {slots.model_name(self.model)} — "
                                f"the editor has unsaved edits: Write stores them "
                                f"in {self.slot_name(self.patch_number)}, Read "
                                f"loads the slot.")
            else:
                # Auto-load the current slot (best effort).
                import time
                time.sleep(0.5)  # let the amp settle after editor-mode DT1
                self.read_patch(self.patch_number)
        except Exception as e:                       # noqa: BLE001
            if t is not None:
                try:
                    t.stop()
                except Exception:                    # noqa: BLE001
                    pass
            self.transport = None
            self.connected = False
            self._amp_ports = set()
            self.set_status(f"Connect failed: {e}")
            self.refresh()

    def disconnect(self) -> None:
        """Disconnect from the amp in a background thread (no-op when not
        connected)."""
        if self.transport is None:
            return
        self.page.run_thread(self._disconnect_thread)

    def _disconnect_thread(self) -> None:
        """Worker for :meth:`disconnect`: stop the transport and clear all
        connection state."""
        from ..amp.connect import disconnect
        self.live.clear()
        self._cancel_resync()
        self.transport.on_unsolicited = None
        try:
            disconnect(self.transport)
        except Exception:                            # noqa: BLE001
            pass
        self.transport.stop()
        self.connected = False
        self.transport = None
        self._amp_ports = set()
        self.amp_patch = None
        self.system_ps = None  # belonged to this amp; re-read on next visit
        self.set_live_ready(False)
        self.set_status("Disconnected.")
        self.refresh()

    # ------------------------------------------------------- patch operations

    def _require_connected(self) -> bool:
        """Veto an amp operation when no transport is connected.

        Returns:
            True when connected; False after showing "Not connected." in the
            status line.
        """
        if not self.connected or self.transport is None:
            self.set_status("Not connected.")
            return False
        return True

    def read_patch(self, n: int) -> None:
        """Read amp slot ``n`` into the editor in a background thread.

        Supersedes any resync still pending or in flight.

        Args:
            n: the amp slot index (0-based).
        """
        if not self._require_connected():
            return
        gen = self._new_read_request()
        self.page.run_thread(self._read_thread, n, gen)

    def _new_read_request(self) -> int:
        """Start a new read generation, cancelling any older read or timer.

        Returns:
            The generation number the new read must still own when it finishes.
        """
        with self.state_lock:
            self._read_gen += 1
            self._read_pending = True
            self.recent_writes.clear()      # they belonged to the old channel
            gen = self._read_gen
        self._cancel_resync()
        return gen

    def _cancel_resync(self) -> None:
        """Cancel a debounced resync that has not started yet."""
        t, self._resync_timer = self._resync_timer, None
        if t is not None:
            t.cancel()

    def _read_thread(self, n: int, gen: int, select: bool = True) -> None:
        """Read slot ``n``; afterwards the editor, the slot and the amp's
        current sound are the same patch, which is what makes live edit
        meaningful.

        The result is published only if ``gen`` is still the newest request:
        a switch that arrives mid-read bumps the generation, the progress
        callback notices at the next block and abandons the read.

        Args:
            n: the amp slot index (0-based).
            gen: the generation this read was started for.
            select: select the slot on the amp first (user-initiated reads);
                False when the amp already plays it (resync after a switch).
        """
        from ..amp.read import read_patch
        from ..amp.write import select_patch

        def progress(done: int, total: int) -> None:
            if gen != self._read_gen:
                raise _StaleRead()
            self._progress(done, total, "Reading")

        with self._read_lock:
            if gen != self._read_gen:
                return                      # superseded while waiting
            self.set_busy(True, f"Reading {self.slot_name(n)}…")
            try:
                if select:
                    self._note_self_select(n)
                    select_patch(self.transport, n)
                    self.amp_patch = n
                ps = read_patch(self.transport, n, progress=progress)
                with self.state_lock:
                    if gen != self._read_gen:
                        return              # a newer request owns the editor now
                    dirty = self._apply_recent_writes(ps, n)
                    self.ps = ps
                    self.dirty = dirty
                    self.patch_number = n
                    self._read_pending = False
                self._synced()
                self.live_ready = True
                self.set_status(f"Read {self.slot_name(n)}: {self.patch_name()!r}")
            except _StaleRead:
                return                      # the newer read does the refresh
            except Exception as e:                   # noqa: BLE001
                if gen == self._read_gen:
                    with self.state_lock:
                        self._read_pending = False
                    self.set_status(f"Read failed: {e}")
            finally:
                if gen == self._read_gen:
                    self.refresh()

    def _apply_recent_writes(self, ps: ParamSet, n: int) -> bool:
        """Re-apply live writes made while the read was pending to ``ps``.

        Called with ``state_lock`` held. The slot read finished after the pedal
        had already changed the edit buffer, so without this the editor would
        show stale values for those parameters.

        Args:
            ps: the freshly read patch.
            n: the slot it was read from.

        Returns:
            True if any re-applied write counts as an edit (marks dirty).
        """
        region = self.region_for(n)
        dirty = False
        for (block, p_index), (value, marks_dirty) in self.recent_writes.items():
            ps.set_param(region, block, p_index, value)
            dirty = dirty or marks_dirty
        self.recent_writes.clear()
        return dirty

    def note_live_write(self, block: str, p_index: int, value: int,
                        marks_dirty: bool) -> None:
        """Remember a live write made while a read is pending (see
        :meth:`_apply_recent_writes`); a no-op otherwise.

        Args:
            block: the parameter's block name.
            p_index: the parameter's index within the block.
            value: the value written to the amp's edit buffer.
            marks_dirty: whether the write counts as an unsaved edit.
        """
        with self.state_lock:
            self._note_live_write_locked(block, p_index, value, marks_dirty)

    def _note_live_write_locked(self, block: str, p_index: int, value: int,
                                marks_dirty: bool) -> None:
        """:meth:`note_live_write` for a caller already holding ``state_lock``."""
        if self._read_pending:
            self.recent_writes[(block, p_index)] = (value, marks_dirty)

    # ------------------------------------------------------------ MIDI router
    # The App is the router's host (see katatone.midi.router.RouterHost). These
    # run on the router's worker thread, never on the UI thread.

    def start_midi(self, manager: MidiManager | None = None) -> None:
        """Start routing: open the saved controllers (they work before the amp
        is connected; actions then report "Amp not connected").

        Args:
            manager: a prebuilt manager (tests pass one with fake inputs).
        """
        if self.midi is not None:
            return
        self.midi = manager or MidiManager(self, exclude_cards=lambda: set(self._amp_ports))
        self.midi.start()
        pane = getattr(self, "midi_pane", None)
        if pane is not None:
            pane.attach(self.midi)

    def stop_midi(self) -> None:
        """Close the controllers and stop the router (on window close)."""
        midi, self.midi = self.midi, None
        pane = getattr(self, "midi_pane", None)
        if pane is not None:
            pane.detach()
        if midi is not None:
            midi.stop()

    def get_transport(self):
        """The amp transport, or None when not connected."""
        return self.transport if self.connected else None

    def status(self, msg: str) -> None:
        """RouterHost spelling of :meth:`set_status`."""
        self.set_status(msg)

    def select_channel(self, n: int, source: str) -> None:
        """Switch the amp to slot ``n`` for the router, then follow it.

        Runs on the router thread so quick presses stay in order. The select
        does not wait for the amp to settle: nothing is read straight away, and
        the resync read starts after a debounce.

        Args:
            n: the patch number.
            source: where the request came from ("pedal"), for the status line.
        """
        from ..amp.write import select_patch
        t = self.get_transport()
        if t is None:
            return
        self._note_self_select(n)
        select_patch(t, n, settle=0.0)
        self.on_channel_selected(n, source)

    def apply_live_value(self, block: str, p_index: int, value: int,
                         mark_dirty: bool) -> None:
        """The router wrote an edit-buffer parameter: mirror it in the editor.

        The editor's patch is updated only while it mirrors the amp
        (``live_ready``); while a slot read is pending the value is remembered
        and replayed onto the fresh patch (see :meth:`_apply_recent_writes`).

        Args:
            block: the parameter's block name.
            p_index: the parameter's index within the block.
            value: the value written.
            mark_dirty: whether the write counts as an unsaved edit.
        """
        dirty_now = False
        with self.state_lock:
            self._note_live_write_locked(block, p_index, value, mark_dirty)
            if self.live_ready:
                self.ps.set_param(self.region_for(self.patch_number),
                                  block, p_index, value)
                dirty_now = mark_dirty
        if dirty_now:
            self.mark_dirty()

    def show_param(self, block: str, p_index: int, value: int) -> None:
        """Move the on-screen widget of a parameter the router changed.

        Args:
            block: the parameter's block name.
            p_index: the parameter's index within the block.
            value: the new value.
        """
        if self.live_ready:
            self.editor.show_value(block, p_index, value)

    # ------------------------------------------------------- following the amp

    def _note_self_select(self, n: int) -> None:
        """Record that we are about to select slot ``n`` on the amp, so the
        notification the amp sends back is recognised as our own echo."""
        self._self_select = (n, time.monotonic())

    def _adopt_amp_patch(self, t) -> None:
        """Read which patch the amp is on and adopt it (instead of forcing
        Panel on connect). Falls back silently to the previous behaviour.

        Args:
            t: the connected transport.
        """
        try:
            n = ParamSet.raw_value(2, t.rq1(0x0000_0000, 2))
        except Exception as e:                       # noqa: BLE001
            log.warning("could not read the amp's patch number: %s", e)
            return
        if n not in [x.index for x in self.slots()]:
            return
        self.amp_patch = n
        if not self.dirty:
            self.patch_number = n

    def _on_unsolicited(self, nib: int, payload: bytes) -> None:
        """Transport hook (RX thread): the amp announced its current patch.

        Everything else the amp volunteers (echoes of our own writes included)
        is ignored. Returns quickly; the real work runs on a worker thread.

        Args:
            nib: the nibble address of the DT1.
            payload: its data bytes.
        """
        if nib != PATCH_NUMBER_NIB or len(payload) < 2:
            return
        n = ParamSet.raw_value(2, payload)
        if n not in [x.index for x in self.slots()]:
            return
        sel_n, sel_t = self._self_select
        if n == sel_n and time.monotonic() - sel_t < SELF_SELECT_WINDOW_S:
            return                          # echo of a select we sent
        if n == self.amp_patch:
            return                          # nothing new (the amp sends two)
        self.page.run_thread(self.on_amp_patch_changed, n)

    def on_amp_patch_changed(self, n: int) -> None:
        """The amp switched channel by itself (its own buttons): follow it.

        Args:
            n: the patch the amp now plays.
        """
        self.amp_patch = n
        self.set_live_ready(False)
        self.set_status(f"Amp switched to {self.slot_name(n)} — reading…")
        self.editor.refresh_channels()
        self.resync(n)

    def on_channel_selected(self, n: int, source: str = "pedal") -> None:
        """The router selected slot ``n`` on the amp: follow it in the editor.

        The select itself was already sent (and recorded with
        :meth:`_note_self_select`); this repaints and re-reads.

        Args:
            n: the slot that was selected.
            source: where the request came from, for the status line.
        """
        self.amp_patch = n
        self.set_live_ready(False)
        discarded = " — unsaved edits discarded" if self.dirty else ""
        self.set_status(f"Switched to {self.slot_name(n)} from the {source}"
                        f"{discarded}")
        self.editor.refresh_channels()
        self.resync(n)

    def resync(self, n: int, delay: float = RESYNC_DELAY_S) -> None:
        """Load slot ``n`` into the editor after the amp changed channel.

        Debounced: a burst of switches produces one read, for the last one.
        A read already in flight is abandoned at its next block.

        Args:
            n: the slot the amp now plays.
            delay: seconds to wait for further switches before reading.
        """
        if not self.connected:
            return
        gen = self._new_read_request()
        if delay <= 0:
            self.page.run_thread(self._read_thread, n, gen, False)
            return
        timer = threading.Timer(delay, self.page.run_thread,
                                args=(self._read_thread, n, gen, False))
        timer.daemon = True
        self._resync_timer = timer
        timer.start()

    def write_patch(self) -> None:
        """Write the working patch to its current amp slot in a background
        thread."""
        if not self._require_connected():
            return
        n = self.patch_number
        ps = self.ps
        self.page.run_thread(self._write_thread, n, ps)

    def _write_thread(self, n: int, ps: ParamSet) -> None:
        """Worker for :meth:`write_patch`.

        Args:
            n: the amp slot index (0-based).
            ps: the ``ParamSet`` to store in the slot.
        """
        from ..amp.write import write_patch
        self.set_busy(True, f"Writing {self.slot_name(n)}…")
        self.busy_writing = True
        try:
            write_patch(self.transport, n, ps, self.patch_name() or None)
            self.dirty = False
            self._synced()
            self.set_status(f"Wrote {self.slot_name(n)}: {self.patch_name()!r}")
        except Exception as e:                       # noqa: BLE001
            self.set_status(f"Write failed: {e}")
        finally:
            self.busy_writing = False
            self.refresh()

    def clear_patch(self, n: int) -> None:
        """Clear amp slot ``n`` on the amp in a background thread.

        Args:
            n: the amp slot index (0-based).
        """
        if not self._require_connected():
            return
        self.page.run_thread(self._clear_thread, n)

    def _clear_thread(self, n: int) -> None:
        """Worker for :meth:`clear_patch`.

        Args:
            n: the amp slot index (0-based).
        """
        from ..amp.write import clear_patch
        self.set_busy(True, f"Clearing {self.slot_name(n)}…")
        self.busy_writing = True
        try:
            clear_patch(self.transport, n)
            self.set_status(f"Cleared {self.slot_name(n)}.")
        except Exception as e:                       # noqa: BLE001
            self.set_status(f"Clear failed: {e}")
        finally:
            self.busy_writing = False
            self.refresh()

    def select_patch(self, n: int) -> None:
        """Select amp slot ``n`` on the amp in a background thread.

        Args:
            n: the amp slot index (0-based).
        """
        if not self._require_connected():
            return
        self.page.run_thread(self._select_thread, n)

    def _select_thread(self, n: int) -> None:
        """Worker for :meth:`select_patch`.

        Args:
            n: the amp slot index (0-based).
        """
        from ..amp.write import select_patch
        try:
            self._note_self_select(n)
            select_patch(self.transport, n)
            # The amp now plays the stored slot: the editor only still mirrors
            # it when it showed that slot without unsaved edits.
            if n != self.patch_number or self.dirty:
                self.set_live_ready(False)
            self.amp_patch = n
            self.patch_number = n
            self.set_status(f"Selected {self.slot_name(n)} on the amp.")
        except Exception as e:                       # noqa: BLE001
            self.set_status(f"Select failed: {e}")
        finally:
            self.refresh()

    def set_mute(self, mute: bool) -> None:
        """Mute or unmute the amp's preview in a background thread.

        Args:
            mute: True to mute the preview, False to unmute it.
        """
        if not self._require_connected():
            return
        self.page.run_thread(self._mute_thread, mute)

    def _mute_thread(self, mute: bool) -> None:
        """Worker for :meth:`set_mute`.

        Args:
            mute: True to mute the preview, False to unmute it.
        """
        from ..amp.write import set_preview_mute
        try:
            set_preview_mute(self.transport, mute)
            self.set_status(f"Preview {'muted' if mute else 'unmuted'}.")
        except Exception as e:                       # noqa: BLE001
            self.set_status(f"Mute failed: {e}")

    # ------------------------------------------------------------- param edit

    def set_live_ready(self, ready: bool) -> None:
        """Start/stop live edit; losing sync drops any queued sends."""
        self.live_ready = ready
        if not ready:
            self.live.clear()

    def _live(self) -> bool:
        """True when editor changes go straight to the amp's edit buffer."""
        return self.live_ready and self.connected

    def param_changed(self, block: str, p_index: int, value: int) -> None:
        """An editor control changed a patch parameter.

        Args:
            block: the parameter's block name (e.g. ``"AMP"``).
            p_index: the parameter's index within the block.
            value: the new value, already clamped to the parameter's min..max.
        """
        if self._live():
            self.live.queue_param(block, p_index, value)

    def name_changed(self, name: str) -> None:
        """An editor control changed the patch name."""
        if self._live():
            self.live.queue_name(name)

    def _synced(self) -> None:
        """Editor and amp now agree (after Read / Write): nothing pending."""
        self.live.clear()

    def mark_dirty(self) -> None:
        """Flag the patch modified and reflect that in the header label.

        Called on every param change — including 60 Hz knob pan events — so it
        must only push the small dirty label.  A full ``page.update()`` here
        would serialize the entire editor tree on every knob tick and saturate
        the client connection (the knob then feels frozen/unresponsive).
        """
        self.dirty = True
        self.editor.dirty_label.value = "● unsaved changes"
        self.editor.dirty_label.update()

    def refresh(self) -> None:
        """Full refresh: patch list, editor, system, status widgets.

        Every amp operation ends with a refresh, so this also ends the busy
        state (progress bar; it holds live-edit sends while the wire is used).
        """
        self.busy = False
        self.progress.visible = False
        self.editor.refresh()
        if hasattr(self, "system") and self.system is not None:
            self.system.refresh()
        statusbar.refresh(self)
        midi_pane.refresh(self)
        self.page.update()

    def _progress(self, done: int, total: int, verb: str) -> None:
        """Update the status line and progress bar while a read/write runs.

        Args:
            done: blocks already transferred.
            total: blocks to transfer in total (0 leaves the bar at 0).
            verb: the status verb (e.g. "Reading").
        """
        # Called per block from the worker thread during read/write (80x per
        # patch read).  Targeted pushes keep the wire free so the UI stays
        # responsive while a read is in flight — a full page.update() per block
        # floods the client with ~3 KB tree patches and stalls all interaction.
        self.status_text.value = f"{verb}… block {done}/{total}"
        self.status_text.update()
        self.progress.value = done / total if total else 0
        self.progress.update()

    # ------------------------------------------------------- system region

    def _on_tab_change(self, e: ft.ControlEvent) -> None:
        """Read the SYSTEM settings the first time their tab is shown while
        connected (later visits keep any unsaved edits; the tab's Read
        button re-reads).

        Args:
            e: the tab-change event (``e.control.selected_index``).
        """
        tabs = e.control
        if (tabs.selected_index == TAB_ORDER.index(TAB_SYSTEM)
                and self.connected and self.transport is not None
                and self.system_ps is None and not self.busy):
            self.load_system()

    def load_system(self) -> None:
        """Read the amp's SYSTEM region into the editor in a background
        thread."""
        if not self._require_connected():
            return
        self.page.run_thread(self._system_thread, "load")

    def save_system(self) -> None:
        """Write the SYSTEM region from the editor to the amp in a background
        thread."""
        if not self._require_connected():
            return
        self.page.run_thread(self._system_thread, "save")

    def _system_thread(self, op: str) -> None:
        """Worker for :meth:`load_system` / :meth:`save_system`.

        Args:
            op: ``"load"`` to read the region into the editor, ``"save"`` to
                write it back to the amp.
        """
        from ..amp.read import read_region
        from ..amp.write import _dt1_chunked
        self.set_busy(True, f"{'Reading' if op == 'load' else 'Writing'} system…")
        self.busy_writing = op == "save"
        try:
            if op == "load":
                self.system_ps = read_region(
                    self.transport, "SYSTEM",
                    progress=lambda d, tot: self._progress(d, tot, "Reading system"))
                self.set_status("Read SYSTEM region.")
            else:
                ps = self.system_ps
                region = self._map["SYSTEM"]
                for b in region.blocks:
                    data = ps.block(ParamSet.block_key(region.name, b.name))
                    if data:
                        _dt1_chunked(self.transport, b.base_addr, data)
                self.set_status("Wrote SYSTEM region.")
        except Exception as e:                       # noqa: BLE001
            self.set_status(f"System {'read' if op == 'load' else 'write'} failed: {e}")
        finally:
            self.busy_writing = False
            self.refresh()

    # ------------------------------------------------------- library / tone central

    def library_refresh(self) -> None:
        """Rebuild the library pane and repaint the page."""
        library_pane.refresh(self)
        self.page.update()

    def load_into_editor(self, ps: ParamSet, label: str) -> None:
        """Put a librarian patch into the editor (unsaved: Write stores it in
        the currently selected amp slot).

        Args:
            ps: the ``ParamSet`` to load.
            label: the patch's display name, shown in the status line.
        """
        self.ps = ps
        self.dirty = True
        self.set_live_ready(False)
        self.set_status(f"Loaded {label} into the editor — Write to store it "
                        f"in {self.slot_name(self.patch_number)}.")
        self.refresh()

    def read_amp_patch(self, n: int, on_done) -> None:
        """Read amp user patch ``n`` in the background; ``on_done(ps)``.

        Args:
            n: the amp slot index (0-based).
            on_done: callback invoked with the read ``ParamSet`` (not called
                on failure).
        """
        if not self._require_connected():
            return
        self.page.run_thread(self._read_amp_thread, n, on_done)

    def _read_amp_thread(self, n: int, on_done) -> None:
        """Worker for :meth:`read_amp_patch`.

        Args:
            n: the amp slot index (0-based).
            on_done: callback invoked with the read ``ParamSet``.
        """
        from ..amp.read import read_patch
        self.set_busy(True, f"Reading {self.slot_name(n)} from the amp…")
        try:
            ps = read_patch(self.transport, n,
                            progress=lambda d, tot: self._progress(d, tot, "Reading"))
            on_done(ps)
        except Exception as e:                       # noqa: BLE001
            self.set_status(f"Read failed: {e}")
        finally:
            self.set_busy(False)

    def write_amp_patch(self, n: int, ps: ParamSet, name: str | None) -> None:
        """Write a librarian patch straight into amp user patch ``n``.

        Args:
            n: the amp slot index (0-based).
            ps: the ``ParamSet`` to store.
            name: the patch name to store, or ``None`` to keep the patch's
                own name.
        """
        if not self._require_connected():
            return
        self.page.run_thread(self._write_amp_thread, n, ps, name)

    def _write_amp_thread(self, n: int, ps: ParamSet, name: str | None) -> None:
        """Worker for :meth:`write_amp_patch`.

        Args:
            n: the amp slot index (0-based).
            ps: the ``ParamSet`` to store.
            name: the patch name to store, or ``None`` to keep the patch's
                own name.
        """
        from ..amp.write import write_patch
        self.set_busy(True, f"Writing {name or 'patch'!r} to {self.slot_name(n)}…")
        self.busy_writing = True
        try:
            write_patch(self.transport, n, ps, name or None)
            self.set_status(f"Wrote {name or 'patch'!r} to {self.slot_name(n)}.")
        except Exception as e:                       # noqa: BLE001
            self.set_status(f"Write failed: {e}")
        finally:
            self.busy_writing = False
            self.set_busy(False)

    def import_liveset(self, path: str) -> None:
        """Import a ``.tsl`` liveset file into the library in a background
        thread.

        Args:
            path: the ``.tsl`` file to import.
        """
        self.page.run_thread(self._import_thread, path)

    def _import_thread(self, path: str) -> None:
        """Worker for :meth:`import_liveset`.

        Args:
            path: the ``.tsl`` file to import.
        """
        try:
            p = self.lib.import_file(path)
            self.set_status(f"Imported -> {p}")
            self.library_refresh()
        except Exception as e:                       # noqa: BLE001
            self.set_status(f"Import failed: {e}")

    def tonecentral_refresh(self) -> None:
        """Refresh the Library pane's Tone Central status (and its list of
        downloaded livesets), then repaint the page."""
        library_pane.refresh_tone(self)
        self.page.update()

    def _tc_spawn(self, args: list[str], busy_msg: str) -> None:
        """Launch the webview subprocess and refresh when it exits.

        The subprocess's stderr is written to ``webview.log`` in the
        app's data dir (one log per session), so crashes and download
        saves are inspectable after the fact; an abnormal exit also
        surfaces the log's tail in the status bar, so a broken webview
        can never silently fall back to the system browser without a
        trace (see :func:`katatone.remote.webview.main`).

        Args:
            args: command-line arguments for :mod:`katatone.remote.webview`.
            busy_msg: status text while the window is running.
        Returns:
            True when a new webview process was started.
        """
        import subprocess
        import sys
        from ..remote.webview import data_dir
        if self.tc_proc is not None and self.tc_proc.poll() is None:
            self.set_status("Tone Exchange window is already open.")
            self.tonecentral_refresh()
            return False
        try:
            log_file = open(data_dir() / "webview.log", "wb")
        except OSError as e:                         # noqa: BLE001
            self.set_status(f"Tone Exchange failed to start: {e}")
            return False
        try:
            self.tc_proc = subprocess.Popen(
                [sys.executable, "-m", "katatone.remote.webview"] + args,
                stdout=subprocess.DEVNULL, stderr=log_file)
        except Exception as e:                       # noqa: BLE001
            self.tc_proc = None
            log_file.close()
            self.set_status(f"Tone Exchange failed to start: {e}")
            return False
        self.set_status(busy_msg)
        self.tonecentral_refresh()

        def poll_downloads(proc: "subprocess.Popen") -> None:
            """Refresh the pane while the window runs, so downloaded
            patches are listed as they arrive."""
            while proc.poll() is None:
                time.sleep(1.0)
                try:
                    self.page.run_thread(self.tonecentral_refresh)
                except Exception:                    # noqa: BLE001
                    return  # page gone (app shutting down)

        threading.Thread(target=poll_downloads, args=(self.tc_proc,),
                         daemon=True).start()

        def watchdog(proc: "subprocess.Popen") -> None:
            """Report the exit, then refresh the Tone Central pane."""
            proc.wait()
            try:
                log_file.close()
            except OSError:
                pass

            def report() -> None:
                """Surface an abnormal exit in the status bar."""
                if proc.returncode not in (0, None):
                    self.set_status(self._tc_exit_status(proc.returncode))
                self.tonecentral_refresh()

            try:
                self.page.run_thread(report)
            except Exception:                        # noqa: BLE001
                pass  # page gone (app shutting down)

        threading.Thread(target=watchdog, args=(self.tc_proc,),
                         daemon=True).start()
        return True

    def _tc_exit_status(self, code: int) -> str:
        """Build a status line for an abnormal webview exit.

        Args:
            code: the subprocess's exit code.
        Returns:
            A human-readable status line with the webview log's tail.
        """
        from pathlib import Path
        from ..remote.webview import data_dir
        tail = ""
        try:
            lines = Path(data_dir() / "webview.log").read_text(
                encoding="utf-8", errors="replace").strip().splitlines()
            tail = " ".join(l for l in lines[-2:] if l.strip())
        except OSError:
            pass
        msg = f"Tone Exchange window exited (code {code})"
        if tail:
            msg += f": {tail}"
        return msg

    def tonecentral_open(self, url: str) -> None:
        """Open the BOSS Tone Exchange in a confined webview window.

        Runs :mod:`katatone.remote.webview` in a separate process (it
        owns its own GTK main loop, which Flet's window cannot host on
        Linux).  The window is confined to the Roland/BOSS domains, so
        the user can sign in with their BOSS ID, search the remote
        library and download livesets.

        Args:
            url: the page to start on (default: the Katana Gen 3 page,
                see :data:`katatone.remote.webview.DEFAULT_URL`).
        """
        self._tc_spawn([url], f"Opening Tone Exchange: {url}")

    def open_analyzer(self) -> None:
        """Open the analyzer window (level meter and spectrum of a sound-card
        input) in its own process, unless it is already open.

        The window shares nothing with the editor; the amp's card name is
        only passed so the right input is preselected.  Its output goes to
        ``analyzer.log`` in the data dir.
        """
        import subprocess
        import sys
        from ..remote.webview import data_dir
        if self.an_proc is not None and self.an_proc.poll() is None:
            self.set_status("The analyzer window is already open.")
            return
        cmd = [sys.executable, "-m", "katatone.gui.analyzer"]
        card = getattr(getattr(self, "sb_amp", None), "value", None)
        if card:
            cmd += ["--card", card]
        try:
            with open(data_dir() / "analyzer.log", "wb") as log_file:
                self.an_proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=log_file)
        except OSError as e:
            self.an_proc = None
            self.set_status(f"Analyzer failed to start: {e}")
            return
        self.set_status("Analyzer window opened.")

    def stop_analyzer(self) -> None:
        """Close the analyzer window if it is open."""
        proc, self.an_proc = self.an_proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()


def main(argv: list[str] | None = None) -> None:
    """Parse the CLI flags and run the Flet app.

    Args:
        argv: the argument list (defaults to ``sys.argv[1:]``); unknown
            Flet args are tolerated.
    """
    import argparse
    import os
    import sys
    # Debug logging is OFF by default; pass -v/--verbose to trace execution
    # (mirrors the CLI).  Logs go to stderr so UI/JSON stay on stdout.
    from ..cli import _setup_logging
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="katatone.gui", add_help=False)
    parser.add_argument("-v", "--verbose", action="store_true", dest="verbose",
                        default=False)
    # Tolerate any extra/unknown Flet args without crashing.
    opts, _ = parser.parse_known_args(argv)
    _setup_logging(opts.verbose)
    apps: list[App] = []
    ft.run(main=lambda page: apps.append(App(page)))
    for app in apps:
        app.stop_midi()
        app.stop_analyzer()
    sys.stdout.flush()
    # skip atexit: alsa-lib static cleanup segfaults on PipeWire hosts
    os._exit(0)


if __name__ == "__main__":
    main()
