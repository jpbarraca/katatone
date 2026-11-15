"""What a reply would change in the patch, row by row, and which rows are taken.

Nothing here knows about the GUI: the tab shows the rows and flips ``selected``, then asks for
:meth:`Proposal.changes`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

Change = tuple[str, int, int]                  # (block, parameter index, display value)


@dataclass(eq=False)
class Row:
    """One parameter the reply sets."""
    key: str
    label: str
    block: str
    p_index: int
    old: int
    new: int
    old_text: str
    new_text: str
    role: str = "value"                 # "switch", "type" or "value"
    note: str = ""                      # e.g. "clamped from 130"
    selected: bool = True
    #: The type row this one is only meaningful with (when that type is not the current one).
    needs: "Row | None" = None

    @property
    def changed(self) -> bool:
        """Whether applying it changes the patch."""
        return self.new != self.old

    @property
    def blocked(self) -> bool:
        """Whether the type it needs is changed but not taken, so it cannot be applied."""
        return self.needs is not None and self.needs.changed and not self.needs.selected

    @property
    def active(self) -> bool:
        """Whether applying the proposal changes the patch by this row."""
        return self.selected and self.changed and not self.blocked


@dataclass
class Section:
    """The rows of one section of the reply."""
    key: str
    title: str
    rows: list[Row] = field(default_factory=list)

    @property
    def state(self) -> bool | None:
        """True when every row that can be applied is taken, False when none is, else None."""
        usable = [r for r in self.rows if r.changed and not r.blocked]
        taken = [r for r in usable if r.selected]
        if not usable or not taken:
            return False
        return True if len(taken) == len(usable) else None

    def set_all(self, on: bool) -> None:
        """Take (or drop) every row of the section."""
        for r in self.rows:
            r.selected = on and r.changed


@dataclass
class Proposal:
    """A parsed reply: what it matched, the sections it gave, and what was left out."""
    song: str = ""
    artist: str = ""
    confidence: str = ""
    rig: str = ""
    reasoning: str = ""
    notes: str = ""
    sections: list[Section] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)     # titles of the sections not in the reply
    skipped: list[str] = field(default_factory=list)     # "Delay: type 'x': not one of ..."

    def rows(self) -> list[Row]:
        """Every row of every section."""
        return [r for s in self.sections for r in s.rows]

    def changes(self) -> list[Change]:
        """The parameter changes of the rows taken, types before the settings that depend on them."""
        active = [r for r in self.rows() if r.active]
        active.sort(key=lambda r: r.role != "type")          # stable: the order is kept otherwise
        return [(r.block, r.p_index, r.new) for r in active]

    def select_all(self, on: bool) -> None:
        """Take (or drop) every row that changes the patch."""
        for s in self.sections:
            s.set_all(on)

    @property
    def count(self) -> int:
        """How many parameters would change."""
        return len(self.changes())
