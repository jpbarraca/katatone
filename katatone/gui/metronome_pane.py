"""The METRONOME tab: a click track with tap tempo, played to a sound card.

The tempo, the bar and the output are set here; a footswitch mapped to a
"Metronome" action (see the MIDI tab) starts and stops it, taps the tempo or
changes it.  The click goes out through ``aplay`` (see :mod:`katatone.audio.metronome`),
so it needs ALSA's ``alsa-utils`` and a sound card; it does not talk to the amp.
"""
from __future__ import annotations

import atexit
import threading
import time
from typing import TYPE_CHECKING

import flet as ft

from ..audio import metronome as mt
from ..audio.devices import list_output_devices
from ..audio.reference import TapTempo
from . import params_view as pv, theme

if TYPE_CHECKING:
    from .app import App

TAB_METRONOME = "METRONOME"
#: The output channels offered: "all" plays the click on both channels of a stereo pair.
CHANNELS = {"all": "Both (stereo)", **{str(n): f"Channel {n}" for n in range(1, 5)}}
SUBDIVISIONS = {"1": "Beats only", "2": "Eighths (2 per beat)", "3": "Triplets (3 per beat)",
                "4": "Sixteenths (4 per beat)"}


def build(app: App) -> ft.Control:
    """Instantiate the pane, keep it on ``app.metronome_view`` and return its root control."""
    app.metronome_view = _MetronomePane(app)
    return app.metronome_view.root


class _MetronomePane:
    def __init__(self, app: App) -> None:
        """Build the tempo display, the controls, the beat lights and the output selectors."""
        self.app = app
        self.tap = TapTempo()
        self.engine = mt.Metronome(on_click=self._clicked, on_error=self._failed)
        atexit.register(self.engine.stop)
        self.bpm_text = ft.Text("", size=72, weight=ft.FontWeight.W_900, color=theme.ACCENT,
                                text_align=ft.TextAlign.CENTER)
        self.state = ft.Text("Stopped", size=14, color=theme.MUTED, text_align=ft.TextAlign.CENTER)
        self.slider = ft.Slider(min=mt.BPM_MIN, max=mt.BPM_MAX, value=self.engine.bpm,
                                divisions=int(mt.BPM_MAX - mt.BPM_MIN), expand=True,
                                active_color=theme.ACCENT,
                                on_change=lambda e: self._set_bpm(e.control.value))
        self.lights = ft.Row([], spacing=10, alignment=ft.MainAxisAlignment.CENTER)
        self.start_btn = pv.button("Start", ft.Icons.PLAY_ARROW, lambda e: self.op("toggle"),
                                   primary=True)
        # `aplay -L` can take a while: the list is filled in by a thread (see _load_outputs).
        self.output = ft.Dropdown(
            label="Output", width=360, value="default",
            on_select=lambda e: self._output_changed(),
            options=[ft.DropdownOption(key="default", text="default")], **pv.FIELD_STYLE)
        self.channel = self._dropdown("Click on", CHANNELS, "all", 170,
                                      lambda e: self._output_changed())
        self.beats = self._dropdown("Beats per bar", {str(n): str(n) for n in range(1, mt.BEATS_MAX + 1)},
                                    str(self.engine.gen.beats), 140, lambda e: self._bar_changed())
        self.subdivision = self._dropdown("Subdivision", SUBDIVISIONS, "1", 220,
                                          lambda e: self._bar_changed())
        self.volume = ft.Slider(min=0, max=1, value=self.engine.gen.volume, divisions=20, expand=True,
                                active_color=theme.ACCENT,
                                on_change=lambda e: setattr(self.engine.gen, "volume", e.control.value))
        tempo = ft.Column([
            ft.Container(self.bpm_text, alignment=ft.Alignment.CENTER),
            ft.Container(ft.Text("beats per minute", size=12, color=theme.MUTED),
                         alignment=ft.Alignment.CENTER),
            ft.Row([pv.button("-5", ft.Icons.REMOVE, lambda e: self.op("tempo_down", 5)),
                    pv.button("-1", ft.Icons.REMOVE, lambda e: self.op("tempo_down", 1)),
                    self.slider,
                    pv.button("+1", ft.Icons.ADD, lambda e: self.op("tempo_up", 1)),
                    pv.button("+5", ft.Icons.ADD, lambda e: self.op("tempo_up", 5))],
                   spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([self.start_btn, pv.button("Tap tempo", ft.Icons.TOUCH_APP, lambda e: self.op("tap")),
                    ft.Container(self.state, padding=ft.Padding(left=12, right=0, top=0, bottom=0))],
                   spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            self.lights,
        ], spacing=10)
        setup = ft.Column([
            ft.Row([self.beats, self.subdivision], spacing=10, wrap=True),
            ft.Row([ft.Text("Volume", size=12, color=theme.MUTED, width=60), self.volume], spacing=8),
            ft.Row([self.output, self.channel], spacing=10, wrap=True),
            ft.Text("The click is played through the chosen sound card output.  Pick the amp's "
                    "card to hear it in the amp's headphones or speaker, or another output to keep it "
                    "out of the amp.  A footswitch can start, stop and tap it: see MIDI > Mappings > "
                    "Metronome.", size=11, color=theme.MUTED),
        ], spacing=10)
        self.root = ft.Column([pv.module_card("Metronome", [tempo]),
                               pv.module_card("Sound", [setup])],
                              expand=True, spacing=12, scroll=ft.ScrollMode.AUTO)
        self._lit = -1
        self._rebuild_lights()
        self._show_bpm()
        self._outputs_thread = threading.Thread(target=self._load_outputs, daemon=True)
        self._outputs_thread.start()

    def _load_outputs(self) -> None:
        """List the sound card outputs (slow) and offer them; the choice made meanwhile,
        if any, is kept."""
        devices = list_output_devices()
        if not devices:
            return
        names = [d.name for d in devices]
        self.output.options = [ft.DropdownOption(key=d.name, text=d.label) for d in devices]
        if self.output.value not in names:
            self.output.value = "default" if "default" in names else names[0]
        try:
            self.output.page                # raises until the dropdown is on the page
        except RuntimeError:
            return                          # it shows the new options when it gets there
        self.output.update()

    def stop(self) -> None:
        """Stop the click (the app is closing or its amp session ends)."""
        self.engine.stop()

    @staticmethod
    def _dropdown(label: str, options: dict[str, str], value: str, width: int,
                  on_select) -> ft.Dropdown:
        """A dense dropdown over a {key: text} table."""
        return ft.Dropdown(label=label, value=value, width=width, on_select=on_select,
                           options=[ft.DropdownOption(key=k, text=t) for k, t in options.items()],
                           **pv.FIELD_STYLE)

    # -- what the controls mean --------------------------------------------

    def op(self, op: str, step: int = 5) -> None:
        """Carry out a metronome command, from a button or a MIDI mapping.

        Args:
            op: one of :data:`katatone.midi.mapping.METRONOME_OPS`.
            step: BPM added or taken away by ``tempo_up`` / ``tempo_down``.
        """
        if op == "toggle":
            op = "stop" if self.engine.running else "start"
        if op == "start":
            self._start()
        elif op == "stop":
            self._stop()
        elif op == "tap":
            bpm = self.tap.tap(time.monotonic())
            if bpm is not None:
                self._set_bpm(bpm)
        elif op in ("tempo_up", "tempo_down"):
            self._set_bpm(self.engine.bpm + (step if op == "tempo_up" else -step))
        self._refresh()

    def _read_settings(self) -> None:
        """Copy the bar, the subdivision and the output routing into the engine."""
        gen = self.engine.gen
        gen.beats = int(self.beats.value or 4)
        gen.subdivision = int(self.subdivision.value or 1)
        self.engine.device = self.output.value or "default"
        ch = self.channel.value or "all"
        self.engine.channels = 2 if ch == "all" else max(2, int(ch))
        self.engine.route = None if ch == "all" else (int(ch) - 1,)

    def _start(self) -> None:
        """Start the click with the settings on screen."""
        self._read_settings()
        try:
            self.engine.start()
        except OSError as e:
            self.app.set_status(f"Metronome: cannot run aplay ({e})")

    def _stop(self) -> None:
        """Stop the click."""
        self.engine.stop()
        self._lit = -1

    def _set_bpm(self, bpm: float) -> None:
        """Set the tempo (from the slider, tap or a button) and show it."""
        self.engine.set_bpm(round(bpm))
        self._show_bpm()
        try:
            self.slider.value = self.engine.bpm
            self.slider.update()
        except RuntimeError:
            pass

    def _bar_changed(self) -> None:
        """The bar or the subdivision changed: heard from the next click."""
        ft.context.disable_auto_update()
        gen = self.engine.gen
        gen.beats = int(self.beats.value or 4)
        gen.subdivision = int(self.subdivision.value or 1)
        self._refresh()

    def _output_changed(self) -> None:
        """The output or channel changed: restart the click on the new one if it is playing."""
        ft.context.disable_auto_update()
        if self.engine.running:
            self.engine.stop()
            self._start()
            self._refresh()

    # -- engine callbacks and drawing --------------------------------------

    def _clicked(self, beat: int, part: int) -> None:
        """A click was made (writer thread): light the beat's lamp."""
        if part == 0 and beat != self._lit:
            self._lit = beat
            self._paint_lights()

    def _failed(self, msg: str) -> None:
        """``aplay`` stopped: say why."""
        self.app.set_status(f"Metronome stopped: {msg}")
        self._lit = -1
        self._refresh()

    def _show_bpm(self) -> None:
        """Show the tempo."""
        self.bpm_text.value = f"{self.engine.bpm:.0f}"
        try:
            self.bpm_text.update()
        except RuntimeError:
            pass

    def _rebuild_lights(self) -> None:
        """One lamp per beat of the bar."""
        n = max(1, int(self.engine.gen.beats))
        self.lights.controls = [ft.Container(width=26, height=26, border_radius=13,
                                             bgcolor=theme.KNOB_LO,
                                             border=ft.Border.all(1, theme.PANEL_EDGE))
                                for _ in range(n)]

    def _paint_lights(self) -> None:
        """Light the lamp of the current beat (the first one in the accent colour)."""
        for i, lamp in enumerate(self.lights.controls):
            on = i == self._lit and self.engine.running
            lamp.bgcolor = (theme.ACCENT if i == 0 else theme.OK) if on else theme.KNOB_LO
        try:
            self.lights.update()
        except RuntimeError:
            pass

    def _refresh(self) -> None:
        """Redraw the state line, the start button and the lamps."""
        running = self.engine.running
        self.state.value = (f"Playing, {self.engine.gen.beats} beats per bar" if running
                            else "Stopped")
        self.start_btn.content.value = "Stop" if running else "Start"
        self.start_btn.icon = ft.Icons.STOP if running else ft.Icons.PLAY_ARROW
        if len(self.lights.controls) != max(1, int(self.engine.gen.beats)):
            self._rebuild_lights()
        self._paint_lights()
        try:
            self.root.update()
        except RuntimeError:
            pass
