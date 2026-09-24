"""``seleric-voice`` — the LiveKit agent worker process.

**Phase 0 scope: echo only.** Speech in, the same words spoken back out. No
LLM, no mission submission, no narration. The point is to prove transport and
the auth hop in isolation, before the hard parts.

Phase 1 replaces ``EchoAgent`` with a conversational agent holding an
``ask_seleric`` tool plus a narrator over the run event stream — see
``docs/features/voice-agent/02_PHASED_PLAN.md``. Note blocker B7: the V3 agent
loop currently emits no mid-mission events, so narration has no input yet.

Run it the way ``seleric-recover`` is run — a long-lived non-HTTP process:

```console
seleric-voice
```
"""

from __future__ import annotations

import os

# Must be set before livekit-rtc's native module (livekit_ffi.dll / .so) is
# loaded anywhere in the process, hence at import time rather than in main().
# livekit-rtc resamples audio through libsoxr, and mixing sample rates (e.g.
# 48kHz WebRTC mic input against a TTS vendor's native output rate) drives
# concurrent resampling on multiple threads. libsoxr's FFT twiddle-factor
# cache (fft4g_cache.h) is a global initialized without a lock, so concurrent
# first-use from two threads trips `Assertion failed: LSX_FFT_BR == NULL` and
# crashes the whole process. Forcing single-threaded resampling avoids the
# race. A deployment can still override by setting the env var itself.
os.environ.setdefault("SOXR_MAX_THREADS", "1")

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Coroutine

from seleric_swarm.config.settings import Settings, get_settings

logger = logging.getLogger("seleric.voice.worker")

# asyncio.create_task() only holds a *weak* reference to the task via the event
# loop; without a strong reference elsewhere, a task can be garbage-collected
# mid-flight and silently vanish (no exception, no log — the turn just never
# finishes). Every fire-and-forget task in this module goes through
# _track_task() so it stays referenced until it completes.
_background_tasks: set[asyncio.Task[Any]] = set()


def _track_task(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


@dataclass(frozen=True)
class VoicePrincipal:
    """Identity carried from the token route through room participant metadata."""

    workspace_id: str
    user_id: str
    thread_id: str


def principal_from_metadata(raw: str | None) -> VoicePrincipal | None:
    """Parse participant metadata written by ``voice/token.py``.

    Returns ``None`` rather than a default-principal fallback: a participant we
    cannot identify must not be silently treated as the default workspace user
    (blocker B6). The caller decides how to refuse.
    """

    if not raw or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("voice participant metadata is not valid json")
        return None
    if not isinstance(payload, dict):
        return None
    workspace_id = str(payload.get("workspace_id") or "").strip()
    user_id = str(payload.get("user_id") or "").strip()
    thread_id = str(payload.get("thread_id") or "").strip()
    if not (workspace_id and user_id and thread_id):
        logger.warning("voice participant metadata is missing identity fields")
        return None
    return VoicePrincipal(
        workspace_id=workspace_id, user_id=user_id, thread_id=thread_id
    )


def _build_stt(settings: Settings) -> Any:
    """Resolve the STT model via LiveKit's inference gateway.

    Model ids are gateway-qualified (e.g. ``deepgram/nova-3``), so no separate
    vendor key is needed — the LiveKit credentials cover it.
    """

    provider = (settings.stt_provider or "fake").strip()
    if provider.lower() == "fake":
        raise RuntimeError(
            "STT_PROVIDER=fake cannot drive a live room; set a real model id "
            "(e.g. deepgram/nova-3) to run the worker"
        )
    from livekit.agents import inference

    return inference.STT(provider)


def _build_tts(settings: Settings) -> Any:
    provider = (settings.tts_provider or "fake").strip()
    if provider.lower() == "fake":
        raise RuntimeError(
            "TTS_PROVIDER=fake cannot drive a live room; set a real model id "
            "(e.g. cartesia/sonic-3) to run the worker"
        )
    from livekit.agents import inference

    voice = (settings.voice_tts_voice or "").strip()
    return inference.TTS(provider, voice=voice) if voice else inference.TTS(provider)


def format_spoken_summary(text: str, max_chars: int = 350) -> str:
    """Format an assistant markdown response for natural spoken TTS output.

    Removes markdown formatting syntax (headers, bolding, table bars) and trims
    length so spoken responses remain punchy and clear, pointing the user to
    the thread transcript for full data breakdown.
    """
    import re

    cleaned = text
    # Remove code blocks
    cleaned = re.sub(r"```[\s\S]*?```", "", cleaned)
    # Remove markdown headers
    cleaned = re.sub(r"#+\s*", "", cleaned)
    # Remove bold/italic markers
    cleaned = re.sub(r"[*_]{1,3}", "", cleaned)
    # Remove links [text](url) -> text
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    # Remove table separators
    cleaned = re.sub(r"\|[-:\s|]+\|", "", cleaned)
    cleaned = re.sub(r"\|", " ", cleaned)
    # Collapse whitespace
    cleaned = " ".join(cleaned.split())

    if not cleaned:
        return "Seleric completed the analysis. Please check your thread for details."

    if len(cleaned) > max_chars:
        trimmed = cleaned[:max_chars].rsplit(".", 1)[0]
        if not trimmed:
            trimmed = cleaned[:max_chars]
        return f"{trimmed}. Full evidence breakdown is available in your thread."

    return cleaned


_voice_http_client: Any = None


def _get_voice_client() -> Any:
    global _voice_http_client
    import httpx

    if _voice_http_client is None or _voice_http_client.is_closed:
        _voice_http_client = httpx.AsyncClient(timeout=30.0)
    return _voice_http_client


async def execute_seleric_turn(
    session: Any,
    principal: VoicePrincipal,
    query: str,
    settings: Settings,
    ctx: Any = None,
) -> None:
    """Execute a voice turn: submit mission to Seleric API, narrate progress, and speak summary."""
    import json
    import time

    def broadcast_ui_transcript(text: str, speaker: str = "Seleric") -> None:
        """Publish a data message for real-time Web UI display without queueing TTS audio."""
        if ctx is not None and hasattr(ctx, "room"):
            try:
                msg = json.dumps({"type": "transcript", "speaker": speaker, "text": text, "is_final": True})
                _track_task(ctx.room.local_participant.publish_data(msg.encode("utf-8")))
            except Exception as broadcast_err:
                logger.warning(f"Failed to publish transcript data message: {broadcast_err}")

    def say_and_broadcast(text: str) -> None:
        """Speak TTS response via LiveKit and update the Web UI transcript."""
        try:
            session.say(text)
        except Exception as err:
            if "closing" in str(err).lower() or "closed" in str(err).lower():
                return
            logger.warning(f"session.say error: {err}")
        broadcast_ui_transcript(text)

    api_url = getattr(settings, "seleric_api_url", "http://127.0.0.1:8000").rstrip("/")
    if "localhost" in api_url:
        api_url = api_url.replace("localhost", "127.0.0.1")
    # The "local-voice-dev-key" fallback only ever matters in a dev deployment,
    # where ApiSecurityMiddleware treats an unset API_KEY as no-auth-required
    # and the literal value is never checked. main() refuses to start the
    # worker with a blank API_KEY outside a dev environment, so this can't
    # silently mask a production misconfiguration.
    api_key = (getattr(settings, "api_key", "") or "").strip() or "local-voice-dev-key"

    headers = {
        "content-type": "application/json",
        "x-api-key": api_key,
        "x-workspace-id": principal.workspace_id,
        "x-user-id": principal.user_id,
    }

    # Publish query submission to UI transcript box visually (keep TTS queue clean)
    broadcast_ui_transcript(f"Submitting query to Seleric: {query}")

    try:
        client = _get_voice_client()
        submit_url = f"{api_url}/v1/threads/{principal.thread_id}/messages"
        payload = {
            "execution_mode": "development",
            "parts": [{"type": "TEXT", "content": query}],
        }
        res = await client.post(submit_url, headers=headers, json=payload)
        if not res.is_success:
            logger.error(f"failed to submit voice message: {res.status_code} {res.text}")
            say_and_broadcast("Sorry, I was unable to submit your query to Seleric.")
            return

        data = res.json()
        run_id = data.get("run_id")
        if not run_id:
            say_and_broadcast("Received invalid response from Seleric API.")
            return

        start_time = time.monotonic()
        last_ui_narration = start_time
        has_spoken_narration = False
        timeout_s = float(getattr(settings, "voice_mission_timeout_s", 180.0))
        min_gap_s = float(getattr(settings, "voice_narration_min_gap_s", 8.0))

        while time.monotonic() - start_time < timeout_s:
            await asyncio.sleep(1.5)

            msgs_res = await client.get(
                f"{api_url}/v1/threads/{principal.thread_id}/messages",
                headers=headers,
            )
            if not msgs_res.is_success:
                continue

            items = msgs_res.json()
            if isinstance(items, dict) and "items" in items:
                items = items["items"]
            if not isinstance(items, list):
                items = []

            # Find assistant response message corresponding to run_id
            for msg in reversed(items):
                if msg.get("role") == "ASSISTANT" and (
                    msg.get("run_id") == run_id or not run_id
                ):
                    parts = msg.get("parts", [])
                    for part in parts:
                        part_type = str(part.get("type") or "").upper()
                        content = str(part.get("content") or "").strip()
                        if part_type == "TEXT" and content and content != "Working…":
                            spoken = format_spoken_summary(content)
                            say_and_broadcast(spoken)
                            return

            now = time.monotonic()
            elapsed = now - start_time

            # Update UI transcript display every min_gap_s visually
            if now - last_ui_narration >= min_gap_s:
                last_ui_narration = now
                broadcast_ui_transcript("Seleric is working on your query...")

            # Optional: speak a single audio progress update only if a mission takes > 15s
            if elapsed >= 15.0 and not has_spoken_narration:
                has_spoken_narration = True
                try:
                    session.say("Still analyzing your query...")
                except Exception:
                    pass

        say_and_broadcast("The query timed out before completing. Please check your thread later.")
    except Exception as exc:
        logger.exception("Error executing voice turn against Seleric API")
        say_and_broadcast("An error occurred while communicating with Seleric.")


def build_server() -> Any:
    """Construct the LiveKit ``AgentServer`` with the Phase 1 conversational session.

    Imported lazily so that importing this module (for the metadata helpers, or
    for tests) does not require the heavy ``livekit-agents`` dependency.
    """
    from livekit.agents import (
        Agent,
        AgentServer,
        AgentSession,
        JobContext,
        TurnHandlingOptions,
    )
    from livekit.plugins import silero

    settings = get_settings()
    server = AgentServer()

    # Loaded once per process, not per session: the ONNX model is expensive to
    # initialise and every room shares it.
    vad = silero.VAD.load()

    @server.rtc_session()
    async def entrypoint(ctx: JobContext) -> None:
        participant = await ctx.wait_for_participant()
        principal = principal_from_metadata(getattr(participant, "metadata", None))
        if principal is None:
            logger.error(
                "refusing voice session: participant carries no usable identity",
                extra={"room": getattr(ctx.room, "name", "")},
            )
            return

        logger.info(
            "voice session started",
            extra={
                "room": getattr(ctx.room, "name", ""),
                "workspace_id": principal.workspace_id,
                "user_id": principal.user_id,
                "thread_id": principal.thread_id,
            },
        )

        session = AgentSession(
            stt=_build_stt(settings),
            tts=_build_tts(settings),
            vad=vad,
            min_endpointing_delay=0.5,
            # Barge-in: user speech interrupts TTS narration
            turn_handling=TurnHandlingOptions(interruption={"enabled": True}),
        )

        @session.on("user_input_transcribed")
        def _on_transcript(event: Any) -> None:
            transcript = str(getattr(event, "transcript", "") or "").strip()
            is_final = bool(getattr(event, "is_final", False))
            if not transcript:
                return

            # Broadcast live transcript to room data channel for UI display
            try:
                msg = json.dumps({"type": "transcript", "speaker": "You", "text": transcript, "is_final": is_final})
                _track_task(ctx.room.local_participant.publish_data(msg.encode("utf-8")))
            except Exception:
                pass

            if not is_final:
                return

            # Prevent duplicate overlapping mission turns while a query is already running
            if getattr(session, "_in_flight_turn", False):
                logger.info(f"Skipping duplicate turn trigger while turn is in-flight: '{transcript}'")
                return

            logger.info(
                "voice transcript received",
                extra={
                    "transcript": transcript,
                    "thread_id": principal.thread_id,
                    "user_id": principal.user_id,
                },
            )

            async def _run_turn() -> None:
                setattr(session, "_in_flight_turn", True)
                try:
                    await execute_seleric_turn(session, principal, transcript, settings, ctx)
                except Exception as err:
                    # `say_and_broadcast` is a closure private to
                    # execute_seleric_turn's own scope, not available here —
                    # calling it by name from this except block would raise
                    # NameError. execute_seleric_turn already catches and
                    # speaks its own errors internally, so reaching this
                    # branch means something failed outside that coverage;
                    # fall back to session.say() directly rather than
                    # reference an out-of-scope name.
                    logger.exception(f"Unhandled exception executing seleric turn: {err}")
                    try:
                        session.say(f"Error processing query: {err}")
                    except Exception:
                        pass
                finally:
                    setattr(session, "_in_flight_turn", False)

            _track_task(_run_turn())

        await session.start(
            agent=Agent(
                instructions="You are Seleric Voice Assistant. Transcribe user questions and submit them to Seleric."
            ),
            room=ctx.room,
        )
        session.say("Seleric Voice is ready. Ask any business question.")

    return server


def _export_livekit_env(settings: Settings) -> None:
    """Bridge Settings into the env vars the LiveKit SDK reads directly.

    ``Settings`` (and therefore ``.env``) is the single source of truth for this
    project, but ``livekit-agents`` resolves its credentials straight from the
    process environment. Without this the worker starts and then fails with
    "ws_url is required" despite the values being configured. Anything already
    exported wins, so a deployment can still override.
    """

    import os

    for name, value in (
        ("LIVEKIT_URL", settings.livekit_url),
        ("LIVEKIT_API_KEY", settings.livekit_api_key),
        ("LIVEKIT_API_SECRET", settings.livekit_api_secret),
    ):
        if value.strip() and not os.environ.get(name):
            os.environ[name] = value.strip()


def main() -> None:
    """Console-script entry point for ``seleric-voice``."""

    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    if not settings.voice_enabled:
        raise SystemExit(
            "VOICE_ENABLED is false — refusing to start the voice worker."
        )
    missing = settings.missing_voice_credentials()
    if missing:
        raise SystemExit(
            "voice worker cannot start; missing " + ", ".join(missing)
        )
    # Outside a dev environment, execute_seleric_turn's blank-API_KEY fallback
    # ("local-voice-dev-key") would otherwise mint a header the API's
    # ApiSecurityMiddleware always rejects — every turn fails with a 401 that
    # just looks like "unable to submit your query", with nothing pointing at
    # a missing API_KEY in the worker's own environment. Fail fast instead.
    if not settings.is_dev_surface() and not settings.api_key.strip():
        raise SystemExit(
            "API_KEY is not configured for the voice worker — it cannot "
            "authenticate to the Seleric API outside a dev environment."
        )
    _export_livekit_env(settings)
    from livekit.agents import cli

    cli.run_app(build_server())


if __name__ == "__main__":  # pragma: no cover
    main()
