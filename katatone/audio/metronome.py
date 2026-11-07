"""A metronome: a click track played to a sound card through ``aplay``.

:class:`ClickGenerator` makes the samples (pure numpy, no I/O) and :class:`Metronome`
streams them to ``aplay``.  The click of the first beat of a bar is higher and louder
than the others, and a beat can be split into 2 to 4 softer clicks.  The tempo, the
bar, the subdivision and the volume can change while it runs: a change is heard from
the next click.
"""
from __future__ import annotations

import fcntl
import subprocess
import threading
from typing import Callable

import numpy as np

from .capture import RATE

BPM_MIN, BPM_MAX = 20.0, 300.0
BEATS_MAX, SUBDIVISION_MAX = 12, 4
#: A click is a short sine burst: its length, the fade-in and the decay time constant (s),
#: and the pitch of an accented click and of the others (Hz).
CLICK_S, ATTACK_S, DECAY_S = 0.04, 0.001, 0.007
ACCENT_HZ, BEAT_HZ = 1600.0, 1000.0
#: Loudness of an accented click, of a beat and of a subdivision (before the volume).
ACCENT_GAIN, BEAT_GAIN, SUB_GAIN = 1.0, 0.7, 0.35
#: Frames written to ``aplay`` at a time; with the pipe and ``aplay``'s buffer
#: this is also how late a tempo change or a stop is heard.
BLOCK_FRAMES = 512
BUFFER_US = 40000
#: ``fcntl`` request that sets a pipe's size (Linux): one page keeps the pipe from adding
#: a fifth of a second of delay on top of ``aplay``'s own buffer.
F_SETPIPE_SZ, PIPE_BYTES = getattr(fcntl, "F_SETPIPE_SZ", 1031), 4096


def clamp_bpm(bpm: float) -> float:
    """``bpm`` limited to :data:`BPM_MIN` ... :data:`BPM_MAX`."""
    return float(min(BPM_MAX, max(BPM_MIN, bpm)))


def _click(freq: float, rate: int) -> np.ndarray:
    """One click: a sine burst with a quick attack and an exponential decay."""
    t = np.arange(int(CLICK_S * rate)) / rate
    env = np.minimum(1.0, t / ATTACK_S) * np.exp(-t / DECAY_S)
    return (np.sin(2 * np.pi * freq * t) * env).astype(np.float32)


class ClickGenerator:
    """Produces the click track block by block.

    Attributes:
        bpm: beats per minute.
        beats: beats per bar; the first one is accented (0 or 1: every beat is the same).
        subdivision: clicks per beat (1 to :data:`SUBDIVISION_MAX`); the extra ones are soft.
        volume: 0 to 1.
    """

    def __init__(self, bpm: float = 100.0, beats: int = 4, subdivision: int = 1,
                 volume: float = 0.8, rate: int = RATE) -> None:
        self.bpm = clamp_bpm(bpm)
        self.beats = beats
        self.subdivision = subdivision
        self.volume = volume
        self.rate = rate
        self._clicks = {"accent": _click(ACCENT_HZ, rate), "beat": _click(BEAT_HZ, rate),
                        "sub": _click(BEAT_HZ, rate)}
        self._now = 0                   # frames made so far
        self._next = 0.0                # frame of the next click (fractional: no drift)
        self._index = 0                 # clicks started so far
        self._tail = np.zeros(0, np.float32)    # the rest of a click that crosses a block

    def restart(self) -> None:
        """Begin again: the next click is a first beat, immediately."""
        self._next, self._index = float(self._now), 0
        self._tail = np.zeros(0, np.float32)

    def position(self, index: int) -> tuple[int, int]:
        """``(beat in the bar, subdivision)``, both from 0, of the click number ``index``."""
        sub = max(1, min(SUBDIVISION_MAX, int(self.subdivision)))
        beat, part = divmod(index, sub)
        return beat % max(1, int(self.beats)), part

    def next_block(self, frames: int,
                   on_click: Callable[[int, int], None] | None = None) -> np.ndarray:
        """The next ``frames`` mono float32 samples.

        Args:
            frames: the block length.
            on_click: called as ``on_click(beat, subdivision)`` for every click that
                starts in the block.
        """
        out = np.zeros(frames, np.float32)
        n = min(frames, self._tail.size)
        out[:n] = self._tail[:n]
        self._tail = self._tail[n:]
        end = self._now + frames
        while self._next < end:
            beat, part = self.position(self._index)
            kind = "sub" if part else "accent" if beat == 0 and self.beats > 1 else "beat"
            gain = {"accent": ACCENT_GAIN, "beat": BEAT_GAIN, "sub": SUB_GAIN}[kind]
            click = self._clicks[kind] * (gain * min(1.0, max(0.0, self.volume)))
            start = max(0, int(self._next) - self._now)
            room = frames - start
            out[start:] += click[:room]
            if click.size > room:
                spill = click[room:]
                tail = np.zeros(max(self._tail.size, spill.size), np.float32)
                tail[:self._tail.size] += self._tail
                tail[:spill.size] += spill
                self._tail = tail
            if on_click is not None:
                on_click(beat, part)
            self._index += 1
            sub = max(1, min(SUBDIVISION_MAX, int(self.subdivision)))
            self._next += self.rate * 60.0 / self.bpm / sub
        self._now = end
        return np.clip(out, -1.0, 1.0)


class Metronome:
    """A :class:`ClickGenerator` played through ``aplay`` on a thread.

    Attributes:
        device: the ALSA output device (``aplay -D``).
        channels: how many channels the device is opened with.
        route: which of them (0-based) carry the click; the others are silent.
    """

    def __init__(self, device: str = "default", channels: int = 2,
                 route: tuple[int, ...] | None = None,
                 on_click: Callable[[int, int], None] | None = None,
                 on_error: Callable[[str], None] | None = None,
                 bpm: float = 100.0, beats: int = 4, subdivision: int = 1,
                 volume: float = 0.8) -> None:
        """Prepare a metronome; call :meth:`start` to hear it.

        Args:
            device: the ALSA output device.
            channels: channels to open the device with (at least 1).
            route: channels the click goes to (None: all of them).
            on_click: called on the writer thread as ``(beat, subdivision)`` when a click is
                made (a little before it is heard).
            on_error: called once with a message if ``aplay`` fails.
            bpm, beats, subdivision, volume: see :class:`ClickGenerator`.
        """
        self.device = device
        self.channels = max(1, int(channels))
        self.route = route
        self.gen = ClickGenerator(bpm, beats, subdivision, volume)
        self._on_click = on_click
        self._on_error = on_error
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        """Whether the click is being played."""
        return self._proc is not None

    @property
    def bpm(self) -> float:
        """The tempo."""
        return self.gen.bpm

    def set_bpm(self, bpm: float) -> float:
        """Change the tempo (limited to the supported range); returns the tempo set."""
        self.gen.bpm = clamp_bpm(bpm)
        return self.gen.bpm

    def start(self) -> None:
        """Start ``aplay`` and the writer thread; does nothing if already running.

        Raises:
            OSError: if ``aplay`` cannot be started.
        """
        with self._lock:
            if self._proc is not None:
                return
            cmd = ["aplay", "-q", "-D", self.device, "-f", "FLOAT_LE", "-c", str(self.channels),
                   "-r", str(RATE), "-t", "raw", f"--buffer-time={BUFFER_US}"]
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                fcntl.fcntl(proc.stdin.fileno(), F_SETPIPE_SZ, PIPE_BYTES)
            except OSError:
                pass                    # a larger pipe only means a later stop
            self.gen.restart()
            self._stopping = False
            self._proc = proc
            self._thread = threading.Thread(target=self._run, args=(proc,), daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Stop playing (idempotent)."""
        with self._lock:
            self._stopping = True
            proc, self._proc = self._proc, None
            thread, self._thread = self._thread, None
        if proc is None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        for f in (proc.stdin, proc.stderr):
            try:
                f.close()
            except (OSError, ValueError):
                pass

    def toggle(self) -> bool:
        """Start or stop; returns whether it is running now."""
        if self.running:
            self.stop()
        else:
            self.start()
        return self.running

    def _frames(self, mono: np.ndarray) -> bytes:
        """Spread a mono block over the device's channels (the routed ones carry it)."""
        out = np.zeros((mono.size, self.channels), np.float32)
        route = range(self.channels) if self.route is None else \
            [c for c in self.route if 0 <= c < self.channels]
        for c in route:
            out[:, c] = mono
        return out.tobytes()

    def _run(self, proc: subprocess.Popen) -> None:
        """Write blocks until stopped; ``aplay``'s pipe makes this run in real time."""
        try:
            while not self._stopping:
                block = self.gen.next_block(BLOCK_FRAMES, self._on_click)
                proc.stdin.write(self._frames(block))
                proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass
        if not self._stopping:
            err = ""
            try:
                err = proc.stderr.read().decode(errors="replace").strip()
            except (OSError, ValueError):
                pass
            with self._lock:
                if self._proc is proc:
                    self._proc = None
            if self._on_error:
                self._on_error(err or "the output stopped")
