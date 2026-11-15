"""The OpenAI server settings file."""
from __future__ import annotations

import json
import stat

from katatone.ai import store


def test_defaults_when_there_is_no_file(tmp_path) -> None:
    s = store.load(tmp_path / "ai.json", env={})
    assert (s.base_url, s.model, s.api_key) == (store.DEFAULT_URL, store.DEFAULT_MODEL, "")
    assert not s.ready                    # OpenAI's own server needs a key


def test_round_trip_and_owner_only(tmp_path) -> None:
    path = tmp_path / "data" / "ai.json"
    store.save(store.Settings("http://localhost:11434/v1", "llama3", "sk-secret"), path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob("*.tmp"))
    s = store.load(path, env={})
    assert (s.base_url, s.model, s.api_key) == ("http://localhost:11434/v1", "llama3", "sk-secret")


def test_environment_key_is_the_fallback_only(tmp_path) -> None:
    path = tmp_path / "ai.json"
    assert store.load(path, env={"OPENAI_API_KEY": " sk-env "}).api_key == "sk-env"
    store.save(store.Settings(api_key="sk-file"), path)
    assert store.load(path, env={"OPENAI_API_KEY": "sk-env"}).api_key == "sk-file"


def test_a_local_server_needs_no_key() -> None:
    assert store.Settings("http://localhost:11434/v1", "llama3", "").ready
    assert not store.Settings("http://localhost:11434/v1", "", "").ready


def test_unreadable_file_gives_the_defaults(tmp_path) -> None:
    path = tmp_path / "ai.json"
    path.write_text("{not json")
    assert store.load(path, env={}).model == store.DEFAULT_MODEL
    path.write_text(json.dumps({"model": 3, "base_url": "  "}))
    assert store.load(path, env={}).model == store.DEFAULT_MODEL


def test_the_agent_settings_round_trip(tmp_path) -> None:
    path = tmp_path / "ai.json"
    d = store.Settings()
    assert (d.provider, d.acp_command, d.acp_model) == ("openai", "claude-agent-acp", "sonnet")
    store.save(store.Settings(provider="acp", acp_command="npx -y @agentclientprotocol/claude-agent-acp",
                              acp_model=""), path)
    s = store.load(path, env={})
    assert s.provider == "acp" and s.acp_model == ""            # empty: the agent's own model
    assert s.acp_command == "npx -y @agentclientprotocol/claude-agent-acp"


def test_the_agent_needs_no_key_but_a_command() -> None:
    assert store.Settings(provider="acp").ready
    assert not store.Settings(provider="acp", acp_command=" ").ready


def test_an_unknown_provider_is_ignored(tmp_path) -> None:
    path = tmp_path / "ai.json"
    path.write_text(json.dumps({"provider": "gemini", "acp_model": 7}))
    s = store.load(path, env={})
    assert s.provider == "openai" and s.acp_model == "sonnet"
