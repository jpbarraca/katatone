"""Unit tests for the Booster rotary knob (:mod:`katatone.gui.knob`).

The knob sweeps 270° clockwise on the canvas: min at bottom-left (0.75π,
Flutter angles: 0 = 3 o'clock, clockwise positive), max at bottom-right
(2.25π), 12 o'clock = midpoint.  Dragging is relative: up/right increase.

The handlers are exercised headless (the knob is not mounted, so ``update()``
is skipped).
"""
from __future__ import annotations

import math
from types import SimpleNamespace as NS

import pytest

from katatone.gui.knob import (SWEEP_ANGLE, Knob, drag_to_dt, pointer_angle,
                          t_to_value, value_to_t)

P = math.pi


# ------------------------------------------------------------------ math

@pytest.mark.parametrize("t, angle", [
    (0.0, 0.75 * P),      # bottom-left  -> min
    (0.5, 1.5 * P),       # 12 o'clock   -> mid
    (1.0, 2.25 * P),      # bottom-right -> max
    (-1.0, 0.75 * P),     # clamped
    (2.0, 2.25 * P),
])
def test_pointer_angle(t: float, angle: float) -> None:
    """pointer_angle maps the normalized position onto the 270° sweep, clamping outside 0..1."""
    assert abs(pointer_angle(t) - angle) < 1e-9


def test_value_t_round_trip() -> None:
    """value_to_t / t_to_value round-trip every integer in the range for several min/max pairs."""
    for lo, hi in ((0, 100), (0, 10), (-10, 10)):
        for v in range(lo, hi + 1):
            assert t_to_value(value_to_t(v, lo, hi), lo, hi) == v


def test_value_to_t_clamps_and_degenerate_range() -> None:
    """value_to_t clamps out-of-range values to 0/1 and returns 0.0 for a degenerate range."""
    assert value_to_t(-5, 0, 10) == 0.0
    assert value_to_t(50, 0, 10) == 1.0
    assert value_to_t(3, 3, 3) == 0.0


def test_drag_direction() -> None:
    """drag_to_dt treats up/right drags as increase and down/left as decrease."""
    assert drag_to_dt(0, -100, 200) == 0.5      # up   -> increase
    assert drag_to_dt(100, 0, 200) == 0.5       # right -> increase
    assert drag_to_dt(0, 100, 200) == -0.5      # down -> decrease
    assert drag_to_dt(-100, 0, 200) == -0.5     # left -> decrease


# --------------------------------------------------------------- control

def _pos(x: float, y: float) -> NS:
    """Build a minimal position object (a SimpleNamespace with x/y) for the handlers."""
    return NS(x=x, y=y)


def _drag(knob: Knob, steps: list[tuple[float, float]]) -> None:
    """Press the knob at its center and pan through the given dx/dy steps.

    Args:
        knob: the knob to drive.
        steps: list of (dx, dy) increments applied as pan updates.
    """
    x = y = knob.knob_size / 2
    knob._on_pan_start(NS(local_position=_pos(x, y)))
    for dx, dy in steps:
        x, y = x + dx, y + dy
        knob._on_pan_update(NS(local_position=_pos(x, y)))
    knob._on_pan_end()


@pytest.fixture
def calls():
    """Collects the knob's change and change-end callback values."""
    return {"change": [], "end": []}


@pytest.fixture
def knob(calls) -> Knob:
    """A Knob (0..100, starting at 50) whose callbacks append to the calls fixture."""
    return Knob(value=50, min=0, max=100,
                on_change=calls["change"].append,
                on_change_end=calls["end"].append)


def test_initial_render_matches_value(knob: Knob) -> None:
    """A freshly constructed knob renders its initial value and a half-sweep arc."""
    assert knob._value_text.value == "50"
    assert abs(knob._value_arc.sweep_angle - 0.5 * SWEEP_ANGLE) < 1e-9


def test_drag_up_steps_value_once_per_change(knob: Knob, calls) -> None:
    """A 20-px upward drag raises the value from 50 to 60, firing on_change once per integer step and on_change_end once."""
    _drag(knob, [(0, -2)] * 10)                  # 20 px of 200 -> +10
    assert knob.value == 60
    assert calls["change"] == list(range(51, 61))
    assert calls["end"] == [60]
    assert knob._value_text.value == "60"


def test_grab_does_not_jump(knob: Knob, calls) -> None:
    """Pressing the knob far from the pointer and not moving changes nothing (no absolute jump on grab)."""
    # Pressing far from the pointer and not moving must not change anything
    # (the old absolute-angle mapping jumped to the press position).
    knob._on_pan_start(NS(local_position=_pos(0, 64)))
    knob._on_pan_update(NS(local_position=_pos(0, 64)))
    knob._on_pan_end()
    assert knob.value == 50 and calls["change"] == []


def test_drag_clamps_and_reverses_immediately(knob: Knob) -> None:
    """An upward drag clamps at the max, and the first step back immediately counts as a decrease."""
    x = y = 32
    knob._on_pan_start(NS(local_position=_pos(x, y)))
    for _ in range(20):                          # overshoot far past max
        y -= 50
        knob._on_pan_update(NS(local_position=_pos(x, y)))
    assert knob.value == 100
    y += 2                                       # first step back counts
    knob._on_pan_update(NS(local_position=_pos(x, y)))
    assert knob.value == 99
    knob._on_pan_end()


def test_small_range_accumulates_fractional_travel(calls) -> None:
    """On a 0..10 knob, sub-step drags accumulate fractional travel until a whole step is reached."""
    k = Knob(value=5, min=0, max=10, on_change=calls["change"].append)
    _drag(k, [(0, 1)] * 40)                      # 40 px down, 20 px/step
    assert k.value == 3
    assert calls["change"] == [4, 3]


def test_scroll_wheel_and_touchpad(knob: Knob, calls) -> None:
    """Wheel notches and touchpad deltas step the value by one each, firing on_change_end per step."""
    for dy in (-50, -50, 50):                    # wheel: one step per notch
        knob._on_scroll(NS(scroll_delta=_pos(0, dy)))
    assert knob.value == 51
    for _ in range(4):                           # touchpad: 4 x 5 px = 2 steps
        knob._on_scroll(NS(scroll_delta=_pos(0, -5)))
    assert knob.value == 53
    assert calls["end"] == [51, 52, 51, 52, 53]


def test_double_tap_resets_to_default(knob: Knob) -> None:
    """A double tap restores the knob to its default (mid-range) value after a drag."""
    _drag(knob, [(0, 10)] * 5)
    assert knob.value == 25
    knob._on_double_tap()
    assert knob.value == 50


def test_set_value_clamps_without_callbacks(knob: Knob, calls) -> None:
    """set_value clamps to the range and updates the display without firing change callbacks."""
    knob.set_value(250)
    assert knob.value == 100 and knob._value_text.value == "100"
    assert calls["change"] == []


def test_knob_is_isolated(knob: Knob) -> None:
    """The knob reports itself as isolated so Flet's auto-update scopes to its subtree."""
    # Isolation scopes Flet's auto-update to the knob subtree.
    assert knob.is_isolated()
