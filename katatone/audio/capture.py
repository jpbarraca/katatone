"""Capture one channel of a sound card by reading ``arecord``'s stdout."""
from __future__ import annotations

import subprocess
import threading
from typing import Callable

import numpy as np

RATE = 48000
BLOCK_FRAMES = 1024


class CaptureError(RuntimeError):
    """The capture process could not start or stopped with an error."""


class Capture:
    """Reads float samples of one channel and hands them to a callback.

    ``arecord`` is asked for as many channels as the selected channel's
    number, and only that last channel is kept, so any device that has the
    channel works (the ALSA ``plug`` layer converts where needed).
    """

    def __init__(self, device: str, channel: int,
                 on_block: Callable[[np.ndarray], None],
                 on_error: Callable[[str], None] | None = None) -> None:
        """Prepare a capture; call :meth:`start` to begin.

        Args:
            device: the ALSA device name (``arecord -D``).
            channel: 1-based channel to listen to.
            on_block: called on the reader thread with each block of
                mono float32 samples (full scale = +/-1.0).
            on_error: called once with a message if capture fails.
        """
        self.device = device
        self.channel = max(1, int(channel))
        self._on_block = on_block
        self._on_error = on_error
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False

    def start(self) -> None:
        """Start ``arecord`` and the reader thread.

        Raises:
            CaptureError if ``arecord`` cannot be started.
        """
        cmd = ["arecord", "-q", "-D", self.device, "-f", "FLOAT_LE",
               "-c", str(self.channel), "-r", str(RATE), "-t", "raw"]
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE)
        except OSError as e:
            raise CaptureError(f"cannot run arecord: {e}") from e
        self._stopping = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        """Read blocks until the process ends or :meth:`stop` is called."""
        proc = self._proc
        size = BLOCK_FRAMES * self.channel * 4
        while not self._stopping:
            data = proc.stdout.read(size)
            if len(data) < size:
                break
            samples = np.frombuffer(data, dtype="<f4").reshape(-1, self.channel)
            self._on_block(samples[:, -1].astype(np.float32))
        if not self._stopping and self._on_error:
            err = proc.stderr.read().decode(errors="replace").strip()
            self._on_error(err or "the capture stopped")

    def stop(self) -> None:
        """Stop capturing (idempotent)."""
        self._stopping = True
        proc, self._proc = self._proc, None
        if proc is None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        for f in (proc.stdout, proc.stderr):
            try:
                f.close()
            except OSError:
                pass
