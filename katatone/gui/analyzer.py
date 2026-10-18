"""Analyzer window: level meter and spectrum of one sound-card channel.

A separate program (``python -m katatone.gui.analyzer [--card KATANA3]``)
started from the main window's Analyzer button.  It only listens to the
sound card; it shares nothing with the editor window.  Layout: the input
selector along the top, the vertical level meter on the left and the
spectrum on the right.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from collections import deque
import traceback

import flet as ft
import flet.canvas as cv
import numpy as np

from ..audio import bands, comparison, content, dsp, envelope, reference
from ..audio.capture import RATE, Capture, CaptureError
from ..audio.devices import list_input_devices, preferred
from . import files, params_view as pv, plot, theme

FPS = 15
METER_MIN_DB = -60.0
SPEC_MIN_DB, SPEC_MAX_DB = -120.0, 0.0
FREQ_LABELS = (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)
FREQ_LABELS_LINEAR = tuple(range(1000, int(dsp.F_MAX_LINEAR) + 1, 1000))
STRING_COLOR = "#4f9d69"
#: Colours of the held spectra drawn behind the live one, and of the difference curve.
TRACE_COLORS = {"reference": "#6aa6ff", "A": "#8bc34a", "B": "#ba68c8"}
DIFF_RANGE_DB = 12.0
DIFF_ABOVE, DIFF_BELOW = "#d7a03e", "#4a90d9"
HINT_PERIOD_S = 0.5
#: How long a notice (a file that could not be read ...) stays on the status line.
NOTICE_S = 8.0
#: Seconds of playing kept for the spectrogram and for the analysis of the notes, and how
#: often that analysis is repeated.
HISTORY_S = 12.0
INSIGHT_PERIOD_S = 3.0
SPECTROGRAM_S = 15.0
SG_MIN_DB, SG_MAX_DB = -100.0, -20.0
#: The lower panel's height in pixels: the start value and the range the divider allows, and
#: the least height kept for the spectrum above it.
LOWER_DEFAULT, LOWER_MIN, LOWER_MAX, SPEC_MIN_HEIGHT = 120.0, 60.0, 600.0, 120.0
PREVIEW_COLOR = "#ff8a65"
EQ_KINDS = {"off": "Off", "GE-10": "Graphic EQ (GE-10)", "PEQ": "Parametric EQ (PEQ)"}
LOWER_PANELS = {"diff": "Difference", "sg": "Spectrogram"}
COMPARE_LABELS = {"ref": "Live vs reference", "A": "Live vs take A", "B": "Live vs take B",
                  "AB": "Take A vs take B"}
ZONE_COLORS = ("#22d7a03e", "#228a93a0")      # alternating translucent bands
CLIP_DB = -0.1
SPEC_POINTS = 160


def _stroke(color: str, width: float = 1) -> ft.Paint:
    """A line paint."""
    return ft.Paint(color=color, stroke_width=width, style=ft.PaintingStyle.STROKE)


def _fill(color: str) -> ft.Paint:
    """A fill paint."""
    return ft.Paint(color=color, style=ft.PaintingStyle.FILL)


def _label(x: float, y: float, text: str, align=None) -> cv.Text:
    """A small grey canvas label."""
    return cv.Text(x, y, text, alignment=align,
                   style=ft.TextStyle(size=10, color=theme.MUTED))


def meter_y(db: float, height: float) -> float:
    """Vertical position of ``db`` on the level meter (0 dB at the top)."""
    frac = (min(0.0, max(METER_MIN_DB, db)) - METER_MIN_DB) / -METER_MIN_DB
    return height * (1.0 - frac)


def freq_x(f: float, width: float, linear: bool = False) -> float:
    """Horizontal position of frequency ``f`` on the log (default) or linear
    axis, spanning :data:`dsp.F_MIN` to :data:`dsp.F_MAX` (log) or :data:`dsp.F_MAX_LINEAR`."""
    top = dsp.f_max(linear)
    f = min(top, max(dsp.F_MIN, f))
    if linear:
        return float(width * (f - dsp.F_MIN) / (top - dsp.F_MIN))
    lo, hi = np.log10(dsp.F_MIN), np.log10(top)
    return float(width * (np.log10(f) - lo) / (hi - lo))


def string_shapes(w: float, h: float, linear: bool = False) -> list:
    """The guitar strings as sloped lines over the spectrum.

    Each string runs from its lowest note (the open string) at the bottom of
    the plot to its highest note (:data:`bands.FRETS` frets up) at the top, so
    the line's slope and its two ends show which frequencies the string covers.

    Args:
        w: plot width.
        h: plot height.
        linear: linear (not logarithmic) frequency axis.
    Returns:
        Canvas shapes: a line and the two note labels for every string.
    """
    shapes: list = []
    style = ft.TextStyle(size=10, color=STRING_COLOR)
    for i, (name, f_lo, top, f_hi) in enumerate(bands.string_ranges()):
        x0, x1 = freq_x(f_lo, w, linear), freq_x(f_hi, w, linear)
        shapes.append(cv.Line(x0, h, x1, 0, paint=_stroke(STRING_COLOR, 1)))
        shapes.append(cv.Text(x0 + 2, h - 44 - (i % 2) * 12, f"{name} {f_lo:.0f}", style=style))
        shapes.append(cv.Text(x1 + 3, 14 + (i % 2) * 12, f"{top} {f_hi:.0f}", style=style))
    return shapes


def spec_y(db: float, height: float) -> float:
    """Vertical position of ``db`` on the spectrum."""
    frac = (min(SPEC_MAX_DB, max(SPEC_MIN_DB, db)) - SPEC_MIN_DB) / (SPEC_MAX_DB - SPEC_MIN_DB)
    return height * (1.0 - frac)


class Analyzer:
    """The window: builds the controls and runs the capture / update loops."""

    def __init__(self, page: ft.Page, card: str | None = None) -> None:
        """Build the window and start listening.

        Args:
            page: the Flet page.
            card: ALSA card id to preselect (e.g. ``KATANA3``).
        """
        self.page = page
        page.title = "KataTone Analyzer"
        page.bgcolor = theme.BG
        page.theme_mode = ft.ThemeMode.DARK
        page.window.width, page.window.height = 1100, 820
        page.window.min_width, page.window.min_height = 720, 520
        page.padding = 12

        self.lock = threading.Lock()
        self.spectrum = dsp.Spectrum(RATE, points=SPEC_POINTS)
        self.smooth = dsp.Smoother(len(self.spectrum.freqs))
        self.hold = dsp.PeakHold()
        self._peak_acc = 0.0
        self._clipped = False
        self.capture: Capture | None = None
        self._error = ""
        self._running = True
        self._warned = False
        self.linear = False
        self._meter_size = (110.0, 360.0)
        self._spec_size = (700.0, 360.0)
        self._diff_size = (700.0, LOWER_DEFAULT)
        self._lower_height = LOWER_DEFAULT
        self.cmp = comparison.Comparison()
        self.tap = reference.TapTempo()
        self._picker = ft.FilePicker()
        self._loaded: reference.Reference | None = None     # handed over by the loader thread
        self._load_error = ""
        self._hint_lines: list[str] = []
        self._hint_at = 0.0
        self.history = dsp.History(HISTORY_S, RATE)
        self._insight = comparison.Insight()
        self._insight_at = 0.0
        self._fits: dict | None = None
        self._columns: deque = deque(maxlen=int(FPS * SPECTROGRAM_S))      # spectrogram, oldest first
        self._notice: tuple[str, str, float] = ("", theme.MUTED, 0.0)   # text, colour, until

        devices = list_input_devices()
        self.devices = {d.name: d for d in devices}
        chosen = preferred(devices, card)
        self.device = ft.Dropdown(
            label="Sound card input", expand=True,
            options=[ft.DropdownOption(key=d.name, text=d.label) for d in devices],
            value=chosen.name if chosen else None,
            on_select=lambda e: self._restart(), **pv.FIELD_STYLE)
        self.channel = ft.Dropdown(
            label="Channel", width=110, value="1",
            options=[ft.DropdownOption(key=str(c), text=str(c)) for c in (1, 2, 3, 4)],
            on_select=lambda e: self._restart(), **pv.FIELD_STYLE)
        self.toggle = pv.button("Stop", ft.Icons.STOP, lambda e: self._toggle(),
                                primary=True)
        self.clear = pv.button("Clear", ft.Icons.CLEAR_ALL, lambda e: self._clear())
        self.scale = ft.Checkbox(
            label="Linear frequency scale", value=False,
            tooltip="Off: logarithmic, 20 Hz to 20 kHz (default); on: linear, 20 Hz to 10 kHz",
            on_change=lambda e: self._set_scale())
        self.show_strings = ft.Checkbox(
            label="Guitar strings", value=False,
            tooltip="Each string in standard tuning, sloping from its open note (E2 A2 D3 G3 B3 E4) "
                    f"up to its highest note at fret {bands.FRETS}",
            on_change=lambda e: self._draw_spectrum_grid())
        self.show_zones = ft.Checkbox(
            label="Tone zones", value=False,
            tooltip="Typical ranges of muddy, boxy, airy ... sounds (approximate)",
            on_change=lambda e: self._draw_spectrum_grid())
        self.status = ft.Text("", size=12, color=theme.MUTED)
        # ---- comparing: a reference recording, takes A and B, tap tempo ----
        self.ref_btn = pv.button("Reference…", ft.Icons.AUDIO_FILE, self._pick_reference)
        self.ref_label = ft.Text("no reference", size=12, color=theme.MUTED, no_wrap=True,
                                 overflow=ft.TextOverflow.ELLIPSIS, width=170)
        self.ref_clear = ft.IconButton(ft.Icons.CLOSE, icon_size=16, tooltip="Remove the reference",
                                       on_click=lambda e: self._clear_reference())
        self.match = ft.Checkbox(
            label="Match level", value=True,
            tooltip="Line the reference and the takes up with your level, so only the "
                    "tone is compared, not the loudness",
            on_change=lambda e: self._set_match())
        self.take_a = pv.button("Set A", ft.Icons.CAMERA, lambda e: self._capture("A"))
        self.take_b = pv.button("Set B", ft.Icons.CAMERA, lambda e: self._capture("B"))
        self.compare_dd = ft.Dropdown(
            label="Compare", width=190, value="ref",
            options=[ft.DropdownOption(key=k, text=v) for k, v in COMPARE_LABELS.items()],
            on_select=lambda e: self._set_compare(), **pv.FIELD_STYLE)
        self.eq_preview = ft.Dropdown(
            label="EQ preview", width=190, value="off",
            options=[ft.DropdownOption(key=k, text=v) for k, v in EQ_KINDS.items()],
            tooltip="Draws your spectrum as it would be with the proposed EQ (orange), and the "
                    "difference that would be left",
            on_select=lambda e: self._set_preview(), **pv.FIELD_STYLE)
        self.lower = ft.Dropdown(
            label="Lower panel", width=160, value="diff",
            options=[ft.DropdownOption(key=k, text=v) for k, v in LOWER_PANELS.items()],
            on_select=lambda e: self._set_lower(), **pv.FIELD_STYLE)
        self.divider = ft.GestureDetector(
            content=ft.Container(ft.Icon(ft.Icons.DRAG_HANDLE, size=14, color=theme.MUTED),
                                 height=12, alignment=ft.Alignment.CENTER, bgcolor=theme.PANEL_EDGE,
                                 border_radius=3,
                                 tooltip="Drag to change the height of the spectrum and the lower "
                                         "panel; double-click to reset"),
            mouse_cursor=ft.MouseCursor.RESIZE_UP_DOWN, drag_interval=16,
            on_vertical_drag_update=self._drag_divider,
            on_double_tap=lambda e: self._set_lower_height(LOWER_DEFAULT))
        self.tap_btn = pv.button("Tap tempo", ft.Icons.TOUCH_APP, lambda e: self._tap())
        self.tap_label = ft.Text("", size=12, color=theme.FOREGROUND)
        self.hints = ft.Column([], spacing=2, scroll=ft.ScrollMode.AUTO, expand=True)
        self.diff_title = ft.Text("Difference", size=11, color=theme.MUTED)
        self.readout = ft.Text("", size=12, color=theme.FOREGROUND,
                               text_align=ft.TextAlign.CENTER)

        # Static scales (redrawn only on resize) under the moving parts, which
        # are PNG images replaced every frame (see katatone.gui.plot).
        self.meter_grid = cv.Canvas(expand=True, resize_interval=100,
                                    on_resize=lambda e: self._resized("_meter_size", e))
        self.spec_grid = cv.Canvas(expand=True, resize_interval=100,
                                   on_resize=lambda e: self._resized("_spec_size", e))
        self.diff_grid = cv.Canvas(expand=True, resize_interval=100,
                                   on_resize=lambda e: self._resized("_diff_size", e))
        self.meter_img = self._overlay()
        self.spec_img = self._overlay()
        self.diff_img = self._overlay()
        self.meter = ft.Stack([self.meter_grid, self.meter_img], expand=True)
        self.spec = ft.Stack([self.spec_grid, self.spec_img], expand=True)
        self.diff = ft.Stack([self.diff_grid, self.diff_img], expand=True)
        self.lower_box = ft.Container(self.diff, height=LOWER_DEFAULT)   # holds the lower panel
        page.add(ft.Column([
            ft.Container(ft.Row([self.device, self.channel, self.toggle, self.clear],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER),
                         padding=ft.Padding(left=0, right=0, top=8, bottom=0)),
            ft.Row([self.scale, self.show_strings, self.show_zones], spacing=16,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([self.ref_btn, self.ref_label, self.ref_clear,
                    ft.Container(self.match, width=140),       # a bare labelled Checkbox fills the row
                    self.take_a, self.take_b, self.compare_dd, self.eq_preview, self.lower,
                    self.tap_btn, self.tap_label],
                   spacing=8, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([
                ft.Container(ft.Column([self.meter, self.readout], spacing=6,
                                       horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                             width=130, padding=8, bgcolor=theme.KNOB_LO,
                             border=ft.Border.all(1, theme.PANEL_EDGE), border_radius=6),
                ft.Container(ft.Column([ft.Container(self.spec, expand=True),
                                        self.divider,
                                        self.diff_title,
                                        self.lower_box], spacing=4),
                             expand=True, padding=8, bgcolor=theme.KNOB_LO,
                             border=ft.Border.all(1, theme.PANEL_EDGE), border_radius=6),
            ], expand=True, spacing=12),
            ft.Container(self.hints, height=150, padding=8, bgcolor=theme.KNOB_LO,
                         border=ft.Border.all(1, theme.PANEL_EDGE), border_radius=6),
            self.status,
        ], expand=True, spacing=10, horizontal_alignment=ft.CrossAxisAlignment.STRETCH))
        self._draw_meter_grid()
        self._draw_spectrum_grid()
        self._draw_diff_grid()
        self._refresh_hints(np.full(len(self.spectrum.freqs), dsp.FLOOR_DB), False, force=True)
        self._start()
        page.run_task(self._loop)

    # ------------------------------------------------------------ capture

    def _on_block(self, block: np.ndarray) -> None:
        """Capture thread: remember the block's peak and feed the spectrum."""
        with self.lock:
            self._peak_acc = max(self._peak_acc, float(np.max(np.abs(block))))
            self.spectrum.push(block)
            self.history.push(block)

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
            cap = Capture(name, int(self.channel.value or 1),
                          self._on_block, self._on_error)
            cap.start()
        except CaptureError as e:
            self._error = str(e)
            return
        self.capture = cap
        self.toggle.content = ft.Text("Stop", no_wrap=True)
        self.toggle.icon = ft.Icons.STOP

    def _stop(self) -> None:
        """Stop listening."""
        cap, self.capture = self.capture, None
        if cap is not None:
            cap.stop()
        self.toggle.content = ft.Text("Start", no_wrap=True)
        self.toggle.icon = ft.Icons.PLAY_ARROW

    def _restart(self) -> None:
        """Re-open the capture after the device or channel changed."""
        self._start()
        self._clear()

    def _toggle(self) -> None:
        """Start / Stop button."""
        if self.capture is None:
            self._start()
        else:
            self._stop()
        self._push_controls()

    def _set_scale(self) -> None:
        """Switch the frequency axis between logarithmic and linear."""
        linear = bool(self.scale.value)
        with self.lock:
            old = self.spectrum
            self.linear = linear
            self.spectrum = dsp.Spectrum(RATE, points=SPEC_POINTS, linear=linear)
            self.spectrum._buf = old._buf.copy()
            self.smooth = dsp.Smoother(len(self.spectrum.freqs))
            self._columns.clear()                  # they were on the other axis
            self._fits = None
        self._draw_spectrum_grid()
        self._draw_diff_grid()

    def _clear(self) -> None:
        """Reset the peak hold, the clip light and the spectrum average."""
        with self.lock:
            self.smooth.reset()
            self.hold = dsp.PeakHold()
            self._clipped = False
            self.history.clear()
            self._columns.clear()
        self._insight, self._insight_at = comparison.Insight(), 0.0

    def _resized(self, attr: str, e) -> None:
        """Canvas resize: remember the size for drawing."""
        setattr(self, attr, (float(e.width), float(e.height)))
        if attr == "_meter_size":
            self._draw_meter_grid()
        elif attr == "_diff_size":
            self._draw_diff_grid()
        else:
            self._draw_spectrum_grid()

    def _push_controls(self) -> None:
        """Repaint the buttons (called off the UI thread too)."""
        try:
            self.toggle.update()
        except RuntimeError:
            pass

    # ------------------------------------------------------------ drawing

    async def _loop(self) -> None:
        """Redraw at :data:`FPS`, on the page's own event loop.

        Every change to the controls happens on that loop, as it does for
        event handlers; pushing updates from a separate thread raced with
        the handlers' automatic page updates and left the display frozen.
        """
        while self._running:
            await asyncio.sleep(1.0 / FPS)
            try:
                self._tick()
            except Exception:                         # noqa: BLE001
                if not self._running:
                    return
                traceback.print_exc()           # ends up in analyzer.log

    def _tick(self) -> None:
        """Compute levels and the spectrum and redraw both canvases."""
        listening = self.capture is not None and not self._error
        with self.lock:
            peak_lin, self._peak_acc = self._peak_acc, 0.0
            if listening:
                spec = self.spectrum.compute()
                self.smooth.update(spec)
                self._columns.append(spec)
            rms_db = dsp.levels(self.spectrum._buf[-2400:])[1] if listening else dsp.FLOOR_DB
            peak_db = dsp.to_db(peak_lin)
            held = self.hold.update(peak_db)
            if peak_db >= CLIP_DB:
                self._clipped = True
            clipped = self._clipped
            avg, peak = self.smooth.avg.copy(), self.smooth.peak.copy()
        self._apply_loaded()
        if listening:
            self._update_insight()
        self._draw_meter(rms_db, held, clipped)
        self._draw_spectrum(avg, peak)
        self._draw_diff(avg)
        self._refresh_hints(avg, listening)
        self.readout.value = (f"RMS {rms_db:6.1f} dB\nPeak {held:6.1f} dB"
                              if listening else "")
        text, color, until = self._notice
        if self._error:
            self.status.value, self.status.color = self._error, theme.DANGER
        elif text and time.monotonic() < until:
            self.status.value, self.status.color = text, color
        else:
            self.status.value, self.status.color = ("" if listening else "Stopped."), theme.MUTED
        for c in (self.meter_img, self.spec_img, self.diff_img, self.readout, self.status):
            try:
                c.update()
            except RuntimeError as e:
                if not self._warned:
                    self._warned = True
                    print(f"update failed: {e!r}", file=sys.stderr)

    @staticmethod
    def _overlay() -> ft.Image:
        """An image laid over its canvas, replaced every frame."""
        return ft.Image(src=plot.png_bytes(np.zeros((2, 2, 4), dtype=np.uint8)),
                        fit=ft.BoxFit.FILL, gapless_playback=True,
                        left=0, top=0, right=0, bottom=0)

    def _draw_meter_grid(self) -> None:
        """Draw the meter's scale (ticks and dB labels); static."""
        w, h = self._meter_size
        bar_x = 40.0
        shapes: list = [cv.Rect(bar_x, 0, max(10.0, w - 52.0), h, paint=_fill(theme.BG))]
        for db in range(0, int(METER_MIN_DB) - 1, -6):
            y = meter_y(db, h)
            shapes.append(cv.Line(bar_x - 4, y, bar_x, y, paint=_stroke(theme.TICK)))
            shapes.append(_label(bar_x - 8, max(0.0, y - 6), f"{db}", ft.Alignment.TOP_RIGHT))
        self.meter_grid.shapes = shapes
        self._update(self.meter_grid)

    def _draw_spectrum_grid(self) -> None:
        """Draw the spectrum's grid and labels, and the optional tone zones and
        string frequencies; static, redrawn on resize and when toggled."""
        w, h = self._spec_size
        lin = self.linear
        shapes: list = []
        if self.show_zones.value:
            for i, (name, lo, hi) in enumerate(bands.TONE_BANDS):
                x0, x1 = freq_x(lo, w, lin), freq_x(hi, w, lin)
                shapes.append(cv.Rect(x0, 0, max(1.0, x1 - x0), h,
                                      paint=_fill(ZONE_COLORS[i % 2])))
                shapes.append(_label(x0 + 3, 2 + (i % 3) * 12, name))
        for db in range(int(SPEC_MIN_DB), int(SPEC_MAX_DB) + 1, 20):
            y = spec_y(db, h)
            shapes.append(cv.Line(0, y, w, y, paint=_stroke(theme.PANEL_EDGE)))
            shapes.append(_label(2, y + 1, f"{db} dB"))
        for f in (FREQ_LABELS_LINEAR if lin else FREQ_LABELS):
            x = freq_x(f, w, lin)
            shapes.append(cv.Line(x, 0, x, h, paint=_stroke(theme.PANEL_EDGE)))
            text = f"{f // 1000}k" if f >= 1000 else str(f)
            shapes.append(_label(x + 2, h - 14, text))
        if self.show_strings.value:
            shapes += string_shapes(w, h, lin)
        self.spec_grid.shapes = shapes
        self._update(self.spec_grid)

    @staticmethod
    def _update(control) -> None:
        """Push a control, ignoring "not on a page yet"."""
        try:
            control.update()
        except RuntimeError:
            pass

    def _draw_meter(self, rms_db: float, held: float, clipped: bool) -> None:
        """Render the moving level bar (RMS, peak-hold line, clip square)."""
        w, h = (max(8, int(v)) for v in self._meter_size)
        zones = [(meter_y(hi, h), meter_y(lo, h), plot.rgba(color))
                 for lo, hi, color in ((METER_MIN_DB, -18, theme.OK),
                                       (-18, -6, theme.ACCENT), (-6, 0, theme.DANGER))]
        img = plot.meter_image(
            w, h, meter_y(rms_db, h) if rms_db > METER_MIN_DB else h,
            meter_y(held, h) if held > METER_MIN_DB else None, zones,
            plot.rgba(theme.POINTER), clipped, plot.rgba(theme.DANGER),
            bar_x=40, bar_w=max(10, w - 52))
        self.meter_img.src = plot.png_bytes(img)

    def _draw_spectrum(self, avg: np.ndarray, peak: np.ndarray) -> None:
        """Render the moving spectrum traces."""
        w, h = (max(8, int(v)) for v in self._spec_size)
        xs = np.array([freq_x(f, w, self.linear) for f in self.spectrum.freqs])
        avg_y = np.array([spec_y(v, h) for v in avg])
        peak_y = np.array([spec_y(v, h) for v in peak])
        extras = [(np.array([spec_y(v, h) for v in lv]), plot.rgba(TRACE_COLORS[name]))
                  for name, lv in self.cmp.traces(avg, self.spectrum.freqs)]
        preview = self._preview_response()
        if preview is not None:
            extras.append((np.array([spec_y(v, h) for v in avg + preview]), plot.rgba(PREVIEW_COLOR)))
        img = plot.spectrum_image(w, h, xs, avg_y, peak_y, plot.rgba(theme.ACCENT),
                                  plot.rgba(theme.ACCENT_DIM), plot.rgba(theme.ACCENT, 45),
                                  extras=extras)
        self.spec_img.src = plot.png_bytes(img)

    # ------------------------------------------------------------ comparing

    def _draw_diff_grid(self) -> None:
        """The lower panel's scale: for the difference the centre line and the dB range,
        for the spectrogram the frequencies and the seconds."""
        w, h = self._diff_size
        if self.lower.value == "sg":
            self.diff_grid.shapes = self._spectrogram_grid(w, h)
            self._update(self.diff_grid)
            return
        shapes = [cv.Line(0, h / 2, w, h / 2, paint=_stroke(theme.PANEL_EDGE)),
                  _label(2, 1, f"+{DIFF_RANGE_DB:.0f} dB"), _label(2, h - 13, f"-{DIFF_RANGE_DB:.0f} dB")]
        for f in FREQ_LABELS_LINEAR if self.linear else FREQ_LABELS:
            x = freq_x(f, w, self.linear)
            shapes.append(cv.Line(x, 0, x, h, paint=_stroke(theme.PANEL_EDGE)))
        self.diff_grid.shapes = shapes
        self._update(self.diff_grid)

    def _spectrogram_grid(self, w: float, h: float) -> list:
        """Frequency lines (horizontal) and seconds ago (vertical) of the spectrogram."""
        shapes = []
        for f in (100, 1000, 10000):
            y = self._sg_y(f, h)
            shapes.append(cv.Line(0, y, w, y, paint=_stroke(theme.PANEL_EDGE)))
            shapes.append(_label(2, y - 13 if y > 14 else y + 1, f"{f // 1000} kHz" if f >= 1000 else f"{f} Hz"))
        for sec in range(5, int(SPECTROGRAM_S), 5):
            x = w * (1.0 - sec / SPECTROGRAM_S)
            shapes.append(cv.Line(x, 0, x, h, paint=_stroke(theme.PANEL_EDGE)))
            shapes.append(_label(x + 2, h - 13, f"-{sec} s"))
        return shapes

    def _sg_y(self, f: float, h: float) -> float:
        """Vertical position of a frequency on the spectrogram (high at the top)."""
        return h - freq_x(f, h, self.linear)

    def _draw_spectrogram(self) -> None:
        """Render the lower panel as a spectrogram of the last seconds."""
        w, h = (max(8, int(v)) for v in self._diff_size)
        freqs = self.spectrum.freqs
        ys = np.arange(h)
        # the frequency at each pixel row, then the nearest display point
        if self.linear:
            f_row = dsp.F_MIN + (h - 1 - ys) / max(1, h - 1) * (dsp.F_MAX_LINEAR - dsp.F_MIN)
        else:
            f_row = np.geomspace(dsp.F_MIN, dsp.F_MAX, h)[::-1]
        rows = np.abs(freqs[None, :] - f_row[:, None]).argmin(axis=1)
        cols = np.array(self._columns) if self._columns else np.zeros((0, len(freqs)))
        img = plot.spectrogram_image(w, h, cols, rows, self._columns.maxlen or 1,
                                     SG_MIN_DB, SG_MAX_DB, plot.rgba(theme.ACCENT))
        self.diff_title.value = f"Spectrogram, last {SPECTROGRAM_S:.0f} s (frequency up, newest at the right)"
        self.diff_img.src = plot.png_bytes(img)

    def _preview_response(self) -> np.ndarray | None:
        """The response of the proposed EQ chosen for the preview, if any."""
        kind = self.eq_preview.value
        fit = (self._fits or {}).get(kind) if kind != "off" else None
        return None if fit is None or len(fit.response) != len(self.spectrum.freqs) else fit.response

    def _draw_diff(self, avg: np.ndarray) -> None:
        """Render the lower panel: the difference of the chosen comparison (or the spectrogram)."""
        if self.lower.value == "sg":
            self._draw_spectrogram()
            return
        w, h = (max(8, int(v)) for v in self._diff_size)
        freqs = self.spectrum.freqs
        preview = self._preview_response()
        d = self.cmp.difference(avg if preview is None else avg + preview, freqs)
        if d is None:
            img = np.zeros((2, 2, 4), dtype=np.uint8)
            self.diff_title.value = "Difference: " + self._missing_text()
        else:
            xs = np.array([freq_x(f, w, self.linear) for f in freqs])
            img = plot.diff_image(w, h, xs, d[0], DIFF_RANGE_DB, plot.rgba(DIFF_ABOVE, 70),
                                  plot.rgba(DIFF_BELOW, 70), plot.rgba(theme.POINTER, 160))
            self.diff_title.value = (f"Difference, {d[1]} minus {d[2]} (up: {d[1]} has more)"
                                     + ("" if preview is None else f", with the {self.eq_preview.value} proposal"))
        self.diff_img.src = plot.png_bytes(img)

    def _missing_text(self) -> str:
        """What is needed before the chosen comparison can be shown."""
        need = {"ref": "load a reference", "A": "set take A", "B": "set take B",
                "AB": "set takes A and B"}
        return f"{need.get(self.cmp.mode, '')} to compare"

    def _refresh_hints(self, avg: np.ndarray, listening: bool, force: bool = False) -> None:
        """Rebuild the hints panel when its text changed (checked twice a second)."""
        now = time.monotonic()
        if not force and now - self._hint_at < HINT_PERIOD_S:
            return
        self._hint_at = now
        fits = self.cmp.eq_fits(avg, self.spectrum.freqs) if listening else None
        self._fits = fits
        lines = self.cmp.hints(avg, self.spectrum.freqs, listening, self._insight, fits)
        if lines == self._hint_lines and not force:
            return
        self._hint_lines = lines
        self.hints.controls = [ft.Text(l, size=12, color=theme.FOREGROUND if l and not l.startswith(" ")
                                       else theme.MUTED, selectable=True) for l in lines]
        self._update(self.hints)

    async def _pick_reference(self, e=None) -> None:
        """Choose an audio file and analyse it on a worker thread."""
        try:
            paths = await files.pick_files(self.page, self._picker, "Reference recording",
                                           reference.AUDIO_TYPES)
        except Exception as ex:                       # noqa: BLE001
            self._load_error = f"File chooser: {ex}"
            return
        if not paths:
            return
        self.ref_label.value = f"Reading {paths[0].name}…"
        self._update(self.ref_label)
        self.page.run_thread(self._load_reference, str(paths[0]))

    def _load_reference(self, path: str) -> None:
        """Worker: analyse the file; the result is picked up by the next frame."""
        try:
            self._loaded = reference.analyse(path, SPEC_POINTS, self.linear)
        except Exception as ex:                       # noqa: BLE001
            self._load_error = str(ex)

    def _apply_loaded(self) -> None:
        """Take over what the loader thread produced (on the page's loop)."""
        loaded, self._loaded = self._loaded, None
        if loaded is not None:
            self.cmp.reference = loaded
            self.cmp.mode = "ref"
            self.compare_dd.value = "ref"
            self.ref_label.value = loaded.name
            self.ref_label.color = theme.FOREGROUND
            if loaded.bpm > 0 and loaded.bpm_confidence >= reference.PULSE_CONFIDENCE:
                self.tap_label.value = f"{loaded.bpm:.0f} BPM from the reference"
            for c in (self.ref_label, self.compare_dd, self.tap_label):
                self._update(c)
            self._refresh_hints(np.full(len(self.spectrum.freqs), dsp.FLOOR_DB), False, force=True)
        if self._load_error:
            msg, self._load_error = self._load_error, ""
            held = self.cmp.reference                 # a failed load keeps the one already held
            self.ref_label.value = held.name if held else "no reference"
            self.ref_label.color = theme.FOREGROUND if held else theme.MUTED
            self._say(msg, error=True)
            self._update(self.ref_label)

    def _say(self, text: str, error: bool = False) -> None:
        """Show a message on the status line for :data:`NOTICE_S` seconds."""
        color = theme.DANGER if error else theme.MUTED
        self._notice = (text, color, time.monotonic() + NOTICE_S)
        self.status.value, self.status.color = text, color

    def _clear_reference(self) -> None:
        """Forget the reference."""
        self.cmp.reference = None
        self.ref_label.value, self.ref_label.color = "no reference", theme.MUTED
        self._update(self.ref_label)

    def _capture(self, name: str) -> None:
        """Keep the live average spectrum as take A or B."""
        with self.lock:
            avg, freqs = self.smooth.avg.copy(), self.spectrum.freqs.copy()
        if float(avg.max()) <= dsp.FLOOR_DB + 1:
            self._say(f"Nothing heard yet: start listening and play, then set take {name}.")
            return
        self._update_insight(force=True)
        self.cmp.set_take(name, freqs, avg, self._insight)
        btn = self.take_a if name == "A" else self.take_b
        btn.content = ft.Text(f"Take {name} set", no_wrap=True, size=13)
        self._update(btn)
        if self.cmp.mode == "ref" and self.cmp.reference is None:
            self.cmp.mode = name
            self.compare_dd.value = name
            self._update(self.compare_dd)

    def _update_insight(self, force: bool = False) -> None:
        """Analyse the last seconds of playing (the notes, their pitch content), every few seconds."""
        now = time.monotonic()
        if not force and now - self._insight_at < INSIGHT_PERIOD_S:
            return
        self._insight_at = now
        with self.lock:
            x = self.history.last()
        if x.size < RATE * content.MIN_SECONDS:
            return
        self._insight = comparison.Insight(envelope.analyse(x, RATE), content.analyse(x, RATE))

    def _set_preview(self) -> None:
        """The EQ preview choice changed: redraw at once."""
        self._update(self.eq_preview)

    def _drag_divider(self, e) -> None:
        """Dragging the divider up gives the lower panel more height (and the spectrum less)."""
        delta = e.primary_delta if e.primary_delta is not None else e.local_delta.y
        self._set_lower_height(self._lower_height - float(delta))

    def _set_lower_height(self, height: float) -> None:
        """Set the lower panel's height, keeping the spectrum above at least :data:`SPEC_MIN_HEIGHT`."""
        room = self._spec_size[1] + self._lower_height - SPEC_MIN_HEIGHT       # what the pair can share
        height = max(LOWER_MIN, min(LOWER_MAX, height, max(LOWER_MIN, room)))
        if height == self._lower_height:
            return
        self._lower_height = height
        self.lower_box.height = height
        self._update(self.lower_box)

    def _set_lower(self) -> None:
        """Switch the lower panel between the difference and the spectrogram."""
        self._draw_diff_grid()
        self.diff_title.value = ""

    def _set_compare(self) -> None:
        self.cmp.mode = self.compare_dd.value or "ref"

    def _set_match(self) -> None:
        self.cmp.match_level = bool(self.match.value)

    def _tap(self) -> None:
        """Tap tempo: show the tempo and the delay times that fit it."""
        bpm = self.tap.tap(time.monotonic())
        if bpm is None:
            self.tap_label.value = "keep tapping…"
        else:
            d = ", ".join(f"{n} {ms} ms" for n, ms in reference.delay_times(bpm)[:3])
            self.tap_label.value = f"{bpm:.0f} BPM: {d}"
        self._update(self.tap_label)

    def close(self) -> None:
        """Stop the loops and the capture."""
        self._running = False
        self._stop()


def main(argv: list[str] | None = None) -> None:
    """Run the analyzer window.

    Args:
        argv: arguments (default ``sys.argv[1:]``): ``--card NAME`` to
            preselect a sound card, ``--web [--port N]`` to open in the
            browser; unknown (Flet) arguments are ignored.
    """
    import argparse
    import os
    import sys
    parser = argparse.ArgumentParser(prog="katatone.gui.analyzer", add_help=False)
    parser.add_argument("--card", default=None)
    parser.add_argument("--web", action="store_true", default=False)
    parser.add_argument("--port", type=int, default=0)
    opts, _ = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    windows: list[Analyzer] = []
    build = lambda page: windows.append(Analyzer(page, opts.card))   # noqa: E731
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
