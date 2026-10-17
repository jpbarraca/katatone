"""Flet GUI for katatone.

The data-driven editor logic lives in :mod:`katatone.gui.model` and has *no* Flet
dependency, so it is unit-testable without a GUI runtime.  The widget modules
(app / statusbar / patch_editor / system_pane / library_pane)
import Flet lazily and are only loaded when the GUI actually runs.
"""
from __future__ import annotations

from .model import (RowDesc, block_order, control_kind, friendly_block_label,
                    group_blocks, param_label, row_for_block, rows_for_region)

__all__ = [
    "RowDesc",
    "block_order",
    "control_kind",
    "friendly_block_label",
    "group_blocks",
    "param_label",
    "row_for_block",
    "rows_for_region",
]
