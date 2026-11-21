"""The Pedal tab's position meter: where the pedal is, and the lowest and highest it has shown."""
from __future__ import annotations

import types

import flet as ft
import pytest

from katatone.address.map import load_map
from katatone.gui import pedal
from katatone.model.paramset import ParamSet

from test_follow import nib_of, payload_of
from test_gui_resync import app  # noqa: F401  (fixture)

REGION = load_map()["PATCH(1)"]


def find(control, kind):
    found = [control] if isinstance(control, kind) else []
    for child in [*(getattr(control, "controls", None) or []), getattr(control, "content", None)]:
        if child is not None:
            found += find(child, kind)
    return found


# ---- the reading

def test_the_reading_keeps_the_lowest_and_the_highest() -> None:
    r = pedal.PedalReading()
    assert (r.position, r.lowest, r.highest) == (None, None, None)
    for v in (40, 12, 97, 50):
        r.feed(v)
    assert (r.position, r.lowest, r.highest) == (50, 12, 97)


def test_reset_forgets_the_range_but_not_where_the_pedal_is() -> None:
    r = pedal.PedalReading(30)
    r.feed(10)
    r.feed(90)
    r.reset()
    assert (r.position, r.lowest, r.highest) == (90, None, None)
    r.reset(position=55)
    assert r.position == 55
    r.feed(60)
    assert (r.lowest, r.highest) == (60, 60)                       # counted from the reset


@pytest.mark.parametrize("kind, position, has_range", [
    (0, "PEDALFX_PEDALWAH_PEDAL_POSITION", True), (1, "PEDALFX_BEND_PEDAL_POS", False),
    (2, "PEDALFX_EWAH_PEDAL_POS", True)])
def test_each_pedal_effect_has_its_position_and_range(kind, position, has_range) -> None:
    block, i = pedal.position_param(REGION, kind)
    assert block == "PEDALFX" and REGION.block(block).params[i].name == "PRMID_PATCH_" + position
    span = pedal.range_params(REGION, kind)
    if not has_range:
        assert span is None
        return
    (b1, i1), (b2, i2) = span
    names = [REGION.block(b).params[i].name for b, i in span]
    assert names[0].endswith("_PEDAL_MIN") and names[1].endswith("_PEDAL_MAX")


def test_the_selected_pedal_effect_is_read_from_the_patch() -> None:
    ps = ParamSet()
    for kind in (0, 1, 2):
        ps.set_param(REGION, "PEDALFX_COM", pedal.param_index(REGION, *pedal.PEDAL_TYPE_PARAM), kind)
        assert pedal.pedal_type(ps, REGION) == kind


# ---- the card on the Pedal tab

def _tab(app):  # noqa: F811
    i = next(i for i, (label, _) in enumerate(app.editor._tab_specs) if label == "Pedal")
    app.editor._build_tab(i)
    return i, app.editor._tab_view.controls[i].content


def _texts(content):
    return [t.value for t in find(content, ft.Text) if isinstance(t.value, str)]


def _meter(content):
    """The Pedal Calibration block: the last row of the tab (a column of rows of blocks)."""
    return content.controls[0].controls[-1]


def test_the_first_block_of_the_pedal_tab_is_called_general(app) -> None:  # noqa: F811
    from katatone.gui import model
    i, content = _tab(app)
    titles = [t.value.upper() for t in find(content, ft.Text) if isinstance(t.value, str)]
    assert "GENERAL" in titles and "PEDALFX COM" not in titles
    assert titles.index("GENERAL") < titles.index("PEDALFX")            # still the first, as before
    assert model.block_title("PEDALFX_COM") == "PEDALFX COM"            # lists of blocks elsewhere keep it


def test_the_meter_is_the_last_block_of_the_pedal_tab_and_full_width(app) -> None:  # noqa: F811
    i, content = _tab(app)
    last = _meter(content)
    assert isinstance(last, ft.Row)
    card = last.controls[0]
    assert card.expand is True
    assert any(t.value.upper() == "PEDAL CALIBRATION" for t in find(card, ft.Text))   # its title
    rows = content.controls[0].controls
    assert len(rows) >= 3 and rows[-1] is last                         # the blocks of the effects, then it
    before = [t.value.upper() for r in rows[:-1] for t in find(r, ft.Text) if isinstance(t.value, str)]
    assert "PEDAL CALIBRATION" not in before and "PEDALFX" in before   # not above them
    assert not any(t.value == "Pedal position" for t in find(content, ft.Text))   # the old title
    assert any("heel" in t for t in _texts(card))                   # how to calibrate


def test_the_meter_follows_the_pedal_and_remembers_the_range(app) -> None:  # noqa: F811
    i, content = _tab(app)
    _, pi = pedal.position_param(REGION, 0)
    bar = find(content, ft.ProgressBar)[0]
    for v in (40, 12, 97, 50):
        app.editor.show_value("PEDALFX", pi, v)
    texts = _texts(content)
    assert "50" in texts and any("Lowest 12" in t and "Highest 97" in t for t in texts)
    assert bar.value == 0.5


def test_before_the_pedal_moves_it_says_so(app) -> None:  # noqa: F811
    i, content = _tab(app)
    assert any("Move the pedal from heel to toe" in t for t in _texts(content))


def test_reset_range_starts_the_range_again(app) -> None:  # noqa: F811
    i, content = _tab(app)
    _, pi = pedal.position_param(REGION, 0)
    for v in (5, 95):
        app.editor.show_value("PEDALFX", pi, v)
    reset = next(b for b in find(content, ft.FilledButton) if "Reset" in b.content.value)
    reset.on_click(None)
    texts = _texts(content)
    assert "95" in texts and any("Move the pedal from heel to toe" in t for t in texts)
    app.editor.show_value("PEDALFX", pi, 60)
    assert any("Lowest 60" in t and "Highest 60" in t for t in _texts(content))


def test_only_the_selected_pedal_effect_moves_it(app) -> None:  # noqa: F811
    region = app.region_for(app.patch_number)
    app.ps.set_param(region, "PEDALFX_COM", pedal.param_index(region, *pedal.PEDAL_TYPE_PARAM), 1)   # bend
    i, content = _tab(app)
    _, wah = pedal.position_param(region, 0)
    _, bend = pedal.position_param(region, 1)
    app.editor.show_value("PEDALFX", wah, 77)                       # the wah is not the one in use
    assert not any("Highest 77" in t for t in _texts(content))
    app.editor.show_value("PEDALFX", bend, 33)
    assert any("Lowest 33" in t for t in _texts(content))


def test_changing_the_pedal_effect_starts_the_range_again(app, monkeypatch) -> None:  # noqa: F811
    i, content = _tab(app)
    reads = []
    monkeypatch.setattr(app, "read_pedal_position", lambda: reads.append(1))
    _, wah = pedal.position_param(REGION, 0)
    app.editor.show_value("PEDALFX", wah, 10)
    app.editor.show_value("PEDALFX", wah, 90)
    region = app.region_for(app.patch_number)
    t = pedal.param_index(region, *pedal.PEDAL_TYPE_PARAM)
    app.ps.set_param(region, "PEDALFX_COM", t, 2)                    # now the 95E
    app.editor.show_value("PEDALFX_COM", t, 2)
    assert any("Move the pedal from heel to toe" in x for x in _texts(content))
    assert reads == [1]                                              # and where its pedal is now is read


def test_the_effects_own_range_is_shown_and_follows_its_knobs(app) -> None:  # noqa: F811
    region = app.region_for(app.patch_number)
    (bmin, imin), (bmax, imax) = pedal.range_params(region, 0)
    app.ps.set_param(region, bmin, imin, 10)
    app.ps.set_param(region, bmax, imax, 90)
    i, content = _tab(app)
    assert any("PEDAL MIN 10" in t and "PEDAL MAX 90" in t for t in _texts(content))
    app.ps.set_param(region, bmin, imin, 25)                        # the knob was turned
    app.editor.show_value(bmin, imin, 25)
    assert any("PEDAL MIN 25" in t for t in _texts(content))


def test_the_bend_has_no_range_of_its_own(app) -> None:  # noqa: F811
    region = app.region_for(app.patch_number)
    app.ps.set_param(region, "PEDALFX_COM", pedal.param_index(region, *pedal.PEDAL_TYPE_PARAM), 1)
    i, content = _tab(app)
    meter = _meter(content)                                         # the meter, not the knobs above it
    assert not any("PEDAL MIN" in t for t in _texts(meter))


# ---- what the amp announces and reads

def test_the_amps_announcements_move_the_meter(app) -> None:  # noqa: F811
    app.live_ready = True
    i, content = _tab(app)
    nib, pi, p = nib_of("PEDALFX", "_PEDALWAH_PEDAL_POSITION")
    for v in (20, 80):
        app._on_unsolicited(nib, payload_of(p, v))
    app._follower._flush()                                           # the screen follows 40 ms later
    assert any("Lowest 20" in t and "Highest 80" in t for t in _texts(content))


def test_the_position_is_read_from_the_amp(app, monkeypatch) -> None:  # noqa: F811
    from katatone.amp import write
    app.live_ready, app.connected, app.transport = True, True, object()
    i, content = _tab(app)
    asked = []
    monkeypatch.setattr(write, "read_param", lambda t, b, k: asked.append((b, k)) or 63)
    app._read_pedal_position()
    assert asked == [pedal.position_param(app.region_for(app.patch_number), 0)]
    assert "63" in _texts(content)


@pytest.mark.parametrize("live, connected", [(False, True), (True, False)])
def test_nothing_is_read_unless_the_editor_follows_the_amp(app, monkeypatch, live, connected) -> None:  # noqa: F811
    app.live_ready, app.connected, app.transport = live, connected, object()
    ran = []
    monkeypatch.setattr(app.page, "run_thread", lambda fn, *a: ran.append(fn))
    app.read_pedal_position()
    assert ran == []


def test_a_failed_read_leaves_the_meter_alone(app, monkeypatch) -> None:  # noqa: F811
    from katatone.amp import write
    app.live_ready, app.connected, app.transport = True, True, object()
    i, content = _tab(app)

    def boom(t, b, k):
        raise TimeoutError("no answer")
    monkeypatch.setattr(write, "read_param", boom)
    app._read_pedal_position()                                       # no exception
    assert not any("63" == t for t in _texts(content))


def test_opening_the_pedal_tab_reads_the_position(app, monkeypatch) -> None:  # noqa: F811
    asked = []
    monkeypatch.setattr(app, "read_pedal_position", lambda: asked.append(1))
    i = next(i for i, (label, _) in enumerate(app.editor._tab_specs) if label == "Pedal")
    app.editor._on_tab(types.SimpleNamespace(data=str(i)))
    app.editor._on_tab(types.SimpleNamespace(data=str(i)))           # every visit, not only the first
    assert asked == [1, 1]
    app.editor._on_tab(types.SimpleNamespace(data="0"))              # another tab: not
    assert asked == [1, 1]
