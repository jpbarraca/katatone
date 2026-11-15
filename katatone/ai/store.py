"""The Tone AI settings: ``ai.json`` in the data directory (mode 0600).

Two ways to ask: an OpenAI-compatible server (``provider`` ``"openai"``), or an agent that
speaks the Agent Client Protocol, such as Claude (``"acp"``, see :mod:`katatone.ai.acp`).

For the server the API key is kept in the file; the ``OPENAI_API_KEY`` environment variable
is used when none is saved.  A server other than OpenAI's is chosen with the base URL
(anything that speaks the chat-completions API: Ollama, LM Studio, vLLM, ...).  For the
agent, ``acp_command`` is the program to start and ``acp_model`` the model to ask it for.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import rofs
from ..paths import data_root

DEFAULT_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o"
ENV_KEY = "OPENAI_API_KEY"
PROVIDERS = ("openai", "acp")
DEFAULT_ACP_COMMAND = "claude-agent-acp"
DEFAULT_ACP_MODEL = "sonnet"


@dataclass
class Settings:
    base_url: str = DEFAULT_URL
    model: str = DEFAULT_MODEL
    api_key: str = ""
    provider: str = "openai"
    acp_command: str = DEFAULT_ACP_COMMAND
    acp_model: str = DEFAULT_ACP_MODEL      # empty: the agent's own choice

    @property
    def ready(self) -> bool:
        """Whether a request can be made (a key is needed for OpenAI's own server only)."""
        if self.provider == "acp":
            return bool(self.acp_command.strip())
        local = not self.base_url.startswith(DEFAULT_URL)
        return bool(self.base_url and self.model and (self.api_key or local))


def default_path() -> Path:
    """Where the settings are stored (not created here)."""
    return data_root() / "ai.json"


def load(path: str | Path | None = None, env: dict | None = None) -> Settings:
    """Read the settings; a missing or unreadable file gives the defaults.

    Args:
        path: the file (defaults to :func:`default_path`).
        env: the environment to take the key from (defaults to ``os.environ``).
    Returns:
        The settings; an empty key is replaced by ``OPENAI_API_KEY`` when that is set.
    """
    p = Path(path) if path else default_path()
    s = Settings()
    try:
        raw = json.loads(p.read_text())
        if isinstance(raw, dict):
            for name in ("base_url", "model", "api_key", "acp_command"):
                if isinstance(raw.get(name), str) and raw[name].strip():
                    setattr(s, name, raw[name].strip())
            if isinstance(raw.get("acp_model"), str):
                s.acp_model = raw["acp_model"].strip()
            if raw.get("provider") in PROVIDERS:
                s.provider = raw["provider"]
    except (OSError, ValueError):
        pass
    if not s.api_key:
        s.api_key = (os.environ if env is None else env).get(ENV_KEY, "").strip()
    return s


def save(settings: Settings, path: str | Path | None = None) -> None:
    """Write the settings atomically with owner-only permissions.

    Args:
        settings: what to store.
        path: the file (defaults to :func:`default_path`).
    """
    p = Path(path) if path else default_path()
    tmp = p.with_suffix(".json.tmp")
    with rofs.writable(p.parent):
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(asdict(settings), indent=2) + "\n")
        os.replace(tmp, p)
