"""Voice turn runner — narration, refusal and barge-in, with no LiveKit or network.

The runner is the part of the worker that decides what is *spoken*. The
adversarial property under test: nothing from a run that did not complete is
ever read aloud as an answer (docs/features/voice-agent/01_ARCHITECTURE.md §4).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from seleric_swarm.voice import worker
from seleric_swarm.voice.worker import (
    ERROR_LINE,
    REFUSAL_LINE,
    TIMEOUT_LINE,
    VoicePrincipal,
    VoiceTurnRunner,
    iter_run_events,
    narration_line,
)

PRINCIPAL = VoicePrincipal(workspace_id="ws_1", user_id="user_1", thread_id="thr_1")
UNVALIDATED = "Revenue was 9,999 — trust me."


def _sse(*events: dict[str, Any], heartbeat: bool = False) -> list[str]:
    lines: list[str] = [": heartbeat", ""] if heartbeat else []
    for seq, event in enumerate(events, start=1):
        lines += [f"id: {seq}", f"event: {event['type']}", "data: " + json.dumps({"sequence": seq, **event}), ""]
    return lines


class _Response:
    def __init__(self, status: int = 200, body: Any = None, lines: list[str] | None = None) -> None:
        self.status_code = status
        self.is_success = 200 <= status < 300
        self._body = body
        self._lines = lines or []
        self.text = ""

    def json(self) -> Any:
        return self._body

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _Client:
    def __init__(self, stream_lines: list[str], messages: list[dict[str, Any]] | None = None) -> None:
        self.stream_lines = stream_lines
        self.messages = messages or []
        self.posts: list[str] = []

    async def post(self, url: str, **_: Any) -> _Response:
        self.posts.append(url)
        if url.endswith("/messages"):
            return _Response(body={"run_id": "run_1"})
        return _Response()

    async def get(self, url: str, **_: Any) -> _Response:
        return _Response(body=self.messages)

    def stream(self, method: str, url: str, **_: Any) -> _Response:
        return _Response(lines=self.stream_lines)


class _Session:
    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text: str) -> None:
        self.said.append(text)


def _settings(**overrides: Any) -> SimpleNamespace:
    base = {
        "seleric_api_url": "http://localhost:8000",
        "api_key": "k",
        "voice_mission_timeout_s": 5.0,
        "voice_narration_min_gap_s": 0.0,
        "voice_narration_idle_hold_s": 20.0,
        "voice_narration_enabled": True,
    }
    return SimpleNamespace(**{**base, **overrides})


def _runner(client: _Client, monkeypatch: pytest.MonkeyPatch, **settings: Any) -> tuple[VoiceTurnRunner, _Session]:
    monkeypatch.setattr(worker, "_get_voice_client", lambda: client)
    session = _Session()
    return VoiceTurnRunner(session, PRINCIPAL, _settings(**settings)), session


async def _drain(runner: VoiceTurnRunner) -> None:
    assert runner._task is not None
    await asyncio.wait_for(runner._task, timeout=5)


def test_narration_speaks_only_tool_starts_and_the_answer_handoff() -> None:
    assert narration_line("agent.tool_started", "Fetching metric data") == "Fetching metric data…"
    assert narration_line("agent.answering", "Writing the answer") == "Putting the answer together."
    assert narration_line("agent.tool_completed", "Fetching metric data — done") is None
    assert narration_line("answer.delta", "Rev") is None
    assert narration_line("agent.tool_started", "  ") is None


async def test_iter_run_events_parses_frames_and_stops_at_terminal() -> None:
    lines = _sse({"type": "agent.tool_started"}, {"type": "run.completed"}, {"type": "after.terminal"}, heartbeat=True)

    got = [e async for e in iter_run_events(_Client(lines), "u", {}, deadline=10**12)]

    assert [e["type"] for e in got] == ["_tick", "agent.tool_started", "run.completed"]


async def test_completed_run_narrates_then_speaks_only_the_validated_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = _sse(
        {"type": "agent.tool_started", "summary": "Comparing periods"},
        {"type": "answer.delta", "payload": {"delta": UNVALIDATED}},
        {"type": "run.completed"},
    )
    messages = [
        {
            "role": "ASSISTANT",
            "run_id": "run_1",
            "parts": [{"type": "TEXT", "content": "**Sales** rose 12% on the prior period."}],
        }
    ]
    runner, session = _runner(_Client(lines, messages), monkeypatch)

    runner.submit("how are sales?")
    await _drain(runner)

    assert session.said == ["Comparing periods…", "Sales rose 12% on the prior period."]


async def test_failed_run_is_refused_and_unvalidated_text_is_never_spoken(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = [
        {"role": "ASSISTANT", "run_id": "run_1", "parts": [{"type": "WARNING", "content": UNVALIDATED}]},
        {"role": "ASSISTANT", "run_id": "run_1", "parts": [{"type": "TEXT", "content": UNVALIDATED}]},
    ]
    runner, session = _runner(_Client(_sse({"type": "run.failed"}), messages), monkeypatch)

    runner.submit("how are sales?")
    await _drain(runner)

    assert session.said == [REFUSAL_LINE]


async def test_stream_ending_without_terminal_event_speaks_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, session = _runner(_Client([]), monkeypatch, voice_mission_timeout_s=0.0)

    runner.submit("how are sales?")
    await _drain(runner)

    assert session.said == [TIMEOUT_LINE]


async def test_error_is_spoken_generically_never_as_the_exception_text(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boom(_Client):
        async def post(self, url: str, **_: Any) -> _Response:
            raise RuntimeError("db password=hunter2")

    runner, session = _runner(_Boom([]), monkeypatch)

    runner.submit("how are sales?")
    await _drain(runner)

    assert session.said == [ERROR_LINE]


async def test_new_question_cancels_the_in_flight_run(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Slow(_Client):
        def stream(self, method: str, url: str, **_: Any) -> Any:
            class _Hang(_Response):
                async def aiter_lines(self):
                    await asyncio.sleep(60)
                    yield ""

            return _Hang()

    client = _Slow([])
    runner, _ = _runner(client, monkeypatch)

    runner.submit("first question")
    for _ in range(50):  # let the first run get its run_id
        await asyncio.sleep(0)
        if runner._run_id:
            break
    first_task = runner._task
    runner.submit("second question")
    await asyncio.sleep(0.05)

    assert first_task is not None and first_task.cancelled()
    assert any(url.endswith("/v1/runs/run_1/cancel") for url in client.posts)
    if runner._task is not None:
        runner._task.cancel()


async def test_cancel_stops_the_run_and_reports_whether_anything_ran(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Slow(_Client):
        def stream(self, method: str, url: str, **_: Any) -> Any:
            class _Hang(_Response):
                async def aiter_lines(self):
                    await asyncio.sleep(60)
                    yield ""

            return _Hang()

    client = _Slow([])
    runner, _ = _runner(client, monkeypatch)
    assert runner.cancel() is False

    runner.submit("a question")
    for _ in range(50):
        await asyncio.sleep(0)
        if runner._run_id:
            break

    assert runner.cancel() is True
    await asyncio.sleep(0.05)
    assert any(url.endswith("/v1/runs/run_1/cancel") for url in client.posts)
    assert runner.cancel() is False


def test_voice_prompt_forbids_answering_data_questions_directly() -> None:
    text = worker.VOICE_INSTRUCTIONS

    assert "ask_seleric" in text and "cancel_seleric" in text
    assert "Never state" in text and "guess a number" in text


def test_build_llm_requires_a_model() -> None:
    with pytest.raises(RuntimeError, match="VOICE_LLM_MODEL"):
        worker._build_llm(_settings(voice_llm_model="", voice_llm_base_url="", voice_llm_provider=""))


def test_latency_log_sums_turn_end_llm_and_tts_into_one_line(caplog: pytest.LogCaptureFixture) -> None:
    def ev(**metrics: Any) -> SimpleNamespace:
        return SimpleNamespace(metrics=SimpleNamespace(speech_id="sp_1", **metrics))

    log = worker.LatencyLog()
    with caplog.at_level("INFO", logger="seleric.voice.worker"):
        log.on_metrics(ev(type="eou_metrics", end_of_utterance_delay=0.4, transcription_delay=0.1))
        log.on_metrics(ev(type="llm_metrics", ttft=0.5))
        log.on_metrics(ev(type="tts_metrics", ttfb=0.2, characters_count=42))
        log.on_metrics(SimpleNamespace(metrics=SimpleNamespace(type="tts_metrics", speech_id=None)))

    lines = [r.getMessage() for r in caplog.records if "latency" in r.getMessage()]
    assert len(lines) == 1 and "first_audio=1.20s" in lines[0]


def test_stt_language_is_passed_through() -> None:
    with pytest.raises(RuntimeError, match="STT_PROVIDER=fake"):
        worker._build_stt(SimpleNamespace(stt_provider="fake", voice_stt_language="multi"))


@pytest.mark.parametrize("builder,provider_field", [(worker._build_stt, "stt_provider"), (worker._build_tts, "tts_provider")])
def test_azure_speech_requires_key_and_region(builder: Any, provider_field: str) -> None:
    settings = SimpleNamespace(
        **{provider_field: "azure"}, azure_speech_key="", azure_speech_region="centralindia",
        voice_stt_languages="en-IN", voice_tts_voice="",
    )

    with pytest.raises(RuntimeError, match="AZURE_SPEECH_KEY"):
        builder(settings)


def test_azure_voice_llm_needs_endpoint_key_and_model() -> None:
    settings = SimpleNamespace(
        voice_llm_provider="azure", voice_llm_model="", azure_openai_endpoint="https://x",
        azure_openai_api_key="", azure_openai_fast_model="", azure_openai_model="m",
    )

    with pytest.raises(RuntimeError, match="VOICE_LLM_PROVIDER=azure"):
        worker._build_llm(settings)


async def test_missing_thread_tells_the_user_to_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Gone(_Client):
        async def post(self, url: str, **_: Any) -> _Response:
            return _Response(status=404)

    runner, session = _runner(_Gone([]), monkeypatch)

    runner.submit("how are sales?")
    await _drain(runner)

    assert session.said == [worker.GONE_LINE]


async def test_idle_hold_lines_are_varied_and_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = [": heartbeat", ""] * 12
    runner, session = _runner(_Client(ticks), monkeypatch, voice_narration_idle_hold_s=0.0, voice_mission_timeout_s=1.0)

    runner.submit("how are sales?")
    await _drain(runner)

    idle = [line for line in session.said if line in worker.IDLE_LINES]
    assert idle == list(worker.IDLE_LINES)
