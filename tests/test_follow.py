"""Following the amp: a knob turned on it, or a pedal moving an effect's setting, shown live."""
from __future__ import annotations

import pytest

from katatone.address.map import load_map
from katatone.amp import follow, notify
from katatone.gui import pedal
from katatone.model.paramset import ParamSet
from katatone.protocol import rc6

from test_gui_resync import app  # noqa: F401  (fixture)

EDIT = load_map()["PATCH"]
REGION = load_map()["PATCH(1)"]


def nib_of(block: str, name_ends: str):
    """The nibble address and parameter of ``block``'s parameter whose name ends so."""
    b = EDIT.block(block)
    i, p = next((i, p) for i, p in enumerate(b.params) if p.name.endswith(name_ends))
    return rc6.to_nib(b.base_addr + p.off), i, p


def payload_of(p, value: int) -> bytes:
    return ParamSet.raw_bytes(p.size, value + p.ofs)


# ---- decoding what the amp announces

def test_a_parameter_is_named_with_its_value() -> None:
    nib, i, p = nib_of("AMP", "_AMP_GAIN")
    c = notify.decode(nib, payload_of(p, 37))
    assert (c.block, c.p_index, c.value, c.name) == ("AMP", i, 37, "AMP.AMP_GAIN")


def test_a_mod_or_fx_setting_and_a_negative_value() -> None:
    nib, i, p = nib_of("FX_DETAIL(6)", "_PITCHSHIFT_PITCH1")
    assert p.min < 0                                              # -24..24, stored with a bias
    for v in (-24, -1, 0, 24):
        c = notify.decode(nib, payload_of(p, v))
        assert (c.block, c.value) == ("FX_DETAIL(6)", v) and c.name.endswith("PITCHSHIFT_PITCH1")


@pytest.mark.parametrize("size", [1, 4])                  # the sizes of the numeric parameters of a patch
def test_parameters_of_every_size(size) -> None:
    block, i, p = next((b.name, i, p) for b in EDIT.blocks for i, p in enumerate(b.params)
                       if p.size == size and p.max - p.min >= 2 and not b.name.startswith("PATCH_KNOB"))
    nib = rc6.to_nib(EDIT.block(block).base_addr + p.off)
    for v in (p.min, p.max, (p.min + p.max) // 2):
        c = notify.decode(nib, payload_of(p, v))
        assert (c.block, c.p_index, c.value) == (block, i, v)


def test_the_delay_time_is_four_nibbles_and_decodes() -> None:
    nib, i, p = nib_of("DELAY(1)", "_DELAY_TIME")
    assert p.size == 4
    assert notify.decode(nib, payload_of(p, 380)).value == 380


def test_what_is_not_one_parameter_is_not_decoded() -> None:
    nib, i, p = nib_of("AMP", "_AMP_GAIN")
    assert notify.decode(nib, b"\x01\x02") is None                # wrong size for the parameter
    assert notify.decode(nib, b"") is None
    assert notify.decode(rc6.to_nib(0x0000_0000), b"\x00\x03") is None     # the patch number
    assert notify.decode(0x12345678, b"\x01") is None             # no parameter there
    block = EDIT.block("AMP")
    assert notify.decode(rc6.to_nib(block.base_addr), bytes(block.length)) is None   # a whole block


def test_every_numeric_parameter_of_the_edit_buffer_can_be_told_apart() -> None:
    index = notify._index()
    numeric = sum(1 for b in EDIT.blocks for p in b.params if p.size in (1, 2, 4))
    assert len(index) == numeric                                  # no two share an address


# ---- the follower, on its own

class Host:
    def __init__(self) -> None:
        self.live_ready = True
        self.mirrored, self.shown, self.pedals = [], [], set()

    def mirror_announced(self, block, p_index, value, marks_dirty) -> None:
        self.mirrored.append((block, p_index, value, marks_dirty))

    def show_param(self, block, p_index, value) -> None:
        self.shown.append((block, p_index, value))

    def pedal_targets(self):
        return self.pedals


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class FakeTimer:
    made: list = []

    def __init__(self, interval, fn) -> None:
        self.interval, self.fn, self.daemon = interval, fn, False
        FakeTimer.made.append(self)

    def start(self) -> None:
        pass


@pytest.fixture
def rig():
    FakeTimer.made = []
    host, clock = Host(), Clock()
    return host, clock, follow.AmpFollower(host, clock, FakeTimer)


def gain(value: int):
    nib, i, p = nib_of("AMP", "_AMP_GAIN")
    return nib, i, payload_of(p, value)


def test_a_knob_turned_on_the_amp_is_an_edit(rig) -> None:
    host, clock, f = rig
    nib, i, data = gain(40)
    assert f.announced(nib, data) is True
    assert host.mirrored == [("AMP", i, 40, True)] and host.shown == [("AMP", i, 40)]


def test_a_pedal_moving_its_setting_is_not_an_edit(rig) -> None:
    host, clock, f = rig
    nib, i, p = nib_of("FX_DETAIL(6)", "_PITCHSHIFT_PITCH1")
    host.pedals = {("FX_DETAIL(6)", i)}
    f.announced(nib, payload_of(p, 12))
    assert host.mirrored == [("FX_DETAIL(6)", i, 12, False)]      # mirrored, not unsaved


def test_nothing_is_followed_while_the_editor_does_not_mirror_the_amp(rig) -> None:
    host, clock, f = rig
    host.live_ready = False
    nib, i, data = gain(40)
    assert f.announced(nib, data) is False
    assert host.mirrored == [] and host.shown == []


def test_what_is_not_a_parameter_is_left_alone(rig) -> None:
    host, clock, f = rig
    assert f.announced(rc6.to_nib(0x0000_0000), b"\x00\x03") is False
    assert f.announced(0x12345678, b"\x01") is False
    assert host.mirrored == []


def test_the_echo_of_our_own_write_is_not_followed(rig) -> None:
    host, clock, f = rig
    nib, i, data = gain(40)
    f.wrote("AMP", i)
    clock.now += 0.1
    assert f.announced(nib, gain(39)[2]) is False                  # a late echo: not news
    assert host.mirrored == []
    clock.now += follow.ECHO_WINDOW_S                             # later, it is the amp's own change
    assert f.announced(nib, data) is True and len(host.mirrored) == 1


def test_our_write_of_one_parameter_does_not_hide_another(rig) -> None:
    host, clock, f = rig
    f.wrote("AMP", 99)
    nib, i, data = gain(40)
    assert f.announced(nib, data) is True


def test_every_value_reaches_the_patch_but_the_screen_follows_gently(rig) -> None:
    host, clock, f = rig
    nib, i, p = nib_of("AMP", "_AMP_GAIN")
    for v in range(10, 20):                                       # a fast sweep: 10 messages at once
        f.announced(nib, payload_of(p, v))
        clock.now += 0.001
    assert [m[2] for m in host.mirrored] == list(range(10, 20))   # the patch has every one
    assert host.shown == [("AMP", i, 10)]                         # the screen moved once, so far
    assert len(FakeTimer.made) == 1 and FakeTimer.made[0].interval == follow.SHOW_INTERVAL_S
    FakeTimer.made[0].fn()                                        # the timer fires: the latest value
    assert host.shown[-1] == ("AMP", i, 19) and len(host.shown) == 2
    clock.now += 1.0
    f.announced(nib, payload_of(p, 25))                           # calm again: shown at once
    assert host.shown[-1] == ("AMP", i, 25) and len(FakeTimer.made) == 1


def test_each_parameter_is_throttled_on_its_own(rig) -> None:
    host, clock, f = rig
    nib_a, ia, pa = nib_of("AMP", "_AMP_GAIN")
    nib_b, ib, pb = nib_of("AMP", "_AMP_BASS")
    f.announced(nib_a, payload_of(pa, 1))
    f.announced(nib_b, payload_of(pb, 2))
    assert host.shown == [("AMP", ia, 1), ("AMP", ib, 2)]


# ---- in the app

class FakeTransport:
    """Takes what the live writer sends (the app's transport is a stand-in in these tests)."""

    def __init__(self) -> None:
        self.sent = []

    def dt1(self, addr, data) -> None:
        self.sent.append((addr, bytes(data)))


def _ready(app):  # noqa: F811
    app.live_ready = True
    app.transport = FakeTransport()
    return app


def test_the_app_mirrors_a_knob_turned_on_the_amp(app) -> None:  # noqa: F811
    _ready(app)
    nib, i, p = nib_of("AMP", "_AMP_GAIN")
    app._on_unsolicited(nib, payload_of(p, 63))
    assert app.ps.get_param(app.region_for(app.patch_number), "AMP", i) == 63
    assert app.dirty                                              # an edit of the amp's patch


def test_the_app_mirrors_the_pedal_without_calling_it_an_edit(app) -> None:  # noqa: F811
    _ready(app)
    region = app.region_for(app.patch_number)
    ps = app.ps
    ps.set_param(region, "ASSIGN_EXPPDL_FUNC", 0, 6)              # the pedal drives the FX slot
    ps.set_param(region, "COLOR", 2, 2)                           # yellow: FX(6)
    ps.set_param(region, "FX(6)", 0, 11)                          # a pitch shifter
    detail = region.block("ASSIGN_EXPPDL_DETAIL").params
    k = next(k for k, p in enumerate(detail) if p.name.endswith("_DETAIL_PITCHSHIFTER"))
    names = pedal._choices()["PITCHSHIFTER"]
    ps.set_param(region, "ASSIGN_EXPPDL_DETAIL", k, names.index("PS1:PITCH"))
    (block, index), = pedal.targets(ps, region)
    assert block == "FX_DETAIL(6)" and region.block(block).params[index].name.endswith("PITCHSHIFT_PITCH1")
    app.dirty = False
    nib, i, p = nib_of(block, "_PITCHSHIFT_PITCH1")
    app._on_unsolicited(nib, payload_of(p, 17))
    assert ps.get_param(region, block, index) == 17               # the editor follows the pedal
    assert not app.dirty                                          # and the patch is not changed by it


def test_the_editors_own_changes_do_not_come_back_as_the_amps(app) -> None:  # noqa: F811
    _ready(app)
    nib, i, p = nib_of("AMP", "_AMP_GAIN")
    app.param_changed("AMP", i, 80)                               # a knob of the editor
    app.ps.set_param(app.region_for(app.patch_number), "AMP", i, 80)
    app._on_unsolicited(nib, payload_of(p, 78))                   # the amp's late echo
    assert app.ps.get_param(app.region_for(app.patch_number), "AMP", i) == 80


def test_the_routers_writes_are_not_followed_back(app) -> None:  # noqa: F811
    _ready(app)
    nib, i, p = nib_of("AMP", "_AMP_GAIN")
    app.apply_live_value("AMP", i, 55, mark_dirty=False)          # the MIDI router wrote it
    app._on_unsolicited(nib, payload_of(p, 54))
    assert app.ps.get_param(app.region_for(app.patch_number), "AMP", i) == 55


def test_nothing_is_followed_when_the_editor_is_not_the_amps_patch(app) -> None:  # noqa: F811
    app.live_ready = False                                        # for example a library patch
    nib, i, p = nib_of("AMP", "_AMP_GAIN")
    app._on_unsolicited(nib, payload_of(p, 63))
    assert app.ps.get_param(app.region_for(app.patch_number), "AMP", i) == 0 and not app.dirty


def test_the_widget_is_moved(app, monkeypatch) -> None:  # noqa: F811
    _ready(app)
    shown = []
    monkeypatch.setattr(app.editor, "show_value", lambda b, i, v: shown.append((b, i, v)))
    nib, i, p = nib_of("AMP", "_AMP_GAIN")
    app._on_unsolicited(nib, payload_of(p, 63))
    assert shown == [("AMP", i, 63)]


def test_the_patch_number_is_still_handled(app) -> None:  # noqa: F811
    seen = []
    app.on_amp_patch_changed = lambda n: seen.append(n)
    app.connected = True
    app._on_unsolicited(rc6.to_nib(0x0000_0000), b"\x00\x02")
    app.page.join()
    assert seen == [2]


def test_which_parameters_a_pedal_drives_is_not_worked_out_again_and_again(app, monkeypatch) -> None:  # noqa: F811
    calls = []
    real = pedal.targets
    monkeypatch.setattr(pedal, "targets", lambda ps, region: calls.append(1) or real(ps, region))
    for _ in range(50):
        app.pedal_targets()
    assert len(calls) == 1
    region = app.region_for(app.patch_number)
    app.ps.set_param(region, "ASSIGN_EXPPDL_FUNC", 0, 7)          # the assignment changes
    app.pedal_targets()
    assert len(calls) == 2
    app.ps = ParamSet()                                           # another patch
    app.pedal_targets()
    assert len(calls) == 3
