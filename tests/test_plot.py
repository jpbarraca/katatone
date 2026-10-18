"""Raster drawing for the analyzer."""
from __future__ import annotations

import struct
import zlib

import numpy as np

from katatone.gui import plot

RED, GREEN, BLUE = plot.rgba("#ff0000"), plot.rgba("#00ff00"), plot.rgba("#0000ff", 50)


def test_png_is_a_valid_png_of_the_right_size() -> None:
    img = np.zeros((3, 5, 4), dtype=np.uint8)
    img[1, 2] = RED
    data = plot.png_bytes(img)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    assert (w, h) == (5, 3)
    i = data.index(b"IDAT")
    n = struct.unpack(">I", data[i - 4:i])[0]
    raw = zlib.decompress(data[i + 4:i + 4 + n])
    rows = np.frombuffer(raw, dtype=np.uint8).reshape(3, 1 + 5 * 4)
    assert (rows[:, 0] == 0).all()                       # filter byte: none
    assert (rows[1, 1:].reshape(5, 4)[2] == RED).all()   # the red pixel survived


def test_spectrum_trace_is_drawn_at_the_value_height() -> None:
    xs = np.array([0.0, 99.0])
    img = plot.spectrum_image(100, 60, xs, np.array([30.0, 30.0]),
                              np.array([10.0, 10.0]), RED, GREEN, BLUE)
    assert (img[30, 50] == RED).all()                    # average line
    assert (img[10, 50] == GREEN).all()                  # held peak line
    assert (img[50, 50] == BLUE).all()                   # area under the average
    assert img[5, 50, 3] == 0                            # nothing above the peak


def test_steep_steps_are_joined() -> None:
    img = plot.spectrum_image(10, 40, np.array([0.0, 4.0, 5.0, 9.0]),
                              np.array([35.0, 35.0, 5.0, 5.0]),
                              np.array([39.0] * 4), RED, GREEN, BLUE)
    assert all((img[y, 4:6] == RED).any() for y in range(8, 33))


def test_meter_fills_zones_from_the_level_down() -> None:
    zones = [(0, 20, RED), (20, 40, GREEN)]
    img = plot.meter_image(30, 40, 10.0, 5.0, zones, BLUE, False, RED, 5, 10)
    assert (img[15, 8] == RED).all() and (img[30, 8] == GREEN).all()
    assert img[2, 8, 3] == 0                              # above the level: empty
    assert (img[5, 8] == BLUE).all()                      # held peak line
    assert img[30, 2, 3] == 0                             # outside the bar


def test_meter_clip_square() -> None:
    lit = plot.meter_image(30, 40, 40.0, None, [], BLUE, True, RED, 5, 10)
    off = plot.meter_image(30, 40, 40.0, None, [], BLUE, False, RED, 5, 10)
    assert (lit[5, 8] == RED).all() and off[5, 8, 3] == 0
