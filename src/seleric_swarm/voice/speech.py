"""One voice, one utterance at a time.

Every spoken line of a voice session goes through ``SpeechQueue``: answers wait their turn in the order they were queued
(a multi-chunk answer is never split by another), status chatter is dropped when anything is speaking or waiting (by the
time it would play it is stale), and the next utterance starts only after the previous one finished playing.

The user can talk over speech: LiveKit then stops the line, and the rest of that answer and everything queued is dropped
so the agent listens and carries on with the new question. "Never mind" does the same through ``stop()``.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

UTTERANCE_TIMEOUT_S = 90.0  # a handle that never reports playout must not block every later answer


def _closing(err: Exception) -> bool:
    return any(word in str(err).lower() for word in ("closing", "closed"))


class SpeechQueue:
    def __init__(
        self,
        session: Any,
        *,
        interruptible: bool = True,
        utterance_timeout_s: float = UTTERANCE_TIMEOUT_S,
        on_start: Callable[[str], None] | None = None,
    ) -> None:
        self._session = session
        self._interruptible = interruptible
        self._timeout = utterance_timeout_s
        self._on_start = on_start
        self._items: deque[tuple[list[str], str | None]] = deque()
        self._task: asyncio.Task[None] | None = None
        self._current: Any = None
        self._epoch = 0

    @property
    def busy(self) -> bool:
        return bool(self._items) or (self._task is not None and not self._task.done())

    def speak(self, chunks: list[str], *, narration: bool = False, broadcast: str | None = None) -> bool:
        """Queue one utterance (its chunks play back to back). Narration is dropped, and False returned, when busy."""
        chunks = [c for c in chunks if c.strip()]
        if not chunks or (narration and self.busy):
            return False
        self._items.append((chunks, broadcast))
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._pump())
        return True

    def stop(self) -> None:
        """Drop everything waiting and silence what is playing."""
        self._epoch += 1
        self._items.clear()
        self._interrupt(self._current)

    async def wait_idle(self) -> None:
        while self.busy:
            task = self._task
            if task is None:
                return
            await asyncio.shield(task)

    def _interrupt(self, handle: Any) -> None:
        if handle is None:
            return
        try:
            handle.interrupt(force=True)
        except TypeError:
            handle.interrupt()
        except Exception as err:
            logger.debug(f"speech interrupt failed: {err}")

    async def _pump(self) -> None:
        while self._items:
            chunks, broadcast = self._items.popleft()
            epoch = self._epoch
            if broadcast and self._on_start:
                self._on_start(broadcast)
            for chunk in chunks:
                if epoch != self._epoch:
                    break
                if not await self._play(chunk):  # the session is closing: nothing more will ever play
                    self._items.clear()
                    return

    async def _play(self, text: str) -> bool:
        try:
            handle = self._session.say(text, allow_interruptions=self._interruptible)
        except Exception as err:
            if _closing(err):
                return False
            logger.warning(f"session.say error: {err}")
            return True
        wait = getattr(handle, "wait_for_playout", None)
        if wait is None:
            return True
        self._current = handle
        try:
            await asyncio.wait_for(wait(), timeout=self._timeout)
        except TimeoutError:
            logger.warning("speech did not finish playing in time; moving on")
            self._interrupt(handle)
        except Exception as err:
            logger.warning(f"speech playout wait failed: {err}")
        else:
            if getattr(handle, "interrupted", False):  # the user spoke over it: drop the rest, listen to the new question
                self._epoch += 1
                self._items.clear()
        finally:
            self._current = None
        return True
