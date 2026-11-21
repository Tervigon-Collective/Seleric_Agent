"""Settings → Models API (Rakazo-style model selection).

``GET    /v1/model-settings``                          providers, connections (keys masked), Main / Helper lists
``POST   /v1/model-settings/connections``              connect a provider with an API key (checked by listing models)
``DELETE /v1/model-settings/connections/{id}``         disconnect (its models leave both lists)
``GET    /v1/model-settings/connections/{id}/models``  the models that connection offers
``PUT    /v1/model-settings/roles``                    save Main + fallbacks and Helper; the first Main model must call a tool

Under ``/v1`` so the API-key middleware applies; every route needs an authenticated principal (as Monitoring).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from seleric_swarm.api.mission_access import request_principal
from seleric_swarm.config.settings import Settings
from seleric_swarm.llm import models_store as store

router = APIRouter(prefix="/v1/model-settings", dependencies=[Depends(request_principal)])


def _settings(request: Request) -> Settings:
    provider = getattr(request.app.state, "runtime_provider", None)
    runtime = provider() if callable(provider) else None
    return getattr(runtime, "settings", None) or Settings()


def _bad(exc: store.ModelsError) -> HTTPException:
    return HTTPException(status_code=409 if isinstance(exc, store.VersionConflict) else 400, detail=str(exc))


async def _list_models(provider: str, base_url: str, api_key: str) -> list[str]:
    headers = {"Authorization": f"Bearer {api_key}"}
    if provider == "anthropic":  # Anthropic's model list takes its own headers
        headers |= {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.get(f"{base_url.rstrip('/')}/models", headers=headers)
    except httpx.HTTPError as exc:
        raise store.ModelsError(f"Could not reach {base_url} ({type(exc).__name__})") from None
    if response.status_code in (401, 403):
        raise store.ModelsError("The provider rejected this API key")
    if response.status_code != 200:
        raise store.ModelsError(f"The provider answered {response.status_code} when listing models")
    try:
        rows = response.json().get("data") or []
    except ValueError:
        raise store.ModelsError("The server did not return a model list") from None
    return sorted({str(r["id"]).removeprefix("models/") for r in rows if isinstance(r, dict) and r.get("id")})


@router.get("")
async def get_settings(request: Request) -> dict[str, Any]:
    return store.view(_settings(request))


@router.post("/connections")
async def connect(request: Request) -> dict[str, Any]:
    body = await request.json()
    provider = str(body.get("provider", ""))
    base_url = str(body.get("base_url") or store.PROVIDERS.get(provider, {}).get("base_url", ""))
    api_key = str(body.get("api_key", "")).strip()
    try:
        await asyncio.to_thread(store.check_base_url, base_url)
        models = await _list_models(provider, base_url, api_key)  # proves the key before it is stored
        row = await asyncio.to_thread(store.add_connection, provider, api_key, base_url, str(body.get("label", "")))
    except store.ModelsError as exc:
        raise _bad(exc) from None
    return {"connection": {k: row[k] for k in ("id", "provider", "label", "base_url", "key_hint")}, "models": models}


@router.delete("/connections/{connection_id}")
async def disconnect(connection_id: str) -> dict[str, Any]:
    try:
        await asyncio.to_thread(store.remove_connection, connection_id)
    except store.ModelsError as exc:
        raise _bad(exc) from None
    return {"ok": True}


@router.get("/connections/{connection_id}/models")
async def models(connection_id: str, request: Request) -> dict[str, Any]:
    settings = _settings(request)
    conn = store.connections(settings).get(connection_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="Unknown connection")
    if conn.builtin and conn.base_url is None:  # Azure deployments are named in the server config, not listable
        return {"models": sorted({m for c, m in settings.loop_chain() + settings.helper_chain() if c == "azure"})}
    try:
        return {"models": await _list_models(conn.provider, conn.base_url or "", conn.api_key or "")}
    except store.ModelsError as exc:
        raise _bad(exc) from None


async def _calls_tools(settings: Settings, row: dict[str, Any]) -> None:
    """The agent works through tool calls: a model that lists fine but cannot call a tool would fail every mission."""
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    from seleric_swarm.llm.adapters.azure_openai_compatible import AzureOpenAICompatibleAdapter

    conn = store.connections(settings)[row["connection"]]
    adapter = AzureOpenAICompatibleAdapter(
        settings.model_copy(update={"llm_timeout_s": 45.0}), base_url=conn.base_url, api_key=conn.api_key
    )
    tuning = {"openai_reasoning_effort": row["thinking"]} if row.get("thinking") else None  # test what the loop sends
    agent = Agent(OpenAIChatModel(row["model"], provider=OpenAIProvider(openai_client=adapter.async_client)), model_settings=tuning)
    called: list[str] = []

    @agent.tool_plain
    def today_total(metric: str) -> str:
        """Return today's total for a metric."""
        called.append(metric)
        return "1234"

    try:
        await asyncio.wait_for(agent.run("Use the today_total tool for metric 'orders', then reply with the number."), 60)
    except Exception as exc:  # noqa: BLE001 - shown to the user; provider errors carry no key
        raise store.ModelsError(f"{row['model']} failed a tool-call test: {str(exc)[:300]}") from None
    if not called:
        raise store.ModelsError(f"{row['model']} answered without calling a tool; the agent needs tool calls")


@router.put("/roles")
async def save_roles(request: Request) -> dict[str, Any]:
    settings = _settings(request)
    body = await request.json()
    try:
        roles = {role: store.clean_role(settings, body.get(role, [])) for role in store.ROLES}
        if roles["main"]:
            await _calls_tools(settings, roles["main"][0])
        expected = body.get("version")
        await asyncio.to_thread(store.save_roles, roles, int(expected) if expected is not None else None)
    except store.ModelsError as exc:
        raise _bad(exc) from None
    return store.view(settings)
