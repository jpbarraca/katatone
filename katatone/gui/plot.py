"""Raster drawing for the analyzer: the moving parts (spectrum traces and the
level bar) are drawn into RGBA arrays with numpy and sent to the window as
small PNG images.  One image per frame is much lighter for the UI than
hundreds of vector shapes that all change every frame."""
from __future__ import annotations

import struct
import zlib

import numpy as np


def rgba(hex_color: str, alpha: int = 255) -> np.ndarray:
    """``#rrggbb`` to an RGBA uint8 vector."""
    h = hex_color.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)] + [alpha], dtype=np.uint8)


def png_bytes(img: np.ndarray) -> bytes:
    """Encode an (h, w, 4) uint8 RGBA array as a PNG file."""
    h, w, _ = img.shape
    raw = np.concatenate((np.zeros((h, 1), dtype=np.uint8),
                          img.reshape(h, w * 4)), axis=1).tobytes()

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 1))
            + chunk(b"IEND", b""))


def _trace(img: np.ndarray, xs: np.ndarray, ys: np.ndarray, color: np.ndarray,
           thickness: int) -> None:
    """Draw a polyline given by pixel points, joining steep steps."""
    h, w, _ = img.shape
    cols = np.arange(w)
    y = np.interp(cols, xs, ys)
    yi = np.clip(np.round(y).astype(int), 0, h - 1)
    prev = np.concatenate(([yi[0]], yi[:-1]))
    lo = np.clip(np.minimum(yi, prev) - thickness // 2, 0, h - 1)
    hi = np.clip(np.maximum(yi, prev) + thickness // 2, 0, h - 1)
    for c in range(w):
        img[lo[c]:hi[c] + 1, c] = color


def spectrum_image(w: int, h: int, xs: np.ndarray, avg_y: np.ndarray,
                   peak_y: np.ndarray, avg_color: np.ndarray,
                   peak_color: np.ndarray, fill_color: np.ndarray,
                   extras: "list[tuple[np.ndarray, np.ndarray]]" = ()) -> np.ndarray:
    """The spectrum overlay: a translucent area under the average, the held
    peak line and the average line.

    Args:
        w, h: size in pixels.
        xs: x pixel of each spectrum point (ascending).
        avg_y, peak_y: y pixel (0 = top) of each point for the two traces.
        avg_color, peak_color, fill_color: RGBA colours.
        extras: more lines ``(y pixel of each point, RGBA colour)`` drawn under the
            average (a reference, takes A and B).
    Returns:
        An (h, w, 4) uint8 array.
    """
    img = np.zeros((h, w, 4), dtype=np.uint8)
    cols = np.arange(w)
    top = np.interp(cols, xs, avg_y)
    below = np.arange(h)[:, None] >= top[None, :]
    img[below] = fill_color
    for ys, color in extras:
        _trace(img, xs, ys, color, 2)
    _trace(img, xs, peak_y, peak_color, 1)
    _trace(img, xs, avg_y, avg_color, 2)
    return img


def meter_image(w: int, h: int, rms_y: float, held_y: float, zones: list,
                held_color: np.ndarray, clip: bool, clip_color: np.ndarray,
                bar_x: int, bar_w: int) -> np.ndarray:
    """The level bar overlay.

    Args:
        w, h: size in pixels.
        rms_y: y pixel of the RMS level (0 = top, ``h`` = silence).
        held_y: y pixel of the held peak, or ``None`` below the scale.
        zones: ``[(y_top, y_bottom, colour), ...]`` colour bands of the scale,
            filled only from ``rms_y`` downwards.
        held_color: colour of the peak line.
        clip: whether the clip square is lit.
        clip_color: colour of the lit clip square.
        bar_x, bar_w: horizontal position and width of the bar.
    Returns:
        An (h, w, 4) uint8 array.
    """
    img = np.zeros((h, w, 4), dtype=np.uint8)
    for y0, y1, color in zones:
        a = int(max(y0, rms_y))
        b = int(min(y1, h))
        if b > a:
            img[a:b, bar_x:bar_x + bar_w] = color
    if held_y is not None:
        y = int(min(max(held_y, 0), h - 2))
        img[y:y + 2, bar_x:bar_x + bar_w] = held_color
    if clip:
        img[2:12, bar_x:bar_x + bar_w] = clip_color
    return img


def diff_image(w: int, h: int, xs: np.ndarray, diff_db: np.ndarray, range_db: float,
               above: np.ndarray, below: np.ndarray, zero: np.ndarray) -> np.ndarray:
    """A difference curve around a centre line: filled up where it is positive and
    down where it is negative, with the line itself on top.

    Args:
        w, h: size in pixels.
        xs: x pixel of each point (ascending).
        diff_db: the difference at each point, in dB.
        range_db: the dB shown at the top and at the bottom edge.
        above, below, zero: RGBA colours of the positive area, the negative area and the line.
    Returns:
        An (h, w, 4) uint8 array.
    """
    img = np.zeros((h, w, 4), dtype=np.uint8)
    mid = (h - 1) / 2.0
    ys = mid - np.clip(diff_db, -range_db, range_db) / range_db * mid
    cols = np.arange(w)
    y = np.interp(cols, xs, ys)
    rows = np.arange(h)[:, None]
    img[(rows >= y[None, :]) & (rows < mid)] = above          # curve above the centre
    img[(rows <= y[None, :]) & (rows > mid)] = below          # curve below it
    img[int(round(mid)), :] = zero
    _trace(img, xs, ys, zero, 2)
    return img
