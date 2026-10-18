"""Running the application with no browser or window attached.

In web mode the amp link and the MIDI controllers live as long as the server
does, not as long as a browser tab.  The :class:`~katatone.gui.app.App` is
created once, on a :class:`HeadlessPage`, and a browser session only borrows it
(``App.attach`` / ``App.detach``).  While nobody is attached the widgets exist
but are not on any page, so :func:`install_safe_update` makes their ``update()``
harmless.
"""
from __future__ import annotations

import threading
import types


class HeadlessPage:
    """Just enough of ``ft.Page`` for the App to run without a client.

    Worker threads really run; everything that would draw does nothing.
    """

    web = True

    def __init__(self) -> None:
        self.window = types.SimpleNamespace()
        self.controls: list = []

    def run_thread(self, fn, *args) -> None:
        """Run ``fn(*args)`` on a daemon thread, like ``Page.run_thread``."""
        threading.Thread(target=fn, args=args, daemon=True).start()

    def add(self, *controls) -> None:
        """Drop the controls: there is nothing to draw on."""

    def clean(self) -> None:
        """Nothing to clean."""

    def update(self, *args, **kwargs) -> None:
        """Nothing to repaint."""

    def show_dialog(self, dialog) -> None:
        """No one to show it to."""

    def pop_dialog(self) -> None:
        """Nothing to close."""


_installed = False


def install_safe_update() -> None:
    """Make ``Control.update()`` a no-op for controls that are not on a page.

    The App keeps its widgets between browser sessions, and background threads
    (the MIDI router, the amp's notifications) update them; with no session
    attached Flet would raise "Control must be added to the page first".
    Other errors still propagate.
    """
    global _installed
    if _installed:
        return
    from flet.controls.base_control import BaseControl
    original = BaseControl.update

    def update(self) -> None:
        try:
            original(self)
        except RuntimeError as e:
            if "added to the page" not in str(e):
                raise

    BaseControl.update = update
    _installed = True
