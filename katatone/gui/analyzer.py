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
import traceback

import flet as ft
import flet.canvas as cv
import numpy as np

from ..audio import bands, dsp
from ..audio.capture import RATE, Capture, CaptureError
from ..audio.devices import list_input_devices, preferred
from . import params_view as pv, plot, theme

FPS = 15
METER_MIN_DB = -60.0
SPEC_MIN_DB, SPEC_MAX_DB = -120.0, 0.0
FREQ_LABELS = (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)
FREQ_LABELS_LINEAR = tuple(range(2000, 20001, 2000))
STRING_COLOR = "#4f9d69"
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
    axis, both spanning :data:`dsp.F_MIN` to :data:`dsp.F_MAX`."""
    f = min(dsp.F_MAX, max(dsp.F_MIN, f))
    if linear:
        return float(width * (f - dsp.F_MIN) / (dsp.F_MAX - dsp.F_MIN))
    lo, hi = np.log10(dsp.F_MIN), np.log10(dsp.F_MAX)
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
        page.window.width, page.window.height = 980, 560
        page.window.min_width, page.window.min_height = 640, 360
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
            tooltip="Off: logarithmic (default); on: linear",
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
        self.readout = ft.Text("", size=12, color=theme.FOREGROUND,
                               text_align=ft.TextAlign.CENTER)

        # Static scales (redrawn only on resize) under the moving parts, which
        # are PNG images replaced every frame (see katatone.gui.plot).
        self.meter_grid = cv.Canvas(expand=True, resize_interval=100,
                                    on_resize=lambda e: self._resized("_meter_size", e))
        self.spec_grid = cv.Canvas(expand=True, resize_interval=100,
                                   on_resize=lambda e: self._resized("_spec_size", e))
        self.meter_img = self._overlay()
        self.spec_img = self._overlay()
        self.meter = ft.Stack([self.meter_grid, self.meter_img], expand=True)
        self.spec = ft.Stack([self.spec_grid, self.spec_img], expand=True)
        page.add(ft.Column([
            ft.Container(ft.Row([self.device, self.channel, self.toggle, self.clear],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER),
                         padding=ft.Padding(left=0, right=0, top=8, bottom=0)),
            ft.Row([self.scale, self.show_strings, self.show_zones], spacing=16,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([
                ft.Container(ft.Column([self.meter, self.readout], spacing=6,
                                       horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                             width=130, padding=8, bgcolor=theme.KNOB_LO,
                             border=ft.Border.all(1, theme.PANEL_EDGE), border_radius=6),
                ft.Container(self.spec, expand=True, padding=8, bgcolor=theme.KNOB_LO,
                             border=ft.Border.all(1, theme.PANEL_EDGE), border_radius=6),
            ], expand=True, spacing=12),
            self.status,
        ], expand=True, spacing=10))
        self._draw_meter_grid()
        self._draw_spectrum_grid()
        self._start()
        page.run_task(self._loop)

    # ------------------------------------------------------------ capture

    def _on_block(self, block: np.ndarray) -> None:
        """Capture thread: remember the block's peak and feed the spectrum."""
        with self.lock:
            self._peak_acc = max(self._peak_acc, float(np.max(np.abs(block))))
            self.spectrum.push(block)

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
        self._draw_spectrum_grid()

    def _clear(self) -> None:
        """Reset the peak hold, the clip light and the spectrum average."""
        with self.lock:
            self.smooth.reset()
            self.hold = dsp.PeakHold()
            self._clipped = False

    def _resized(self, attr: str, e) -> None:
        """Canvas resize: remember the size for drawing."""
        setattr(self, attr, (float(e.width), float(e.height)))
        if attr == "_meter_size":
            self._draw_meter_grid()
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
            rms_db = dsp.levels(self.spectrum._buf[-2400:])[1] if listening else dsp.FLOOR_DB
            peak_db = dsp.to_db(peak_lin)
            held = self.hold.update(peak_db)
            if peak_db >= CLIP_DB:
                self._clipped = True
            clipped = self._clipped
            avg, peak = self.smooth.avg.copy(), self.smooth.peak.copy()
        self._draw_meter(rms_db, held, clipped)
        self._draw_spectrum(avg, peak)
        self.readout.value = (f"RMS {rms_db:6.1f} dB\nPeak {held:6.1f} dB"
                              if listening else "")
        self.status.value = self._error or (
            "" if listening else "Stopped.")
        self.status.color = theme.DANGER if self._error else theme.MUTED
        for c in (self.meter_img, self.spec_img, self.readout, self.status):
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
        img = plot.spectrum_image(w, h, xs, avg_y, peak_y, plot.rgba(theme.ACCENT),
                                  plot.rgba(theme.ACCENT_DIM), plot.rgba(theme.ACCENT, 45))
        self.spec_img.src = plot.png_bytes(img)

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
