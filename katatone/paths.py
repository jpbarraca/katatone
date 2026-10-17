"""Where KataTone keeps its data: ``~/.local/share/katatone`` (or the
platform's equivalent user data directory).

It holds the patch library (``livesets/``), the MIDI controller profiles
(``midi/``), the Tone Central session cookies and the webview log.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "katatone"


def data_root() -> Path:
    """Return the data directory (not created if missing).

    Returns:
        The path of the KataTone data directory.
    """
    try:
        from platformdirs import user_data_dir
        return Path(user_data_dir(APP_NAME))
    except ImportError:
        return Path(os.environ.get("XDG_DATA_HOME",
                                   Path.home() / ".local" / "share")) / APP_NAME
