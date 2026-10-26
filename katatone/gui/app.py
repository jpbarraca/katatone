"""Flet application shell: state controller + background-op runner + entrypoint.

The GUI is a *pure consumer* of the core APIs (``katatone.amp``, ``katatone.library``,
``katatone.liveset``, ``katatone.remote``).  All blocking MIDI / network work runs in
``page.run_thread`` threads; they mutate the shared :class:`App` state and then
call :meth:`App._ui` to refresh the widgets (Flet marshals ``page.update()`` to
the main event loop, so it is safe to call from a worker thread).

Run with:  ``python -m katatone.gui.app``
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import traceback
import urllib.parse

import flet as ft

from ..address.map import load_map
from .. import __version__, auth as auth_mod
from .. import rofs
from ..amp import slots
from ..amp.live import LiveWriter
from ..library.store import Library
from ..midi.manager import MidiManager
from ..model.paramset import ParamSet
from ..protocol import rc6
from . import theme
from .history import EditHistory
from .model import display_label
from .headless import HeadlessPage
from . import (library_pane, login, midi_pane, patch_editor, statusbar, system_pane)

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
# Auto-connect: how often the amp's MIDI port is looked for (and its loss
# noticed), and how long to wait after a failed attempt before the next one.
AUTO_CONNECT_INTERVAL_S = 2.0
AUTO_CONNECT_RETRY_S = 6.0
# --reduce-writes: how long the file systems stay writable after start-up.
RW_SECONDS = 120.0


#: Whether Roland data writes are allowed over Bluetooth (``--no-ble-writes`` turns them off).
_ble_writes = True

#: Whether the helper processes' output is kept in log files (``--log``).
_log_files = False


def _log_path(name: str):
    """Where a helper process writes its log, or None when ``--log`` is off.

    That is the data dir, or (with ``--reduce-writes``) the temporary
    directory, which is not on the read-only file system.
    """
    import pathlib
    import tempfile
    if not _log_files:
        return None
    if rofs.enabled():
        return pathlib.Path(tempfile.gettempdir()) / f"katatone-{name}"
    from ..remote.webview import data_dir
    return data_dir() / name


def _open_log(name: str):
    """Open the log file ``name`` for a helper process's stderr.

    Returns:
        The open binary file, or None when logs are off.
    Raises:
        OSError: if it cannot be created.
    """
    path = _log_path(name)
    return open(path, "wb") if path else None


class _StaleRead(Exception):
    """Raised from a read's progress callback when a newer read superseded it."""


class App:
    """Holds all GUI state and orchestrates background operations."""

    def __init__(self, page: ft.Page | None = None, *, start_midi: bool = True,
                 wifi: bool = False) -> None:
        """Create the shared GUI state and build the whole window.

        Args:
            page: the Flet page this app runs on (window, event loop and
                ``run_thread`` workers); None runs it headless, with no UI
                (see :meth:`attach`).
            start_midi: open the saved MIDI controllers and start routing
                (tests turn this off, or call :meth:`start_midi` with fakes).
            wifi: offer the Wi-Fi settings (web mode on a device that manages
                its own network, see ``--wifi``).
        """
        self.page = page if page is not None else HeadlessPage()
        self.wifi_enabled = wifi
        self.last_status = "Ready."
        # Lets the session that shows the app open the sound-card tools in new
        # tabs without logging in again (see attach / _open_web_session).
        self.launch_tokens = auth_mod.LaunchTokens()
        self.launch_token: str | None = None
        # ---- state ----
        self.transport = None
        self.connected = False
        self.patch_number = 0
        self.ps = ParamSet()            # working patch
        self.dirty = False
        self.history = EditHistory()    # undo / redo of the edits to `ps`
        self.system_ps: ParamSet | None = None
        self.lib = Library()
        # Popen of the confined Tone Exchange webview (see tonecentral_open).
        self.tc_proc: "subprocess.Popen | None" = None
        # Popen of the analyzer window (see open_analyzer); fully independent.
        self.an_proc: "subprocess.Popen | None" = None
        # Popen of the guitar tuner window (see open_tuner); likewise independent.
        self.tuner_proc: "subprocess.Popen | None" = None
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
        # ---- keeping the amp connected ----
        self.auto_connect = True        # look for the amp and reconnect to it
        self.amp_port: str | None = None            # the card we last connected to
        self._connecting = False
        self._auto_retry_at = 0.0
        self._auto_thread: threading.Thread | None = None
        self._auto_stop = threading.Event()
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
        p.on_keyboard_event = self._on_key

        self.status_text = ft.Text(self.last_status, color=theme.MUTED, size=13)
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

    # ------------------------------------------------------- browser sessions

    @property
    def ui_attached(self) -> bool:
        """True while a window or browser session shows the app."""
        return not isinstance(self.page, HeadlessPage)

    def attach(self, page: ft.Page) -> None:
        """Show the app on ``page`` (a browser session that logged in).

        The state (amp link, controllers, patch) is kept; only the widgets are
        built afresh.  A session that was attached before is told it lost the
        app.

        Args:
            page: the session's page.
        """
        old = self.page
        if old is page:
            return
        self._stop_midi_pane()              # the old widgets' pump, headless or not
        self.launch_tokens.revoke(self.launch_token)
        self.launch_token = self.launch_tokens.issue()
        if self.ui_attached:
            try:
                old.clean()
                old.add(ft.Text("KataTone is open in another session.",
                                color=theme.MUTED))
                old.update()
            except Exception:                        # noqa: BLE001
                pass            # that session is gone
        self.page = page
        self._rebuild_ui()

    def detach(self, page: ft.Page | None = None) -> None:
        """Stop showing the app (the session ended); everything keeps running.

        Args:
            page: the session that ended; ignored if it is no longer the one
                attached (it was replaced by a newer session).
        """
        if page is not None and page is not self.page:
            return
        if not self.ui_attached:
            return
        self._stop_midi_pane()
        self.launch_tokens.revoke(self.launch_token)
        self.launch_token = None
        self.page = HeadlessPage()
        self._rebuild_ui()

    def _stop_midi_pane(self) -> None:
        """Stop the MIDI pane's UI pump (it belongs to the old widgets)."""
        pane = getattr(self, "midi_pane", None)
        if pane is not None:
            pane.detach()

    def _rebuild_ui(self) -> None:
        """Build the widgets again on the current page and reconnect the MIDI
        pane to the running controllers."""
        self.page.clean()
        self._build()
        pane = getattr(self, "midi_pane", None)
        if pane is not None and self.midi is not None:
            pane.attach(self.midi)

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
        self.last_status = msg
        if not self.ui_attached:
            return
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
        if msg:
            self.last_status = msg
        if not self.ui_attached:
            return
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
        self.amp_port = in_port
        self._connecting = True
        self.page.run_thread(self._connect_thread, in_port, out_port)

    def _connect_thread(self, in_port: str, out_port: str) -> None:
        """Worker for :meth:`connect`: open the transport, run the identity
        handshake, then auto-read the current slot (unless the editor has
        unsaved edits).

        Args:
            in_port: the ALSA port name for incoming MIDI.
            out_port: the ALSA port name for outgoing MIDI.
        """
        try:
            self._do_connect(in_port, out_port)
        finally:
            self._connecting = False

    def _do_connect(self, in_port: str, out_port: str) -> None:
        """The body of :meth:`_connect_thread`."""
        from ..amp.connect import disconnect, handshake
        from ..protocol.transport import make_alsa_transport
        self.set_busy(True, "Connecting…")
        t = None
        try:
            if in_port.startswith("ble:"):
                from ..protocol.blemidi import make_ble_transport
                self.set_status("Connecting over Bluetooth…")
                t = make_ble_transport(in_port[len("ble:"):], allow_writes=_ble_writes)
            else:
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
        self.set_auto_connect(False)        # the user wants it left alone
        self.page.run_thread(self._disconnect_thread)

    def _disconnect_thread(self, graceful: bool = True,
                           msg: str = "Disconnected.") -> None:
        """Worker for :meth:`disconnect`: stop the transport and clear all
        connection state.

        Args:
            graceful: say goodbye to the amp first (not when it is gone).
            msg: the status line to show afterwards.
        """
        from ..amp.connect import disconnect
        t = self.transport
        if t is None:
            return
        self.live.clear()
        self._cancel_resync()
        t.on_unsolicited = None
        if graceful:
            try:
                disconnect(t)
            except Exception:                        # noqa: BLE001
                pass
        try:
            t.stop()
        except Exception:                            # noqa: BLE001
            pass
        self.connected = False
        self.transport = None
        self._amp_ports = set()
        self.amp_patch = None
        self.system_ps = None  # belonged to this amp; re-read on next visit
        self.set_live_ready(False)
        self.set_status(msg)
        self.refresh()

    # ------------------------------------------------------ keeping it connected

    def set_auto_connect(self, on: bool) -> None:
        """Turn looking for the amp (and reconnecting to it) on or off.

        Args:
            on: whether to connect by itself whenever the amp's MIDI port is
                there and nothing is connected.
        """
        self.auto_connect = on
        self._auto_retry_at = 0.0
        sw = getattr(self, "sb_auto", None)
        if sw is not None and sw.value != on:
            sw.value = on
            try:
                sw.update()
            except RuntimeError:
                pass                    # not on a page

    def start_auto_connect(self) -> None:
        """Start the thread that connects to the amp when it appears and
        notices when it goes away (idempotent)."""
        if self._auto_thread is not None:
            return
        self._auto_stop.clear()
        self._auto_thread = threading.Thread(
            target=self._auto_connect_loop, name="auto-connect", daemon=True)
        self._auto_thread.start()

    def stop_auto_connect(self) -> None:
        """Stop the auto-connect thread."""
        self._auto_stop.set()
        self._auto_thread = None

    def _auto_connect_loop(self) -> None:
        """Poll the MIDI ports until stopped."""
        from ..protocol.transport import list_alsa_ports
        while not self._auto_stop.wait(AUTO_CONNECT_INTERVAL_S):
            try:
                ports = list(list_alsa_ports().get("out", []))
                self._auto_connect_step(ports, time.monotonic())
            except Exception:                        # noqa: BLE001
                log.exception("auto-connect step failed")

    def _auto_connect_step(self, ports: list[str], now: float) -> None:
        """One look at the MIDI ports.

        Connected and the amp's port is gone: it was unplugged or switched
        off, so drop the link (the next steps reconnect).  Not connected,
        auto-connect on and the amp's port there: connect, but not again for
        :data:`AUTO_CONNECT_RETRY_S` if that fails.

        Args:
            ports: the MIDI cards present now.
            now: a monotonic time.
        """
        if self.connected:
            if (self.amp_port and not self.amp_port.startswith("ble:")
                    and self.amp_port not in ports):
                self._disconnect_thread(graceful=False, msg="Amp disconnected.")
            return
        if (not self.auto_connect or self._connecting or self.busy
                or now < self._auto_retry_at):
            return
        taken = self.midi.enabled_cards() if self.midi is not None else set()
        usable = [p for p in ports if p not in taken]
        port = (self.amp_port if self.amp_port in usable else
                next((p for p in usable if "katana" in p.lower()), None))
        if port is None:
            return
        self._auto_retry_at = now + AUTO_CONNECT_RETRY_S
        self.connect(port, port)

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
        self.page.run_thread(self._read_thread, n, gen, False)

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

    def _read_thread(self, n: int, gen: int, select: bool = True,
                     live: bool | None = None) -> None:
        """Read slot ``n``; afterwards the editor, the slot and the amp's
        current sound are the same patch, which is what makes live edit
        meaningful.

        The result is published only if ``gen`` is still the newest request:
        a switch that arrives mid-read bumps the generation, the progress
        callback notices at the next block and abandons the read.

        Args:
            n: the amp slot index (0-based).
            gen: the generation this read was started for.
            select: select the slot on the amp first; False to read the stored
                slot without touching the amp's channel (the Read button).
            live: whether live edit may start afterwards; None: only if the amp
                plays this slot (a resync after a switch passes True).
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
                # Live edit only while the amp plays this slot; a slot that is only
                # being looked at (or edited) is stored with Write, not heard.
                self.live_ready = (self.amp_patch == n) if live is None else live
                note = "" if self.live_ready else " (not selected on the amp)"
                self.set_status(f"Read {self.slot_name(n)}: {self.patch_name()!r}{note}")
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

    def release(self) -> None:
        """Give up everything the app holds: the controllers, the analyzer
        window and the amp link (idempotent; used when a window closes or the
        server stops)."""
        if getattr(self, "_tuner_muted", False):
            self._tuner_muted = False
            try:
                from ..amp.write import set_preview_mute
                if self.transport is not None:
                    set_preview_mute(self.transport, False)
            except Exception:                        # noqa: BLE001
                pass
        self.stop_auto_connect()
        self.stop_midi()
        self.stop_analyzer()
        t, self.transport = self.transport, None
        self.connected = False
        if t is not None:
            try:
                self.live.clear()
                self._cancel_resync()
                t.on_unsolicited = None
                from ..amp.connect import disconnect
                disconnect(t)
            except Exception:                        # noqa: BLE001
                pass
            t.stop()

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
        if self.live_ready and self.ui_attached:
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
            self.page.run_thread(self._read_thread, n, gen, False, True)
            return
        timer = threading.Timer(delay, self.page.run_thread,
                                args=(self._read_thread, n, gen, False, True))
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
            self._store(n, ps, self.patch_name() or None)
            self.dirty = False
            self._synced()
            self.set_status(f"Wrote {self.slot_name(n)}: {self.patch_name()!r}")
        except Exception as e:                       # noqa: BLE001
            self.set_status(f"Write failed: {e}")
        finally:
            self.busy_writing = False
            self.refresh()

    def _store(self, n: int, ps: ParamSet, name: str | None) -> None:
        """Store a patch in slot ``n``.

        The amp stores its edit buffer, so writing to the slot it plays is a plain
        write (the editor then equals the amp); any other slot is written with the
        amp's own sound put back afterwards: the slot is not made active.

        Args:
            n: the amp slot index (0-based).
            ps: the ``ParamSet`` to store.
            name: the patch name to store, or ``None`` to keep the patch's own.
        """
        from ..amp.write import write_patch, write_patch_keep_sound
        if self.amp_patch is None or self.amp_patch == n:
            write_patch(self.transport, n, ps, name)
        else:
            write_patch_keep_sound(self.transport, n, ps, name)

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
            else:
                self.set_live_ready(True)       # the editor shows what the amp plays now
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

    # ------------------------------------------------------------ undo / redo

    def _on_key(self, e: ft.KeyboardEvent) -> None:
        """Ctrl+Z undoes and Ctrl+Shift+Z or Ctrl+Y redoes an edit, unless the
        name field has the focus (it has its own undo)."""
        if not (e.ctrl or e.meta) or self.editor.name_field_focused:
            return
        key = e.key.lower()
        if key == "z":
            self.redo() if e.shift else self.undo()
        elif key == "y":
            self.redo()

    def undo(self) -> None:
        """Take back the last edit to the patch in the editor."""
        self._step(self.history.undo, "old", "Undid")

    def redo(self) -> None:
        """Make the edit that was undone last again."""
        self._step(self.history.redo, "new", "Redid")

    def _step(self, take, side: str, verb: str) -> None:
        """Apply one history step to the patch, the editor and the amp.

        Args:
            take: ``history.undo`` or ``history.redo``.
            side: the edit's value to set, ``"old"`` or ``"new"``.
            verb: how the status line starts.
        """
        if self.busy:
            return self.set_status("Wait for the current operation to finish.")
        with self.state_lock:
            edit = take(self.ps)
            if edit is not None:
                value = getattr(edit, side)
                if edit.key[0] == "name":
                    self.ps.set_block("PATCH%COM", value)
                    what = "the patch name"
                else:
                    _, block, p_index = edit.key
                    region = self.region_for(self.patch_number)
                    self.ps.set_param(region, block, p_index, value)
                    what = display_label(region.block(block).params[p_index].name, block)
        if edit is None:
            return self.set_status("Nothing to " + ("undo." if side == "old" else "redo."))
        self.mark_dirty()
        if edit.key[0] == "name":
            self.name_changed(ParamSet.patch_name(value))
        else:
            self.param_changed(block, p_index, value)
        self.set_status(f"{verb} {what}.")
        if self.ui_attached:
            self.editor.refresh()

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
        if not self.ui_attached:
            return
        self.editor.dirty_label.value = "● unsaved changes"
        self.editor.dirty_label.update()

    def refresh(self) -> None:
        """Full refresh: patch list, editor, system, status widgets.

        Every amp operation ends with a refresh, so this also ends the busy
        state (progress bar; it holds live-edit sends while the wire is used).
        """
        self.busy = False
        if not self.ui_attached:
            return
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
        if not self.ui_attached:
            return
        self.status_text.value = f"{verb}… block {done}/{total}"
        self.status_text.update()
        self.progress.value = done / total if total else 0
        self.progress.update()

    def _step_progress(self, done: int, total: int, text: str) -> None:
        """Like :meth:`_progress` for a job in steps (a backup or restore)."""
        if not self.ui_attached:
            return
        self.status_text.value = f"{text}… ({done}/{total})"
        self.status_text.update()
        self.progress.value = done / total if total else 0
        self.progress.update()

    # ------------------------------------------------------- backup / restore

    def backup_amp(self, on_done) -> None:
        """Read the ten user patches and the system settings in the background.

        Args:
            on_done: coroutine function called with the ``Backup`` on the
                page's event loop (it offers the file to the user); not
                called on failure.
        """
        if self._require_connected():
            self.page.run_thread(self._backup_thread, on_done)

    def _backup_thread(self, on_done) -> None:
        """Worker for :meth:`backup_amp`."""
        from ..amp.backup import read_backup
        self.set_busy(True, "Backing up the amp…")
        try:
            backup = read_backup(self.transport, self._step_progress)
        except Exception as e:                       # noqa: BLE001
            self.set_status(f"Backup failed: {e}")
            return
        finally:
            self.set_busy(False)
        self.set_status("Backup read.")
        self.page.run_task(on_done, backup)

    def restore_amp(self, backup) -> None:
        """Write a backup to the amp in the background: all ten user patches
        and the system settings. What the amp holds is first kept in the data
        directory.

        Args:
            backup: the ``Backup`` to write.
        """
        if self._require_connected():
            self.page.run_thread(self._restore_thread, backup)

    def _restore_thread(self, backup) -> None:
        """Worker for :meth:`restore_amp`."""
        from ..amp.backup import read_backup, save_before_restore, write_backup
        self.set_busy(True, "Restoring the amp…")
        self.busy_writing = True
        done = False
        try:
            kept = save_before_restore(read_backup(
                self.transport,
                lambda i, n, text: self._step_progress(i, n, "Keeping a copy: " + text)))
            write_backup(self.transport, backup, self._step_progress)
            self.system_ps = backup.system
            self.set_status(f"Restored the amp; what it held before is in {kept}.")
            done = True
        except Exception as e:                       # noqa: BLE001
            self.set_status(f"Restore failed: {e}")
        finally:
            self.busy_writing = False
            self.refresh()
        if done:
            self.read_patch(self.patch_number)       # the editor shows the restored slot

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
        from ..amp.write import write_system
        self.set_busy(True, f"{'Reading' if op == 'load' else 'Writing'} system…")
        self.busy_writing = op == "save"
        try:
            if op == "load":
                self.system_ps = read_region(
                    self.transport, "SYSTEM",
                    progress=lambda d, tot: self._progress(d, tot, "Reading system"))
                self.set_status("Read SYSTEM region.")
            else:
                write_system(self.transport, self.system_ps)
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
            self._store(n, ps, name or None)
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
        if self.tc_proc is not None and self.tc_proc.poll() is None:
            self.set_status("Tone Exchange window is already open.")
            self.tonecentral_refresh()
            return False
        try:
            log_file = _open_log("webview.log")
        except OSError as e:                         # noqa: BLE001
            self.set_status(f"Tone Exchange failed to start: {e}")
            return False
        try:
            self.tc_proc = subprocess.Popen(
                [sys.executable, "-m", "katatone.remote.webview"] + args,
                stdout=subprocess.DEVNULL, stderr=log_file or subprocess.DEVNULL)
        except Exception as e:                       # noqa: BLE001
            self.tc_proc = None
            if log_file:
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
                if log_file:
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
        tail = ""
        path = _log_path("webview.log")
        try:
            if path:
                lines = path.read_text(
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
        ``analyzer.log`` in the data dir when ``--log`` is given (else discarded).
        """
        self.an_proc = self._open_tool(self.an_proc, "katatone.gui.analyzer", "analyzer",
                                       "Analyzer")

    def open_tuner(self) -> None:
        """Open the guitar tuner window in its own process, unless it is
        already open (it is built like the analyzer, see :meth:`open_analyzer`)."""
        self.tuner_proc = self._open_tool(self.tuner_proc, "katatone.gui.tuner", "tuner",
                                          "Tuner", mute_pipe=True)
        if self.tuner_proc is not None and getattr(self.tuner_proc, "stdout", None):
            threading.Thread(target=self._follow_tuner, args=(self.tuner_proc,),
                             daemon=True).start()

    def _follow_tuner(self, proc) -> None:
        """Apply the tuner window's mute requests; restore the amp when it ends."""
        for line in proc.stdout:
            if line.strip() in ("MUTE 1", "MUTE 0"):
                self.set_tuner_mute(line.strip() == "MUTE 1")
        if getattr(self, "_tuner_muted", False):
            self.set_tuner_mute(False)          # the window closed or crashed

    def set_tuner_mute(self, mute: bool) -> None:
        """Mute (or restore) the amp's output for the tuner.

        Args:
            mute: True while the tuner asks for silence.
        """
        self._tuner_muted = mute
        self.set_mute(mute)

    def _open_tool(self, proc, module: str, name: str, title: str,
                   mute_pipe: bool = False):
        """Start a sound-card tool window as its own process.

        Only used on the desktop: a browser session opens the tool as another
        page of the server instead (see :func:`_tool_class`).

        Args:
            proc: the tool's current process, if any.
            module: the module to run (``python -m``).
            name: its log's base name and the word used in status messages.
            title: its title for status messages.
        Returns:
            The process now running (the old one if it was still open), or None.
        """
        import subprocess
        import sys
        if proc is not None and proc.poll() is None:
            self.set_status(f"The {name} window is already open.")
            return proc
        cmd = [sys.executable, "-m", module] + (["--mute-pipe"] if mute_pipe else [])
        card = getattr(getattr(self, "sb_amp", None), "value", None)
        if card:
            cmd += ["--card", card]
        try:
            log_file = _open_log(f"{name}.log")
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE if mute_pipe else subprocess.DEVNULL,
                    stderr=log_file or subprocess.DEVNULL, text=mute_pipe)
            finally:
                if log_file:
                    log_file.close()
        except OSError as e:
            self.set_status(f"{title} failed to start: {e}")
            return None
        self.set_status(f"{title} window opened.")
        return proc

    def stop_analyzer(self) -> None:
        """Close the analyzer and the tuner windows if they are open."""
        procs = (getattr(self, "an_proc", None), getattr(self, "tuner_proc", None))
        self.an_proc = self.tuner_proc = None
        for proc in procs:
            if proc is not None and proc.poll() is None:
                proc.terminate()


def _parse_args(argv: list[str], web: bool = False):
    """Parse the flags of ``katatone-gui`` (a window) or ``katatone-web`` (a
    browser interface); unknown (Flet) arguments are ignored.

    ``-h`` / ``--help`` prints the options and exits.

    Args:
        argv: the argument list.
        web: parse for ``katatone-web``: the browser interface is on and its
            options are the ones listed.  (``--web`` is still accepted by
            ``katatone-gui`` and means the same.)
    Returns:
        The parsed options: ``verbose``, ``web``, ``host``, ``port``, ``wifi``,
        ``prog``, ``reduce_writes``, ``rw_seconds``, ``log``, ``auto_connect`` and
        ``ble_writes``.
    """
    import argparse
    hide = argparse.SUPPRESS
    prog = "katatone-web" if web else "katatone-gui"
    if web:
        description = ("KataTone as a browser interface for the BOSS Katana 50 Gen 3, behind a\n"
                       "login.  The amp link and the MIDI controllers belong to the server, so\n"
                       "pedals keep working with no browser open (for example on a Raspberry Pi\n"
                       "next to the amp).  Other (Flet) arguments are passed through.")
        examples = ("  katatone-web                       http://127.0.0.1:8550/ on this machine\n"
                    "  katatone-web --host 0.0.0.0        reachable from the network\n"
                    "  katatone-web --wifi --reduce-writes --host 0.0.0.0\n"
                    "                                     headless Raspberry Pi, see docs/raspberry-pi.md\n"
                    "\nThe desktop window is 'katatone-gui'; patches, the library and Wi-Fi from\n"
                    "a shell are 'katatone --help'.")
    else:
        description = ("KataTone: patch editor for the BOSS Katana 50 Gen 3 in a desktop window.\n"
                       "Other (Flet) arguments are passed through.")
        examples = ("  katatone-gui                       desktop window\n"
                    "  katatone-gui --no-auto-connect     do not connect to the amp by itself\n"
                    "\nThe browser interface is 'katatone-web'; patches, the library and Wi-Fi\n"
                    "from a shell are 'katatone --help'.")
    parser = argparse.ArgumentParser(
        prog=prog, formatter_class=argparse.RawDescriptionHelpFormatter,
        description=description, epilog="examples:\n" + examples)
    parser.add_argument("--version", action="version", version=f"{prog} {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", dest="verbose", default=False,
                        help="trace to the terminal (stderr); writes no file")
    # The browser flags: listed for katatone-web, accepted (hidden) by katatone-gui.
    net = parser.add_argument_group("browser interface") if web else parser
    net.add_argument("--web", action="store_true", default=False, help=hide)
    net.add_argument("--host", default="127.0.0.1",
                     help=("address to listen on (default 127.0.0.1; 0.0.0.0 for the whole "
                           "network; traffic is plain HTTP)") if web else hide)
    net.add_argument("--port", type=int, default=8550,
                     help="port to listen on (default 8550)" if web else hide)
    net.add_argument("--wifi", action="store_true", default=False,
                     help=("add the Wi-Fi settings (network, fallback access point, IP, host "
                           "name); needs NetworkManager (nmcli)") if web else hide)
    amp = parser.add_argument_group("amp connection")
    amp.add_argument("--no-auto-connect", action="store_false", dest="auto_connect", default=True,
                     help="do not connect to the amp by itself when it appears")
    amp.add_argument("--no-ble-writes", action="store_false", dest="ble_writes", default=True,
                     help="over Bluetooth, only read from the amp (no patch or system writes)")
    disk = parser.add_argument_group("storage")
    disk.add_argument("--reduce-writes", action="store_true", default=False,
                      help="the file system is read-only: remount the one holding the data and "
                           "/etc read-write only while writing (needs root or sudo)")
    disk.add_argument("--rw-seconds", type=float, default=RW_SECONDS, metavar="SECONDS",
                      help="with --reduce-writes, how long those file systems stay writable "
                           "after start (default %(default).0f)")
    disk.add_argument("--log", action="store_true", default=False,
                      help="keep the log files of the Analyzer and Tone Central helpers "
                           "(off by default)")
    opts, _ = parser.parse_known_args(argv)
    opts.prog = prog
    if web:
        opts.web = True
    return opts


def web_banner(host: str, port: int, default_password: bool = False) -> str:
    """The text printed when the web server starts.

    Args:
        host: the address it listens on.
        port: the port it listens on.
        default_password: whether the factory password is still set.
    Returns:
        The address to open, plus a note when it is reachable from other
        machines (the login password travels unencrypted) and when the
        factory password is still set.
    """
    text = f"KataTone web GUI: http://{host}:{port}/"
    if host not in ("127.0.0.1", "localhost", "::1"):
        text += (f"\nNote: listening on {host}: the login password and the "
                 "session travel over plain HTTP; use it on a network you trust.")
    if default_password:
        text += ("\nThe factory password is still set; "
                 "you will be asked to change it at the first login.")
    return text


def _open_window(page: ft.Page, apps: list, opts) -> None:
    """Create the app for a new desktop window.

    Args:
        page: the window's page.
        apps: every app created so far (released at exit).
        opts: the parsed options (see :func:`_parse_args`).
    """
    app = App(page, wifi=opts.wifi)
    apps.append(app)
    if opts.auto_connect:
        app.start_auto_connect()


def _open_web_session(page: ft.Page, backend: App,
                      auth: "auth_mod.Auth | None" = None) -> None:
    """A browser opened the page: show the login form, and the app after it.

    The app itself is the server's ``backend``, created once at start: it
    owns the amp link and the controllers whether or not anyone is looking.
    A session that logs in borrows it (and takes it from the previous one);
    closing the tab, or the session timing out, gives it back and the backend
    keeps running.  A page that reconnects (a refresh, a phone waking up)
    gets the app again without logging in once more.

    Args:
        page: the new session's page.
        backend: the persistent app.
        auth: the password store (default: the device's).
    """
    tool_class = _tool_class(page)
    # A tool opened from the app's own button carries a token (see
    # LaunchTokens): that is the login.  The token is never good for the app.
    signed_in = tool_class is not None and backend.launch_tokens.valid(_launch_token(page))
    tool = None

    def show() -> None:
        """Put the app, or the tool this page's address names, on the page."""
        nonlocal tool
        if tool_class is None:
            backend.attach(page)
            return
        if tool is not None:
            tool.close()
        page.clean()
        extra = ({"on_mute": backend.set_tuner_mute}
                 if getattr(tool_class, "wants_mute", False) else {})
        tool = tool_class(page, backend.amp_port, **extra)

    def hide(e=None) -> None:
        """The tab went away: give the app back / stop listening."""
        nonlocal tool
        if tool_class is None:
            backend.detach(page)
        elif tool is not None:
            tool.close()
            tool = None

    def start() -> None:
        """Called by the login page once the user is in."""
        nonlocal signed_in
        signed_in = True
        show()

    def reconnected(e=None) -> None:
        """The browser is back (a refresh, or another address in the same tab):
        show what its address names now."""
        nonlocal tool_class
        if not signed_in:
            return
        wanted = _tool_class(page)
        if wanted is not tool_class:
            hide()
            tool_class = wanted
        show()

    page.on_connect = reconnected
    page.on_disconnect = hide
    page.on_close = hide
    if signed_in:
        show()
        return
    login.LoginPage(page, auth or auth_mod.Auth(), start)


def _launch_token(page: ft.Page) -> str | None:
    """The ``token`` in the page's address, if any."""
    query = urllib.parse.urlsplit(getattr(page, "route", "") or "").query
    return (urllib.parse.parse_qs(query).get("token") or [None])[0]


#: Sound-card tools served by the web server as pages of their own, at
#: ``/analyzer`` and ``/tuner`` (the top bar's buttons open them in a new tab).
TOOL_ROUTES = ("analyzer", "tuner")


def _tool_class(page: ft.Page):
    """The sound-card tool the page's address asks for, or None for the app.

    These tools run inside the server, so a browser on another machine (a phone
    on a Raspberry Pi's access point) can use them, behind the same login.

    Args:
        page: the session's page.
    Returns:
        ``Analyzer`` for ``/analyzer``, ``TunerWindow`` for ``/tuner``, else None.
    """
    route = urllib.parse.urlsplit(getattr(page, "route", "") or "").path.strip("/").lower()
    if route == "analyzer":
        from .analyzer import Analyzer
        return Analyzer
    if route == "tuner":
        from .tuner import TunerWindow
        return TunerWindow
    return None


def _require_web_support() -> None:
    """Exit with install advice unless ``flet-web`` (the same version as Flet)
    is installed; Flet would otherwise try to ``pip install`` it at start."""
    import sys
    try:
        import flet.version
        import flet_web.version
        ok = flet_web.version.version in ("", None, flet.version.flet_version)
    except ImportError:
        ok = False
    if not ok:
        sys.exit("katatone-web needs the flet-web package, in the same "
                 "version as flet: pip install 'katatone[web]'")


def _end_init_after(seconds: float) -> None:
    """Call :func:`rofs.end_init` after ``seconds``, from a background thread.

    A fixed time, not an event: it works whether or not anyone ever opens the
    page.

    Args:
        seconds: how long the file systems stay writable.
    """
    timer = threading.Timer(seconds, rofs.end_init)
    timer.daemon = True
    timer.start()


def main_web(argv: list[str] | None = None) -> None:
    """Entry point of ``katatone-web``: :func:`main` with the browser interface on."""
    main(argv, web=True)


def main(argv: list[str] | None = None, web: bool = False) -> None:
    """Parse the CLI flags and run the Flet app (``katatone-gui``; with ``web``,
    ``katatone-web``).

    The web interface serves the GUI to a browser instead of opening a desktop
    window, on ``--host`` (default ``127.0.0.1``, this machine only) and
    ``--port`` (default 8550); the address is printed at start.  The amp link
    and the MIDI controllers then belong to the server, not to the browser tab:
    they keep working with no browser open.  ``--wifi`` (with ``--web``) adds
    the Wi-Fi settings, for a device that manages its own network through
    NetworkManager; without it the web GUI is the same on any system and
    touches no network settings.

    Args:
        argv: the argument list (defaults to ``sys.argv[1:]``); unknown
            Flet args are tolerated.
        web: run the browser interface (``katatone-web``).
    """
    import os
    import sys
    # Debug logging is OFF by default; pass -v/--verbose to trace execution
    # (mirrors the CLI).  Logs go to stderr so UI/JSON stay on stdout.
    from ..cli import _setup_logging
    opts = _parse_args(sys.argv[1:] if argv is None else argv, web)
    _setup_logging(opts.verbose)
    rofs.enable(opts.reduce_writes)          # finds the data file system
    global _log_files, _ble_writes
    _log_files = opts.log
    _ble_writes = opts.ble_writes
    apps: list[App] = []
    if opts.wifi:
        from ..net.wifi import Wifi
        if not opts.web:
            sys.exit(f"{opts.prog}: --wifi is a setting of the browser interface: run katatone-web --wifi")
        if not Wifi.available():
            sys.exit(f"{opts.prog}: --wifi needs NetworkManager (nmcli), which is not installed")
    if opts.web:
        _require_web_support()
    _begin_init(opts)
    from . import headless
    headless.install_safe_update()

    async def on_loop(page) -> None:
        """Runs on the pages' event loop at start: remember it (see headless.set_loop)."""
        headless.set_loop(asyncio.get_running_loop())

    if opts.web:
        auth = auth_mod.Auth()
        backend = App(wifi=opts.wifi)
        apps.append(backend)
        backend.start_midi()
        if opts.auto_connect:
            backend.start_auto_connect()
        print(web_banner(opts.host, opts.port, auth.is_default()), flush=True)
        # no_cdn: the page loads CanvasKit and its fonts from this server, so it
        # works on a network with no internet (the device's own access point).
        ft.run(main=lambda page: _open_web_session(page, backend, auth),
               before_main=on_loop, view=ft.AppView.WEB_BROWSER, host=opts.host,
               port=opts.port, no_cdn=True)
    else:
        ft.run(main=lambda page: _open_window(page, apps, opts), before_main=on_loop)
    for app in apps:
        app.release()
    rofs.end_init()                 # leave the file systems read-only
    sys.stdout.flush()
    # skip atexit: alsa-lib static cleanup segfaults on PipeWire hosts
    os._exit(0)


def _begin_init(opts) -> None:
    """With ``--reduce-writes``: keep the file system that holds the data
    directory read-write while the app starts up and for ``--rw-seconds`` after,
    then put it back read-only with :func:`rofs.end_init` whether or not anyone
    connected.

    Args:
        opts: the parsed options.
    """
    import sys
    if not opts.reduce_writes:
        return
    try:
        rofs.begin_init()
    except rofs.RemountError as e:
        sys.exit(f"{opts.prog}: {e}")
    _end_init_after(opts.rw_seconds)


if __name__ == "__main__":
    main()
