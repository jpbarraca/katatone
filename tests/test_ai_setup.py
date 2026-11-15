"""Checking the sign-in, installing the agent and signing in, with stand-in npm and claude."""
from __future__ import annotations

import json
import shutil
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from katatone.ai import setup


def _script(path: Path, body: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\nimport sys, os, json\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A data directory in tmp_path, and a PATH-free `which` that knows only what is given."""
    monkeypatch.setattr(setup, "agent_dir", lambda: tmp_path / "data" / "claude-agent")
    known: dict[str, str] = {}
    real = shutil.which
    monkeypatch.setattr(setup.shutil, "which",
                        lambda name, *a, **k: known.get(name) or (real(name) if "/" in name else None))
    return tmp_path, known


def _claude(tmp_path, answer: str, name="claude") -> str:
    return _script(tmp_path / "bin" / name, f"print({answer!r}); sys.exit({0 if answer else 1})")


def _agent_layout(root: Path) -> tuple[str, str]:
    """node_modules/.bin/claude-agent-acp next to a claude of its own."""
    agent = _script(root / "node_modules" / ".bin" / "claude-agent-acp", "pass")
    claude = _script(root / "node_modules" / "@anthropic-ai" / "claude-agent-sdk-linux-x64" / "claude",
                     "print(json.dumps({'loggedIn': True, 'email': 'me@x.org', 'authMethod': 'claude.ai'}))")
    return agent, claude


def test_signed_in(home) -> None:
    tmp, _ = home
    agent, _claude_path = _agent_layout(tmp / "own")
    s = setup.status(agent)
    assert s.ok and s.kind == "ok" and s.text == "Signed in as me@x.org (claude.ai)."


def test_the_claude_next_to_the_agent_is_the_one_asked(home) -> None:
    tmp, known = home
    known["claude"] = _claude(tmp, json.dumps({"loggedIn": False}))          # one on the PATH too
    agent, claude = _agent_layout(tmp / "own")
    assert setup.find_claude(agent) == claude
    assert setup.status(agent).kind == "ok"


def test_signed_out_and_a_claude_from_the_path(home) -> None:
    tmp, known = home
    known["claude"] = _claude(tmp, json.dumps({"loggedIn": False}))
    agent = _script(tmp / "usr" / "bin" / "agent", "pass")
    assert setup.find_claude(agent) == known["claude"]
    s = setup.status(agent)
    assert s.kind == "signed_out" and "Not signed in" in s.text


def test_no_agent(home) -> None:
    assert setup.status("/nonexistent/claude-agent-acp").kind == "no_agent"
    assert setup.status("").kind == "no_agent"
    assert setup.status("agent 'unclosed").kind == "error"


def test_agent_without_a_claude_cannot_be_checked(home) -> None:
    tmp, _ = home
    agent = _script(tmp / "usr" / "bin" / "agent", "pass")
    s = setup.status(agent)
    assert s.kind == "unknown" and "ask a question" in s.text


def test_unreadable_answer_from_claude(home) -> None:
    tmp, known = home
    known["claude"] = _script(tmp / "bin" / "claude", "print('no json'); sys.stderr.write('bad things')")
    s = setup.status(_script(tmp / "usr" / "agent", "pass"))
    assert s.kind == "error" and "bad things" in s.text


def test_npm_missing_or_node_too_old(home) -> None:
    tmp, known = home
    assert "Node.js 22 or newer and npm" in setup.npm_problem()
    known["npm"] = _script(tmp / "bin" / "npm", "pass")
    known["node"] = _script(tmp / "bin" / "node", "print('v20.19.0')")          # the agent needs 22
    assert "22 or newer" in setup.npm_problem() and "this is 20" in setup.npm_problem()
    known["node"] = _script(tmp / "bin" / "node", "print('v22.1.0')")
    assert setup.npm_problem() == ""


FAKE_NPM = """
args = sys.argv[1:]
print('adding packages'); sys.stdout.flush()
open(os.path.join(os.path.dirname(__file__), 'npm-args.json'), 'w').write(json.dumps(args))
if os.environ.get('NPM_FAIL'):
    print('npm ERR! network'); sys.exit(1)
if os.environ.get('NPM_SLOW'):
    import time; time.sleep(60)
prefix = args[args.index('--prefix') + 1]
if not os.environ.get('NPM_NOTHING'):
    b = os.path.join(prefix, 'node_modules', '.bin'); os.makedirs(b, exist_ok=True)
    open(os.path.join(b, 'claude-agent-acp'), 'w').write('#!/bin/sh\\n')
"""


def test_install_into_the_data_directory(home) -> None:
    tmp, _ = home
    npm = _script(tmp / "bin" / "npm", FAKE_NPM)
    lines: list[str] = []
    command = setup.install(lines.append, npm=npm)
    assert command == setup.local_command() == str(
        tmp / "data" / "claude-agent" / "node_modules" / ".bin" / "claude-agent-acp")
    assert Path(command).exists() and lines == ["adding packages"]
    args = json.loads((tmp / "bin" / "npm-args.json").read_text())
    assert args[0] == "install" and "--ignore-scripts" in args
    assert args[args.index("--prefix") + 1] == str(tmp / "data" / "claude-agent")
    assert args[-1] == "@agentclientprotocol/claude-agent-acp"      # a fixed name, nothing typed


def test_a_failed_install_says_why(home, monkeypatch) -> None:
    tmp, _ = home
    npm = _script(tmp / "bin" / "npm", FAKE_NPM)
    monkeypatch.setenv("NPM_FAIL", "1")
    with pytest.raises(setup.SetupError, match=r"exit 1.*network"):
        setup.install(npm=npm)
    monkeypatch.delenv("NPM_FAIL")
    monkeypatch.setenv("NPM_NOTHING", "1")
    with pytest.raises(setup.SetupError, match="not where it should be"):
        setup.install(npm=npm)


def test_an_install_can_be_stopped(home, monkeypatch) -> None:
    tmp, _ = home
    npm = _script(tmp / "bin" / "npm", FAKE_NPM)
    monkeypatch.setenv("NPM_SLOW", "1")
    stop = threading.Event()
    threading.Timer(0.5, stop.set).start()
    started = time.monotonic()
    with pytest.raises(setup.SetupError, match="stopped"):
        setup.install(npm=npm, cancel=stop)
    assert time.monotonic() - started < 10


def test_install_without_npm_explains(home) -> None:
    with pytest.raises(setup.SetupError, match="Node.js 22 or newer and npm"):
        setup.install()


def test_login_passes_on_the_address(home) -> None:
    tmp, _ = home
    claude = _script(tmp / "bin" / "claude", """
assert sys.argv[1:] == ['auth', 'login', '--claudeai'], sys.argv
print('Opening browser to sign in...')
print('If it does not open, visit: https://claude.ai/oauth/authorize?code=abc&state=1 ')
""")
    urls, lines = [], []
    assert setup.login(claude, lines.append, urls.append) is True
    assert urls == ["https://claude.ai/oauth/authorize?code=abc&state=1"] and len(lines) == 2


def test_login_that_fails_or_is_given_up(home) -> None:
    tmp, _ = home
    assert setup.login(_script(tmp / "bin" / "c1", "sys.exit(1)")) is False
    hang = _script(tmp / "bin" / "c2", "import time; time.sleep(60)")
    started = time.monotonic()
    assert setup.login(hang, timeout=0.5) is False
    stop = threading.Event()
    threading.Timer(0.5, stop.set).start()
    assert setup.login(hang, cancel=stop) is False
    assert time.monotonic() - started < 10
    with pytest.raises(setup.SetupError, match="Cannot run"):
        setup.login("/nonexistent/claude")


# ---- inside a Flatpak: a private, empty home directory

@pytest.fixture
def flatpak(home, monkeypatch):
    tmp, known = home
    info = tmp / "flatpak-info"
    info.write_text("[Application]\n")
    monkeypatch.setattr(setup, "FLATPAK_INFO", info)
    monkeypatch.setattr(setup, "data_root", lambda: tmp / "data")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp / "cache"))
    return tmp, known


def test_outside_a_flatpak_nothing_is_moved(home, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert not setup.in_flatpak()
    assert "CLAUDE_CONFIG_DIR" not in setup.child_env()
    assert setup._cache_args() == []


def test_in_a_flatpak_the_sign_in_lives_in_the_data_directory(flatpak) -> None:
    tmp, _ = flatpak
    env = setup.child_env()
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp / "data" / "claude-config")
    assert (tmp / "data" / "claude-config").is_dir()
    assert stat.S_IMODE((tmp / "data" / "claude-config").stat().st_mode) == 0o700


def test_a_config_directory_that_is_set_is_kept(flatpak, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/somewhere/else")
    assert setup.child_env()["CLAUDE_CONFIG_DIR"] == "/somewhere/else"


def test_claude_is_asked_and_signs_in_with_that_directory(flatpak) -> None:
    tmp, known = flatpak
    known["claude"] = _script(tmp / "bin" / "claude", "print(json.dumps({'loggedIn': True, "
                              "'email': os.environ.get('CLAUDE_CONFIG_DIR', 'none')}))")
    agent = _script(tmp / "usr" / "agent", "pass")
    assert str(tmp / "data" / "claude-config") in setup.status(agent).text
    seen = []
    login = _script(tmp / "bin" / "claude2", "print(os.environ.get('CLAUDE_CONFIG_DIR'))")
    assert setup.login(login, seen.append)
    assert seen == [str(tmp / "data" / "claude-config")]


def test_install_in_a_flatpak_keeps_npms_cache_out_of_the_private_home(flatpak) -> None:
    tmp, _ = flatpak
    setup.install(npm=_script(tmp / "bin" / "npm", FAKE_NPM))
    args = json.loads((tmp / "bin" / "npm-args.json").read_text())
    assert args[args.index("--cache") + 1] == str(tmp / "cache" / "katatone-npm")


def test_no_node_in_a_flatpak_does_not_advise_apt(flatpak) -> None:
    problem = setup.npm_problem()
    assert "not part of this Flatpak" in problem and "apt" not in problem


# ---- started from a terminal: a program must not be able to stop KataTone

PTY_HARNESS = str(Path(__file__).with_name("pty_background.py"))
TOUCHES_TERMINAL = """
import termios
if os.isatty(0):                  # what claude does: from a background group this stops the group
    termios.tcsetattr(0, termios.TCSANOW, termios.tcgetattr(0))
print(json.dumps({'loggedIn': True, 'email': 'me@x.org'}))
"""


@pytest.mark.skipif(shutil.which("pkill") is None, reason="needs pkill")
@pytest.mark.parametrize("args, expected", [
    ([], "RESULT Signed in as me@x.org"),
    (["old"], "STOPPED"),           # the harness does catch a program attached to the terminal
])
def test_a_program_touching_the_terminal_does_not_freeze_the_check(tmp_path, args, expected) -> None:
    import subprocess
    claude = _script(tmp_path / "bin" / "claude", TOUCHES_TERMINAL)
    out = subprocess.run([sys.executable, PTY_HARNESS, claude, *args], capture_output=True,
                         text=True, timeout=60).stdout
    assert out.startswith(expected), out


def test_every_program_is_started_detached(home, monkeypatch) -> None:
    import subprocess
    tmp, known = home
    calls = []
    real = subprocess.run
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **k: calls.append(k) or real(*a, **k))
    known["npm"], known["node"] = (_script(tmp / "bin" / "npm", "pass"),
                                   _script(tmp / "bin" / "node", "print('v22.1.0')"))
    known["claude"] = _script(tmp / "bin" / "claude", "print(json.dumps({'loggedIn': False}))")
    setup.npm_problem()
    setup.status(_script(tmp / "usr" / "agent", "pass"))
    assert len(calls) == 2
    assert all(k["stdin"] == subprocess.DEVNULL and k["start_new_session"] for k in calls)
