"""The signal chain of the Amp tab: the effects in the order the amp plays them.

The order follows from five settings: the chain preset (``OTHER_CHAIN``, 0 to 8, which sets the
order of the Booster, MOD, FX and Delay around the amp) and the positions of the Pedal FX, of
the two EQs and of the Send/Return.  :func:`order` turns them into the element names in signal
order, and :func:`diagram` draws them.
"""
from __future__ import annotations

from typing import Callable, Optional

import flet as ft

from . import params_view as pv, theme

#: (width, height) of an element and of the AMP, in pixels.
BLOCK_SIZE, AMP_SIZE = (78, 46), (96, 58)

#: Chain preset -> the order of the Booster (B), MOD (M), FX (F), Delay (D) and the amp group
#: (A: the amp with its EQs, post-amp Pedal FX and pre-FV Send/Return).  The Foot Volume group
#: (V) follows the FX or, in presets 3, 6 and 8, the amp group; Delay 2 and Reverb come last.
_PRESETS = ("BAMFVD", "BMAFVD", "BMFAVD", "BMFDAV", "MBAFVD", "MBFAVD", "MBFDAV", "MFBAVD", "MFBDAV")
_NAMES = {"B": "Booster", "M": "Mod", "F": "FX", "D": "Delay"}


def order(chain: int, eq1: int, eq2: int, send_return: int, pedal_fx: int) -> list[str]:
    """The chain as element names, input to output.

    Args:
        chain: the chain preset, 0 to 8.
        eq1: position of EQ 1: 0 before the amp, 1 after it.
        eq2: position of EQ 2: 0 before the amp, 1 after it.
        send_return: 0 after the Foot Volume, 1 after the Reverb, 2 before the Foot Volume.
        pedal_fx: 0 at the input, 1 after the amp.
    Returns:
        The element names, e.g. ``["Pedal FX", "Booster", "EQ 1", "AMP", ...]``.
    """
    pattern = _PRESETS[max(0, min(len(_PRESETS) - 1, int(chain)))]
    out: list[str] = ["Pedal FX"] if pedal_fx == 0 else []
    for step in pattern:
        if step in _NAMES:
            out.append(_NAMES[step])
        elif step == "A":
            out += ["EQ 1"] * (eq1 == 0) + ["EQ 2"] * (eq2 == 0) + ["AMP"]
            out += ["EQ 1"] * (eq1 == 1) + ["EQ 2"] * (eq2 == 1)
            out += ["Pedal FX"] * (pedal_fx == 1) + ["Send/Return"] * (send_return == 2)
        else:                                       # "V": the Foot Volume group, then the rest
            out += ["Foot Volume"] + ["Send/Return"] * (send_return == 0)
    # the ends of every chain: after the Delay come Delay 2 and the Reverb
    out += ["Delay 2", "Reverb"] + ["Send/Return"] * (send_return == 1)
    return out


#: The effects that can be clicked: diagram name -> (switch label, colour label).  Delay 2
#: shares the Delay's colour, so it only switches on and off.
EFFECTS = {"Booster": ("BOOSTER", "BOOSTER"), "Mod": ("MOD", "MOD"), "FX": ("FX", "FX"),
           "Delay": ("DELAY", "DELAY"), "Delay 2": ("DELAY2", None), "Reverb": ("REVERB", "REVERB")}
#: Colour values (as the amp stores them: 0 green, 1 red, 2 yellow) in the order a click
#: goes through them, after "off": green, yellow, red.
COLOR_CYCLE = (0, 2, 1)

#: ``state(name)``: ``(on, colour value or None)`` of an effect, None for what is no effect.
State = Callable[[str], Optional[tuple[bool, Optional[int]]]]


def next_state(on: bool, color: Optional[int], has_color: bool = True) -> tuple[bool, Optional[int]]:
    """What a click does to an effect: off -> green -> yellow -> red -> off (an effect without
    a colour of its own only goes on and off).

    Args:
        on: whether the effect is on.
        color: its colour value (0 green, 1 red, 2 yellow), if it has one.
        has_color: whether the effect has a colour of its own.
    Returns:
        The new ``(on, colour)``; the colour stays when the effect goes off.
    """
    if not has_color:
        return (not on, color)
    if not on:
        return (True, COLOR_CYCLE[0])
    i = COLOR_CYCLE.index(color) if color in COLOR_CYCLE else -1
    if i + 1 < len(COLOR_CYCLE):
        return (True, COLOR_CYCLE[i + 1])
    return (False, color)


def _block(position: int, name: str, state: Optional[tuple[bool, Optional[int]]] = None,
           on_click: Optional[Callable[[str], None]] = None) -> ft.Control:
    """One element of the chain: a square block with its place in the order and its name;
    the AMP is larger and in the accent colour.  An effect (``state`` given) is dimmed while it
    is off and framed in its colour while it is on; clicking it calls ``on_click(name)``."""
    amp = name.upper() == "AMP"
    ink = theme.BG if amp else theme.FOREGROUND
    on, color = state if state is not None else (True, None)
    frame = pv.COLOR_HEX[color] if state and on and color is not None else (
        theme.ACCENT if amp else theme.ACCENT_DIM)
    return ft.Container(
        ft.Column([ft.Text(str(position), size=10, color=theme.BG if amp else theme.MUTED),
                   ft.Text(name.upper(), size=13 if amp else 11, weight=ft.FontWeight.BOLD,
                           color=ink, no_wrap=True, text_align=ft.TextAlign.CENTER)],
                  spacing=2, tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                  alignment=ft.MainAxisAlignment.CENTER),
        width=AMP_SIZE[0] if amp else BLOCK_SIZE[0], height=AMP_SIZE[1] if amp else BLOCK_SIZE[1],
        alignment=ft.Alignment.CENTER, border_radius=3,
        bgcolor=theme.ACCENT if amp else theme.TAB_ACTIVE,
        border=ft.Border.all(2 if amp or (state and on) else 1, frame),
        opacity=1.0 if on else 0.4, tooltip=("Click: off, green, yellow, red" if state is not None else None),
        on_click=(lambda e, n=name: on_click(n)) if state is not None and on_click else None)


def diagram(names: list[str], state: Optional[State] = None,
            on_click: Optional[Callable[[str], None]] = None) -> ft.Control | None:
    """The chain as a row of square blocks, numbered in signal order and joined by arrows
    (None: nothing to draw).

    Args:
        names: the element names in signal order (see :func:`order`).
        state: gives an effect's ``(on, colour)`` (None for the rest); effects are then
            drawn dimmed or framed in their colour.
        on_click: called with an effect's name when it is clicked.
    """
    if not names:
        return None
    items: list[ft.Control] = []
    for i, name in enumerate(names):
        if i:
            items.append(ft.Icon(ft.Icons.ARROW_FORWARD, size=16, color=theme.MUTED))
        items.append(_block(i + 1, name, state(name) if state else None, on_click))
    return ft.Row(items, spacing=6, wrap=True, run_spacing=8,
                  vertical_alignment=ft.CrossAxisAlignment.CENTER)
