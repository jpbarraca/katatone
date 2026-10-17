"""Amp-style rotary knob control (Flet 1.x).

Interaction
-----------
* **Drag** anywhere on the knob: up/right turns it clockwise, down/left
  counter-clockwise.  The drag is *relative* (like hardware-emulating audio
  plugins): grabbing the knob never makes it jump, and ``drag_pixels`` of
  travel cover the full range regardless of the knob's on-screen size.
* **Mouse wheel**: one ``wheel_step`` per notch (default ~1% of the range).
* **Double-click**: reset to ``default`` (the initial value if unset).

Why it is built this way
------------------------
The previous attempt rotated a ``Container`` with ``rotate=Rotate(degrees)``
and mapped the absolute pointer angle to a value.  That had three problems:

1. ``Rotate.angle`` is in *radians*; passing degrees spun the pointer to an
   unrelated position, so the visual never matched the value.
2. The pointer was only re-rendered on pan end, so dragging gave no feedback.
3. Flet 1.x auto-updates after every event handler that does not call
   ``update()`` itself.  The handler's "value unchanged" early return
   therefore triggered a *full page* diff on every ~60 Hz drag tick, flooding
   the client connection - the UI stalled and appeared to loop.

Here the knob is drawn on a :class:`flet.canvas.Canvas` (only the value arc,
pointer line and value text change per tick), the control is *isolated* so
any update is scoped to the knob subtree, auto-update is disabled inside its
handlers, and ``update()`` is pushed only when the integer value changes.

Callbacks
---------
``on_change(value)`` fires on every value change, ``on_change_end(value)``
once when a drag ends (and after wheel / reset changes).  The knob does not
trigger page-wide updates, so callbacks must ``.update()`` any controls they
modify themselves.

Because the knob is isolated, a parent's ``update()`` does not refresh it:
set the value with :meth:`Knob.set_value` (or assign ``value`` and call
``knob.update()``).
"""
from __future__ import annotations

import math
from typing import Callable, Optional

import flet as ft
import flet.canvas as cv
from flet.controls.base_control import skip_field

from . import theme

# Canvas angles: 0 rad = 3 o'clock, positive = clockwise (Flutter convention).
START_ANGLE = 0.75 * math.pi   # min position: bottom-left (7:30)
SWEEP_ANGLE = 1.5 * math.pi    # 270° clockwise through 12 o'clock to 4:30


def value_to_t(value: float, lo: int, hi: int) -> float:
    """Value -> knob travel 0..1 (clamped)."""
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def t_to_value(t: float, lo: int, hi: int) -> int:
    """Knob travel 0..1 -> nearest integer value in [lo, hi]."""
    t = max(0.0, min(1.0, t))
    return max(lo, min(hi, int(round(lo + t * (hi - lo)))))


def drag_to_dt(dx: float, dy: float, drag_pixels: float) -> float:
    """Pointer delta (px) -> change of travel.  Up and right increase."""
    return (dx - dy) / drag_pixels


def pointer_angle(t: float) -> float:
    """Knob travel 0..1 -> canvas angle (radians) of the pointer."""
    return START_ANGLE + max(0.0, min(1.0, t)) * SWEEP_ANGLE


@ft.control(isolated=True)
class Knob(ft.Column):
    """Amp-style rotary knob: knob, value readout, then the label underneath."""

    value: int = 0
    min: int = 0
    max: int = 100
    default: Optional[int] = None
    label: Optional[str] = None
    knob_size: int = 64
    tile_width: Optional[int] = None    # fixed width so knob rows line up
    drag_pixels: float = 200
    wheel_step: Optional[int] = None    # None: ~1% of the range, at least 1
    accent: str = theme.ACCENT
    on_change: Optional[Callable[[int], None]] = skip_field()
    on_change_end: Optional[Callable[[int], None]] = skip_field()

    def init(self) -> None:
        """Build the knob: clamp the value, derive the defaults and draw the
        canvas (ticks, arcs, pointer, cap) behind the gesture handlers."""
        super().init()
        self.value = self._clamp(self.value)
        if self.default is None:
            self.default = self.value
        if self.wheel_step is None:
            self.wheel_step = max(1, round((self.max - self.min) / 100))
        self._drag_t: Optional[float] = None    # unrounded travel during a drag
        self._drag_pos: Optional[ft.Offset] = None
        self._scroll_acc = 0.0

        s = self.knob_size
        c = s / 2
        ring_r = c - 7                           # value ring radius
        arc_box = dict(x=c - ring_r, y=c - ring_r,
                       width=2 * ring_r, height=2 * ring_r)
        self._body_r = c - 11                    # knob cap radius

        def stroke(color: str, width: float) -> ft.Paint:
            """A rounded-cap stroke ``Paint`` for the canvas shapes."""
            return ft.Paint(color=color, stroke_width=width,
                            style=ft.PaintingStyle.STROKE,
                            stroke_cap=ft.StrokeCap.ROUND)

        # Scale ticks (11 marks, like the 0-10 print on an amp panel), drawn
        # as one LINES shape (pairs of points) to keep the control count low.
        tick_pts = []
        for i in range(11):
            a = pointer_angle(i / 10)
            for r in (c - 3.5, c - 1):
                tick_pts.append(ft.Offset(c + r * math.cos(a),
                                          c + r * math.sin(a)))
        ticks = [cv.Points(points=tick_pts, point_mode=cv.PointMode.LINES,
                           paint=stroke(theme.TICK, 1.5))]
        self._value_arc = cv.Arc(**arc_box, start_angle=START_ANGLE,
                                 sweep_angle=0, paint=stroke(self.accent, 3))
        self._pointer = cv.Line(c, c, c, c, paint=stroke(theme.POINTER, 2.5))
        r = self._body_r
        self._canvas = cv.Canvas(
            width=s, height=s,
            shapes=[
                *ticks,
                cv.Arc(**arc_box, start_angle=START_ANGLE,
                       sweep_angle=SWEEP_ANGLE, paint=stroke(theme.BORDER, 3)),
                self._value_arc,
                cv.Circle(c, c + 1.5, r, paint=ft.Paint(          # drop shadow
                    color=theme.SHADOW, style=ft.PaintingStyle.FILL)),
                cv.Circle(c, c, r, paint=ft.Paint(
                    style=ft.PaintingStyle.FILL,
                    gradient=ft.PaintRadialGradient(
                        center=ft.Offset(c - r * 0.35, c - r * 0.45),
                        radius=r * 1.6,
                        colors=[theme.KNOB_HI, theme.KNOB_LO]))),
                cv.Circle(c, c, r, paint=stroke(theme.KNOB_RIM, 1.2)),
                self._pointer,
            ])
        self._value_text = ft.Text(size=12, color=self.accent,
                                   weight=ft.FontWeight.BOLD)
        self._gesture = ft.GestureDetector(
            content=self._canvas,
            mouse_cursor=ft.MouseCursor.RESIZE_UP_DOWN,
            drag_interval=20,           # client-side throttle (~50 ev/s)
            on_pan_start=self._on_pan_start,
            on_pan_update=self._on_pan_update,
            on_pan_end=self._on_pan_end,
            on_pan_cancel=self._on_pan_end,
            on_scroll=self._on_scroll,
            on_double_tap=self._on_double_tap,
        )

        self.spacing = 0
        self.tight = True
        self.horizontal_alignment = ft.CrossAxisAlignment.CENTER
        self.width = self.tile_width
        self.controls = [self._gesture, self._value_text]
        if self.label:
            # Fixed two-line box: knobs in a row keep the same height (and
            # line up when centred) whether their label wraps or not.
            self.controls.append(ft.Container(ft.Text(
                self.label.upper(), size=10, color=theme.LABEL,
                weight=ft.FontWeight.W_600, max_lines=2,
                text_align=ft.TextAlign.CENTER,
                overflow=ft.TextOverflow.ELLIPSIS,
                style=ft.TextStyle(letter_spacing=0.8)),
                height=28, alignment=ft.Alignment.TOP_CENTER))
        self._sync()

    def before_update(self) -> None:
        """Re-clamp the value and redraw the shapes before the control is
        serialized."""
        super().before_update()
        self.value = self._clamp(self.value)
        self._sync()

    # ------------------------------------------------------------ public API

    def set_value(self, value: int) -> None:
        """Set the value programmatically (no callbacks) and redraw."""
        self.value = self._clamp(value)
        self._sync()
        self._push()

    # ------------------------------------------------------------- internals

    def _clamp(self, v: float) -> int:
        """Round ``v`` and clamp it to the knob's [min, max] range."""
        return max(self.min, min(self.max, int(round(v))))

    def _sync(self) -> None:
        """Move the drawn shapes to match ``self.value``."""
        t = value_to_t(self.value, self.min, self.max)
        a = pointer_angle(t)
        c = self.knob_size / 2
        r0, r1 = self._body_r * 0.3, self._body_r * 0.88
        self._value_arc.sweep_angle = t * SWEEP_ANGLE
        self._pointer.x1 = c + r0 * math.cos(a)
        self._pointer.y1 = c + r0 * math.sin(a)
        self._pointer.x2 = c + r1 * math.cos(a)
        self._pointer.y2 = c + r1 * math.sin(a)
        self._value_text.value = str(self.value)

    def _push(self) -> None:
        """Push an update to the page (a no-op while the knob is not
        mounted)."""
        try:
            self.update()
        except RuntimeError:
            pass                    # not mounted on a page yet

    def _apply(self, new_value: int) -> bool:
        """Commit a user-driven value; returns True if it changed."""
        new_value = self._clamp(new_value)
        if new_value == self.value:
            return False
        self.value = new_value
        self._sync()
        self._push()
        if self.on_change:
            self.on_change(new_value)
        return True

    def _end(self) -> None:
        """Fire ``on_change_end`` with the current value, if set."""
        if self.on_change_end:
            self.on_change_end(self.value)

    # ---------------------------------------------------------------- events
    _SCROLL_NOTCH = 10.0    # px of scroll delta per step

    # Every handler disables Flet's auto-update: we push the knob only when
    # the integer value changes, never the whole page.

    def _on_pan_start(self, e: ft.DragStartEvent) -> None:
        """Remember the travel and pointer position the drag starts from."""
        ft.context.disable_auto_update()
        self._drag_t = value_to_t(self.value, self.min, self.max)
        self._drag_pos = e.local_position

    def _on_pan_update(self, e: ft.DragUpdateEvent) -> None:
        """Turn the pointer's movement since the last event into a value
        change (relative drag)."""
        # Deltas are derived from successive absolute positions: unambiguous,
        # and nothing is lost when the client coalesces events (drag_interval).
        ft.context.disable_auto_update()
        pos = e.local_position
        if self._drag_t is None or self._drag_pos is None:
            self._on_pan_start(e)             # update without a start event
            return
        dt = drag_to_dt(pos.x - self._drag_pos.x, pos.y - self._drag_pos.y,
                        self.drag_pixels)
        self._drag_pos = pos
        self._drag_t = max(0.0, min(1.0, self._drag_t + dt))
        self._apply(t_to_value(self._drag_t, self.min, self.max))

    def _on_pan_end(self, e=None) -> None:
        """End the drag: clear the drag state and fire ``on_change_end``."""
        ft.context.disable_auto_update()
        if self._drag_t is not None:
            self._drag_t = self._drag_pos = None
            self._end()

    def _on_scroll(self, e: ft.ScrollEvent) -> None:
        """Step the value by ``wheel_step`` per wheel notch (touchpad deltas
        are accumulated into notches)."""
        ft.context.disable_auto_update()
        # Wheel notches arrive as one large delta (one step each), touchpads
        # as many small ones (accumulated).  Wheel-up / swipe-up = increase.
        dy = e.scroll_delta.y
        if abs(dy) >= self._SCROLL_NOTCH:
            self._scroll_acc = 0.0
            steps = -1 if dy > 0 else 1
        else:
            self._scroll_acc -= dy
            steps = int(self._scroll_acc / self._SCROLL_NOTCH)
            self._scroll_acc -= steps * self._SCROLL_NOTCH
        if steps and self._apply(self.value + steps * self.wheel_step):
            self._end()

    def _on_double_tap(self, e=None) -> None:
        """Reset the knob to ``default`` (the initial value if unset)."""
        ft.context.disable_auto_update()
        if self.default is not None and self._apply(self.default):
            self._end()
