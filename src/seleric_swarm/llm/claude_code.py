"""Claude subscription via the official Claude Code CLI.

The CLI is already signed in on this machine (`claude auth login`). Seleric
asks it to answer and never reads, stores, or refreshes the Claude credential.
API keys in the server environment are not passed through, so the CLI uses its
own sign-in.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models import Model, ModelRequestParameters

from seleric_swarm.agent.model_health import ReplayedStreamedResponse
from seleric_swarm.llm.models_store import ModelsError

# Aliases the Claude Code CLI documents for `--model`.
MODEL_ALIASES = ("sonnet", "opus", "haiku", "fable")
_KEEP_ENV = {
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TERM", "TMPDIR", "TMP", "TEMP",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
}


def binary() -> str:
    found = shutil.which("claude")
    if not found:
        raise ModelsError("Claude Code is not installed on this machine")
    return found


def _env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key in _KEEP_ENV}


def status() -> dict[str, str]:
    """Confirm the CLI is signed in. The account email is not kept."""
    try:
        completed = subprocess.run(
            [binary(), "auth", "status", "--json"],
            capture_output=True, text=True, timeout=20, env=_env(), check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ModelsError("Could not ask Claude Code whether it is signed in") from exc
    try:
        body = json.loads(completed.stdout or "{}")
    except ValueError as exc:
        raise ModelsError("Claude Code did not report its sign-in") from exc
    if completed.returncode != 0 or not body.get("loggedIn"):
        raise ModelsError("Sign in first, in a terminal on this machine: claude auth login")
    plan = str(body.get("subscriptionType") or body.get("planTier") or "plan")
    return {"plan": plan, "method": str(body.get("authMethod") or "")}


def run(model: str, prompt: str, *, system: str = "", timeout_s: float = 120) -> str:
    """One print-mode turn. The working directory is empty so the CLI does not read the repo."""
    command = [
        binary(), "-p", prompt, "--model", model, "--output-format", "json",
        "--no-session-persistence", "--permission-mode", "plan", "--permission-prompts", "none",
    ]
    if system:
        command += ["--append-system-prompt", system]
    with tempfile.TemporaryDirectory(prefix="seleric-claude-") as work:
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout_s, env=_env(), cwd=work, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ModelsError(f"{model} timed out") from exc
        except OSError as exc:
            raise ModelsError("Could not run Claude Code") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise ModelsError(detail[-1][:300] if detail else f"Claude Code exited {completed.returncode}")
    try:
        body: Any = json.loads(completed.stdout)
    except ValueError:
        return completed.stdout.strip()
    if isinstance(body, dict):
        if body.get("is_error"):
            raise ModelsError(str(body.get("result") or "Claude Code failed")[:300])
        result = body.get("result")
        if isinstance(result, str):
            return result.strip()
    return completed.stdout.strip()


def _transcript(messages: list[Any], parameters: ModelRequestParameters) -> tuple[str, str]:
    lines: list[str] = []
    for message in messages:
        for part in getattr(message, "parts", []):
            content = getattr(part, "content", None)
            if isinstance(content, str) and content.strip():
                lines.append(content.strip())
            elif getattr(part, "tool_name", None):
                lines.append(f"Tool call {part.tool_name}: {getattr(part, 'args', '')}")
    tools = [f"- {tool.name}: {tool.description or ''}" for tool in parameters.function_tools]
    system = "You are the model for Seleric. Answer from the conversation. Do not use your own coding tools."
    if tools:
        system += (
            " To call one of these tools, reply with only a JSON object "
            '{"tool":"<name>","arguments":{...}} and no other text.\n' + "\n".join(tools)
        )
    return "\n\n".join(lines), system


def _as_response(model_name: str, text: str, parameters: ModelRequestParameters) -> ModelResponse:
    names = {tool.name for tool in parameters.function_tools}
    raw = text.strip()
    if raw.startswith("{") and names:
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        if isinstance(body, dict) and body.get("tool") in names:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=str(body["tool"]), args=body.get("arguments") or {})],
                model_name=model_name,
            )
    return ModelResponse(parts=[TextPart(content=text)], model_name=model_name)


class ClaudeCodeModel(Model):
    """pydantic-ai model that asks the signed-in Claude Code CLI for one turn."""

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        super().__init__()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def system(self) -> str:
        return "claude-code"

    async def request(
        self, messages: list[Any], model_settings: Any, model_request_parameters: ModelRequestParameters
    ) -> ModelResponse:
        import asyncio

        prompt, system = _transcript(messages, model_request_parameters)
        try:
            text = await asyncio.to_thread(run, self._model_name, prompt, system=system)
        except ModelsError as exc:
            raise ModelAPIError(self._model_name, str(exc)) from exc
        return _as_response(self._model_name, text, model_request_parameters)

    @asynccontextmanager
    async def request_stream(
        self, messages: list[Any], model_settings: Any, model_request_parameters: ModelRequestParameters, run_context: Any = None
    ) -> AsyncIterator[Any]:
        response = await self.request(messages, model_settings, model_request_parameters)
        yield ReplayedStreamedResponse(model_request_parameters=model_request_parameters, response=response)
