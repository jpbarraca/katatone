"""Dark amp-panel theme palette."""
from __future__ import annotations

BG = "#1a1c1f"            # main background
BG_PANEL = "#23262b"      # panel / card background
HEADER = "#313a47"        # header / tab strip
FOREGROUND = "#e6e8ea"    # primary text
MUTED = "#8a93a0"         # secondary text
ACCENT = "#d7a03e"        # amber accent
ACCENT_DIM = "#8a6a2c"
DANGER = "#c85450"
OK = "#4f9d69"
BORDER = "#2e3238"
TAB_ACTIVE = "#3b4657"

# Katana control-panel look: black brushed-metal modules, chrome-edged knobs,
# white pointers and LED-style indicators.
PANEL_TOP = "#2a2d32"     # module gradient (top -> bottom)
PANEL_BOTTOM = "#1c1e22"
PANEL_EDGE = "#3a3f47"    # module bevel
SHADOW = "#99000000"
KNOB_HI = "#5a5f68"       # knob cap highlight (radial gradient centre)
KNOB_LO = "#141518"       # knob cap shade
KNOB_RIM = "#6b717b"      # chrome rim
POINTER = "#f4f4f4"       # white pointer line
TICK = "#6b717b"          # scale ticks around the knob
LED_OFF = "#3a2a1a"
LABEL = "#c9ced6"         # silk-screen label text


class Theme:
    """Convenience namespace of the palette (import as ``th = Theme()``)."""

    bg = BG
    bg_panel = BG_PANEL
    header = HEADER
    foreground = FOREGROUND
    muted = MUTED
    accent = ACCENT
    accent_dim = ACCENT_DIM
    danger = DANGER
    ok = OK
    border = BORDER
    tab_active = TAB_ACTIVE


# Singleton for convenience.
theme = Theme()
