"""Getting Claude ready for Tone AI: check the sign-in, install the agent, sign in.

The agent is the npm package ``@agentclientprotocol/claude-agent-acp``.  It brings the
``claude`` program it runs on, and that is what knows whether you are signed in
(``claude auth status``) and signs you in (``claude auth login``); both keep the session in
``~/.claude``.  The agent is installed into the data directory, so no administrator rights
are needed, and with ``--ignore-scripts`` so that no package of the tree runs code when it is
installed.

In a Flatpak the home directory is private and empty, so the sign-in is kept in the data
directory (``CLAUDE_CONFIG_DIR``, see :func:`child_env`) and npm's cache in the cache
directory, and Node.js is part of the Flatpak itself.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import rofs
from ..paths import data_root

PACKAGE = "@agentclientprotocol/claude-agent-acp"
#: What the agent needs (its package's ``engines``).
MIN_NODE = 22
#: Exists inside a Flatpak sandbox.
FLATPAK_INFO = Path("/.flatpak-info")
INSTALL_TIMEOUT_S = 900.0
LOGIN_TIMEOUT_S = 300.0
STATUS_TIMEOUT_S = 30.0
URL = re.compile(r"https?://[^\s\"'<>]+")

Lines = Callable[[str], None]


class SetupError(Exception):
    """A step failed; the message is fit to show the user."""


@dataclass(frozen=True)
class Status:
    """Whether Claude can be asked."""
    kind: str       # "ok", "signed_out", "no_agent", "unknown" or "error"
    text: str

    @property
    def ok(self) -> bool:
        return self.kind == "ok"


#: How every program is started: no input, and in a session of its own.  Started from a
#: terminal, KataTone shares it with what it runs; a program that touches the terminal while
#: KataTone is not its foreground job (``claude`` does) is stopped by the kernel, and so is the
#: whole process group with it, KataTone included: the window freezes for good.
DETACHED = dict(stdin=subprocess.DEVNULL, start_new_session=True)


def in_flatpak() -> bool:
    """Whether this runs inside a Flatpak sandbox."""
    return FLATPAK_INFO.exists()


def child_env() -> dict[str, str]:
    """The environment for ``claude``, the agent and npm.

    In a Flatpak the sign-in cannot stay in ``~/.claude`` (the home directory is private and
    empty, and does not hold what the host has), so ``CLAUDE_CONFIG_DIR`` is the data
    directory's ``claude-config``; a ``CLAUDE_CONFIG_DIR`` that is already set is kept.
    """
    env = dict(os.environ)
    if in_flatpak() and "CLAUDE_CONFIG_DIR" not in env:
        config = data_root() / "claude-config"
        try:
            with rofs.writable(config.parent):
                config.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            pass
        env["CLAUDE_CONFIG_DIR"] = str(config)
    return env


def agent_dir() -> Path:
    """Where the agent is installed (not created here)."""
    return data_root() / "claude-agent"


def local_command() -> str:
    """The command that starts the agent installed by :func:`install`."""
    return str(agent_dir() / "node_modules" / ".bin" / "claude-agent-acp")


def find_claude(command: str) -> str | None:
    """The ``claude`` program that belongs to the agent ``command``.

    It is the one installed next to the agent (in the same ``node_modules``), else the one
    of the local install, else the one on the PATH.
    """
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = []
    exe = shutil.which(argv[0]) if argv else None
    roots = [p for p in Path(exe).resolve().parents if p.name == "node_modules"] if exe else []
    roots.append(agent_dir() / "node_modules")
    for root in roots:
        for c in sorted(root.glob("@anthropic-ai/claude-agent-sdk-*/claude")):
            if c.is_file() and os.access(c, os.X_OK):
                return str(c)
    return shutil.which("claude")


def status(command: str, *, claude: str | None = None) -> Status:
    """Whether the agent ``command`` is installed and Claude is signed in.

    Args:
        command: the agent command (``Settings.acp_command``).
        claude: the ``claude`` program to ask (found from ``command`` when not given).
    """
    try:
        argv = shlex.split(command)
    except ValueError:
        return Status("error", "The agent command is not valid.")
    if not argv or shutil.which(argv[0]) is None:
        return Status("no_agent", "The agent is not installed.")
    claude = claude or find_claude(command)
    if claude is None:
        return Status("unknown", "The agent is installed. Whether you are signed in cannot be "
                                 "checked here: ask a question to find out.")
    try:
        p = subprocess.run([claude, "auth", "status"], capture_output=True, text=True,
                           timeout=STATUS_TIMEOUT_S, env=child_env(), **DETACHED)
        info = json.loads(p.stdout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Status("error", f"Cannot check the sign-in: {e}")
    except ValueError:
        return Status("error", "Cannot check the sign-in: " + (p.stderr.strip()[:200] or
                                                              "unexpected answer."))
    if not isinstance(info, dict) or not info.get("loggedIn"):
        return Status("signed_out", "Not signed in to Claude.")
    who = info.get("email") or "Claude"
    how = info.get("subscriptionType") or info.get("authMethod")
    return Status("ok", f"Signed in as {who}" + (f" ({how})." if how else "."))


def npm_problem() -> str:
    """Why the agent cannot be installed here, or "" when it can."""
    npm, node = shutil.which("npm"), shutil.which("node")
    if not npm or not node:
        if in_flatpak():
            return "Node.js is not part of this Flatpak build: install a newer KataTone Flatpak."
        return (f"Installing needs Node.js {MIN_NODE} or newer and npm (for example "
                "`sudo apt install nodejs npm` or `sudo dnf install nodejs npm`; or see "
                "nodejs.org).")
    try:
        found = subprocess.run([node, "--version"], capture_output=True, text=True,
                               timeout=20, **DETACHED).stdout
        major = int(re.match(r"v?(\d+)", found.strip()).group(1))
    except (OSError, subprocess.TimeoutExpired, AttributeError, ValueError):
        return ""                       # cannot tell; let npm try
    return (f"The agent needs Node.js {MIN_NODE} or newer (this is {major}) "
            "and will not run on this one.") if major < MIN_NODE else ""


def _stream(argv: list[str], on_line: Lines | None, timeout: float,
            cancel: threading.Event | None = None) -> int:
    """Run ``argv``, passing its output (both streams) to ``on_line``; returns its exit code.

    The process is stopped when ``timeout`` seconds pass or ``cancel`` is set (exit code -1).
    """
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                env=child_env(), **DETACHED)
    except OSError as e:
        raise SetupError(f"Cannot run {argv[0]!r}: {e}") from None

    def pump() -> None:
        for line in proc.stdout:
            if on_line and line.strip():
                on_line(line.rstrip())

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    end = time.monotonic() + timeout
    stopped = False
    while proc.poll() is None:
        if time.monotonic() > end or (cancel is not None and cancel.is_set()):
            stopped = True
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                except (ProcessLookupError, PermissionError):
                    break
                try:
                    proc.wait(timeout=3)
                    break
                except subprocess.TimeoutExpired:
                    continue
            break
        time.sleep(0.1)
    proc.wait()
    reader.join(timeout=2)
    return -1 if stopped else proc.returncode


def _cache_args() -> list[str]:
    """npm's cache: in a Flatpak the default (``~/.npm``) is in the private home, which is in
    memory and would hold the whole download; use the cache directory instead."""
    if not in_flatpak():
        return []
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "katatone-npm"
    return ["--cache", str(cache)]


def install(on_line: Lines | None = None, *, npm: str | None = None,
            cancel: threading.Event | None = None) -> str:
    """Install the agent into the data directory.

    Args:
        on_line: receives npm's output as it comes.
        npm: the npm program (found on the PATH when not given).
        cancel: set it to stop the install.
    Returns:
        The command that starts the agent (:func:`local_command`).
    Raises:
        SetupError: npm is missing, or the install failed or was stopped.
    """
    if npm is None:
        problem = npm_problem()
        if problem:
            raise SetupError(problem)
        npm = shutil.which("npm")
    dest = agent_dir()
    tail: list[str] = []

    def keep(line: str) -> None:
        tail.append(line)
        del tail[:-6]
        if on_line:
            on_line(line)

    with rofs.writable(dest.parent):
        dest.mkdir(parents=True, exist_ok=True)
        code = _stream([npm, "install", "--prefix", str(dest), "--ignore-scripts",
                        "--no-audit", "--no-fund", *_cache_args(), PACKAGE],
                       keep, INSTALL_TIMEOUT_S, cancel)
    if code == -1:
        raise SetupError("The install was stopped.")
    if code != 0:
        raise SetupError(f"npm failed (exit {code}): " + " | ".join(tail)[-300:])
    if not Path(local_command()).exists():
        raise SetupError("npm finished, but the agent is not where it should be.")
    return local_command()


def login(claude: str, on_line: Lines | None = None, on_url: Lines | None = None, *,
          cancel: threading.Event | None = None, timeout: float = LOGIN_TIMEOUT_S) -> bool:
    """Sign in to Claude with the account (subscription) flow.

    ``claude`` opens the sign-in page in the browser of this machine and waits for it; the
    address is also passed to ``on_url`` for when no browser opens.

    Args:
        claude: the ``claude`` program (see :func:`find_claude`).
        on_line: receives the program's output.
        on_url: receives the sign-in address.
        cancel: set it to give up.
        timeout: seconds to wait for the sign-in.
    Returns:
        Whether the program finished successfully.
    """
    def seen(line: str) -> None:
        if on_line:
            on_line(line)
        if on_url and (m := URL.search(line)):
            on_url(m.group(0))

    return _stream([claude, "auth", "login", "--claudeai"], seen, timeout, cancel) == 0
