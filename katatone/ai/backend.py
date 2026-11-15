"""Which way a question is asked: the OpenAI-style server or an ACP agent."""
from __future__ import annotations

from . import acp, client
from .store import Settings


def chat(settings: Settings, system: str, user: str) -> str:
    """Ask the configured backend and return its reply text.

    Raises:
        client.AiError: it could not give an answer.
    """
    if settings.provider == "acp":
        return acp.chat(settings, system, user)
    return client.chat(settings, system, user)


def describe(settings: Settings) -> str:
    """Where questions go, for the user ("https://api.openai.com/v1")."""
    if settings.provider == "acp":
        return f"{settings.acp_command} (ACP)"
    return settings.base_url


def model_label(settings: Settings) -> str:
    """The model asked, for the status line."""
    if settings.provider == "acp":
        return f"{settings.acp_model or 'the agent'} (ACP)"
    return settings.model
