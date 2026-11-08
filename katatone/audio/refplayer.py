"""Play a stretch of a reference recording to a sound card, block by block.

The blocks go to ``aplay`` through a small pipe, so the writer thread runs in real time, and
each block is also handed to a callback just before it is written: the Analyzer feeds it to the
display as if it came from the sound card input, so the display follows what is heard (a few
tens of milliseconds ahead of it).
"""
from __future__ import annotations

import fcntl
import subprocess
import threading
from typing import Callable

import numpy as np

from .capture import BLOCK_FRAMES, RATE

BUFFER_US = 40000
#: ``fcntl`` request that sets a pipe's size (Linux): one page keeps the pipe from adding delay.
F_SETPIPE_SZ, PIPE_BYTES = getattr(fcntl, "F_SETPIPE_SZ", 1031), 4096


class ReferencePlayer:
    """Plays mono float32 samples through ``aplay`` on a thread.

    Attributes:
        device: the ALSA output device (``aplay -D``).
        seconds: the length of the samples.
    """

    def __init__(self, samples: np.ndarray, device: str = "default", start: float = 0.0,
                 on_block: Callable[[np.ndarray], None] | None = None,
                 on_end: Callable[[], None] | None = None,
                 on_error: Callable[[str], None] | None = None) -> None:
        """Prepare a player; call :meth:`start` to hear it.

        Args:
            samples: mono float32 samples at :data:`RATE`.
            device: the ALSA output device.
            start: where in the file the samples begin, in seconds (for :attr:`position`).
            on_block: called on the writer thread with each block before it is written.
            on_end: called once on the writer thread when the samples have all been played.
            on_error: called once with a message if ``aplay`` fails.
        """
        self.samples = np.asarray(samples, np.float32)
        self.device = device
        self.offset = float(start)
        self._on_block, self._on_end, self._on_error = on_block, on_end, on_error
        self.seconds = len(self.samples) / RATE
        self._begin = 0                 # frame the next start() begins at
        self._written = 0               # frames written since then
        self.paused = False
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        """Whether the samples are being played."""
        return self._proc is not None

    @property
    def position(self) -> float:
        """Where the playing has got to, in seconds of the file."""
        return self.offset + (self._begin + self._written) / RATE

    def _frame(self, seconds: float) -> int:
        """The sample index of a time in the file, kept inside the samples."""
        return max(0, min(len(self.samples), int(round((seconds - self.offset) * RATE))))

    def start(self, at: float | None = None) -> None:
        """Start ``aplay`` and the writer thread; does nothing if already running.

        Args:
            at: where in the file to begin, in seconds (None: where it was paused or stopped).
        Raises:
            OSError: if ``aplay`` cannot be started.
        """
        with self._lock:
            if self._proc is not None:
                return
            if at is not None:
                self._begin = self._frame(at)
            elif self._begin >= len(self.samples):
                self._begin = 0         # after the end: from the start again
            cmd = ["aplay", "-q", "-D", self.device, "-f", "FLOAT_LE", "-c", "2", "-r", str(RATE),
                   "-t", "raw", f"--buffer-time={BUFFER_US}"]
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                fcntl.fcntl(proc.stdin.fileno(), F_SETPIPE_SZ, PIPE_BYTES)
            except OSError:
                pass                    # a larger pipe only means the display leads a bit more
            self._written, self._stopping, self.paused = 0, False, False
            self._proc = proc
            self._thread = threading.Thread(target=self._run, args=(proc,), daemon=True)
            self._thread.start()

    def pause(self) -> None:
        """Stop the sound but keep the place: :meth:`start` goes on from there."""
        place = self._begin + self._written
        self.stop()
        self._begin, self._written, self.paused = place, 0, True

    def seek(self, seconds: float) -> None:
        """Go to a time of the file; keeps playing if it was playing, else stays where it is."""
        was_running = self.running
        self.stop()
        self._begin, self._written = self._frame(seconds), 0
        if was_running:
            self.start()

    def stop(self) -> None:
        """Stop playing (idempotent); :meth:`start` would go on from the place reached."""
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

    def _run(self, proc: subprocess.Popen) -> None:
        """Write the samples block by block, then say that they are over."""
        finished = False
        try:
            begin = self._begin
            for i in range(begin, len(self.samples), BLOCK_FRAMES):
                if self._stopping:
                    break
                block = self.samples[i:i + BLOCK_FRAMES]
                if self._on_block is not None:
                    self._on_block(block)
                proc.stdin.write(np.repeat(block[:, None], 2, axis=1).tobytes())
                proc.stdin.flush()
                self._written = i + len(block) - begin
            else:
                proc.stdin.close()      # aplay plays out what it holds and exits
                proc.wait(timeout=5)
                finished = True
        except (BrokenPipeError, OSError, ValueError, subprocess.TimeoutExpired):
            pass
        if self._stopping:
            return
        err = ""
        if not finished:
            try:
                err = proc.stderr.read().decode(errors="replace").strip()
            except (OSError, ValueError):
                pass
        with self._lock:
            if self._proc is proc:
                self._proc = None
                if finished:
                    self._begin, self._written = 0, 0       # played out: the next start is from the top
        if not finished and self._on_error:
            self._on_error(err or "the output stopped")
        elif finished and self._on_end:
            self._on_end()
