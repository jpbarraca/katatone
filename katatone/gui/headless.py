"""Running the application with no browser or window attached.

In web mode the amp link and the MIDI controllers live as long as the server
does, not as long as a browser tab.  The :class:`~katatone.gui.app.App` is
created once, on a :class:`HeadlessPage`, and a browser session only borrows it
(``App.attach`` / ``App.detach``).  While nobody is attached the widgets exist
but are not on any page, so :func:`install_safe_update` makes their ``update()``
harmless.
"""
from __future__ import annotations

import asyncio
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
_loop: "asyncio.AbstractEventLoop | None" = None


def set_loop(loop: "asyncio.AbstractEventLoop | None") -> None:
    """Remember the event loop the pages are served on.

    Flet queues a control's update and wakes its sender with an ``asyncio.Event``,
    which must be set from the loop's own thread: set from another thread (the MIDI
    monitor, the amp's notifications) the update waits until some other event, such
    as a click, wakes the loop.  :func:`install_safe_update` therefore forwards
    updates made from other threads to this loop.
    """
    global _loop
    _loop = loop


def _on_loop() -> bool:
    """Whether the caller is running on the pages' event loop."""
    try:
        return asyncio.get_running_loop() is _loop
    except RuntimeError:
        return False


def install_safe_update() -> None:
    """Make ``update()`` safe for the headless / multi-threaded app (idempotent).

    * A control that is not on a page (the app keeps its widgets between browser
      sessions) is ignored instead of raising "Control must be added to the page
      first"; other errors still propagate.
    * An update made from another thread is handed to the pages' event loop (see
      :func:`set_loop`), so the browser sees it at once.
    """
    global _installed
    if _installed:
        return
    from flet.controls.base_control import BaseControl
    from flet.controls.page import Page

    def guard(original):
        def call(self, *args) -> None:
            try:
                original(self, *args)
            except RuntimeError as e:
                if "added to the page" not in str(e):
                    raise

        def update(self, *args) -> None:
            loop = _loop
            if loop is not None and loop.is_running() and not _on_loop():
                loop.call_soon_threadsafe(call, self, *args)
            else:
                call(self, *args)
        return update

    BaseControl.update = guard(BaseControl.update)
    Page.update = guard(Page.update)
    _installed = True
