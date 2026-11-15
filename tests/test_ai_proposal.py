"""Which rows are taken, what they apply to, and the variation (colour) they go to."""
from __future__ import annotations

import pytest

from katatone.address.map import load_map
from katatone.ai import parse, spec
from katatone.model.paramset import ParamSet


@pytest.fixture
def ps() -> ParamSet:
    return ParamSet()


@pytest.fixture
def region():
    return load_map()["PATCH"]


def _rows(p, title_start):
    sec = next(s for s in p.sections if s.title.startswith(title_start))
    return {r.key + ("*" if r.role == "switch" else ""): r for r in sec.rows}


def test_every_key_of_every_section_resolves_in_every_colour(ps, region) -> None:
    for colour in range(3):
        for i in range(5):
            ps.set_param(region, "COLOR", i, colour)
        for s in spec.SECTIONS:
            r = spec.resolve(s, ps)
            assert r.plain, s
            assert all(f.block and f.param for f in r.plain.values())
            if r.detail is not None:
                assert all(r.detail(t) for t in range(2 if s == "eq" else 31)), s


def test_the_selected_colour_is_written(ps, region) -> None:
    for i, colour in enumerate((1, 2, 1, 2, 2)):        # booster, mod, fx, delay, reverb
        ps.set_param(region, "COLOR", i, colour)
    p = parse.parse({"booster": {"drive": 10}, "mod": {"type": "CHORUS", "params": {"low_rate": 5}},
                     "fx": {"type": "TREMOLO", "params": {"rate": 5}}, "delay": {"time_ms": 300},
                     "reverb": {"level": 20}}, ps)
    blocks = {s.key: {r.block for r in s.rows} for s in p.sections}
    assert blocks == {"booster": {"BOOSTER(2)"}, "mod": {"FX(3)", "FX_DETAIL(3)"},
                      "fx": {"FX(5)", "FX_DETAIL(5)"}, "delay": {"DELAY(3)"},
                      "reverb": {"REVERB(3)"}}
    assert [s.title for s in p.sections if s.key == "mod"] == ["Mod · YELLOW"]


def test_rows_that_change_nothing_start_unselected(ps) -> None:
    p = parse.parse({"amp": {"gain": 0, "bass": 30}}, ps)
    amp = _rows(p, "Amp")
    assert not amp["gain"].changed and not amp["gain"].selected
    assert amp["bass"].selected and p.changes() == [("AMP", 2, 30)]


def test_taking_and_dropping_rows(ps) -> None:
    p = parse.parse({"amp": {"gain": 10, "bass": 20, "treble": 30}}, ps)
    amp = _rows(p, "Amp")
    assert p.sections[0].state is True and p.count == 3
    amp["bass"].selected = False
    assert p.sections[0].state is None and p.count == 2
    assert [c[2] for c in p.changes()] == [10, 30]
    p.select_all(False)
    assert p.sections[0].state is False and p.changes() == []
    p.sections[0].set_all(True)
    assert p.count == 3


def test_a_new_type_is_applied_before_its_settings(ps) -> None:
    p = parse.parse({"mod": {"params": {"low_rate": 30}, "type": "CHORUS", "on": True}}, ps)
    changes = p.changes()
    assert changes[0][0] == "FX(1)" and changes[0][2] == 23           # the type, first
    assert {c[0] for c in changes[1:]} == {"SW", "FX_DETAIL(1)"}


def test_settings_wait_for_their_type(ps) -> None:
    p = parse.parse({"mod": {"type": "CHORUS", "params": {"low_rate": 30, "low_depth": 40}}}, ps)
    rows = _rows(p, "Mod")
    assert p.count == 3
    rows["type"].selected = False                                     # keep the current effect
    assert rows["low_rate"].blocked and not rows["low_rate"].active
    assert p.changes() == [] and p.sections[0].state is False
    rows["type"].selected = True
    assert p.count == 3


def test_settings_of_the_current_type_never_wait(ps, region) -> None:
    ps.set_param(region, "FX(1)", 0, 23)                              # already CHORUS
    p = parse.parse({"mod": {"type": "CHORUS", "params": {"low_rate": 30}}}, ps)
    rows = _rows(p, "Mod")
    assert not rows["type"].changed and not rows["low_rate"].blocked
    assert p.changes() == [("FX_DETAIL(1)", rows["low_rate"].p_index, 30)]


def test_switching_off_is_a_row_like_any_other(ps, region) -> None:
    ps.set_param(region, "SW", 3, 1)                                  # delay is on
    p = parse.parse({"delay": {"on": False}}, ps)
    row = _rows(p, "Delay")["on*"]
    assert (row.old_text, row.new_text, row.selected) == ("On", "Off", True)
    assert p.changes() == [("SW", 3, 0)]


def test_eq_follows_its_type(ps) -> None:
    p = parse.parse({"eq": {"on": True, "type": "GE-10", "params": {"125hz": 4, "level": -2}}}, ps)
    blocks = {r.block for r in p.sections[0].rows}
    assert blocks == {"EQ_EACH(1)", "EQ_GE10(1)"}
    p = parse.parse({"eq": {"type": "PARAMETRIC EQ", "params": {"low_gain": 4}}}, ps)
    assert {r.block for r in p.sections[0].rows} == {"EQ_EACH(1)", "EQ_PEQ(1)"}


@pytest.mark.parametrize("song, name", [
    ("Sultans of Swing", "Sultans of Swing"),
    ("  Smells   Like Teen Spirit ", "Smells Like Teen"),          # at most 16 characters
    ("Señorita — Ünïcode", "Senorita Unicode"),                  # accents dropped, not "?"
    ("日本語", "Tone AI"),                                          # nothing the amp can show
    ("", "Tone AI"),
])
def test_the_patch_is_named_after_the_song_as_the_amp_can_show_it(song, name) -> None:
    from katatone.ai.proposal import Proposal
    assert Proposal(song=song).patch_name() == name
