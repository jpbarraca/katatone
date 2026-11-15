"""A stand-in ACP agent for the tests: ``python fake_acp_agent.py MODE``.

It answers with a JSON object describing what the client sent it (``seen``), so the tests
can check the conversation.  ``MODE`` picks a behaviour (see the branches below).
"""
from __future__ import annotations

import json
import os
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
seen: dict = {"cwd_exists": None, "model_set": None, "system": None, "prompt": None, "meta": None}
MODELS = [{"value": "default", "name": "Default"}, {"value": "sonnet", "name": "Sonnet 5.5"},
          {"value": "opus", "name": "Opus 5.5"}]


def send(obj: dict) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **obj}) + "\n")
    sys.stdout.flush()


def read() -> dict:
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    return json.loads(line)


def wait_for(msg_id: int) -> dict:
    while True:
        m = read()
        if m.get("id") == msg_id and "method" not in m:
            return m


def update(kind: str, **content) -> None:
    send({"method": "session/update", "params": {"sessionId": "s1", "update": {
        "sessionUpdate": kind, **content}}})


if mode == "crash":
    sys.stderr.write("boom: cannot start\n")
    sys.exit(3)
if mode == "garbage":
    print("Welcome to the fake agent v1")
    sys.stdout.flush()

while True:
    m = read()
    method, mid, p = m.get("method"), m.get("id"), m.get("params") or {}
    if method == "initialize":
        caps = {"sessionCapabilities": {} if mode == "nosys" else {"systemPrompt": {"mode": {"override": {}}}}}
        send({"id": mid, "result": {"protocolVersion": 1, "agentCapabilities": caps, "authMethods": []}})
    elif method == "session/new":
        if mode == "authfail":
            send({"id": mid, "error": {"code": -32000, "message": "Authentication required"}})
            continue
        seen["cwd_exists"] = os.path.isdir(p["cwd"]) and os.listdir(p["cwd"]) == []
        seen["meta"] = p.get("_meta")
        sp = p.get("systemPrompt")
        seen["system"] = sp["content"][0]["text"] if sp else None
        result = {"sessionId": "s1"}
        if mode != "nomodel":
            result["configOptions"] = [
                {"id": "mode", "category": "mode", "type": "select", "currentValue": "default",
                 "options": [{"value": "default", "name": "Manual"}]},
                {"id": "model", "category": "model", "type": "select", "currentValue": "default",
                 "options": MODELS}]
        send({"id": mid, "result": result})
    elif method == "session/set_config_option":
        seen["model_set"] = [p["configId"], p["value"]]
        send({"id": mid, "result": {"configOptions": []}})
    elif method == "session/prompt":
        seen["prompt"] = p["prompt"][0]["text"]
        if mode == "hang":
            time.sleep(60)
        if mode == "perm":
            send({"id": 900, "method": "session/request_permission", "params": {
                "sessionId": "s1", "toolCall": {"toolCallId": "c1"}, "options": [
                    {"optionId": "yes", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "no", "name": "Reject", "kind": "reject_once"}]}})
            seen["permission"] = wait_for(900)["result"]
        if mode == "fs":
            send({"id": 901, "method": "fs/read_text_file", "params": {"sessionId": "s1", "path": "/etc/passwd"}})
            seen["fs"] = wait_for(901).get("error", {}).get("code")
        update("agent_thought_chunk", content={"type": "text", "text": "thinking..."})
        update("tool_call", toolCallId="c1", title="Read", status="pending")
        body = json.dumps({"seen": seen})
        if mode == "empty":
            body = ""
        update("agent_message_chunk", content={"type": "text", "text": body[:20]})
        update("agent_message_chunk", content={"type": "text", "text": body[20:]})
        stop = {"maxtokens": "max_tokens", "refusal": "refusal"}.get(mode, "end_turn")
        send({"id": mid, "result": {"stopReason": stop}})
    elif method == "session/cancel":
        pass
