"""Guitar tuner window: the note, how far off it is, and which string.

A separate program (``python -m katatone.gui.tuner [--card KATANA3]``) started
from the main window's Tuner button, built like the analyzer: it only listens
to the sound card and shares nothing with the editor.  Layout: the input
selector along the top, the tuning and reference pitch below it, the detected
note large in the middle with a needle showing the error in cents, and a chip
per string (click one to tune to it whatever is played).  The intonation switch
listens for both the open string and its 12th fret: the open string is tuned as
usual and remembered, and the note at the 12th fret is compared with twice that
pitch, which shows whether the saddle is in the right place.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
import traceback

import flet as ft
import flet.canvas as cv
import numpy as np

from ..audio import pitch
from ..audio.capture import RATE, Capture, CaptureError
from ..audio.devices import list_input_devices, preferred
from . import params_view as pv, theme

FPS = 20
#: The needle spans this many cents either side of the centre.
RANGE_CENTS = 50.0
#: Space kept free at both ends so the end labels are not cut off.
MARGIN = 24.0
TICKS = tuple(range(-50, 51, 5))
#: The needle displays: the scale's span either side of the centre in cents, the ticks
#: and the spacing of the labelled ones.
DISPLAYS = {
    "needle": dict(span=RANGE_CENTS, ticks=TICKS, major=25),
    "fine": dict(span=10.0, ticks=tuple(range(-10, 11)), major=5),
}
DISPLAY_LABELS = {"needle": "Needle (±50 cents)", "fine": "Fine needle (±10 cents)",
                  "strobe": "Strobe"}
#: Strobe: the bars' spacing and width (px), how fast they drift per cent of error
#: (px/s) and the fastest drift.  In tune they stand still.
STROBE_SPACING = 40.0
STROBE_BAR = 20.0
STROBE_SPEED = 8.0
STROBE_MAX_SPEED = 360.0
IN_TUNE_COLOR = theme.OK
OFF_COLOR = theme.ACCENT


def cents_x(cents: float, width: float, span: float = RANGE_CENTS) -> float:
    """Horizontal position of an error in cents on the meter (0 in the middle,
    clamped to the ends of a scale ``span`` cents either side of it)."""
    c = max(-span, min(span, cents))
    return MARGIN + (width - 2 * MARGIN) * (0.5 + c / (2 * span))


def meter_shapes(width: float, height: float, cents: float | None, in_tune: bool,
                 span: float = RANGE_CENTS, ticks=TICKS, major: int = 25) -> list:
    """The tuning meter: a scale in cents, the in-tune zone and the needle.

    Args:
        width: canvas width.
        height: canvas height.
        cents: the error to show, or None for no needle.
        in_tune: colour the needle as in tune.
        span: the scale runs this many cents either side of the centre.
        ticks: where the scale has ticks, in cents.
        major: every tick that is a multiple of this is long and labelled.
    Returns:
        Canvas shapes.
    """
    top, base = height * 0.30, height * 0.80
    shapes: list = []
    zone = pitch.IN_TUNE_CENTS
    shapes.append(cv.Rect(cents_x(-zone, width, span), top,
                          cents_x(zone, width, span) - cents_x(-zone, width, span),
                          base - top, paint=ft.Paint(color="#334f9d69", style=ft.PaintingStyle.FILL)))
    shapes.append(cv.Line(MARGIN, base, width - MARGIN, base, paint=ft.Paint(color=theme.PANEL_EDGE, stroke_width=1)))
    for t in ticks:
        x = cents_x(t, width, span)
        long = t % major == 0
        shapes.append(cv.Line(x, base, x, base - (height * 0.28 if long else height * 0.14),
                              paint=ft.Paint(color=theme.TICK, stroke_width=1)))
        if long:
            shapes.append(cv.Text(x, base + 4, f"{t:+d}" if t else "0",
                                  alignment=ft.Alignment.TOP_CENTER,
                                  style=ft.TextStyle(size=10, color=theme.MUTED)))
    if cents is not None:
        x = cents_x(cents, width, span)
        color = IN_TUNE_COLOR if in_tune else OFF_COLOR
        shapes.append(cv.Line(x, top - 6, x, base, paint=ft.Paint(color=color, stroke_width=3)))
        shapes.append(cv.Circle(x, top - 6, 6, paint=ft.Paint(color=color, style=ft.PaintingStyle.FILL)))
    return shapes


def strobe_speed(cents: float | None) -> float:
    """How fast the strobe's bars drift, in px/s: to the right when sharp, to the
    left when flat, none when nothing is heard."""
    if cents is None:
        return 0.0
    return max(-STROBE_MAX_SPEED, min(STROBE_MAX_SPEED, cents * STROBE_SPEED))


def strobe_shapes(width: float, height: float, phase: float, cents: float | None,
                  in_tune: bool) -> list:
    """The strobe display: bars that stand still when the string is in tune and
    drift right when it is sharp, left when flat (faster the further off).

    Args:
        width: canvas width.
        height: canvas height.
        phase: how far the bars have drifted, in px (see :func:`strobe_speed`).
        cents: the error, or None when nothing is heard (the bars are grey).
        in_tune: colour the bars as in tune.
    Returns:
        Canvas shapes.
    """
    top, base = height * 0.15, height * 0.85
    color = theme.TICK if cents is None else (IN_TUNE_COLOR if in_tune else OFF_COLOR)
    left, right = MARGIN, width - MARGIN
    shapes: list = [cv.Rect(left, top, right - left, base - top,
                            paint=ft.Paint(color=theme.PANEL_EDGE, style=ft.PaintingStyle.STROKE,
                                           stroke_width=1))]
    x = left - STROBE_SPACING + phase % STROBE_SPACING
    while x < right:
        x1, x2 = max(x, left), min(x + STROBE_BAR, right)
        if x2 > x1:
            shapes.append(cv.Rect(x1, top + 2, x2 - x1, base - top - 4,
                                  paint=ft.Paint(color=color, style=ft.PaintingStyle.FILL)))
        x += STROBE_SPACING
    mid = width / 2
    shapes.append(cv.Line(mid, top - 8, mid, top - 1, paint=ft.Paint(color=theme.MUTED, stroke_width=2)))
    return shapes


def direction_hint(reading: "pitch.Reading | None", intonation: bool = False) -> tuple[str, str]:
    """What to do next, as ``(text, icon name)`` (icon '' for none).

    Args:
        reading: what the tuner heard.
        intonation: the intonation check is on (an open string in tune is followed by the 12th fret).
    """
    if reading is None:
        return "Play a string", ""
    if intonation and not reading.fret and reading.in_tune:
        return "Open string in tune: now play the 12th fret", "CHECK_CIRCLE"
    if reading.fret:                  # intonation: the fretted note against the open string's octave
        if reading.in_tune:
            return "Intonation is right", "CHECK_CIRCLE"
        return ("Sharp: move the saddle back", "ARROW_DOWNWARD") if reading.cents > 0 \
            else ("Flat: move the saddle forward", "ARROW_UPWARD")
    if reading.in_tune:
        return "In tune", "CHECK_CIRCLE"
    return ("Too low: tune up", "ARROW_UPWARD") if reading.cents < 0 \
        else ("Too high: tune down", "ARROW_DOWNWARD")


def detail_text(reading: "pitch.Reading | None") -> str:
    """The line under the hint: the pitch, what it is compared with and the error."""
    if reading is None:
        return ""
    if not reading.fret:
        return (f"{reading.freq:.1f} Hz   target {reading.target:.1f} Hz   "
                f"{reading.cents:+.0f} cents")
    if reading.open_freq is None:
        against = (f"exact octave {reading.target:.1f} Hz "
                   "(play the open string first to compare with it)")
    else:
        against = (f"twice the open string, {reading.target:.1f} Hz "
                   f"({reading.open_freq:.1f} Hz heard {reading.open_age:.0f} s ago)")
    return (f"12th fret {reading.freq:.1f} Hz   {against}   {reading.cents:+.0f} cents")


class TunerWindow:
    """The window: builds the controls and runs the capture / update loops."""

    #: Tells the server it may pass ``on_mute`` (the Analyzer has no such option).
    wants_mute = True

    def __init__(self, page: ft.Page, card: str | None = None,
                 on_mute=None) -> None:
        """Build the window and start listening.

        Args:
            page: the Flet page.
            card: ALSA card id to preselect (e.g. ``KATANA3``).
            on_mute: called with True / False to mute / restore the amp's
                output while tuning; None (no amp link) disables the checkbox.
        """
        self.page = page
        self._on_mute = on_mute
        self._muted = False
        page.title = "KataTone Tuner"
        page.bgcolor = theme.BG
        page.theme_mode = ft.ThemeMode.DARK
        page.window.width, page.window.height = 640, 690
        page.window.min_width, page.window.min_height = 480, 570
        page.padding = 12

        self.lock = threading.Lock()
        self.tuner = pitch.Tuner(RATE)
        self.capture: Capture | None = None
        self._error = ""
        self._running = True
        self._warned = False
        self._meter_size = (560.0, 110.0)
        self._phase = 0.0                      # the strobe's drift (px)
        self._phase_time = time.monotonic()
        self._chips: list[ft.Container] = []

        devices = list_input_devices()
        chosen = preferred(devices, card)
        self.device = ft.Dropdown(
            label="Sound card input", expand=True,
            options=[ft.DropdownOption(key=d.name, text=d.label) for d in devices],
            value=chosen.name if chosen else None,
            on_select=lambda e: self._start(), **pv.FIELD_STYLE)
        self.channel = ft.Dropdown(
            label="Channel", width=110, value="1",
            options=[ft.DropdownOption(key=str(c), text=str(c)) for c in (1, 2, 3, 4)],
            on_select=lambda e: self._start(), **pv.FIELD_STYLE)
        self.toggle = pv.button("Stop", ft.Icons.STOP, lambda e: self._toggle(), primary=True)
        self.mode = ft.Dropdown(
            label="Tuning", expand=True, value=next(iter(pitch.TUNINGS)),
            options=[ft.DropdownOption(key=k, text=k)
                     for k in (*pitch.TUNINGS, pitch.CHROMATIC)],
            on_select=lambda e: self._set_mode(), **pv.FIELD_STYLE)
        self.intonation = ft.Checkbox(
            label="Check intonation (12th fret)", value=False,
            tooltip=("Play the open string and tune it, then the same string at the 12th fret: "
                     "it should be exactly one octave higher. Both are recognised. Sharp: move "
                     "the saddle back (longer string); flat: move it forward (shorter)"),
            on_change=lambda e: self._set_intonation())
        self.display = ft.Dropdown(
            label="Display", width=210, value="needle",
            options=[ft.DropdownOption(key=k, text=t) for k, t in DISPLAY_LABELS.items()],
            on_select=lambda e: self._set_display(), **pv.FIELD_STYLE)
        self.reference = ft.Dropdown(
            label="Reference", width=170, value="440",
            options=[ft.DropdownOption(key=str(r), text=f"A4 = {r} Hz") for r in pitch.REFERENCES],
            on_select=lambda e: self._set_reference(), **pv.FIELD_STYLE)

        self.note = ft.Text("--", size=120, weight=ft.FontWeight.W_900, color=theme.MUTED,
                            text_align=ft.TextAlign.CENTER)
        self.hint_icon = ft.Icon(ft.Icons.MUSIC_NOTE, color=theme.MUTED, size=22)
        self.hint = ft.Text("Play a string", size=18, color=theme.MUTED)
        self.detail = ft.Text("", size=13, color=theme.MUTED, text_align=ft.TextAlign.CENTER)
        self.meter = cv.Canvas(expand=True, height=110, resize_interval=100,
                               on_resize=self._resized)
        self.strings = ft.Row([], alignment=ft.MainAxisAlignment.CENTER, spacing=8, wrap=True)
        self.status = ft.Text("", size=12, color=theme.MUTED)
        self.mute = ft.Checkbox(
            label="Mute the amp while tuning", value=False, disabled=on_mute is None,
            tooltip=("Mutes the amp's output; unchecking, stopping the tuner or closing "
                     "the window restores it" if on_mute else "No connection to the amp"),
            on_change=lambda e: self._set_mute(bool(self.mute.value)))
        self._build_chips()
        self.help = ft.Text("", size=11, color=theme.MUTED)
        self._set_help()

        page.add(ft.Column([
            ft.Container(ft.Row([self.device, self.channel, self.toggle], spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER),
                         padding=ft.Padding(left=0, right=0, top=8, bottom=0)),
            ft.Row([self.mode, self.reference], spacing=8),
            ft.Row([self.intonation, self.display], spacing=8, wrap=True,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(ft.Column([
                ft.Row([self.note], alignment=ft.MainAxisAlignment.CENTER),
                ft.Row([self.hint_icon, self.hint], alignment=ft.MainAxisAlignment.CENTER,
                       spacing=6),
                self.detail,
                ft.Row([self.meter]),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=4),
                padding=12, bgcolor=theme.KNOB_LO, border=ft.Border.all(1, theme.PANEL_EDGE),
                border_radius=6),
            self.mute,
            self.strings,
            self.help,
            self.status,
        ], expand=True, spacing=10))
        self._draw_meter(None, False)
        self._start()
        page.run_task(self._loop)

    # ------------------------------------------------------------ settings

    def _build_chips(self) -> None:
        """One chip per string of the current tuning."""
        tuning = self.tuner.tuning or ()
        check = len(self.tuner.frets) > 1               # open string and 12th fret
        self._chips = []
        for i, name in enumerate(tuning):
            hz = pitch.note_freq(name, self.tuner.a4)
            lines = [ft.Text(name, size=20, weight=ft.FontWeight.BOLD, color=theme.FOREGROUND),
                     ft.Text(f"{hz:.1f} Hz", size=10, color=theme.MUTED)]
            if check:
                octave = pitch.midi_to_name(pitch.note_to_midi(name) + pitch.INTONATION_FRET)
                lines.append(ft.Text(f"12th {octave} {2 * hz:.0f} Hz", size=10, color=theme.MUTED))
            chip = ft.Container(
                ft.Column(lines, spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                width=104 if check else 84, padding=8, border_radius=6, bgcolor=theme.PANEL_TOP,
                border=ft.Border.all(1, theme.PANEL_EDGE),
                on_click=lambda e, k=i: self._lock(k))
            self._chips.append(chip)
        self.strings.controls = list(self._chips)

    def _lock(self, i: int) -> None:
        """Tune to string ``i`` regardless of what is played (again: release)."""
        with self.lock:
            self.tuner.locked = None if self.tuner.locked == i else i

    def _set_mode(self) -> None:
        """The tuning dropdown changed."""
        with self.lock:
            self.tuner.set_mode(self.mode.value or next(iter(pitch.TUNINGS)))
        chromatic = self.tuner.tuning is None
        self.intonation.disabled = chromatic         # it needs strings to compare with
        self._update(self.intonation)
        self._refresh_strings()

    def _set_intonation(self) -> None:
        """The intonation checkbox changed: listen for the open strings and their 12th frets, or
        only the open strings."""
        with self.lock:
            self.tuner.set_intonation(bool(self.intonation.value))
        self._refresh_strings()

    def _refresh_strings(self) -> None:
        """Redraw the chips and the help line after the targets changed."""
        self._build_chips()
        self._set_help()
        self._update(self.strings)
        self._update(self.help)

    def _set_help(self) -> None:
        """The hint under the chips, for the current check."""
        self.help.value = (
            "Intonation: play the open string and tune it, then fret the same string at the "
            "12th fret (not a harmonic): it is compared with twice the open pitch. Move the "
            "saddle and repeat both until each reads in tune. Click a string to stay on it "
            "(needed when two strings are an octave apart, as in Drop D)."
            if len(self.tuner.frets) > 1 else
            "Click a string to tune to it whatever is played; click it again for automatic.")

    def _set_reference(self) -> None:
        """The reference pitch changed."""
        with self.lock:
            self.tuner.a4 = float(self.reference.value or 440)
        self._build_chips()
        self._update(self.strings)

    def _set_mute(self, mute: bool) -> None:
        """Mute the amp (or restore it) and keep the checkbox in step."""
        if self._on_mute is None or mute == self._muted:
            return
        self._muted = mute
        self.mute.value = mute
        self._update(self.mute)
        try:
            self._on_mute(mute)
        except Exception as e:                        # noqa: BLE001
            self._muted = False
            self.mute.value = False
            self._error = f"Could not change the amp's mute: {e}"

    # ------------------------------------------------------------ capture

    def _on_block(self, block: np.ndarray) -> None:
        """Capture thread: keep the audio for the next detection."""
        with self.lock:
            self.tuner.push(block)

    def _on_error(self, message: str) -> None:
        """Capture thread: remember the failure for the status line."""
        self._error = message

    def _start(self) -> None:
        """Start listening to the chosen device and channel."""
        self._stop()
        name = self.device.value
        if not name:
            self._error = "No sound card input found (is arecord installed?)."
            return
        self._error = ""
        try:
            cap = Capture(name, int(self.channel.value or 1), self._on_block, self._on_error)
            cap.start()
        except CaptureError as e:
            self._error = str(e)
            return
        self.capture = cap
        self.toggle.content = ft.Text("Stop", no_wrap=True)
        self.toggle.icon = ft.Icons.STOP

    def _stop(self) -> None:
        """Stop listening."""
        self._set_mute(False)               # stopping the tuner restores the amp
        cap, self.capture = self.capture, None
        if cap is not None:
            cap.stop()
        self.toggle.content = ft.Text("Start", no_wrap=True)
        self.toggle.icon = ft.Icons.PLAY_ARROW

    def _toggle(self) -> None:
        """Start / Stop button."""
        if self.capture is None:
            self._start()
        else:
            self._stop()
        self._update(self.toggle)

    # ------------------------------------------------------------ drawing

    @staticmethod
    def _update(control) -> None:
        """Push a control, ignoring "not on a page yet"."""
        try:
            control.update()
        except RuntimeError:
            pass

    def _resized(self, e) -> None:
        """Canvas resize: remember the size."""
        self._meter_size = (float(e.width), float(e.height))

    def _set_display(self) -> None:
        """Display dropdown: redraw in the chosen style."""
        self._draw_meter(None, False)

    def _draw_meter(self, cents: float | None, in_tune: bool) -> None:
        """Redraw the needle, or the strobe."""
        w, h = self._meter_size
        style = self.display.value
        if style == "strobe":
            now = time.monotonic()
            dt = min(now - self._phase_time, 0.2)     # a stalled loop must not jump
            self._phase_time = now
            self._phase += strobe_speed(cents) * dt
            self.meter.shapes = strobe_shapes(w, h, self._phase, cents, in_tune)
        else:
            self.meter.shapes = meter_shapes(w, h, cents, in_tune, **DISPLAYS[style])
        self._update(self.meter)

    async def _loop(self) -> None:
        """Redraw at :data:`FPS`, on the page's own event loop (as the analyzer does)."""
        while self._running:
            await asyncio.sleep(1.0 / FPS)
            try:
                self._tick()
            except Exception:                         # noqa: BLE001
                if not self._running:
                    return
                traceback.print_exc()

    def _tick(self) -> None:
        """Detect the pitch and update the display."""
        listening = self.capture is not None and not self._error
        with self.lock:
            reading = self.tuner.reading() if listening else None
            locked = self.tuner.locked
        self.apply(reading, locked)
        self.status.value = self._error or ("" if listening else "Stopped.")
        self.status.color = theme.DANGER if self._error else theme.MUTED
        self._update(self.status)

    def apply(self, reading: "pitch.Reading | None", locked: int | None) -> None:
        """Show a reading (None: nothing heard).

        Args:
            reading: what the tuner heard.
            locked: the string being tuned to by choice, if any.
        """
        color = theme.MUTED if reading is None else (
            IN_TUNE_COLOR if reading.in_tune else OFF_COLOR)
        self.note.value = reading.note if reading else "--"
        self.note.color = color
        text, icon = direction_hint(reading, len(self.tuner.frets) > 1)
        self.hint.value, self.hint.color = text, color
        self.hint_icon.icon = getattr(ft.Icons, icon) if icon else ft.Icons.MUSIC_NOTE
        self.hint_icon.color = color
        self.detail.value = detail_text(reading)
        self._draw_meter(reading.cents if reading else None, bool(reading and reading.in_tune))
        for i, chip in enumerate(self._chips):
            active = reading is not None and reading.string == i
            chip.bgcolor = theme.TAB_ACTIVE if i == locked else theme.PANEL_TOP
            chip.border = ft.Border.all(2 if active or i == locked else 1,
                                        color if active else (theme.ACCENT if i == locked
                                                              else theme.PANEL_EDGE))
        for c in (self.note, self.hint_icon, self.hint, self.detail, *self._chips):
            self._update(c)

    def close(self) -> None:
        """Stop the loop and the capture."""
        self._running = False
        self._stop()


def main(argv: list[str] | None = None) -> None:
    """Run the tuner window.

    Args:
        argv: arguments (default ``sys.argv[1:]``): ``--card NAME`` to
            preselect a sound card, ``--web [--port N]`` to open in the
            browser; unknown (Flet) arguments are ignored.
    """
    import argparse
    import os
    parser = argparse.ArgumentParser(prog="katatone.gui.tuner", add_help=False)
    parser.add_argument("--card", default=None)
    parser.add_argument("--web", action="store_true", default=False)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--mute-pipe", action="store_true", default=False)
    opts, _ = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    windows: list[TunerWindow] = []
    # Started by the main window: it passes mute requests on to the amp ("MUTE 1" /
    # "MUTE 0" lines on stdout), and restores the amp when this process ends.
    pipe = (lambda m: print(f"MUTE {int(m)}", flush=True)) if opts.mute_pipe else None
    build = lambda page: windows.append(TunerWindow(page, opts.card, pipe))   # noqa: E731
    if opts.web:       # a tab in the default browser instead of a window
        ft.run(main=build, view=ft.AppView.WEB_BROWSER, host="127.0.0.1", port=opts.port,
               no_cdn=True)
    else:
        ft.run(main=build)
    for w in windows:
        w.close()
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
