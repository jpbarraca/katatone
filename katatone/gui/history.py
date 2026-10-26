"""Undo and redo for the edits made to the patch in the editor.

An edit is one parameter change, or a change of the patch name.  Changes to the
same thing that follow each other closely (a knob drag, typing in the name
field) merge into one step.  The history belongs to one patch: when the editor
gets another ``ParamSet`` (a read, a library patch) it starts again.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

MERGE_SECONDS = 1.0     # edits to the same target this close in time are one step
MAX_STEPS = 200

# What an edit changed: ("param", block, p_index) holds the display value,
# ("name",) holds the raw bytes of the PATCH%COM block.
Key = tuple


@dataclass
class Edit:
    """One undoable step: ``key`` changed from ``old`` to ``new``."""
    key: Key
    old: object
    new: object


class EditHistory:
    """The undo and redo stacks of the patch in the editor."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Create an empty history.

        Args:
            clock: the time source, in seconds (tests pass a fake one).
        """
        self._clock = clock
        self._ps: object | None = None
        self._undo: list[Edit] = []
        self._redo: list[Edit] = []
        self._last_key: Key | None = None       # the target of the last recorded edit
        self._last_time = 0.0

    def _bind(self, ps: object) -> None:
        """Forget everything when the patch is not the one the history is for."""
        if ps is not self._ps:
            self._ps = ps
            self._undo.clear()
            self._redo.clear()
            self._last_key = None

    def record(self, ps: object, key: Key, old: object, new: object) -> None:
        """Record an edit of ``ps``, merging it into the last step when it
        changes the same target soon after.

        Args:
            ps: the patch that was edited.
            key: what changed (see :data:`Key`).
            old: the value before the edit.
            new: the value after it.
        """
        self._bind(ps)
        if old == new:
            return
        now = self._clock()
        self._redo.clear()
        if (self._undo and key == self._last_key
                and now - self._last_time <= MERGE_SECONDS):
            last = self._undo[-1]
            last.new = new
            if last.old == last.new:                # dragged back to where it began
                self._undo.pop()
                self._last_key = None
                return
        else:
            self._undo.append(Edit(key, old, new))
            del self._undo[:-MAX_STEPS]
        self._last_key, self._last_time = key, now

    def undo(self, ps: object) -> Edit | None:
        """Take the last step back.

        Args:
            ps: the patch in the editor.
        Returns:
            The step to reverse (set ``edit.old``), or None when there is none.
        """
        self._bind(ps)
        if not self._undo:
            return None
        edit = self._undo.pop()
        self._redo.append(edit)
        self._last_key = None                       # never merge with an undone step
        return edit

    def redo(self, ps: object) -> Edit | None:
        """Apply the step that was undone last.

        Args:
            ps: the patch in the editor.
        Returns:
            The step to apply again (set ``edit.new``), or None when there is none.
        """
        self._bind(ps)
        if not self._redo:
            return None
        edit = self._redo.pop()
        self._undo.append(edit)
        self._last_key = None
        return edit

    def can_undo(self, ps: object) -> bool:
        """Whether :meth:`undo` has a step for ``ps``."""
        return ps is self._ps and bool(self._undo)

    def can_redo(self, ps: object) -> bool:
        """Whether :meth:`redo` has a step for ``ps``."""
        return ps is self._ps and bool(self._redo)
