"""EditHistory: steps, merging of a drag into one step, and resetting per patch."""
from __future__ import annotations

from katatone.gui.history import MAX_STEPS, EditHistory


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make():
    clock = Clock()
    return EditHistory(clock), clock, object()


def test_undo_and_redo_walk_the_edits() -> None:
    h, clock, ps = make()
    h.record(ps, ("param", "AMP", 1), 10, 20)
    clock.t = 5
    h.record(ps, ("param", "AMP", 2), 1, 2)
    assert h.undo(ps).old == 1
    assert h.undo(ps).old == 10
    assert h.undo(ps) is None
    assert h.redo(ps).new == 20
    assert h.redo(ps).new == 2
    assert h.redo(ps) is None


def test_a_drag_is_one_step() -> None:
    h, clock, ps = make()
    for i, v in enumerate((11, 12, 13, 14)):
        clock.t = i * 0.1
        h.record(ps, ("param", "AMP", 1), v - 1 if i else 10, v)
    edit = h.undo(ps)
    assert (edit.old, edit.new) == (10, 14)
    assert h.undo(ps) is None


def test_a_pause_starts_a_new_step() -> None:
    h, clock, ps = make()
    h.record(ps, ("param", "AMP", 1), 10, 11)
    clock.t = 3
    h.record(ps, ("param", "AMP", 1), 11, 12)
    assert h.undo(ps).old == 11
    assert h.undo(ps).old == 10


def test_a_drag_back_to_the_start_leaves_no_step() -> None:
    h, clock, ps = make()
    h.record(ps, ("param", "AMP", 1), 10, 15)
    clock.t = 0.2
    h.record(ps, ("param", "AMP", 1), 15, 10)
    assert not h.can_undo(ps)


def test_a_new_edit_drops_the_redo_steps() -> None:
    h, clock, ps = make()
    h.record(ps, ("param", "AMP", 1), 10, 20)
    h.undo(ps)
    assert h.can_redo(ps)
    clock.t = 5
    h.record(ps, ("param", "AMP", 2), 1, 2)
    assert not h.can_redo(ps)


def test_an_edit_right_after_an_undo_does_not_merge_into_it() -> None:
    h, clock, ps = make()
    h.record(ps, ("param", "AMP", 1), 10, 20)
    h.undo(ps)
    h.record(ps, ("param", "AMP", 1), 10, 30)       # same target, no pause
    h.undo(ps)
    assert not h.can_undo(ps)
    assert h.redo(ps).new == 30


def test_another_patch_starts_a_new_history() -> None:
    h, clock, ps = make()
    h.record(ps, ("param", "AMP", 1), 10, 20)
    other = object()
    assert h.undo(other) is None
    assert not h.can_undo(ps)


def test_unchanged_values_are_not_recorded() -> None:
    h, clock, ps = make()
    h.record(ps, ("param", "AMP", 1), 10, 10)
    assert not h.can_undo(ps)


def test_history_is_capped() -> None:
    h, clock, ps = make()
    for i in range(MAX_STEPS + 50):
        clock.t = i * 10
        h.record(ps, ("param", "AMP", 1), i, i + 1)
    count = 0
    while h.undo(ps):
        count += 1
    assert count == MAX_STEPS
