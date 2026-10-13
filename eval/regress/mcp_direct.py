"""Minimal streamable-HTTP MCP client (stdlib only) for eval references; reads SELERIC_MCP_URL/TOKEN from .env."""
import json
import os
import urllib.request

ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env")


def _env_lines():
    with open(ENV) as f:
        return f.read().splitlines()


def _env(name):
    for line in _env_lines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def _post(url, token, body, session=None):
    h = {"content-type": "application/json", "accept": "application/json, text/event-stream",
         "Authorization": f"Bearer {token}"}
    if session:
        h["mcp-session-id"] = session
    r = urllib.request.Request(url, data=json.dumps(body).encode(), headers=h, method="POST")
    with urllib.request.urlopen(r, timeout=60) as f:
        raw = f.read().decode()
        sid = f.headers.get("mcp-session-id")
    payload = raw
    if raw.lstrip().startswith("event:") or "\ndata:" in raw or raw.startswith("data:"):
        payload = "".join(line[5:].strip() for line in raw.splitlines() if line.startswith("data:"))
    return (json.loads(payload) if payload.strip() else {}), sid


def call_tool(name, args):
    url, token = _env("SELERIC_MCP_URL"), _env("SELERIC_MCP_TOKEN")
    _, sid = _post(url, token, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "regress", "version": "1"}}})
    _post(url, token, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    out, _ = _post(url, token, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                "params": {"name": name, "arguments": args}}, sid)
    res = out.get("result") or {}
    if res.get("structuredContent"):
        return res["structuredContent"]
    text = "".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")
    return json.loads(text)
