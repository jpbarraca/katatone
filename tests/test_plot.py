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


def test_extra_traces_are_drawn_under_the_average() -> None:
    xs = np.array([0.0, 99.0])
    img = plot.spectrum_image(100, 50, xs, np.array([40.0, 40.0]), np.array([10.0, 10.0]),
                              RED, GREEN, BLUE, extras=[(np.array([20.0, 20.0]), plot.rgba("#0000ff"))])
    assert (img[20, 50] == plot.rgba("#0000ff")).all()          # the extra line
    assert (img[40, 50] == RED).all()                           # the average on top


def test_diff_image_fills_towards_the_curve_from_the_centre_line() -> None:
    xs = np.array([0.0, 99.0])
    up, down, line = plot.rgba("#ff0000", 90), plot.rgba("#0000ff", 90), plot.rgba("#ffffff")
    img = plot.diff_image(100, 41, xs, np.array([6.0, 6.0]), 12.0, up, down, line)
    assert (img[15, 50] == up).all() and img[25, 50, 3] == 0    # +6 dB: up, not down
    img = plot.diff_image(100, 41, xs, np.array([-6.0, -6.0]), 12.0, up, down, line)
    assert (img[25, 50] == down).all() and img[15, 50, 3] == 0
    flat = plot.diff_image(100, 41, xs, np.zeros(2), 12.0, up, down, line)
    assert (flat[20, :] == line).all()                          # the centre line
    clipped = plot.diff_image(100, 41, xs, np.array([99.0, 99.0]), 12.0, up, down, line)
    assert (clipped[1, 50] == line).all() or (clipped[1, 50] == up).all()   # held at the edge


def test_spectrogram_has_the_newest_column_at_the_right_and_leaves_the_past_blank() -> None:
    cols = np.full((3, 4), -100.0)
    cols[-1, 2] = -20.0                                  # the newest spectrum: loud at point 2
    rows = np.array([3, 2, 1, 0])                        # top row shows the highest point
    img = plot.spectrogram_image(20, 4, cols, rows, slots=10, min_db=-100, max_db=-20, color=GREEN)
    assert img.shape == (4, 20, 4)
    assert (img[:, :14, 3] == 0).all()                   # 3 of 10 slots filled: the left is empty
    assert img[1, -1, 3] == 255 and img[0, -1, 3] == 0   # point 2 is the second row; others quiet
    assert (img[1, -1, :3] == 255).all()                 # full level turns white


def test_spectrogram_without_columns_is_empty() -> None:
    img = plot.spectrogram_image(8, 3, np.zeros((0, 4)), np.array([0, 1, 2]), 5, -100, -20, GREEN)
    assert img.shape == (3, 8, 4) and not img.any()
