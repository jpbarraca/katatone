"""A chat-completions request to an OpenAI-compatible server (standard library only)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable

from .store import Settings

TIMEOUT_S = 120.0


class AiError(Exception):
    """The server could not give an answer; the message is fit to show the user."""


def _detail(body: bytes) -> str:
    """The server's own error message from an error response body."""
    try:
        err = json.loads(body).get("error")
        return str(err.get("message") if isinstance(err, dict) else err or "")
    except (ValueError, AttributeError):
        return body[:200].decode("utf-8", "replace")


def _post(settings: Settings, payload: dict, timeout: float, opener: Callable) -> dict:
    """POST one request; returns the decoded response or raises AiError."""
    req = urllib.request.Request(
        settings.base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {})})
    try:
        with opener(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise _Http(e.code, _detail(e.read())) from None
    except urllib.error.URLError as e:
        raise AiError(f"Cannot reach {settings.base_url}: {e.reason}") from None
    except TimeoutError:
        raise AiError("The server took too long to answer.") from None
    except ValueError:
        raise AiError("The server's answer is not JSON: is the base URL right?") from None


class _Http(Exception):
    def __init__(self, code: int, detail: str) -> None:
        super().__init__(code, detail)
        self.code, self.detail = code, detail


def _http_message(e: _Http) -> str:
    """What an HTTP error status means for the user."""
    why = {401: "The server rejected the API key.",
           403: "The server refused the request (is the key allowed to use this model?).",
           404: "The server has no such endpoint or model: check the base URL and the model name.",
           429: "The server is rate limiting or out of quota; try again later."}.get(e.code)
    return f"{why or f'The server answered {e.code}.'}" + (f" ({e.detail})" if e.detail else "")


def chat(settings: Settings, system: str, user: str, *, timeout: float = TIMEOUT_S,
         opener: Callable = urllib.request.urlopen) -> str:
    """Ask the model and return its reply text.

    A JSON object reply is requested; a server that does not support that is asked again
    without it.

    Args:
        settings: the server, model and key.
        system: the system message.
        user: the user message.
        timeout: seconds to wait for each request.
        opener: ``urlopen`` (tests replace it).
    Raises:
        AiError: the server was unreachable, refused the request or sent no text.
    """
    payload = {"model": settings.model,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}],
               "response_format": {"type": "json_object"}}
    try:
        try:
            data = _post(settings, payload, timeout, opener)
        except _Http as e:
            if e.code != 400 or "response_format" not in e.detail:
                raise
            del payload["response_format"]
            data = _post(settings, payload, timeout, opener)
    except _Http as e:
        raise AiError(_http_message(e)) from None
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AiError("The server's answer has no text.") from None
    if not isinstance(text, str) or not text.strip():
        raise AiError("The server's answer is empty (it may have been cut off).")
    return text
