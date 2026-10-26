"""Applying the analyzer's EQ proposal to the editor's patch."""
from __future__ import annotations

import json
import types

import flet.controls.base_control as bc
import numpy as np
import pytest

from katatone.audio import dsp, eqfit
from katatone.gui import analyzer as an
from katatone.gui import app as app_mod
from katatone.model import eqapply
from katatone.model.paramset import ParamSet
from test_gui_analyzer import _Page, _heard, _wav
from test_gui_resync import StubPage

FREQS = dsp.Spectrum(48000, points=160).freqs
GE = {"gains": [0, 0, 1, 5, 1, 0, 0, -4, -1, 0]}
PEQ = {"low": 2, "high": -1, "mids": [[11, 1, 6], [23, 2, -3]]}


def blank() -> ParamSet:
    """A patch with every block present at its default, as one read from an amp is
    (an empty ParamSet decodes the graphic EQ bands as -24 dB)."""
    from katatone.address.map import load_map
    ps = ParamSet()
    for b in load_map()["PATCH"].blocks:
        if not b.name.startswith("FX_DETAIL"):
            raw = bytearray(b.length)
            for i, p in enumerate(b.params):
                if p.size == 1 and isinstance(p.init, int):
                    ParamSet.encode(b, raw, i, p.init)
            ps.set_block("PATCH%" + b.name, bytes(raw))
    return ps


def put(ps: ParamSet, block: str, i: int, v: int) -> None:
    from katatone.address.map import load_map
    ps.set_param(load_map()["PATCH"], block, i, v)


def get(ps: ParamSet, block: str, i: int) -> int:
    from katatone.address.map import load_map
    return ps.get_param(load_map()["PATCH"], block, i)


# ---- the fits carry their numbers ----

def test_the_fits_carry_the_numbers_behind_their_text() -> None:
    target = eqfit.peaking_db(250, 1.0, 6.0, FREQS) - eqfit.peaking_db(4000, 1.0, 4.0, FREQS)
    ge, peq = eqfit.fit_graphic(target, FREQS), eqfit.fit_parametric(target, FREQS)
    assert len(ge.values["gains"]) == 10
    texts = [t for _, t in ge.settings]
    assert [f"{g:+d} dB" if g else "0 dB" for g in ge.values["gains"]] == texts
    assert set(peq.values) == {"low", "high", "mids"} and len(peq.values["mids"]) == 2
    assert peq.values["mids"][0][0] <= peq.values["mids"][1][0]               # lower band first
    for f, q, g in peq.values["mids"]:
        assert 0 <= f < len(eqfit.PEQ_FREQS) and 0 <= q < len(eqfit.PEQ_Q)


# ---- planning ----

def test_a_free_slot_is_switched_on_as_a_graphic_eq() -> None:
    plan = eqapply.plan(blank(), "GE-10", GE)
    assert plan.slot == 1
    got = {(b, i): v for b, i, v in plan.changes}
    assert got[("EQ_EACH(1)", 1)] == 1 and got[("EQ_EACH(1)", 2)] == eqapply.TYPE_GE10
    assert [got.get(("EQ_GE10(1)", i), 0) for i in range(10)] == GE["gains"]


def test_a_graphic_slot_that_is_on_is_adjusted_not_replaced() -> None:
    ps = blank()
    put(ps, "EQ_EACH(1)", 1, 1)
    put(ps, "EQ_EACH(1)", 2, eqapply.TYPE_GE10)
    put(ps, "EQ_GE10(1)", 3, 22)                    # 250 Hz already +22
    put(ps, "EQ_GE10(1)", 7, -10)
    plan = eqapply.plan(ps, "GE-10", GE)
    got = {(b, i): v for b, i, v in plan.changes}
    assert got[("EQ_GE10(1)", 3)] == 24             # 22 + 5, clamped to the amp's limit
    assert got[("EQ_GE10(1)", 7)] == -14
    assert ("EQ_EACH(1)", 1) not in got             # already on


def test_the_second_slot_is_used_when_the_first_is_busy_with_the_other_kind() -> None:
    ps = blank()
    put(ps, "EQ_EACH(1)", 1, 1)                     # slot 1: on, parametric
    plan = eqapply.plan(ps, "GE-10", GE)
    assert plan.slot == 2 and "EQ 2" in plan.note
    assert ("EQ_EACH(2)", 1, 1) in plan.changes


def test_both_slots_busy_with_the_other_kind_cannot_be_applied() -> None:
    ps = blank()
    for n in (1, 2):
        put(ps, f"EQ_EACH({n})", 1, 1)
    plan = eqapply.plan(ps, "GE-10", GE)
    assert plan.changes == [] and plan.slot is None and "Both EQ slots are on" in plan.note


def test_a_parametric_proposal_fills_a_free_slot() -> None:
    ps = blank()
    plan = eqapply.plan(ps, "PEQ", PEQ)
    for b, i, v in plan.changes:                    # unchanged values (Q 1 is the default) are left out
        put(ps, b, i, v)
    assert get(ps, "EQ_EACH(1)", 1) == 1 and get(ps, "EQ_EACH(1)", 2) == eqapply.TYPE_PEQ
    assert get(ps, "EQ_PEQ(1)", eqapply.PEQ_LOW_GAIN) == 2
    assert get(ps, "EQ_PEQ(1)", eqapply.PEQ_HIGH_GAIN) == -1
    assert [get(ps, "EQ_PEQ(1)", i) for i in eqapply.PEQ_MIDS[0]] == [11, 1, 6]
    assert [get(ps, "EQ_PEQ(1)", i) for i in eqapply.PEQ_MIDS[1]] == [23, 2, -3]


def test_a_parametric_slot_that_is_on_keeps_a_band_that_already_has_a_gain() -> None:
    ps = blank()
    put(ps, "EQ_EACH(1)", 1, 1)                     # on, PEQ
    put(ps, "EQ_PEQ(1)", eqapply.PEQ_LOW_GAIN, 19)
    put(ps, "EQ_PEQ(1)", eqapply.PEQ_MIDS[0][2], 4)  # the low-mid band is in use
    plan = eqapply.plan(ps, "PEQ", PEQ)
    got = {(b, i): v for b, i, v in plan.changes}
    assert got[("EQ_PEQ(1)", eqapply.PEQ_LOW_GAIN)] == 20                       # 19 + 2, clamped
    assert got[("EQ_PEQ(1)", eqapply.PEQ_MIDS[1][2])] == 6                      # the free band takes the first mid
    assert ("EQ_PEQ(1)", eqapply.PEQ_MIDS[0][2]) not in got
    assert "skipped" in plan.note                                               # the second mid had no band


def test_applying_what_is_already_there_changes_nothing() -> None:
    ps = blank()
    put(ps, "EQ_EACH(1)", 1, 1)
    put(ps, "EQ_EACH(1)", 2, eqapply.TYPE_GE10)
    plan = eqapply.plan(ps, "GE-10", {"gains": [0] * 10})
    assert plan.changes == [] and plan.note == "The EQ already has these settings."
    put(ps, "EQ_EACH(1)", 2, eqapply.TYPE_PEQ)
    flat = eqapply.plan(ps, "PEQ", {"low": 0, "high": 0, "mids": [[14, 1, 0], [23, 1, 0]]})
    assert flat.changes == [] and flat.note == "The EQ already has these settings."


# ---- the app ----

@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("KATATONE_NO_BLUETOOTH", "1")
    monkeypatch.setattr(bc.BaseControl, "update", lambda self: None)
    a = app_mod.App(StubPage(), start_midi=False)
    a.ps = blank()
    a.sent = []
    a.param_changed = lambda block, i, v: a.sent.append((block, i, v))
    return a


def test_apply_eq_changes_the_patch_tells_the_amp_and_is_one_undo_step(app) -> None:
    app.apply_eq("GE-10", GE)
    assert get(app.ps, "EQ_EACH(1)", 1) == 1 and get(app.ps, "EQ_GE10(1)", 3) == 5
    assert app.dirty and ("EQ_GE10(1)", 3, 5) in app.sent and app.last_status == "EQ 1 (GE-10) set."
    app.undo()
    assert get(app.ps, "EQ_EACH(1)", 1) == 0 and get(app.ps, "EQ_GE10(1)", 3) == 0
    assert app.last_status == "Undid the EQ."
    assert ("EQ_GE10(1)", 3, 0) in app.sent                       # the amp gets the old values back
    app.redo()
    assert get(app.ps, "EQ_GE10(1)", 3) == 5 and get(app.ps, "EQ_GE10(1)", 7) == -4


def test_two_applies_are_two_steps(app) -> None:
    app.apply_eq("GE-10", GE)
    app.apply_eq("GE-10", GE)                                     # adjusts the same slot again
    assert get(app.ps, "EQ_GE10(1)", 3) == 10
    app.undo()
    assert get(app.ps, "EQ_GE10(1)", 3) == 5
    app.undo()
    assert get(app.ps, "EQ_GE10(1)", 3) == 0


def test_apply_eq_says_why_it_cannot(app) -> None:
    for n in (1, 2):
        put(app.ps, f"EQ_EACH({n})", 1, 1)
    app.apply_eq("GE-10", GE)
    assert app.last_status.startswith("Both EQ slots are on") and not app.dirty and not app.sent
    app.undo()
    assert app.last_status == "Nothing to undo."


def test_apply_eq_waits_for_a_busy_amp(app) -> None:
    app.busy = True
    app.apply_eq("GE-10", GE)
    assert "Wait" in app.last_status and not app.sent


def test_the_follower_applies_what_the_analyzer_sends(app) -> None:
    line = "EQ " + json.dumps({"kind": "GE-10", "values": GE}) + "\n"
    app._follow_analyzer(types.SimpleNamespace(stdout=iter(["noise\n", line])))
    assert get(app.ps, "EQ_GE10(1)", 3) == 5
    app._follow_analyzer(types.SimpleNamespace(stdout=iter(["EQ {broken\n"])))
    assert app.last_status.startswith("Cannot apply the EQ proposal")
    app._follow_analyzer(types.SimpleNamespace(stdout=iter(["EQ " + json.dumps({"kind": "PEQ"}) + "\n"])))
    assert app.last_status.startswith("Cannot apply the EQ proposal")


def test_in_a_browser_the_analyzer_gets_the_backends_apply(monkeypatch) -> None:
    made = []

    class Tool:
        wants_apply = True

        def __init__(self, page, card, on_apply=None):
            made.append(on_apply)

        def close(self): pass
    monkeypatch.setattr(app_mod, "_tool_class", lambda page: Tool)
    backend = types.SimpleNamespace(amp_port=None, apply_eq=lambda k, v: None,
                                    launch_tokens=app_mod.auth_mod.LaunchTokens())
    token = backend.launch_tokens.issue()
    page = types.SimpleNamespace(route=f"/analyzer?token={token}", clean=lambda: None)
    app_mod._open_web_session(page, backend)
    assert made == [backend.apply_eq]


# ---- the analyzer's button ----

@pytest.fixture
def win(monkeypatch, tmp_path):
    monkeypatch.setattr(an, "list_input_devices", lambda: [])
    sent = []
    w = an.Analyzer(_Page(), on_apply=lambda kind, values: sent.append((kind, values)))
    w.sent = sent
    p = tmp_path / "part.wav"
    _wav(p)
    w._load_reference(str(p))
    w._apply_loaded()
    _heard(w)
    w._refresh_hints(w.smooth.avg.copy(), True, force=True)
    return w


def test_the_apply_button_needs_a_proposal_worth_applying(win) -> None:
    assert win.apply_btn.disabled                                  # EQ preview is off
    win.eq_preview.value = "PEQ"
    win._set_preview()
    assert not win.apply_btn.disabled


def test_pressing_apply_sends_the_chosen_proposal(win) -> None:
    win.eq_preview.value = "GE-10"
    win._set_preview()
    win._apply_eq()
    (kind, values), = win.sent
    assert kind == "GE-10" and values == win._fits["GE-10"].values
    assert win.apply_btn.content.value == "Sent to the editor"
    json.dumps(values)                                             # it must survive the pipe


def test_without_a_link_to_the_editor_the_button_stays_off(monkeypatch) -> None:
    monkeypatch.setattr(an, "list_input_devices", lambda: [])
    w = an.Analyzer(_Page())
    w.eq_preview.value = "PEQ"
    w._set_preview()
    assert w.apply_btn.disabled and "No connection" in w.apply_btn.tooltip
    w._apply_eq()                                                  # nothing happens, no error


def test_the_analyzer_prints_the_proposal_for_the_main_window(monkeypatch, capsys) -> None:
    import os
    made = []

    class Recorder:
        def __init__(self, page, card=None, on_apply=None):
            made.append(on_apply)

        def close(self): pass
    monkeypatch.setattr(an, "Analyzer", Recorder)
    monkeypatch.setattr(an.ft, "run", lambda main, **kw: main(_Page()))
    monkeypatch.setattr(os, "_exit", lambda code: None)
    an.main(["--apply-pipe"])
    an.main([])
    assert made[1] is None                                         # started on its own: no link
    made[0]("GE-10", GE)
    line = capsys.readouterr().out
    assert line.startswith("EQ ") and json.loads(line[3:]) == {"kind": "GE-10", "values": GE}
