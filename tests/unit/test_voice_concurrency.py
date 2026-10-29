"""Voice: a new question runs in the background while the current one is spoken, and speech never overlaps.

The fake session's ``say`` returns a handle that finishes later and records any ``say`` made while an earlier handle is
still playing: the queue must never do that. No LiveKit, no network."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Self

import pytest

from seleric_swarm.voice import worker
from seleric_swarm.voice.speech import SpeechQueue
from seleric_swarm.voice.worker import VoicePrincipal, VoiceTurnRunner

PRINCIPAL = VoicePrincipal(workspace_id="ws_1", user_id="user_1", thread_id="thr_1")


class Handle:
    def __init__(self, play_s: float, hang: bool = False) -> None:
        self.done = asyncio.Event()
        self.interrupted_with: Any = None
        self.interrupted = False
        self._hang = hang
        self._task = None if hang else asyncio.get_running_loop().call_later(play_s, self.done.set)

    async def wait_for_playout(self) -> None:
        await self.done.wait()

    def interrupt(self, *, force: bool = False) -> Handle:
        self.interrupted_with = force
        self.done.set()
        return self


class Session:
    """Records what was said, in order, and any ``say`` that began while an earlier one was still playing."""

    def __init__(self, play_s: float = 0.02, hang_on: str | None = None, closing_on: str | None = None) -> None:
        self.play_s, self.hang_on, self.closing_on = play_s, hang_on, closing_on
        self.said: list[str] = []
        self.overlaps: list[str] = []
        self.interruptible: list[Any] = []
        self.handles: list[Handle] = []

    def say(self, text: str, *, allow_interruptions: Any = None, **_: Any) -> Handle:
        if self.closing_on and self.closing_on in text:
            raise RuntimeError("AgentSession is closing")
        if any(not h.done.is_set() for h in self.handles):
            self.overlaps.append(text)
        self.said.append(text)
        self.interruptible.append(allow_interruptions)
        handle = Handle(self.play_s, hang=bool(self.hang_on and self.hang_on in text))
        self.handles.append(handle)
        return handle


async def _idle(queue: SpeechQueue) -> None:
    await asyncio.wait_for(queue.wait_idle(), timeout=5)


# ---- the speech queue --------------------------------------------------------------------------------------------

async def test_answers_are_spoken_one_at_a_time_in_order() -> None:
    s = Session()
    q = SpeechQueue(s)
    q.speak(["first answer"])
    q.speak(["second answer"])
    q.speak(["third answer"])
    await _idle(q)
    assert s.said == ["first answer", "second answer", "third answer"] and s.overlaps == []


async def test_a_multi_chunk_answer_is_never_split_by_another_answer() -> None:
    s = Session()
    q = SpeechQueue(s)
    q.speak(["A one.", "A two.", "A three."])
    await asyncio.sleep(0.01)  # A one is playing
    q.speak(["B one."])
    await _idle(q)
    assert s.said == ["A one.", "A two.", "A three.", "B one."] and s.overlaps == []


async def test_status_chatter_is_dropped_while_anything_is_speaking_or_waiting() -> None:
    s = Session()
    q = SpeechQueue(s)
    q.speak(["the answer"])
    q.speak(["Pulling the numbers…"], narration=True)  # busy: stale by the time it would play
    await _idle(q)
    q.speak(["Comparing periods…"], narration=True)  # idle: spoken
    await _idle(q)
    assert s.said == ["the answer", "Comparing periods…"]


async def test_speech_is_interruptible_and_an_interrupt_drops_the_rest() -> None:
    s = Session(play_s=0.05)
    q = SpeechQueue(s)
    q.speak(["first part", "second part"])
    q.speak(["next answer"])
    await asyncio.sleep(0.01)
    s.handles[0].interrupted = True  # LiveKit marks the handle when the user talks over it
    s.handles[0].done.set()
    await _idle(q)
    assert s.interruptible == [True] and s.said == ["first part"]


async def test_a_handle_that_never_finishes_does_not_block_the_queue() -> None:
    s = Session(hang_on="stuck")
    q = SpeechQueue(s, utterance_timeout_s=0.05)
    q.speak(["stuck answer"])
    q.speak(["next answer"])
    await _idle(q)
    assert s.said == ["stuck answer", "next answer"]


async def test_a_closing_session_drains_quietly() -> None:
    s = Session(closing_on="late")
    q = SpeechQueue(s)
    q.speak(["late answer"])
    q.speak(["later answer"])
    await _idle(q)
    assert s.said == []  # nothing spoke, nothing raised, the queue is empty


async def test_stop_drops_what_is_waiting_and_silences_what_is_playing() -> None:
    s = Session(play_s=5)
    q = SpeechQueue(s)
    q.speak(["long answer"])
    q.speak(["queued answer"])
    await asyncio.sleep(0.01)
    q.stop()
    await _idle(q)
    assert s.said == ["long answer"] and s.handles[0].interrupted_with is True


# ---- the runner: several missions at once ------------------------------------------------------------------------

def _sse(*events: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for seq, event in enumerate(events, start=1):
        lines += [f"id: {seq}", f"event: {event['type']}", "data: " + json.dumps({"sequence": seq, **event}), ""]
    return lines


class _Resp:
    def __init__(self, body: Any = None, lines: list[str] | None = None, gate: asyncio.Event | None = None) -> None:
        self.status_code, self.is_success, self.text = 200, True, ""
        self._body, self._lines, self._gate = body, lines or [], gate

    def json(self) -> Any:
        return self._body

    async def aiter_lines(self):
        if self._gate is not None:
            await self._gate.wait()
        for line in self._lines:
            yield line

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class Api:
    """Run n answers 'answer n'; a run's stream stays open until its gate is set."""

    def __init__(self) -> None:
        self.posts: list[str] = []
        self.gates: dict[str, asyncio.Event] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}
        self.queries: list[str] = []
        self.n = 0

    async def post(self, url: str, **kw: Any) -> _Resp:
        self.posts.append(url)
        if url.endswith("/messages"):
            self.n += 1
            run = f"run_{self.n}"
            self.gates[run] = asyncio.Event()
            self.queries.append(kw["json"]["parts"][0]["content"])
            return _Resp(body={"run_id": run})
        return _Resp()

    async def get(self, url: str, **_: Any) -> _Resp:
        return _Resp(body=[{"role": "ASSISTANT", "run_id": f"run_{i}", "parts": [{"type": "TEXT", "content": f"Answer {i}."}]}
                           for i in range(1, self.n + 1)])

    def stream(self, method: str, url: str, **_: Any) -> _Resp:
        run = url.split("/runs/")[1].split("/")[0]
        events = self.events.get(run, [{"type": "run.completed"}])
        return _Resp(lines=_sse(*events), gate=self.gates[run])

    def finish(self, run: str) -> None:
        self.gates[run].set()


def _settings(**over: Any) -> SimpleNamespace:
    base = {"seleric_api_url": "http://x", "api_key": "k", "voice_mission_timeout_s": 5.0, "voice_narration_min_gap_s": 0.0,
            "voice_narration_idle_hold_s": 20.0, "voice_narration_enabled": True}
    return SimpleNamespace(**{**base, **over})


def _runner(api: Api, monkeypatch: pytest.MonkeyPatch, session: Session | None = None, **over: Any):
    monkeypatch.setattr(worker, "_get_voice_client", lambda: api)
    session = session or Session()
    return VoiceTurnRunner(session, PRINCIPAL, _settings(**over)), session


async def _settle(runner: VoiceTurnRunner) -> None:
    await asyncio.wait_for(runner.wait_idle(), timeout=5)


async def _started(api: Api, n: int) -> None:
    for _ in range(200):
        await asyncio.sleep(0)
        if api.n >= n and all(f"run_{i}" in api.gates for i in range(1, n + 1)):
            await asyncio.sleep(0)
            return


async def test_a_new_question_runs_in_the_background_and_does_not_cancel_the_one_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    api = Api()
    runner, session = _runner(api, monkeypatch)
    assert runner.submit("how are sales") == "started"
    await _started(api, 1)
    assert runner.submit("what about returns") == "started"
    await _started(api, 2)
    api.finish("run_1")
    api.finish("run_2")
    await _settle(runner)
    assert not any(u.endswith("/cancel") for u in api.posts)  # the earlier run was left alone
    assert len(session.said) == 2 and session.overlaps == []


async def test_a_second_answer_waits_for_the_first_to_finish_speaking(monkeypatch: pytest.MonkeyPatch) -> None:
    api = Api()
    runner, session = _runner(api, monkeypatch, session=Session(play_s=0.15))
    runner.submit("how are sales")
    await _started(api, 1)
    api.finish("run_1")
    await asyncio.sleep(0.05)  # the first answer is being spoken
    runner.submit("what about returns")
    await _started(api, 2)
    api.finish("run_2")  # finishes while the first is still playing
    await _settle(runner)
    assert len(session.said) == 2 and session.overlaps == []


async def test_answers_that_overlapped_in_time_say_which_question_they_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    api = Api()
    runner, session = _runner(api, monkeypatch)
    runner.submit("how are sales")
    await _started(api, 1)
    runner.submit("what about returns?")
    await _started(api, 2)
    api.finish("run_2")  # the later question finishes first
    await asyncio.sleep(0.05)
    api.finish("run_1")
    await _settle(runner)
    assert session.said[0].startswith("On your question about what about returns:") and "Answer 2." in session.said[0]
    assert session.said[1].startswith("On your question about how are sales:") and "Answer 1." in session.said[1]


async def test_a_lone_question_gets_no_lead_in(monkeypatch: pytest.MonkeyPatch) -> None:
    api = Api()
    runner, session = _runner(api, monkeypatch)
    runner.submit("how are sales")
    await _started(api, 1)
    api.finish("run_1")
    await _settle(runner)
    assert session.said == ["Answer 1."]


async def test_the_same_question_twice_starts_one_run(monkeypatch: pytest.MonkeyPatch) -> None:
    api = Api()
    runner, _ = _runner(api, monkeypatch)
    assert runner.submit("How are sales?") == "started"
    await _started(api, 1)
    assert runner.submit("how are  sales") == "duplicate"
    assert api.n == 1
    api.finish("run_1")
    await _settle(runner)


async def test_too_many_questions_at_once_are_refused_politely(monkeypatch: pytest.MonkeyPatch) -> None:
    api = Api()
    runner, _ = _runner(api, monkeypatch)
    results = [runner.submit(f"question {i}") for i in range(worker.MAX_CONCURRENT_RUNS + 1)]
    assert results[-1] == "busy" and results[:-1] == ["started"] * worker.MAX_CONCURRENT_RUNS
    await runner.cancel_all()


async def test_progress_lines_come_only_from_a_lone_run_and_only_when_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    api = Api()
    api.events["run_1"] = [{"type": "agent.tool_started", "summary": "Comparing periods"}, {"type": "run.completed"}]
    runner, session = _runner(api, monkeypatch)
    runner.submit("how are sales")
    await _started(api, 1)
    api.finish("run_1")
    await _settle(runner)
    assert session.said == ["Comparing periods…", "Answer 1."]

    api2 = Api()
    api2.events["run_1"] = [{"type": "agent.tool_started", "summary": "Comparing periods"}, {"type": "run.completed"}]
    runner2, session2 = _runner(api2, monkeypatch)
    runner2.submit("how are sales")
    await _started(api2, 1)
    runner2.submit("what about returns")  # now two runs: the first stays quiet until its answer
    await _started(api2, 2)
    api2.finish("run_1")
    api2.finish("run_2")
    await _settle(runner2)
    assert "Comparing periods…" not in session2.said


async def test_a_background_run_that_fails_says_so_with_its_lead_in(monkeypatch: pytest.MonkeyPatch) -> None:
    api = Api()
    api.events["run_2"] = [{"type": "run.failed"}]
    runner, session = _runner(api, monkeypatch)
    runner.submit("how are sales")
    await _started(api, 1)
    runner.submit("what about returns")
    await _started(api, 2)
    api.finish("run_2")
    api.finish("run_1")
    await _settle(runner)
    failed = next(t for t in session.said if worker.REFUSAL_LINE in t)
    assert failed.startswith("On your question about what about returns:")


async def test_never_mind_cancels_every_run_and_silences_speech(monkeypatch: pytest.MonkeyPatch) -> None:
    api = Api()
    runner, session = _runner(api, monkeypatch, session=Session(play_s=5))
    runner.submit("how are sales")
    await _started(api, 1)
    runner.submit("what about returns")
    await _started(api, 2)
    api.finish("run_1")
    await asyncio.sleep(0.05)  # run 1's answer is speaking
    assert runner.cancel() is True
    await _settle(runner)
    assert {u for u in api.posts if u.endswith("/cancel")} >= {"http://x/v1/runs/run_2/cancel"}
    assert session.handles[0].interrupted_with is True and runner.cancel() is False


async def test_submit_reports_what_it_did_for_the_voice_model(monkeypatch: pytest.MonkeyPatch) -> None:
    api = Api()
    runner, _ = _runner(api, monkeypatch)
    assert "looking into it" in worker.ask_reply(runner.submit("one"), others_running=False)
    assert "background" in worker.ask_reply("started", others_running=True)
    assert "already" in worker.ask_reply("duplicate", others_running=True)
    assert "too many" in worker.ask_reply("busy", others_running=True).lower()
    await runner.cancel_all()
