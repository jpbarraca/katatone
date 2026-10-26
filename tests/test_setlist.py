"""The setlist: stepping through a liveset's patches."""
from __future__ import annotations

from katatone.liveset.tsl import Tsl
from katatone.model.paramset import ParamSet
from katatone.setlist import Setlist


def make(*names: str | None) -> Setlist:
    tsl = Tsl(name="Gig")
    for n in names:
        if n is None:
            tsl.add({})
            continue
        ps = ParamSet()
        ps.set_block("PATCH%COM", ParamSet.encode_patch_name(n).ljust(16))
        tsl.add(Tsl.encode(ps))
    return Setlist.from_tsl(tsl)


def test_a_setlist_has_the_names_in_order() -> None:
    s = make("One", "Two", None)
    assert s.name == "Gig" and s.names == ["One", "Two", ""]
    assert [s.playable(i) for i in range(4)] == [True, True, False, False]


def test_next_starts_at_the_first_and_prev_at_the_last() -> None:
    s = make("One", "Two", "Three")
    assert s.target("next") == 0 and s.target("prev") == 2


def test_stepping_skips_empty_slots_and_stops_at_the_ends() -> None:
    s = make("One", None, "Three")
    s.index = 0
    assert s.target("next") == 2 and s.target("prev") is None
    s.index = 2
    assert s.target("next") is None and s.target("prev") == 0


def test_an_empty_setlist_has_nowhere_to_go() -> None:
    s = make()
    assert s.target("next") is None and s.target("prev") is None


def test_patch_returns_a_copy() -> None:
    s = make("One")
    ps = s.patch(0)
    ps.set_block("PATCH%AMP", b"\x01")
    assert s.patches[0].block("PATCH%AMP") == b""
