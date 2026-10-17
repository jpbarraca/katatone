"""MIDI tab: controllers, live monitor, and the mappings of the active profile.

Three module cards, like the editor:

* **Controllers** - every USB MIDI controller found, an enable switch, a state
  chip and an activity LED.
* **Monitor** - the last 50 events and which mapping each one fired.
* **Mappings** - the active profile (new / rename / duplicate / delete / import
  / export) and its mappings; *Add mapping* opens the editor with Learn.

Events arrive on background threads, so they are queued and painted by one UI
pump thread (at most every 100 ms, one ``update()`` per batch).
"""
from __future__ import annotations

import collections
import datetime
import logging
import pathlib
import threading
import time
from typing import TYPE_CHECKING

import flet as ft

from ..midi.events import MidiEvent, describe
from ..midi.mapping import Mapping, Profile, UnknownAction
from . import dialogs, params_view as pv, theme
from .mapping_dialog import MappingDialog

if TYPE_CHECKING:
    from ..midi.manager import MidiManager
    from .app import App

log = logging.getLogger("katatone.gui.midi")

TAB_MIDI = "MIDI"
MONITOR_LINES = 50
MONITOR_PERIOD_S = 0.1
LED_HOLD_S = 0.15
PUMP_PERIOD_S = 0.05

# Controller state -> (text, colour)
_STATES = {"open": ("open", theme.OK),
           "busy": ("in use by another program", theme.DANGER),
           "gone": ("unplugged", theme.ACCENT),
           "closed": ("closed", theme.MUTED)}


def _push(ctrl: ft.Control) -> None:
    """Update one control, ignoring the RuntimeError raised while it is not mounted."""
    try:
        ctrl.update()
    except RuntimeError:
        pass


def state_text(state: str) -> tuple[str, str]:
    """Display text and colour of a controller state.

    Args:
        state: "open", "busy", "gone", "closed" or "error: ...".

    Returns:
        ``(text, colour)``.
    """
    if state in _STATES:
        return _STATES[state]
    return (state or "closed", theme.DANGER if state.startswith("error") else theme.MUTED)


def build(app: App) -> ft.Control:
    """Create the MIDI pane, keep it on ``app.midi_pane`` and return its root control.

    Args:
        app: the application.
    """
    app.midi_pane = _MidiPane(app)
    return app.midi_pane.root


def refresh(app: App) -> None:
    """Light refresh after an amp operation (the connection note); no-op before build.

    Args:
        app: the application.
    """
    pane = getattr(app, "midi_pane", None)
    if pane is not None:
        pane.refresh_note()


class _MidiPane:
    def __init__(self, app: App) -> None:
        """Build the three cards; the manager is attached later (`attach`)."""
        self.app = app
        self.midi: MidiManager | None = None
        self._picker = ft.FilePicker()
        self._unsub = None
        self._stop = threading.Event()
        self._pump: threading.Thread | None = None
        self._lock = threading.Lock()
        self._pending: collections.deque = collections.deque()
        self._paused = False
        self._last_flush = 0.0
        self._lines: collections.deque = collections.deque(maxlen=MONITOR_LINES)
        self._chips: dict[str, ft.Text] = {}
        self._leds: dict[str, ft.Container] = {}
        self._led_until: dict[str, float] = {}
        self._led_lit: dict[str, bool] = {}

        # Controllers
        self.controllers_box = ft.Column(spacing=6)
        self.controllers_card = pv.module_card("Controllers", [
            self.controllers_box,
            pv.button("Rescan", ft.Icons.REFRESH, lambda e: self.rebuild_controllers())])
        # Monitor
        self.monitor_box = ft.Column(spacing=2, height=190, scroll=ft.ScrollMode.AUTO)
        self.pause_btn = pv.button("Pause", ft.Icons.PAUSE, self._toggle_pause)
        self.monitor_card = pv.module_card("Monitor", [
            self.monitor_box,
            ft.Row([self.pause_btn,
                    pv.button("Clear", ft.Icons.CLEAR_ALL, self._clear_monitor)], spacing=8)])
        # Mappings
        self.profile_dd = ft.Dropdown(width=220, on_select=self._on_profile, **pv.FIELD_STYLE)
        self.note = ft.Text("", size=12, color=theme.ACCENT)
        self.mappings_box = ft.Column(spacing=4)
        profile_row = ft.Row([
            self.profile_dd,
            pv.button("New", ft.Icons.ADD, self._new_profile),
            pv.button("Rename", ft.Icons.DRIVE_FILE_RENAME_OUTLINE, self._rename_profile),
            pv.button("Duplicate", ft.Icons.CONTENT_COPY, self._duplicate_profile),
            pv.button("Delete", ft.Icons.DELETE_OUTLINE, self._delete_profile),
            pv.button("Import...", ft.Icons.FILE_UPLOAD, self._import),
            pv.button("Export...", ft.Icons.FILE_DOWNLOAD, self._export),
        ], spacing=8, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self.mappings_card = pv.module_card("Mappings", [
            profile_row, self.note, self.mappings_box,
            pv.button("Add mapping", ft.Icons.ADD, lambda e: self._edit(None), primary=True)])
        self.root = ft.Column(
            [self.controllers_card, self.monitor_card, self.mappings_card],
            spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)
        for c in (self.controllers_card, self.monitor_card, self.mappings_card):
            c.col = None
        self.refresh_all()

    # -- lifecycle -----------------------------------------------------------

    def attach(self, midi: MidiManager) -> None:
        """Start following a MIDI manager (called once the app has started it).

        Args:
            midi: the running manager.
        """
        self.detach()
        self.midi = midi
        self._unsub = midi.subscribe(self._on_notify)
        self._stop.clear()
        self._pump = threading.Thread(target=self._run_pump, name="midi-ui", daemon=True)
        self._pump.start()
        self.refresh_all()

    def detach(self) -> None:
        """Stop the UI pump and the subscription."""
        self._stop.set()
        if self._unsub:
            self._unsub()
            self._unsub = None
        t, self._pump = self._pump, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=1)

    # -- refresh -------------------------------------------------------------

    def refresh_all(self) -> None:
        """Rebuild controllers, profile row and mappings."""
        self.rebuild_controllers()
        self.rebuild_profiles()
        self.rebuild_mappings()

    def refresh_note(self) -> None:
        """Show why mappings may not act (amp not connected)."""
        msg = ("" if self.app.connected else
               "Amp not connected: controller events are shown below, but mappings do "
               "nothing until you connect.")
        if self.note.value != msg:
            self.note.value = msg
            _push(self.note)

    def rebuild_controllers(self) -> None:
        """List the controllers found (plus saved ones that are unplugged)."""
        self._chips.clear()
        self._leds.clear()
        rows: list[ft.Control] = []
        midi = self.midi
        if midi is None:
            rows.append(ft.Text("Starting MIDI...", color=theme.MUTED, size=12))
        else:
            present = [p.label for p in midi.ports()]     # one daemon scan
            labels = present + [l for l in midi.enabled() if l not in present]
            if not labels:
                rows.append(ft.Text(
                    "No MIDI controllers found. Plug in a USB MIDI controller and press Rescan.",
                    color=theme.MUTED, size=12))
            for label in labels:
                rows.append(self._controller_row(label, label in midi.enabled(),
                                                 plugged=label in present))
        self.controllers_box.controls = rows
        _push(self.controllers_box)
        self.app_refresh_ports()

    def _controller_row(self, label: str, enabled: bool, plugged: bool) -> ft.Control:
        """One controller: switch, name, state chip, activity LED.

        Args:
            label: the port label.
            enabled: whether it is enabled in the profile.
            plugged: whether it is currently present.
        """
        state = self.midi.states.get(label, "closed" if plugged else "gone") \
            if enabled else ("closed" if plugged else "gone")
        text, color = state_text(state)
        chip = ft.Text(text, size=12, color=color, width=190, no_wrap=True)
        led = ft.Container(width=10, height=10, border_radius=5, bgcolor=theme.LED_OFF,
                           border=ft.Border.all(1, theme.ACCENT_DIM))
        self._chips[label], self._leds[label] = chip, led
        sw = ft.Switch(value=enabled, on_change=lambda e, l=label: self._enable(l, e.control.value))
        return ft.Row([sw, ft.Text(label, size=13, color=theme.FOREGROUND, width=230, no_wrap=True),
                       chip, led], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _enable(self, label: str, on: bool) -> None:
        """Switch handler: enable or disable a controller.

        Args:
            label: the port label.
            on: the new state.
        """
        ft.context.disable_auto_update()
        self.midi.enable(label, on)
        self.app_refresh_ports()

    def app_refresh_ports(self) -> None:
        """Re-list the amp's port dropdowns without the controllers."""
        from . import statusbar
        statusbar.refresh_ports(self.app)

    # -- notifications (background threads) ----------------------------------

    def _on_notify(self, kind: str, *args) -> None:
        """Manager callback.

        Args:
            kind: "state" or "activity".
            *args: ``(label, state)`` or ``(device, event, matched_labels)``.
        """
        if kind == "state":
            label, state = args
            chip = self._chips.get(label)
            if chip is not None:
                chip.value, chip.color = state_text(state)
                _push(chip)
            self.app_update_midi_tooltip()
        elif kind == "activity":
            device, ev, labels = args
            now = time.monotonic()
            with self._lock:
                self._led_until[device] = now + LED_HOLD_S
                if not self._paused:
                    self._pending.append((datetime.datetime.now(), device, ev, labels))

    def app_update_midi_tooltip(self) -> None:
        """Tell the status bar which controllers are open."""
        from . import statusbar
        statusbar.refresh_midi(self.app)

    def _run_pump(self) -> None:
        """UI pump: batch monitor lines, turn activity LEDs on and off."""
        while not self._stop.wait(PUMP_PERIOD_S):
            try:
                self._tick()
            except Exception:
                log.exception("MIDI UI pump failed")

    def _tick(self) -> None:
        """One pump step: LEDs now, monitor lines at most every `MONITOR_PERIOD_S`."""
        now = time.monotonic()
        with self._lock:
            until = dict(self._led_until)
        for label, led in list(self._leds.items()):
            lit = until.get(label, 0) > now
            if lit != self._led_lit.get(label, False):
                self._led_lit[label] = lit
                led.bgcolor = theme.ACCENT if lit else theme.LED_OFF
                _push(led)
        from . import statusbar
        statusbar.set_midi_activity(self.app, any(v > now for v in until.values()))
        if now - self._last_flush >= MONITOR_PERIOD_S:
            self._flush_monitor(now)

    def _flush_monitor(self, now: float) -> None:
        """Add the queued events to the monitor with a single update.

        Args:
            now: the monotonic time, to rate-limit the next flush.
        """
        with self._lock:
            batch = list(self._pending)
            self._pending.clear()
        if not batch:
            return
        self._last_flush = now
        for when, device, ev, labels in batch:
            self._lines.appendleft((when, device, ev, labels))
        self._paint_monitor()

    def _paint_monitor(self) -> None:
        """Redraw the monitor from `_lines`."""
        self.monitor_box.controls = [
            ft.Text(f"{when:%H:%M:%S}.{when.microsecond // 1000:03d}  {device}  "
                    f"{describe(ev)}  ->  {', '.join(labels) if labels else '-'}",
                    size=12, font_family="monospace",
                    color=theme.FOREGROUND if labels else theme.MUTED)
            for when, device, ev, labels in self._lines]
        _push(self.monitor_box)

    def _toggle_pause(self, e=None) -> None:
        """Pause or resume the monitor."""
        ft.context.disable_auto_update()
        self._paused = not self._paused
        self.pause_btn.content = ft.Text("Resume" if self._paused else "Pause",
                                         no_wrap=True, size=13)
        self.pause_btn.icon = ft.Icons.PLAY_ARROW if self._paused else ft.Icons.PAUSE
        _push(self.pause_btn)

    def _clear_monitor(self, e=None) -> None:
        """Empty the monitor."""
        ft.context.disable_auto_update()
        with self._lock:
            self._pending.clear()
        self._lines.clear()
        self._paint_monitor()

    # -- profiles ------------------------------------------------------------

    def rebuild_profiles(self) -> None:
        """Refill the profile dropdown from the store."""
        midi = self.midi
        if midi is None:
            self.profile_dd.options = []
            return
        self.profile_dd.options = [ft.DropdownOption(key=n, text=n) for n in midi.store.list()]
        self.profile_dd.value = midi.profile.name
        _push(self.profile_dd)

    def _on_profile(self, e) -> None:
        """Dropdown handler: switch profile."""
        ft.context.disable_auto_update()
        self._switch(e.control.value)

    def _switch(self, name: str) -> None:
        """Make `name` the active profile and redraw.

        Args:
            name: an existing profile.
        """
        try:
            self.midi.set_profile(name)
        except Exception as ex:                         # noqa: BLE001
            self.app.set_status(f"Cannot open profile {name!r}: {ex}")
        self.refresh_all()

    def _new_profile(self, e=None) -> None:
        """New empty profile."""
        def ok(name: str) -> None:
            if self.midi.store.exists(name):
                self.app.set_status(f"A profile named {name!r} already exists.")
                return
            self.midi.store.save(Profile(name=name))
            self._switch(name)
        dialogs.prompt(self.app.page, "New profile", "", ok)

    def _rename_profile(self, e=None) -> None:
        """Rename the active profile."""
        old = self.midi.profile.name

        def ok(name: str) -> None:
            try:
                self.midi.store.rename(old, name)
            except (FileExistsError, FileNotFoundError) as ex:
                self.app.set_status(f"Rename failed: {ex}")
                return
            self._switch(self.midi.store.active())
        dialogs.prompt(self.app.page, f"Rename {old!r}", old, ok)

    def _duplicate_profile(self, e=None) -> None:
        """Copy the active profile."""
        old = self.midi.profile.name

        def ok(name: str) -> None:
            try:
                self.midi.save_profile()
                self.midi.store.duplicate(old, name)
            except FileExistsError as ex:
                self.app.set_status(f"Duplicate failed: {ex}")
                return
            self._switch(name)
        dialogs.prompt(self.app.page, f"Duplicate {old!r} as", f"{old} copy", ok)

    def _delete_profile(self, e=None) -> None:
        """Delete the active profile (after confirming)."""
        name = self.midi.profile.name

        def ok() -> None:
            self.midi.store.delete(name)
            self._switch(self.midi.store.active())
        dialogs.confirm(self.app.page, f"Delete profile {name!r} and all its mappings?", ok)

    async def _import(self, e) -> None:
        """Import a profile JSON chosen with the native file chooser."""
        ft.context.disable_auto_update()
        try:
            files = await self._picker.pick_files(
                dialog_title="Import MIDI profile", file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["json"])
        except Exception as ex:                         # noqa: BLE001
            self.app.set_status(f"File chooser unavailable: {ex}")
            return
        paths = [f.path for f in files or [] if f.path]
        if not paths:
            return
        try:
            prof = self.midi.store.import_file(paths[0])
        except ValueError as ex:
            self.app.set_status(f"Import failed: {ex}")
            return
        self.app.set_status(f"Imported profile {prof.name!r}.")
        self._switch(prof.name)

    async def _export(self, e) -> None:
        """Export the active profile to a JSON file chosen with the native dialog."""
        ft.context.disable_auto_update()
        name = self.midi.profile.name
        self.midi.save_profile()
        try:
            chosen = await self._picker.save_file(
                dialog_title=f"Export profile {name!r}", file_name=f"{name}.json",
                initial_directory=str(pathlib.Path.home()),
                file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=["json"])
        except Exception as ex:                         # noqa: BLE001
            self.app.set_status(f"File chooser unavailable: {ex}")
            return
        if not chosen:
            return
        dest = pathlib.Path(chosen)
        if dest.suffix.lower() != ".json":
            dest = dest.with_name(dest.name + ".json")
        try:
            self.midi.store.export_file(name, dest)
            self.app.set_status(f"Exported -> {dest}")
        except OSError as ex:
            self.app.set_status(f"Export failed: {ex}")

    # -- mappings ------------------------------------------------------------

    def rebuild_mappings(self) -> None:
        """Redraw the mappings table of the active profile."""
        self.refresh_note()
        midi = self.midi
        rows: list[ft.Control] = []
        if midi is not None:
            for m in midi.profile.mappings:
                rows.append(self._mapping_row(m))
            if not rows:
                rows.append(ft.Text("No mappings yet. Press Add mapping, then Learn.",
                                    color=theme.MUTED, size=12))
        self.mappings_box.controls = rows
        _push(self.mappings_box)

    def _mapping_row(self, m: Mapping) -> ft.Control:
        """One row: enabled, label, trigger, action, edit, delete.

        Args:
            m: the mapping.
        """
        unknown = isinstance(m.action, UnknownAction)
        label = ft.TextField(value=m.label, hint_text=m.action.summary(self.app.slot_name),
                             width=190, on_blur=lambda e, m=m: self._relabel(m, e.control.value),
                             on_submit=lambda e, m=m: self._relabel(m, e.control.value),
                             **pv.FIELD_STYLE)
        return ft.Row([
            ft.Checkbox(value=m.enabled and not unknown, disabled=unknown,
                        tooltip="Unsupported by this version" if unknown else "Enabled",
                        on_change=lambda e, m=m: self._enabled(m, e.control.value)),
            label,
            ft.Text(m.trigger.summary(), size=12, color=theme.MUTED, expand=2,
                    no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
            ft.Text(m.action.summary(self.app.slot_name), size=12, color=theme.FOREGROUND,
                    expand=2, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
            ft.IconButton(ft.Icons.EDIT, tooltip="Edit", icon_color=theme.MUTED,
                          disabled=unknown, on_click=lambda e, m=m: self._edit(m)),
            ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="Delete", icon_color=theme.MUTED,
                          on_click=lambda e, m=m: self._delete(m)),
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _save(self) -> None:
        """Persist the active profile (every change saves at once)."""
        self.midi.save_profile()

    def _enabled(self, m: Mapping, on: bool) -> None:
        """Checkbox handler: enable or disable a mapping."""
        ft.context.disable_auto_update()
        m.enabled = bool(on)
        self._save()

    def _relabel(self, m: Mapping, text: str) -> None:
        """Label field handler (blur / enter): store a changed label."""
        ft.context.disable_auto_update()
        text = (text or "").strip()
        if text != m.label:
            m.label = text
            self._save()

    def _delete(self, m: Mapping) -> None:
        """Delete a mapping."""
        ft.context.disable_auto_update()
        self.midi.profile.mappings = [x for x in self.midi.profile.mappings if x.id != m.id]
        self._save()
        self.rebuild_mappings()

    def _edit(self, m: Mapping | None) -> None:
        """Open the editor for a mapping, or a new one.

        Args:
            m: the mapping to edit, or None to add.
        """
        ft.context.disable_auto_update()
        MappingDialog(self.app, self.midi, m, self._on_saved).show()

    def _on_saved(self, m: Mapping, is_new: bool) -> None:
        """Dialog Save: add or replace the mapping and persist.

        Args:
            m: the edited copy.
            is_new: whether it is a new mapping.
        """
        maps = self.midi.profile.mappings
        if is_new:
            maps.append(m)
        else:
            maps[:] = [m if x.id == m.id else x for x in maps]
        self._save()
        self.rebuild_mappings()
