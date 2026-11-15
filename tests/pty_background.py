"""Runs ``setup.status`` the way a KataTone started from a shell runs it: in a background
process group of a controlling terminal.  ``python pty_background.py CLAUDE [old]``.

Prints ``RESULT <text>`` when the check finishes, or ``STOPPED`` when the kernel stopped the
process (a program that touches the terminal from a background group gets SIGTTOU/SIGTTIN, and
the whole group is stopped).  ``old`` starts the programs the way KataTone once did, attached
to the terminal.
"""
from __future__ import annotations

import fcntl
import os
import pty
import select
import subprocess
import sys
import termios
import time
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
CODE = ("import sys; from katatone.ai import setup\n"
        "if sys.argv[2:] == ['old']: setup.DETACHED = {}\n"
        "print('RESULT', setup.status(sys.executable, claude=sys.argv[1]).text, flush=True)")
WAIT_S = 6

master, slave = pty.openpty()
shell = os.fork()
if shell == 0:                                  # the shell: session leader of the terminal
    os.setsid()
    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
    for fd in (0, 1, 2):
        os.dup2(slave, fd)
    if os.fork() == 0:                          # the app: a process group of its own, so not
        os.setpgid(0, 0)                        # the terminal's foreground group
        os.environ["PYTHONPATH"] = ROOT
        os.execv(sys.executable, [sys.executable, "-c", CODE, *sys.argv[1:]])
    time.sleep(WAIT_S + 2)
    os._exit(0)
os.close(slave)
seen = b""
end = time.monotonic() + WAIT_S
while time.monotonic() < end and b"RESULT" not in seen:
    if select.select([master], [], [], 0.2)[0]:
        try:
            seen += os.read(master, 4096)
        except OSError:
            break
subprocess.run(["pkill", "-KILL", "-s", str(shell)])
text = seen.decode(errors="replace")
print(text[text.index("RESULT"):].strip() if "RESULT" in text else "STOPPED")
