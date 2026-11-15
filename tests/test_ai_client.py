"""The chat-completions client, against a real server on localhost."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from katatone.ai import client, store


class _Server:
    """Answers each request with the next scripted (status, body); records the requests."""

    def __init__(self, *script) -> None:
        self.script, self.requests = list(script), []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, dict(self.headers), body))
                status, payload = outer.script.pop(0)
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.settings = store.Settings(f"http://127.0.0.1:{self.httpd.server_port}/v1", "m", "sk-x")

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _ok(text: str) -> tuple:
    return 200, {"choices": [{"message": {"content": text}}]}


@pytest.fixture
def server():
    made = []

    def make(*script):
        made.append(_Server(*script))
        return made[-1]
    yield make
    for s in made:
        s.close()


def test_reply_text_and_request_shape(server) -> None:
    s = server(_ok('{"a": 1}'))
    assert client.chat(s.settings, "sys", "usr", timeout=5) == '{"a": 1}'
    path, headers, body = s.requests[0]
    assert path == "/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-x"
    assert body["model"] == "m" and body["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


@pytest.mark.parametrize("status, words", [(401, "API key"), (404, "base URL"), (429, "rate limiting"),
                                           (500, "500")])
def test_http_errors_are_explained(server, status, words) -> None:
    s = server((status, {"error": {"message": "nope"}}))
    with pytest.raises(client.AiError) as e:
        client.chat(s.settings, "s", "u", timeout=5)
    assert words in str(e.value) and "nope" in str(e.value)


def test_server_without_json_mode_is_asked_again_without_it(server) -> None:
    s = server((400, {"error": {"message": "unknown parameter: response_format"}}), _ok("{}"))
    assert client.chat(s.settings, "s", "u", timeout=5) == "{}"
    assert "response_format" in s.requests[0][2] and "response_format" not in s.requests[1][2]


def test_other_400_is_not_retried(server) -> None:
    s = server((400, {"error": {"message": "bad model"}}))
    with pytest.raises(client.AiError, match="bad model"):
        client.chat(s.settings, "s", "u", timeout=5)
    assert len(s.requests) == 1


@pytest.mark.parametrize("body", [{"choices": []}, {"choices": [{"message": {"content": "  "}}]},
                                  {"nothing": 1}])
def test_answer_without_text(server, body) -> None:
    s = server((200, body))
    with pytest.raises(client.AiError):
        client.chat(s.settings, "s", "u", timeout=5)


def test_answer_that_is_not_json(server) -> None:
    s = server((200, b"<html>hi</html>"))
    with pytest.raises(client.AiError, match="not JSON"):
        client.chat(s.settings, "s", "u", timeout=5)


def test_unreachable_server() -> None:
    with pytest.raises(client.AiError, match="Cannot reach"):
        client.chat(store.Settings("http://127.0.0.1:1/v1", "m", ""), "s", "u", timeout=2)
