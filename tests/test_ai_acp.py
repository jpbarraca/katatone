"""The ACP client, against a fake agent run as a real subprocess."""
from __future__ import annotations

import json
import os
import shlex
import sys
import time
from pathlib import Path

import pytest

from katatone.ai import acp, backend, client, store

FAKE = str(Path(__file__).with_name("fake_acp_agent.py"))


def _settings(mode: str = "ok", model: str = "sonnet") -> store.Settings:
    return store.Settings(provider="acp", acp_model=model,
                          acp_command=shlex.join([sys.executable, FAKE, mode]))


def _seen(mode: str = "ok", model: str = "sonnet", **kw) -> dict:
    text = acp.chat(_settings(mode, model), "SYSTEM RULES", "Song: x", **kw)
    return json.loads(text)["seen"]


def test_the_answer_is_the_agents_message_not_its_thoughts_or_tools() -> None:
    text = acp.chat(_settings(), "S", "U", timeout=20)
    assert text.startswith('{"seen"') and "thinking" not in text    # two chunks joined


def test_the_system_prompt_replaces_the_agents_own_and_the_model_is_chosen() -> None:
    seen = _seen()
    assert seen["system"] == "SYSTEM RULES" and seen["prompt"] == "Song: x"
    assert seen["model_set"] == ["model", "sonnet"]


def test_no_tools_and_an_empty_working_directory() -> None:
    seen = _seen()
    assert seen["meta"] == {"claudeCode": {"options": {
        "tools": [], "settingSources": [], "persistSession": False}}}
    assert seen["cwd_exists"] is True


def test_the_working_directory_is_removed_afterwards(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", None)
    _seen()
    assert list(tmp_path.iterdir()) == []


def test_without_system_prompt_support_it_leads_the_prompt() -> None:
    seen = _seen("nosys")
    assert seen["system"] is None and seen["prompt"] == "SYSTEM RULES\n\nSong: x"


def test_model_may_be_given_by_name_or_left_to_the_agent() -> None:
    assert _seen(model="Opus 5.5")["model_set"] == ["model", "opus"]
    assert _seen(model="")["model_set"] is None
    assert _seen(model="default")["model_set"] is None              # already the current one


def test_an_unknown_model_is_reported_with_the_choices() -> None:
    with pytest.raises(client.AiError, match=r"no model 'gpt-9'.*default, sonnet, opus"):
        _seen(model="gpt-9")


def test_an_agent_with_no_model_choice() -> None:
    with pytest.raises(client.AiError, match="does not let the model be chosen"):
        _seen("nomodel")
    assert _seen("nomodel", model="")["model_set"] is None


def test_permissions_are_refused() -> None:
    assert _seen("perm")["permission"] == {"outcome": {"outcome": "selected", "optionId": "no"}}


def test_files_and_terminals_are_not_offered() -> None:
    assert _seen("fs")["fs"] == acp.METHOD_NOT_FOUND


def test_text_that_is_not_protocol_is_skipped() -> None:
    assert _seen("garbage")["system"] == "SYSTEM RULES"


@pytest.mark.parametrize("mode, words", [("maxtokens", "cut off"), ("refusal", "refused"),
                                         ("empty", "empty")])
def test_answers_that_are_no_answer(mode, words) -> None:
    with pytest.raises(client.AiError, match=words):
        _seen(mode)


def test_signing_in_is_explained() -> None:
    with pytest.raises(client.AiError, match="Authentication required.*Sign in"):
        _seen("authfail")


def test_an_agent_that_dies_is_explained_with_what_it_said() -> None:
    with pytest.raises(client.AiError, match="stopped unexpectedly.*boom: cannot start"):
        _seen("crash")


def test_a_missing_program_says_how_to_get_it() -> None:
    s = store.Settings(provider="acp", acp_command="/nonexistent/agent --x")
    with pytest.raises(client.AiError, match="Cannot start.*npm install"):
        acp.chat(s, "s", "u")
    with pytest.raises(client.AiError, match="No agent command"):
        acp.chat(store.Settings(provider="acp", acp_command="  "), "s", "u")
    with pytest.raises(client.AiError, match="not valid"):
        acp.chat(store.Settings(provider="acp", acp_command="agent 'unclosed"), "s", "u")


def test_an_agent_that_hangs_is_stopped() -> None:
    started = time.monotonic()
    with pytest.raises(client.AiError, match="too long"):
        _seen("hang", timeout=1.5)
    assert time.monotonic() - started < 10
    assert not _running(["hang"])           # and nothing of it is left running


def _running(args: list[str]) -> list[str]:
    """The processes that run the fake agent with exactly these arguments."""
    found = []
    for path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            argv = path.read_bytes().decode(errors="replace").split("\0")[:-1]
        except OSError:
            continue
        if len(argv) >= 2 + len(args) and argv[-1 - len(args)] == FAKE and argv[-len(args):] == args:
            found.append(path.parent.name)
    return found


def test_the_backend_picks_by_provider(monkeypatch) -> None:
    monkeypatch.setattr(acp, "chat", lambda s, sys_, usr: "from acp")
    monkeypatch.setattr(client, "chat", lambda s, sys_, usr, **kw: "from openai")
    assert backend.chat(store.Settings(provider="acp"), "s", "u") == "from acp"
    assert backend.chat(store.Settings(), "s", "u") == "from openai"
    assert backend.model_label(store.Settings(provider="acp")) == "sonnet (ACP)"
    assert backend.model_label(store.Settings(provider="acp", acp_model="")) == "the agent (ACP)"
    assert backend.model_label(store.Settings()) == "gpt-4o"


# ---- every question stands alone

def test_each_question_is_a_new_process_session_and_directory() -> None:
    first, second = _seen(), _seen()
    assert first["prompt_count"] == second["prompt_count"] == 1      # a session never saw an earlier question
    assert first["cwd"] != second["cwd"]                              # nor shares a directory
    assert not Path(first["cwd"]).exists() and not Path(second["cwd"]).exists()
    # what is sent is the question and nothing else
    text = acp.chat(_settings(), "SYSTEM", "Song: B")
    assert json.loads(text)["seen"]["prompt"] == "Song: B"


def test_the_agent_is_started_without_memory_and_with_no_settings_loaded() -> None:
    seen = _seen()
    assert seen["env"] == "1"                                         # automatic memory off
    options = seen["meta"]["claudeCode"]["options"]
    assert options["settingSources"] == [] and options["persistSession"] is False
    assert options["tools"] == []
    assert acp.ISOLATED == {"tools": [], "settingSources": [], "persistSession": False}
