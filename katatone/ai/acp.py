"""Ask an agent that speaks the Agent Client Protocol (ACP), such as Claude.

ACP is JSON-RPC 2.0, one message per line, over the agent's standard input and output.  Each
question starts the agent, opens a session in an empty temporary directory, asks for the
model, sends the prompt, collects the text the agent streams back and stops the agent.

The agent is only asked to answer: it gets no file or terminal access from this client, is
told to use no tools and every permission it asks for is refused.

Every question stands alone.  Besides starting a process and a session for each one, the Claude
agent is told (``_meta.claudeCode.options``, which other agents ignore) to use no tools, to
load none of the user's, project's or local settings (so no ``CLAUDE.md``, memory, skills or
hooks of the user reach the model) and not to keep the session on disk (it would otherwise
leave a transcript, and a project, in ``~/.claude`` for each question), and it is started with
its automatic memory off (that makes a project folder per question too).
"""
from __future__ import annotations

import json
import os
import queue
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections import deque
from typing import Any

from .client import AiError
from .setup import child_env
from .store import Settings

#: The Claude agent's options (see the module's doc): nothing in, nothing kept.
ISOLATED = {"tools": [], "settingSources": [], "persistSession": False}
#: The environment the agent is started with, on top of the usual one.
ISOLATED_ENV = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
PROTOCOL_VERSION = 1
TIMEOUT_S = 180.0
#: How long the agent gets to leave when its work is done, before it is killed.
GRACE_S = 3.0
METHOD_NOT_FOUND = -32601
STOP_REASONS = {"max_tokens": "The agent's answer was cut off.",
                "refusal": "The agent refused to answer.",
                "cancelled": "The agent's answer was cancelled."}
_EOF = object()


class _Agent:
    """A running agent and the JSON-RPC conversation with it."""

    def __init__(self, argv: list[str], cwd: str, deadline: float) -> None:
        self.deadline = deadline
        self.text: list[str] = []           # the agent's message so far
        self.collect = False                # whether session/update text is the answer
        self._id = 0
        self._lines: queue.Queue = queue.Queue()
        self._err: deque = deque(maxlen=12)
        try:
            self.proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", cwd=cwd, start_new_session=True,
                env={**child_env(), **ISOLATED_ENV})
        except FileNotFoundError:
            raise AiError(f"Cannot start {argv[0]!r}: is it installed? For Claude: "
                          "npm install -g @agentclientprotocol/claude-agent-acp") from None
        except OSError as e:
            raise AiError(f"Cannot start {argv[0]!r}: {e}") from None
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=lambda: [self._err.append(l.rstrip()) for l in self.proc.stderr],
                         daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put(_EOF)

    def _send(self, msg: dict) -> None:
        try:
            self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", **msg}) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            raise AiError(self._gone()) from None

    def _gone(self) -> str:
        tail = " | ".join(l for l in self._err if l)[-300:]
        return "The agent stopped unexpectedly" + (f": {tail}" if tail else ".")

    def request(self, method: str, params: dict) -> Any:
        """Send a request and return its result, handling what the agent sends meanwhile.

        Raises:
            AiError: the agent answered with an error, left, or took too long.
        """
        self._id += 1
        mine = self._id
        self._send({"id": mine, "method": method, "params": params})
        while True:
            left = self.deadline - time.monotonic()
            if left <= 0:
                raise AiError("The agent took too long to answer.")
            try:
                line = self._lines.get(timeout=min(left, 1.0))
            except queue.Empty:
                continue
            if line is _EOF:
                raise AiError(self._gone())
            try:
                msg = json.loads(line)
            except ValueError:
                continue                    # not protocol: some agents print a banner
            if not isinstance(msg, dict):
                continue
            if "method" in msg and "id" in msg:
                self._answer(msg)
            elif "method" in msg:
                self._notified(msg)
            elif msg.get("id") == mine:
                if "error" in msg:
                    raise AiError(_error_text(msg["error"]))
                return msg.get("result") or {}

    def notify(self, method: str, params: dict) -> None:
        self._send({"method": method, "params": params})

    def _notified(self, msg: dict) -> None:
        """A notification: the text of the agent's message is the answer; the rest is not."""
        if msg["method"] != "session/update" or not self.collect:
            return
        update = (msg.get("params") or {}).get("update") or {}
        content = update.get("content") or {}
        if update.get("sessionUpdate") == "agent_message_chunk" and content.get("type") == "text":
            self.text.append(str(content.get("text") or ""))

    def _answer(self, msg: dict) -> None:
        """A request from the agent: permissions are refused, everything else is not offered."""
        if msg["method"] == "session/request_permission":
            options = (msg.get("params") or {}).get("options") or []
            reject = next((o for o in options if str(o.get("kind", "")).startswith("reject")), None)
            outcome = ({"outcome": "selected", "optionId": reject.get("optionId")} if reject
                       else {"outcome": "cancelled"})
            self._send({"id": msg["id"], "result": {"outcome": outcome}})
        else:
            self._send({"id": msg["id"], "error": {"code": METHOD_NOT_FOUND,
                                                   "message": "Method not found"}})

    def close(self) -> None:
        """Stop the agent and everything it started: it is given a moment to leave when its
        input closes, then its whole process group is terminated, then killed."""
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                break
            if sig == signal.SIGTERM:
                try:
                    self.proc.wait(timeout=GRACE_S)
                except subprocess.TimeoutExpired:
                    pass
        self.proc.wait()


def _error_text(err: Any) -> str:
    """A JSON-RPC error as a sentence."""
    msg = str(err.get("message") if isinstance(err, dict) else err)
    code = err.get("code") if isinstance(err, dict) else None
    data = err.get("data") if isinstance(err, dict) else None
    detail = f" ({data})" if isinstance(data, str) and data else ""
    hint = (" Sign in with the agent first (for Claude, run `claude` and use /login)."
            if code == -32000 or "auth" in msg.lower() else "")
    return f"The agent refused: {msg}{detail}.{hint}"


def _options(option: dict) -> list[dict]:
    """The choices of a select option, flattening groups."""
    out = []
    for o in option.get("options") or []:
        out.extend(o["options"] if isinstance(o.get("options"), list) else [o])
    return out


def _model_value(option: dict, wanted: str) -> str:
    """The value of the choice that is ``wanted`` (by value or by name, any case)."""
    key = wanted.strip().lower()
    choices = _options(option)
    for field in ("value", "name"):
        for c in choices:
            if str(c.get(field, "")).lower() == key:
                return c["value"]
    names = ", ".join(str(c.get("value")) for c in choices[:12])
    raise AiError(f"The agent has no model {wanted!r}. It offers: {names}.")


def _choose_model(agent: _Agent, session: dict, wanted: str) -> None:
    """Ask the agent to use model ``wanted``."""
    option = next((o for o in session.get("configOptions") or []
                   if o.get("category") == "model" or o.get("id") == "model"), None)
    if option is None:
        raise AiError("This agent does not let the model be chosen: empty the model field "
                      "in the settings to use the agent's own.")
    value = _model_value(option, wanted)
    if value != option.get("currentValue"):
        agent.request("session/set_config_option", {
            "sessionId": session["sessionId"], "configId": option.get("id", "model"),
            "value": value})


def chat(settings: Settings, system: str, user: str, *, timeout: float = TIMEOUT_S) -> str:
    """Ask the agent and return its answer.

    Args:
        settings: ``acp_command`` (the program and its arguments) and ``acp_model``.
        system: the instructions; the agent's own system prompt is replaced by it when the
            agent allows that, else it leads the prompt.
        user: the question.
        timeout: seconds for the whole exchange.
    Raises:
        AiError: the agent could not be started, refused, or gave no answer.
    """
    try:
        argv = shlex.split(settings.acp_command)
    except ValueError as e:
        raise AiError(f"The agent command is not valid: {e}") from None
    if not argv:
        raise AiError("No agent command is set.")
    work = tempfile.mkdtemp(prefix="katatone-ai-")
    agent = None
    session_id = None
    try:
        agent = _Agent(argv, work, time.monotonic() + timeout)
        init = agent.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "clientCapabilities": {},
            "clientInfo": {"name": "katatone", "title": "KataTone", "version": "1"}})
        caps = (init.get("agentCapabilities") or {}).get("sessionCapabilities") or {}
        own_prompt = "systemPrompt" in caps
        params: dict[str, Any] = {"cwd": work, "mcpServers": [],
                                  "_meta": {"claudeCode": {"options": dict(ISOLATED)}}}
        if own_prompt:
            params["systemPrompt"] = {"mode": "override",
                                      "content": [{"type": "text", "text": system}]}
        session = agent.request("session/new", params)
        session_id = session["sessionId"]
        if settings.acp_model.strip():
            _choose_model(agent, session, settings.acp_model)
        agent.collect = True
        result = agent.request("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": user if own_prompt else f"{system}\n\n{user}"}]})
        reason = result.get("stopReason", "end_turn")
        if reason in STOP_REASONS:
            raise AiError(STOP_REASONS[reason])
        text = "".join(agent.text)
        if not text.strip():
            raise AiError("The agent's answer is empty.")
        return text
    except KeyError:
        raise AiError("The agent's answer is not what ACP describes.") from None
    except AiError:
        if agent is not None and session_id is not None:
            try:
                agent.notify("session/cancel", {"sessionId": session_id})
            except AiError:
                pass
        raise
    finally:
        if agent is not None:
            agent.close()
        shutil.rmtree(work, ignore_errors=True)
