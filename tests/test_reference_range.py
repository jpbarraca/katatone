"""Using only a part of the reference recording: From and To times."""
from __future__ import annotations

import asyncio
import shutil
import subprocess

import numpy as np
import pytest

from katatone.audio import reference as ref
from katatone.gui import analyzer as an
from katatone.gui import files
from test_gui_analyzer import _Page, _heard                  # noqa: F401
from test_reference import RATE, sine, write_wav

# three parts of two seconds each: 220 Hz, 880 Hz, 3300 Hz
PARTS = (220.0, 880.0, 3300.0)


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "parts.wav"
    write_wav(p, np.concatenate([sine(f, 2.0) for f in PARTS]))
    return p


def peak_hz(x: np.ndarray) -> float:
    spectrum = np.abs(np.fft.rfft(x * np.hanning(x.size)))
    return float(np.argmax(spectrum) * RATE / x.size)


# ---- the times ----

@pytest.mark.parametrize("text,seconds", [
    ("", None), ("  ", None), ("90", 90.0), ("1:30", 90.0), ("0:05", 5.0), ("1:02:03", 3723.0),
    ("1:30.5", 90.5), (" 2:00 ", 120.0), ("0", 0.0),
])
def test_times_are_read_as_seconds_or_minutes_and_seconds(text, seconds) -> None:
    assert ref.parse_time(text) == seconds


@pytest.mark.parametrize("text", ["x", "1:2:3:4", "-5", ":30", "1::3", "nan", "inf", "1:-2", "1,5"])
def test_other_text_is_refused_with_a_message(text) -> None:
    with pytest.raises(ValueError, match="is not a time"):
        ref.parse_time(text)


@pytest.mark.parametrize("seconds,text", [(0, "0:00"), (5, "0:05"), (65, "1:05"), (65.5, "1:05.5"),
                                          (3725, "1:02:05"), (59.95, "1:00"), (600, "10:00")])
def test_times_are_shown_as_minutes_and_seconds(seconds, text) -> None:
    assert ref.format_time(seconds) == text


def test_what_is_shown_can_be_typed_back() -> None:
    for seconds in (0, 7, 61, 61.5, 3601, 125.9):
        assert ref.parse_time(ref.format_time(seconds)) == pytest.approx(seconds, abs=0.05)


# ---- reading a part ----

@pytest.mark.parametrize("start,stop,hz", [(0.0, 2.0, 220.0), (2.0, 4.0, 880.0), (4.0, 6.0, 3300.0),
                                           (2.5, 3.5, 880.0), (5.0, None, 3300.0), (0.0, 1.5, 220.0)])
def test_only_the_asked_part_of_a_wav_is_read(wav, start, stop, hz) -> None:
    x = ref.load_audio(wav, start=start, stop=stop)
    assert peak_hz(x) == pytest.approx(hz, rel=0.02)
    assert x.size == pytest.approx(((6.0 if stop is None else stop) - start) * RATE, abs=2)


def test_without_times_the_whole_file_is_read(wav) -> None:
    assert ref.load_audio(wav).size == 6 * RATE


def test_a_stop_after_the_end_is_the_end(wav) -> None:
    assert ref.load_audio(wav, start=4.0, stop=100.0).size == 2 * RATE


def test_a_part_is_never_longer_than_the_limit(wav, monkeypatch) -> None:
    monkeypatch.setattr(ref, "MAX_SECONDS", 1)
    x = ref.load_audio(wav, start=1.0)
    assert x.size == RATE and peak_hz(x) == pytest.approx(220.0, rel=0.02)


@pytest.mark.parametrize("start,stop", [(3.0, 3.0), (3.0, 2.0), (-1.0, 2.0)])
def test_the_stop_must_be_after_the_start(wav, start, stop) -> None:
    with pytest.raises(ref.AudioError, match="stop time must be after"):
        ref.load_audio(wav, start=start, stop=stop)


def test_a_start_after_the_end_of_the_file_says_so(wav) -> None:
    with pytest.raises(ref.AudioError, match=r"parts\.wav: the file ends before 0:10"):
        ref.load_audio(wav, start=10.0)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_other_formats_are_cut_to_the_part_too(wav, tmp_path) -> None:
    flac = tmp_path / "parts.flac"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), str(flac)], check=True)
    x = ref.load_audio(flac, start=2.0, stop=4.0)
    assert peak_hz(x) == pytest.approx(880.0, rel=0.03) and x.size == pytest.approx(2 * RATE, rel=0.02)
    with pytest.raises(ref.AudioError, match="the file ends before 0:10"):
        ref.load_audio(flac, start=10.0)


# ---- the analysis of a part ----

def test_the_analysis_covers_only_the_part_and_remembers_it(wav) -> None:
    low = ref.analyse(wav, start=0.0, stop=2.0)
    high = ref.analyse(wav, start=4.0, stop=6.0)
    assert low.seconds == pytest.approx(2.0, abs=0.01) and (low.start, low.stop) == (0.0, 2.0)
    assert high.centroid > 5 * low.centroid                   # the 3300 Hz part is far brighter
    assert low.part_text() == "0:00-0:02" and high.part_text() == "0:04-0:06"
    assert "(2 s, 0:04-0:06)" in high.summary()[0]


def test_the_whole_file_has_no_part(wav) -> None:
    whole = ref.analyse(wav)
    assert whole.part_text() == "" and "(6 s)" in whole.summary()[0]
    assert ref.analyse(wav, start=2.0).part_text() == "0:02-end"


def test_a_part_without_sound_is_reported(tmp_path) -> None:
    p = tmp_path / "gap.wav"
    write_wav(p, np.concatenate([sine(440, 2.0), np.zeros(2 * RATE, np.float32)]))
    with pytest.raises(ref.AudioError, match="no sound"):
        ref.analyse(p, start=2.0, stop=4.0)


# ---- the window ----

@pytest.fixture
def win(monkeypatch, wav):
    monkeypatch.setattr(an, "list_input_devices", lambda: [])
    w = an.Analyzer(_Page())
    w.loads = []
    run = w.page.run_thread
    w.page.run_thread = lambda fn, *a: (w.loads.append(a), run(fn, *a))
    w.wav = str(wav)
    return w


def load(win, start="", stop=""):
    win.ref_start.value, win.ref_stop.value = start, stop
    win._start_loading(win.wav, *win._range())
    win._apply_loaded()


def test_the_fields_start_empty_and_mean_the_whole_file(win) -> None:
    assert (win.ref_start.value, win.ref_stop.value) == ("", "")
    assert win._range() == (0.0, None)


@pytest.mark.parametrize("start,stop,want", [("0:30", "", (30.0, None)), ("", "1:00", (0.0, 60.0)),
                                             ("1:00", "1:30", (60.0, 90.0)), ("5", "10.5", (5.0, 10.5))])
def test_the_fields_give_the_range(win, start, stop, want) -> None:
    win.ref_start.value, win.ref_stop.value = start, stop
    assert win._range() == want


@pytest.mark.parametrize("start,stop,message", [("x", "", "is not a time"), ("", "y", "is not a time"),
                                                ("1:00", "0:30", "To time must be after the From"),
                                                ("1:00", "1:00", "To time must be after the From")])
def test_a_wrong_range_is_refused(win, start, stop, message) -> None:
    win.ref_start.value, win.ref_stop.value = start, stop
    with pytest.raises(ValueError, match=message):
        win._range()


def test_a_part_is_loaded_and_shown_in_the_label(win) -> None:
    load(win, "2", "4")
    assert (win.cmp.reference.start, win.cmp.reference.stop) == (2.0, 4.0)
    assert win.ref_label.value == "parts.wav 0:02-0:04" and win._ref_path == win.wav


def test_changing_the_times_analyses_the_part_again(win) -> None:
    load(win)
    assert win.ref_label.value == "parts.wav" and win.cmp.reference.seconds == pytest.approx(6.0, abs=0.01)
    win.loads.clear()
    win.ref_start.value, win.ref_stop.value = "0:02", "0:04"
    win._range_changed()
    assert win.loads == [(win.wav, 2.0, 4.0)]
    win._apply_loaded()
    assert win.cmp.reference.seconds == pytest.approx(2.0, abs=0.01) and win.ref_label.value.endswith("0:02-0:04")


def test_leaving_a_field_without_changing_it_does_not_reload(win) -> None:
    load(win, "2", "4")
    win.loads.clear()
    win._range_changed()                                        # blur or Enter, same times
    assert win.loads == []


def test_the_times_alone_do_nothing_until_there_is_a_reference(win) -> None:
    win.ref_start.value = "0:10"
    win._range_changed()
    assert win.loads == []


def test_a_wrong_time_is_reported_and_the_reference_is_kept(win) -> None:
    load(win, "2", "4")
    win.loads.clear()
    win.ref_stop.value = "zzz"
    win._range_changed()
    assert win.loads == [] and "is not a time" in win.status.value and win.status.color == an.theme.DANGER
    assert win.cmp.reference.stop == 4.0


def test_a_part_the_file_does_not_have_keeps_the_reference_and_says_why(win) -> None:
    load(win, "2", "4")
    win.ref_start.value, win.ref_stop.value = "0:20", ""
    win._range_changed()
    win._apply_loaded()
    assert "the file ends before 0:20" in win.status.value
    assert (win.cmp.reference.start, win.cmp.reference.stop) == (2.0, 4.0)
    assert win.ref_label.value == "parts.wav 0:02-0:04"


def test_choosing_a_file_uses_the_times_in_the_fields(win, monkeypatch) -> None:
    async def pick(page, picker, title, ext, multiple=False):
        from pathlib import Path
        return [Path(win.wav)]
    monkeypatch.setattr(files, "pick_files", pick)
    win.ref_start.value, win.ref_stop.value = "0:04", "0:06"
    asyncio.run(win._pick_reference())
    win._apply_loaded()
    assert win.loads == [(win.wav, 4.0, 6.0)]
    assert win.cmp.reference.part_text() == "0:04-0:06"
    win.ref_start.value = "bad"
    win.loads.clear()
    asyncio.run(win._pick_reference())
    assert win.loads == [] and "is not a time" in win.status.value


def test_removing_the_reference_forgets_the_file(win) -> None:
    load(win, "2", "4")
    win._clear_reference()
    assert win._ref_path is None and win.cmp.reference is None
    win.loads.clear()
    win._range_changed()
    assert win.loads == []
