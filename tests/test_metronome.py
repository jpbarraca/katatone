"""The metronome's click generator and its aplay driver (no sound card needed)."""
from __future__ import annotations

import threading

import numpy as np

from katatone.audio import metronome as mt

RATE = mt.RATE


def render(gen: mt.ClickGenerator, seconds: float, block: int = mt.BLOCK_FRAMES):
    """The samples and the (frame, beat, subdivision) of every click for ``seconds``."""
    out, clicks, made = [], [], 0
    total = int(seconds * RATE)
    while made < total:
        n = min(block, total - made)
        base = made
        out.append(gen.next_block(n, lambda b, p, base=base: clicks.append((base, b, p))))
        made += n
    return np.concatenate(out), clicks


def onsets(x: np.ndarray, floor: float = 0.05) -> list[int]:
    """Frames where a click starts: the first loud sample after a quiet stretch."""
    loud = np.abs(x) > floor
    return [i for i in range(len(x)) if loud[i] and not loud[max(0, i - 400):i].any()]


def test_clicks_come_every_beat_without_drift() -> None:
    gen = mt.ClickGenerator(bpm=100, beats=4, volume=1.0)
    x, clicks = render(gen, 10.0)
    beat = RATE * 60 / 100
    frames = onsets(x)
    assert len(frames) == len(clicks) == 17
    # an onset is heard a few samples after the click starts (the attack), never before
    for k, f in enumerate(frames):
        assert 0 <= f - round(k * beat) < 80


def test_blocks_of_any_size_give_the_same_samples() -> None:
    a, _ = render(mt.ClickGenerator(bpm=173, beats=3, subdivision=3), 3.0, block=512)
    b, _ = render(mt.ClickGenerator(bpm=173, beats=3, subdivision=3), 3.0, block=777)
    assert np.array_equal(a, b)


def test_first_beat_is_accented_and_beats_cycle_through_the_bar() -> None:
    gen = mt.ClickGenerator(bpm=120, beats=3, volume=1.0)
    x, clicks = render(gen, 3.2)
    assert [(b, p) for _, b, p in clicks] == [(0, 0), (1, 0), (2, 0), (0, 0), (1, 0), (2, 0), (0, 0)]
    frames = onsets(x)
    peaks = [float(np.abs(x[f:f + 400]).max()) for f in frames]
    assert peaks[0] > peaks[1] and peaks[0] == peaks[3] and peaks[1] == peaks[2]


def test_one_beat_bar_has_no_accent() -> None:
    x, _ = render(mt.ClickGenerator(bpm=120, beats=1, volume=1.0), 2.2)
    peaks = {round(float(np.abs(x[f:f + 400]).max()), 4) for f in onsets(x)}
    assert len(peaks) == 1


def test_subdivisions_add_softer_clicks() -> None:
    gen = mt.ClickGenerator(bpm=60, beats=2, subdivision=2, volume=1.0)
    x, clicks = render(gen, 2.1)
    assert [(b, p) for _, b, p in clicks] == [(0, 0), (0, 1), (1, 0), (1, 1), (0, 0)]
    frames = onsets(x)
    assert abs(frames[1] - RATE // 2) < 80
    assert np.abs(x[frames[1]:frames[1] + 400]).max() < np.abs(x[frames[2]:frames[2] + 400]).max()


def test_a_new_tempo_is_heard_from_the_next_click() -> None:
    gen = mt.ClickGenerator(bpm=60, beats=4, volume=1.0)
    first, _ = render(gen, 0.5)
    gen.bpm = 120
    rest, _ = render(gen, 2.0)
    frames = onsets(np.concatenate([first, rest]))
    # the click already scheduled keeps its time (1 s), the next ones are half a second apart
    assert abs(frames[1] - RATE) < 80 and abs(frames[2] - frames[1] - RATE // 2) < 80


def test_volume_and_limits() -> None:
    x, _ = render(mt.ClickGenerator(volume=0.0), 1.0)
    assert not x.any()
    assert mt.clamp_bpm(5) == mt.BPM_MIN and mt.clamp_bpm(999) == mt.BPM_MAX and mt.clamp_bpm(90) == 90
    loud, _ = render(mt.ClickGenerator(volume=5.0), 1.0)
    assert np.abs(loud).max() <= 1.0


def test_restart_makes_the_next_click_a_first_beat_at_once() -> None:
    gen = mt.ClickGenerator(bpm=60, beats=4)
    render(gen, 1.5)
    gen.restart()
    _, clicks = render(gen, 0.1)
    assert [(b, p) for _, b, p in clicks] == [(0, 0)] and clicks[0][0] == 0


# ----------------------------------------------------------------- the aplay driver

class FakePipe:
    def __init__(self, die_after: int | None = None) -> None:
        self.chunks: list[bytes] = []
        self.die_after = die_after
        self.written = threading.Event()

    def write(self, data: bytes) -> None:
        if self.die_after is not None and len(self.chunks) >= self.die_after:
            raise BrokenPipeError
        self.chunks.append(data)
        self.written.set()
        threading.Event().wait(0.001)          # a real pipe paces the writer

    def flush(self) -> None: ...
    def close(self) -> None: ...
    def fileno(self) -> int:
        raise OSError("not a real pipe")
    def read(self) -> bytes:
        return b"no such device"


class FakeProc:
    def __init__(self, pipe: FakePipe) -> None:
        self.stdin, self.stderr = pipe, FakePipe()
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None) -> int:
        return 0

    def kill(self) -> None: ...


def fake_popen(monkeypatch, pipe: FakePipe) -> list[list[str]]:
    cmds: list[list[str]] = []

    def popen(cmd, **kw):
        cmds.append(cmd)
        return FakeProc(pipe)

    monkeypatch.setattr(mt.subprocess, "Popen", popen)
    return cmds


def test_start_streams_the_click_to_the_chosen_device_and_stop_ends_it(monkeypatch) -> None:
    pipe = FakePipe()
    cmds = fake_popen(monkeypatch, pipe)
    m = mt.Metronome(device="hw:KATANA3", channels=2)
    m.start()
    assert pipe.written.wait(2) and m.running
    assert cmds[0][:5] == ["aplay", "-q", "-D", "hw:KATANA3", "-f"] and "-c" in cmds[0]
    m.stop()
    assert not m.running
    frame = np.frombuffer(pipe.chunks[0], np.float32).reshape(-1, 2)
    assert frame.shape == (mt.BLOCK_FRAMES, 2) and np.array_equal(frame[:, 0], frame[:, 1])


def test_route_puts_the_click_on_one_channel_only(monkeypatch) -> None:
    pipe = FakePipe()
    fake_popen(monkeypatch, pipe)
    m = mt.Metronome(channels=4, route=(2,), volume=1.0)
    m.start()
    assert pipe.written.wait(2)
    m.stop()
    frames = np.frombuffer(pipe.chunks[0], np.float32).reshape(-1, 4)
    assert np.abs(frames[:, 2]).max() > 0.5 and not frames[:, [0, 1, 3]].any()


def test_toggle_and_double_start(monkeypatch) -> None:
    fake_popen(monkeypatch, FakePipe())
    m = mt.Metronome()
    assert m.toggle() is True
    m.start()                                    # already running: no second process
    assert m.toggle() is False


def test_a_dying_aplay_is_reported_once(monkeypatch) -> None:
    fake_popen(monkeypatch, FakePipe(die_after=2))
    errors: list[str] = []
    done = threading.Event()
    m = mt.Metronome(on_error=lambda msg: (errors.append(msg), done.set()))
    m.start()
    assert done.wait(2)
    assert errors == ["no such device"] and not m.running
