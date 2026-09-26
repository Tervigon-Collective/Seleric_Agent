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
import importlib
import json
import logging
import time
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
    if provider.lower() == "azure":
        key, region = _azure_speech_credentials(settings)
        from livekit.plugins import azure

        languages = [c.strip() for c in (settings.voice_stt_languages or "").split(",") if c.strip()]
        return azure.STT(
            speech_key=key,
            speech_region=region,
            language=languages or ["en-IN", "hi-IN"],
        )
    from livekit.agents import inference

    language = (settings.voice_stt_language or "").strip()
    return inference.STT(provider, language=language) if language else inference.STT(provider)


def _azure_speech_credentials(settings: Settings) -> tuple[str, str]:
    key = (settings.azure_speech_key or "").strip()
    region = (settings.azure_speech_region or "").strip()
    if not (key and region):
        raise RuntimeError(
            "Azure speech needs AZURE_SPEECH_KEY and AZURE_SPEECH_REGION "
            "(STT_PROVIDER/TTS_PROVIDER=azure)"
        )
    return key, region


def _build_tts(settings: Settings) -> Any:
    provider = (settings.tts_provider or "fake").strip()
    if provider.lower() == "fake":
        raise RuntimeError(
            "TTS_PROVIDER=fake cannot drive a live room; set a real model id "
            "(e.g. cartesia/sonic-3) to run the worker"
        )
    voice = (settings.voice_tts_voice or "").strip()
    if provider.lower() == "azure":
        key, region = _azure_speech_credentials(settings)
        from livekit.plugins import azure

        # Multilingual voice: one voice speaks Hindi, English and mixed text.
        # Azure TTS is non-streaming, but LiveKit synthesizes per sentence.
        return azure.TTS(
            voice=voice or "en-US-AvaMultilingualNeural",
            speech_key=key,
            speech_region=region,
        )
    from livekit.agents import inference

    return inference.TTS(provider, voice=voice) if voice else inference.TTS(provider)


VOICE_INSTRUCTIONS = """\
You are Seleric Voice, a warm, concise voice assistant for business analytics.

Style: always reply in the language of the user's latest message: Hindi in Devanagari script if \
they speak Hindi, English if English, mixing naturally if they mix. \
Keep every reply to one or two short sentences. No markdown, lists, emojis or symbols.

You have NO business data of your own. For any question about metrics, sales, performance, \
trends, causes, comparisons or forecasts, call ask_seleric with the user's complete question \
in their own words, resolving references from this conversation (e.g. "and last month?" becomes \
the full question), then say one brief sentence that you are looking into it. Never state, \
estimate or guess a number or business fact yourself. The answer is spoken automatically when \
it is ready, in full, so do not repeat or invent it and never tell the user to check a thread. \
If the user asks whether a slow question has finished, call check_seleric. If the question is missing a metric or a time \
period, ask one short clarifying question first.

Greetings, small talk and clarifying questions you may answer directly. If the user says \
stop, cancel or never mind, call cancel_seleric. You cannot change or write any data by \
voice; if asked, tell the user to use the typed interface.\
"""


def _build_llm(settings: Settings) -> Any:
    """Resolve the conversational LLM.

    A bare model id goes through LiveKit's inference gateway. With
    ``VOICE_LLM_BASE_URL`` set it is any OpenAI-compatible endpoint instead
    (Groq, Gemini's OpenAI endpoint, Azure, a local server), so a free tier can
    be used without changing code.
    """

    if (settings.voice_llm_provider or "").strip().lower() == "azure":
        endpoint = (settings.azure_openai_endpoint or "").strip().rstrip("/")
        api_key = (settings.azure_openai_api_key or "").strip()
        model = (
            (settings.voice_llm_model or "").strip()
            or (settings.azure_openai_fast_model or "").strip()
            or (settings.azure_openai_model or "").strip()
        )
        if not (endpoint and api_key and model):
            raise RuntimeError(
                "VOICE_LLM_PROVIDER=azure needs AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY "
                "and a model (VOICE_LLM_MODEL or AZURE_OPENAI_MODEL)"
            )
        from livekit.plugins import openai as lk_openai

        return lk_openai.LLM(model=model, base_url=f"{endpoint}/openai/v1", api_key=api_key)

    model = (settings.voice_llm_model or "").strip()
    if not model:
        raise RuntimeError("VOICE_LLM_MODEL is not set; the voice agent needs a conversational model")
    base_url = (settings.voice_llm_base_url or "").strip()
    if base_url:
        from livekit.plugins import openai as lk_openai

        api_key = (settings.voice_llm_api_key or "").strip()
        if api_key:
            return lk_openai.LLM(model=model, base_url=base_url, api_key=api_key)
        return lk_openai.LLM(model=model, base_url=base_url)
    from livekit.agents import inference

    return inference.LLM(model)


def _build_turn_handling() -> Any:
    """Semantic end-of-turn + barge-in, so one question is one turn.

    ``inference.TurnDetector()`` picks the hosted model on LiveKit Cloud and the
    local ``v1-mini`` model (Hindi and English included) elsewhere; either way
    endpointing then waits for a finished thought instead of a fixed silence.
    Preemptive generation only starts the LLM early — tools run after the turn
    is confirmed, so ``ask_seleric`` never fires on a half-heard question.
    """

    from livekit.agents import TurnHandlingOptions, inference

    try:
        detector: Any = inference.TurnDetector()
    except Exception:
        logger.warning("turn detector unavailable; using the session default", exc_info=True)
        detector = None
    options: dict[str, Any] = {
        # Dynamic endpointing adapts the silence wait to how fast this user
        # speaks; the 0.3s floor cuts the fixed 0.5s default.
        "endpointing": {"mode": "dynamic", "min_delay": 0.3, "max_delay": 3.0},
        "interruption": {"enabled": True, "resume_false_interruption": True},
        # Start the LLM *and* TTS on the partial transcript; both are discarded
        # if the user keeps talking. Tools still wait for the confirmed turn.
        "preemptive_generation": {"enabled": True, "preemptive_tts": True},
    }
    if detector is not None:
        options["turn_detection"] = detector
    return TurnHandlingOptions(**options)


class LatencyLog:
    """One log line per turn: how long until the agent's first audio.

    Sums end-of-utterance delay, LLM time-to-first-token and TTS time-to-first-byte
    (the same figure LiveKit reports as ``e2e_latency`` minus network). Reads the
    session's ``metrics_collected`` events, so it needs no extra dependency.
    """

    def __init__(self) -> None:
        self._eou: dict[str, float] = {}
        self._ttft: dict[str, float] = {}

    def on_metrics(self, event: Any) -> None:
        metrics = getattr(event, "metrics", None)
        kind = getattr(metrics, "type", "")
        speech_id = getattr(metrics, "speech_id", None)
        if not speech_id:
            return
        if kind == "eou_metrics":
            self._eou[speech_id] = float(metrics.end_of_utterance_delay) + float(metrics.transcription_delay)
        elif kind == "llm_metrics":
            self._ttft[speech_id] = max(float(metrics.ttft), 0.0)
        elif kind == "tts_metrics":
            eou = self._eou.pop(speech_id, None)
            ttft = self._ttft.pop(speech_id, None)
            ttfb = float(metrics.ttfb)
            logger.info(
                "voice turn latency: first_audio=%.2fs (turn_end=%s llm_ttft=%s tts_ttfb=%.2fs chars=%d)",
                (eou or 0.0) + (ttft or 0.0) + ttfb,
                f"{eou:.2f}s" if eou is not None else "n/a",
                f"{ttft:.2f}s" if ttft is not None else "n/a",
                ttfb,
                int(metrics.characters_count),
            )


def build_voice_agent(runner: "VoiceTurnRunner") -> Any:
    """The conversational agent: chats directly, delegates data questions to Seleric."""

    from livekit.agents import Agent
    from livekit.agents.llm import function_tool

    async def ask_seleric(question: str) -> str:
        """Ask Seleric a business-data question. Pass the user's full question."""
        runner.submit(question)
        return "Started. Tell the user in one short sentence that you are looking into it."

    async def cancel_seleric() -> str:
        """Stop the question Seleric is currently working on."""
        return "Cancelled." if runner.cancel() else "Nothing was running."

    async def check_seleric() -> str:
        """Check on a Seleric question that took too long; reads the answer out if ready."""
        return runner.check()

    return Agent(
        instructions=VOICE_INSTRUCTIONS,
        tools=[function_tool(ask_seleric), function_tool(cancel_seleric), function_tool(check_seleric)],
    )


def format_spoken_summary(text: str) -> str:
    """Format an assistant markdown response for natural spoken TTS output.

    Strips markdown syntax (headers, emphasis, links, code, table bars) and
    turns list items and table rows into separate sentences so TTS pauses
    between them. The whole answer is kept; ``split_spoken_chunks`` handles
    length by speaking it in pieces.
    """
    import re

    cleaned = re.sub(r"```[\s\S]*?```", "", text)
    lines: list[str] = []
    for raw in cleaned.splitlines():
        line = raw.strip()
        if not line or (re.fullmatch(r"[|\s:\-]+", line) and "-" in line):
            continue  # blank line or table separator row
        line = re.sub(r"^#+\s*", "", line)
        line = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        line = re.sub(r"[*_`]{1,3}", "", line)
        line = ", ".join(cell.strip() for cell in line.strip("|").split("|") if cell.strip())
        if line:
            lines.append(line if line[-1] in ".!?:\u0964" else line + ".")
    cleaned = " ".join(" ".join(lines).split())

    if not cleaned:
        return "Seleric finished, but I don't have anything to read out."
    return cleaned


def split_spoken_chunks(text: str, max_chars: int = 300) -> list[str]:
    """Group sentences into chunks of about ``max_chars`` so TTS starts fast."""
    import re

    chunks: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?\u0964])\s+", text):
        if current and len(current) + 1 + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


_voice_http_client: Any = None


def _get_voice_client() -> Any:
    global _voice_http_client
    import httpx

    if _voice_http_client is None or _voice_http_client.is_closed:
        _voice_http_client = httpx.AsyncClient(timeout=30.0)
    return _voice_http_client


# Fixed lines, never model- or backend-generated. A run that fails validation
# (INSUFFICIENT_EVIDENCE) still carries its unvalidated text as the final
# message, so nothing from a non-completed run is ever read aloud. The
# failed-run event carries no error code, so the wording covers both an evidence
# refusal and an infrastructure failure (e.g. a rate-limited model).
REFUSAL_LINE = (
    "I wasn't able to complete that one, so I won't guess. "
    "Please try again in a moment."
)
IDLE_LINES = (
    "Still working on it.",
    "This one needs a bit more digging.",
    "Almost there, thanks for waiting.",
)
TIMEOUT_LINE = (
    "This is still running and taking longer than usual. "
    "Ask me to check on it in a moment and I'll read it out."
)
GONE_LINE = "This conversation is no longer available. Please disconnect and reconnect, then ask again."
SUBMIT_FAILED_LINE = "Sorry, I couldn't send that to Seleric. Please try again."
ERROR_LINE = "Sorry, something went wrong on my side. Please try again."

_TERMINAL_EVENTS = frozenset({"run.completed", "run.failed", "run.cancelled"})
_MAX_NARRATION_CHARS = 120


def narration_line(event_type: str, summary: str) -> str | None:
    """Map a run event to one spoken progress line, or ``None`` to stay quiet.

    Speaks only ``summary`` (a label written for display to a person), never
    ``payload``. Only tool starts and the answering hand-off are spoken;
    completions would double the chatter.
    """

    if event_type == "agent.answering":
        return "Putting the answer together."
    if event_type != "agent.tool_started":
        return None
    label = " ".join(str(summary or "").split())[:_MAX_NARRATION_CHARS].rstrip(" .…")
    return f"{label}…" if label else None


async def iter_run_events(
    client: Any,
    url: str,
    headers: dict[str, str],
    deadline: float,
) -> Any:
    """Yield decoded events from a run's SSE stream until a terminal event.

    Yields ``{"type": "_tick"}`` on each heartbeat so callers can run
    time-based logic (idle hold) while the stream is quiet. Reconnects from the
    last seen sequence if the connection drops before a terminal event.
    """

    import httpx

    cursor = 0
    while time.monotonic() < deadline:
        try:
            async with client.stream(
                "GET",
                url,
                headers=headers,
                params={"after_sequence": cursor, "heartbeat_seconds": 1},
            ) as res:
                if not res.is_success:
                    logger.error(f"run event stream refused: {res.status_code}")
                    yield {"type": "_error"}
                    return
                data_lines: list[str] = []
                async for line in res.aiter_lines():
                    if time.monotonic() >= deadline:
                        return
                    if line.startswith(":"):
                        yield {"type": "_tick"}
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                        continue
                    if line:
                        continue  # id: / event: — the data frame carries both
                    raw, data_lines = "".join(data_lines), []
                    if not raw:
                        continue
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    sequence = event.get("sequence")
                    if isinstance(sequence, int):
                        cursor = max(cursor, sequence)
                    yield event
                    if event.get("type") in _TERMINAL_EVENTS:
                        return
        except httpx.HTTPError as err:
            logger.warning(f"run event stream dropped, reconnecting: {err}")
        await asyncio.sleep(0.5)


class VoiceTurnRunner:
    """Owns the one in-flight Seleric mission for a voice session.

    A new user question supersedes the running one: the old run is cancelled
    through the existing cancel route and its narrator stopped, instead of the
    new question being silently dropped.
    """

    def __init__(
        self,
        session: Any,
        principal: VoicePrincipal,
        settings: Settings,
        ctx: Any = None,
    ) -> None:
        self._session = session
        self._principal = principal
        self._settings = settings
        self._ctx = ctx
        self._task: asyncio.Task[Any] | None = None
        self._run_id: str | None = None
        self._pending_run_id: str | None = None  # timed out unfinished; check() can still fetch it

        api_url = str(getattr(settings, "seleric_api_url", "http://127.0.0.1:8000")).rstrip("/")
        self._api_url = api_url.replace("localhost", "127.0.0.1")
        # The "local-voice-dev-key" fallback only matters in a dev deployment,
        # where ApiSecurityMiddleware treats an unset API_KEY as no-auth and the
        # literal value is never checked. main() refuses to start the worker
        # with a blank API_KEY outside a dev environment.
        api_key = (getattr(settings, "api_key", "") or "").strip() or "local-voice-dev-key"
        self._headers = {
            "content-type": "application/json",
            "x-api-key": api_key,
            "x-workspace-id": principal.workspace_id,
            "x-user-id": principal.user_id,
        }

    # -- output ---------------------------------------------------------

    def _broadcast(self, text: str, speaker: str = "Seleric") -> None:
        """Publish a data message for the web UI transcript, without TTS."""
        if self._ctx is None or not hasattr(self._ctx, "room"):
            return
        try:
            msg = json.dumps(
                {"type": "transcript", "speaker": speaker, "text": text, "is_final": True}
            )
            _track_task(self._ctx.room.local_participant.publish_data(msg.encode("utf-8")))
        except Exception as err:
            logger.warning(f"Failed to publish transcript data message: {err}")

    def _say(self, text: str, broadcast: bool = True) -> None:
        try:
            self._session.say(text)
        except Exception as err:
            if "closing" in str(err).lower() or "closed" in str(err).lower():
                return
            logger.warning(f"session.say error: {err}")
        if broadcast:
            self._broadcast(text)

    # -- lifecycle ------------------------------------------------------

    def submit(self, query: str) -> None:
        """Start a turn for ``query``, superseding any run still in flight."""
        previous, previous_run = self._task, self._run_id
        if previous is not None and not previous.done():
            previous.cancel()
            if previous_run:
                _track_task(self._cancel_run(previous_run))
        self._run_id = None
        self._pending_run_id = None
        self._task = _track_task(self._run(query))

    def cancel(self) -> bool:
        """Stop the in-flight run (user said "never mind"); False if none."""
        task, run_id = self._task, self._run_id
        if task is None or task.done():
            return False
        task.cancel()
        if run_id:
            _track_task(self._cancel_run(run_id))
        self._run_id = None
        return True

    def check(self) -> str:
        """Result for the ``check_seleric`` tool: read out a timed-out run's answer if ready."""
        if self._task is not None and not self._task.done():
            return "Still running. Tell the user in one short sentence that it is still in progress."
        run_id = self._pending_run_id
        if not run_id:
            return "Nothing to check."
        _track_task(self._speak_answer(run_id, fallback=TIMEOUT_LINE))
        return "Checking now. Say nothing more; the result will be spoken."

    async def _cancel_run(self, run_id: str) -> None:
        try:
            res = await _get_voice_client().post(
                f"{self._api_url}/v1/runs/{run_id}/cancel", headers=self._headers
            )
            if not res.is_success:
                logger.info(f"run cancel returned {res.status_code} for {run_id}")
        except Exception as err:
            logger.warning(f"run cancel failed for {run_id}: {err}")

    async def _run(self, query: str) -> None:
        self._broadcast(f"Submitting query to Seleric: {query}", speaker="Status")
        run_id: str | None = None
        try:
            run_id = await self._submit(query)
            if run_id is None:
                return
            self._run_id = run_id
            await self._follow(run_id)
        except asyncio.CancelledError:
            raise  # superseded by a newer question; its run is cancelled in submit()
        except Exception:
            logger.exception("Error executing voice turn against Seleric API")
            self._say(ERROR_LINE)
        finally:
            if run_id is not None and self._run_id == run_id:
                self._run_id = None

    async def _submit(self, query: str) -> str | None:
        res = await _get_voice_client().post(
            f"{self._api_url}/v1/threads/{self._principal.thread_id}/messages",
            headers=self._headers,
            json={
                "execution_mode": "development",
                "parts": [{"type": "TEXT", "content": query}],
            },
        )
        if not res.is_success:
            logger.error(f"failed to submit voice message: {res.status_code} {res.text}")
            self._say(GONE_LINE if res.status_code == 404 else SUBMIT_FAILED_LINE)
            return None
        run_id = res.json().get("run_id")
        if not run_id:
            logger.error("voice message accepted without a run_id")
            self._say(SUBMIT_FAILED_LINE)
            return None
        return str(run_id)

    async def _follow(self, run_id: str) -> None:
        """Narrate a run from its event stream, then speak how it ended."""
        settings = self._settings
        timeout_s = float(getattr(settings, "voice_mission_timeout_s", 180.0))
        min_gap_s = float(getattr(settings, "voice_narration_min_gap_s", 4.0))
        idle_hold_s = float(getattr(settings, "voice_narration_idle_hold_s", 20.0))
        narrate = bool(getattr(settings, "voice_narration_enabled", True))

        started = time.monotonic()
        last_spoken = started
        idle_spoken = 0
        terminal: str | None = None
        errored = False

        events = iter_run_events(
            _get_voice_client(),
            f"{self._api_url}/v1/runs/{run_id}/events/stream",
            self._headers,
            deadline=started + timeout_s,
        )
        async for event in events:
            event_type = str(event.get("type") or "")
            if event_type == "_error":
                errored = True
                break
            if event_type in _TERMINAL_EVENTS:
                terminal = event_type
                break
            now = time.monotonic()
            if not narrate:
                continue
            line = narration_line(event_type, str(event.get("summary") or ""))
            if line and now - last_spoken >= min_gap_s:
                last_spoken = now
                self._say(line)
            elif (
                event_type == "_tick"
                and idle_spoken < len(IDLE_LINES)
                and now - last_spoken >= idle_hold_s
            ):
                last_spoken = now
                self._say(IDLE_LINES[idle_spoken])
                idle_spoken += 1

        if terminal == "run.completed":
            await self._speak_answer(run_id)
        elif terminal == "run.failed":
            self._say(REFUSAL_LINE)
        elif terminal == "run.cancelled":
            return
        elif errored:
            self._say(ERROR_LINE)
        else:
            # Out of time. The run may have finished just now; otherwise keep
            # its id so the user can ask again.
            self._pending_run_id = run_id
            await self._speak_answer(run_id, fallback=TIMEOUT_LINE)

    async def _speak_answer(self, run_id: str, fallback: str = ERROR_LINE) -> None:
        """Speak the validated final answer of a *completed* run."""
        client = _get_voice_client()
        url = f"{self._api_url}/v1/threads/{self._principal.thread_id}/messages"
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(0.5)
            res = await client.get(url, headers=self._headers)
            if not res.is_success:
                continue
            items = res.json()
            if isinstance(items, dict):
                items = items.get("items", [])
            if not isinstance(items, list):
                continue
            for msg in reversed(items):
                if msg.get("role") != "ASSISTANT" or msg.get("run_id") != run_id:
                    continue
                for part in msg.get("parts", []):
                    content = str(part.get("content") or "").strip()
                    if str(part.get("type") or "").upper() == "TEXT" and content and content != "Working…":
                        if self._pending_run_id == run_id:
                            self._pending_run_id = None
                        spoken = format_spoken_summary(content)
                        self._broadcast(spoken)
                        for chunk in split_spoken_chunks(spoken):
                            self._say(chunk, broadcast=False)
                        return
        logger.error(f"run {run_id} had no readable answer message")
        self._say(fallback)


def _import_plugins() -> None:
    """Import every LiveKit plugin the session builders may use.

    Plugins register themselves at import time and refuse to do so off the
    main thread. ``entrypoint`` runs in a job thread (Windows runs jobs as
    threads of the main process; Linux as child processes), so importing lazily
    inside the session builders crashes with "Plugins must be registered on the
    main thread". Called from ``build_server`` (main process, main thread) and
    again from ``_prewarm`` (a child process's main thread); repeat imports are
    no-ops.
    """

    for name in ("silero", "azure", "openai"):
        try:
            importlib.import_module(f"livekit.plugins.{name}")
        except ImportError:
            logger.debug(f"livekit plugin {name} not installed; skipping preload")


def _prewarm(proc: Any) -> None:
    """Runs once per job process, before any session: plugins, then the VAD model.

    The ONNX model is expensive to initialise, so it is loaded once per process
    rather than once per room.
    """

    _import_plugins()
    from livekit.plugins import silero

    proc.userdata["vad"] = silero.VAD.load()


def build_server() -> Any:
    """Construct the LiveKit ``AgentServer`` with the Phase 1 conversational session.

    Imported lazily so that importing this module (for the metadata helpers, or
    for tests) does not require the heavy ``livekit-agents`` dependency.
    """
    from livekit.agents import AgentServer, AgentSession, JobContext

    _import_plugins()
    settings = get_settings()
    server = AgentServer()
    server.setup_fnc = _prewarm

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
            llm=_build_llm(settings),
            tts=_build_tts(settings),
            vad=ctx.proc.userdata["vad"],
            turn_handling=_build_turn_handling(),
        )

        runner = VoiceTurnRunner(session, principal, settings, ctx)
        session.on("metrics_collected", LatencyLog().on_metrics)

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

            logger.info(
                "voice transcript received",
                extra={
                    "transcript": transcript,
                    "thread_id": principal.thread_id,
                    "user_id": principal.user_id,
                },
            )
        await session.start(agent=build_voice_agent(runner), room=ctx.room)
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
    llm_from_azure = settings.voice_llm_provider.strip().lower() == "azure"
    if not (settings.voice_llm_model.strip() or llm_from_azure):
        raise SystemExit(
            "VOICE_LLM_MODEL is not set — the voice agent needs a conversational model."
        )
    # Outside a dev environment, the runner's blank-API_KEY fallback
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
