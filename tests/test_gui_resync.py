"""Following the amp: resync debouncing/cancellation, notifications, live-write replay.

The App is built headlessly: Flet controls are never mounted, so their
``update()`` is stubbed out, and the slot read / select are replaced by fakes.
"""
from __future__ import annotations

import threading
import time

import flet.controls.base_control as bc
import pytest

from katatone.gui import app as app_mod
from katatone.model.paramset import ParamSet
from katatone.protocol import rc6


class _Win:
    """Placeholder for ``page.window`` (the App only sets attributes on it)."""


class StubPage:
    """Just enough of ``ft.Page`` for App: threads run for real, update is a no-op."""

    def __init__(self) -> None:
        self.window = _Win()
        self.threads: list[threading.Thread] = []
        self.dialogs: list = []

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        self.dialogs.pop()

    def add(self, *controls) -> None:
        pass

    def clean(self) -> None:
        pass

    def update(self, *a, **k) -> None:
        pass

    def run_thread(self, fn, *args) -> None:
        t = threading.Thread(target=fn, args=args, daemon=True)
        self.threads.append(t)
        t.start()

    def join(self, timeout: float = 5.0) -> None:
        """Wait for every worker started so far (including ones they spawn)."""
        end = time.monotonic() + timeout
        seen = 0
        while seen < len(self.threads) and time.monotonic() < end:
            self.threads[seen].join(max(0.0, end - time.monotonic()))
            seen += 1


def _patch_with_volume(n: int, volume: int) -> ParamSet:
    """A patch whose AMP volume identifies which slot it came from."""
    from katatone.address.map import load_map
    region = load_map()[f"PATCH({n + 1})"]
    ps = ParamSet()
    ps.set_param(region, "AMP", 1, volume)
    return ps


def _volume(app, n: int) -> int:
    return app.ps.get_param(app.region_for(n), "AMP", 1)


@pytest.fixture
def app(monkeypatch, tmp_path):
    """A connected, headless App; slot N reads back with volume 10 + N."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(bc.BaseControl, "update", lambda self: None)
    a = app_mod.App(StubPage(), start_midi=False)
    a.connected = True
    a.transport = object()
    a.reads = []                    # slots actually read, in order
    a.selects = []                  # slots selected on the "amp"
    a.read_delay = 0.0
    a.read_gate = None              # optional Event the fake read waits on
    a.honour_progress = True

    def fake_read(t, n, progress=None):
        a.reads.append(n)
        for i in range(3):
            if a.read_gate is not None:
                a.read_gate.wait(5)
            time.sleep(a.read_delay)
            if progress and a.honour_progress:
                progress(i + 1, 3)
        return _patch_with_volume(n, 10 + n)

    monkeypatch.setattr("katatone.amp.read.read_patch", fake_read)
    monkeypatch.setattr("katatone.amp.write.select_patch",
                        lambda t, n, settle=0.1: a.selects.append(n))
    return a


def test_two_quick_resyncs_make_one_read_of_the_last_slot(app) -> None:
    """A burst of switches debounces to a single read, for the final channel."""
    app.resync(1, delay=0.05)
    app.resync(2, delay=0.05)
    time.sleep(0.3)
    app.page.join()
    assert app.reads == [2]
    assert app.selects == []            # resync never re-selects on the amp
    assert _volume(app, 2) == 12
    assert app.patch_number == 2 and app.live_ready and not app.dirty


def test_newer_switch_abandons_read_in_flight(app) -> None:
    """A read mid-way is abandoned at its next block when another switch arrives."""
    app.read_delay = 0.05
    app.resync(1, delay=0)
    time.sleep(0.08)                    # the first read has started
    app.resync(2, delay=0)
    app.page.join()
    assert app.reads == [1, 2]
    assert _volume(app, 2) == 12
    assert app.patch_number == 2
    assert app._read_pending is False


def test_stale_read_finishing_late_is_discarded(app) -> None:
    """Even a read that ignores cancellation cannot overwrite the newer result."""
    app.honour_progress = False         # never notices; runs to completion
    app.read_gate = threading.Event()
    app.resync(1, delay=0)
    time.sleep(0.05)
    app.resync(2, delay=0)              # waits for the read lock behind read 1
    app.read_gate.set()
    app.page.join()
    assert _volume(app, 2) == 12
    assert app.patch_number == 2


def test_user_read_does_not_select_and_supersedes_pending_resync(app) -> None:
    """read_patch(n) reads the stored slot without making it play, and cancels a pending resync."""
    app.amp_patch = 1
    app.resync(1, delay=0.3)
    app.read_patch(5)
    app.page.join()
    time.sleep(0.4)                     # the cancelled timer would have fired by now
    app.page.join()
    assert app.selects == []            # the amp keeps its channel
    assert app.reads == [5]
    assert app.amp_patch == 1 and app.patch_number == 5
    assert not app.live_ready           # edits to a slot that is not playing are not heard


def test_reading_the_slot_the_amp_plays_keeps_live_edit(app) -> None:
    app.amp_patch = 4
    app.read_patch(4)
    app.page.join()
    assert app.selects == [] and app.live_ready and app.patch_number == 4


def test_select_makes_the_viewed_slot_play_and_live_again(app) -> None:
    app.amp_patch = 1
    app.read_patch(5)
    app.page.join()
    assert not app.live_ready
    app.select_patch(5)
    app.page.join()
    assert app.selects == [5] and app.amp_patch == 5 and app.live_ready


def test_live_writes_during_read_are_replayed(app) -> None:
    """Values the pedal wrote while the read was pending survive the fresh ps."""
    app.read_delay = 0.03
    app.resync(2, delay=0)
    time.sleep(0.04)
    app.note_live_write("AMP", 1, 77, marks_dirty=False)
    app.note_live_write("SW", 0, 1, marks_dirty=True)
    app.page.join()
    region = app.region_for(2)
    assert app.ps.get_param(region, "AMP", 1) == 77
    assert app.ps.get_param(region, "SW", 0) == 1
    assert app.dirty                    # the switch counts as an edit
    assert app.recent_writes == {}


def test_expression_only_replay_stays_clean(app) -> None:
    """Writes that do not mark dirty (expression) leave the patch unmodified."""
    app.read_delay = 0.03
    app.resync(2, delay=0)
    time.sleep(0.04)
    app.note_live_write("AMP", 1, 55, marks_dirty=False)
    app.page.join()
    assert _volume(app, 2) == 55 and not app.dirty


def test_live_write_when_no_read_pending_is_not_recorded(app) -> None:
    """Outside a read there is nothing to replay."""
    app.note_live_write("AMP", 1, 5, marks_dirty=False)
    assert app.recent_writes == {}


def test_failed_read_reports_and_clears_pending(app, monkeypatch) -> None:
    """A read error is shown in the status line and does not leave a read pending."""
    def boom(t, n, progress=None):
        raise TimeoutError("amp silent")

    monkeypatch.setattr("katatone.amp.read.read_patch", boom)
    app.resync(1, delay=0)
    app.page.join()
    assert "Read failed" in app.status_text.value
    assert app._read_pending is False


# --------------------------------------------------------------- notifications


def _announce(app, n: int, nib: int = app_mod.PATCH_NUMBER_NIB) -> None:
    """Deliver an unsolicited DT1 for patch `n` as the transport would."""
    app._on_unsolicited(nib, bytes([0, n]))
    app.page.join()


def test_amp_button_press_is_followed(app) -> None:
    """A patch number the app did not select triggers a resync of that slot."""
    app.amp_patch = 1
    _announce(app, 5)
    time.sleep(0.4)
    app.page.join()
    assert app.amp_patch == 5
    assert app.reads == [5]
    assert app.patch_number == 5


def test_echo_of_our_own_select_is_ignored(app) -> None:
    """The amp answers our select with two notifications; neither starts a read."""
    app._note_self_select(2)
    _announce(app, 2)
    _announce(app, 2)
    time.sleep(0.3)
    app.page.join()
    assert app.reads == []


def test_old_self_select_does_not_hide_a_real_change(app) -> None:
    """After the echo window, the same number from the amp is genuine news."""
    app.amp_patch = 1
    app._self_select = (2, time.monotonic() - 10)
    _announce(app, 2)
    time.sleep(0.4)
    app.page.join()
    assert app.reads == [2]


def test_duplicate_of_current_patch_is_ignored(app) -> None:
    """The second notification for the patch we already track changes nothing."""
    app.amp_patch = 6
    _announce(app, 6)
    time.sleep(0.3)
    assert app.reads == []


def test_other_addresses_and_bad_payloads_are_ignored(app) -> None:
    """Echoes of our DT1 writes, short payloads and non-slots never start a read."""
    app.amp_patch = 1
    app._on_unsolicited(rc6.to_nib(0x2000_0800), b"\x00\x01")        # a param echo
    app._on_unsolicited(app_mod.PATCH_NUMBER_NIB, b"\x00")           # too short
    app._on_unsolicited(app_mod.PATCH_NUMBER_NIB, b"\x00\x03")       # no slot 3 here
    time.sleep(0.3)
    app.page.join()
    assert app.reads == [] and app.amp_patch == 1


# ------------------------------------------------------------ router switches


def test_on_channel_selected_status_mentions_discarded_edits(app) -> None:
    """A pedal switch over unsaved edits still happens and says so."""
    app.dirty = True
    app.on_channel_selected(5, "pedal")
    assert app.status_text.value == \
        "Switched to 1B from the pedal — unsaved edits discarded"
    assert app.amp_patch == 5 and not app.live_ready
    time.sleep(0.4)
    app.page.join()
    assert app.reads == [5] and not app.dirty


def test_resync_when_disconnected_does_nothing(app) -> None:
    """No transport, no read."""
    app.connected = False
    app.resync(1, delay=0)
    app.page.join()
    assert app.reads == []


# --------------------------------------------------------------- connect-time


class _FakeT:
    def __init__(self, raw: bytes | Exception) -> None:
        self.raw = raw

    def rq1(self, addr: int, size: int) -> bytes:
        assert (addr, size) == (0, 2)
        if isinstance(self.raw, Exception):
            raise self.raw
        return self.raw


def test_adopt_amp_patch_uses_what_the_amp_reports(app) -> None:
    """Connecting while the amp is on 2B must not move the editor to Panel."""
    app._adopt_amp_patch(_FakeT(b"\x00\x06"))
    assert app.amp_patch == 6 and app.patch_number == 6


def test_adopt_amp_patch_keeps_dirty_target_slot(app) -> None:
    """Offline edits keep their chosen slot; only the amp's channel is adopted."""
    app.dirty = True
    app.patch_number = 2
    app._adopt_amp_patch(_FakeT(b"\x00\x06"))
    assert app.amp_patch == 6 and app.patch_number == 2


def test_adopt_amp_patch_falls_back_on_error_or_unknown_slot(app) -> None:
    """A failed read or a slot this model lacks leaves the previous behaviour."""
    app._adopt_amp_patch(_FakeT(TimeoutError("no answer")))
    assert app.amp_patch is None and app.patch_number == 0
    app._adopt_amp_patch(_FakeT(b"\x00\x03"))
    assert app.amp_patch is None and app.patch_number == 0


def _record_writes(monkeypatch):
    steps = []
    monkeypatch.setattr("katatone.amp.write.write_patch",
                        lambda t, n, ps, name=None: steps.append(("write", n)))
    monkeypatch.setattr("katatone.amp.write.write_patch_keep_sound",
                        lambda t, n, ps, name=None: steps.append(("write keeping the sound", n)))
    monkeypatch.setattr("katatone.amp.write.select_patch",
                        lambda t, n, settle=0.1: steps.append(("select", n)))
    return steps


def test_writing_the_slot_the_amp_plays_is_a_plain_write(app, monkeypatch) -> None:
    steps = _record_writes(monkeypatch)
    app.patch_number = app.amp_patch = 3
    app.write_patch()
    app.page.join()
    assert steps == [("write", 3)] and not app.dirty


def test_writing_another_slot_keeps_the_amps_sound_and_never_selects(app, monkeypatch) -> None:
    steps = _record_writes(monkeypatch)
    app.amp_patch, app.patch_number = 1, 3
    app.write_patch()
    app.page.join()
    assert steps == [("write keeping the sound", 3)]
    assert app.amp_patch == 1 and not app.dirty


def test_sending_a_library_patch_to_a_slot_does_not_make_it_play(app, monkeypatch) -> None:
    steps = _record_writes(monkeypatch)
    app.amp_patch = 2
    app.write_amp_patch(5, ParamSet(), "From library")
    app.page.join()
    assert steps == [("write keeping the sound", 5)] and app.amp_patch == 2
    steps.clear()
    app.write_amp_patch(2, ParamSet(), "Same slot")
    app.page.join()
    assert steps == [("write", 2)]
