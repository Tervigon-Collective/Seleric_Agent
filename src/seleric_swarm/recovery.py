"""One-shot recovery for expired durable run attempts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from seleric_swarm.conversations.contracts import RunAttemptStatus, RunStatus
from seleric_swarm.conversations.repositories import RunRepository


@dataclass(frozen=True)
class RecoveryResult:
    retryable: int = 0
    failed: int = 0
    already_retryable: int = 0


class RunRecoveryService:
    def __init__(self, runs: RunRepository, *, retry_delay_s: float = 5.0) -> None:
        self._runs = runs
        self._retry_delay_s = retry_delay_s

    def recover_expired(
        self, *, now: datetime | None = None, limit: int = 100
    ) -> RecoveryResult:
        moment = now or datetime.now(UTC)
        retryable = failed = already_retryable = 0
        for attempt in self._runs.list_recoverable(now=moment, limit=limit):
            if attempt.status is RunAttemptStatus.RETRYABLE:
                already_retryable += 1
                continue
            run = self._runs.get(attempt.run_id)
            if run is None:
                continue
            if run.status is RunStatus.CANCELLED:
                self._runs.compare_and_set_attempt(
                    attempt.id,
                    RunAttemptStatus.RUNNING,
                    RunAttemptStatus.CANCELLED,
                    now=moment,
                    error_code="CANCELLED",
                )
                continue
            can_retry = attempt.retryable and attempt.attempt_number < run.max_attempts
            target = RunAttemptStatus.RETRYABLE if can_retry else RunAttemptStatus.FAILED
            updated = self._runs.compare_and_set_attempt(
                attempt.id,
                RunAttemptStatus.RUNNING,
                target,
                now=moment,
                error_code="LEASE_EXPIRED",
                error_message="Worker lease expired before completion.",
            )
            if updated is None:
                continue
            if can_retry:
                retryable += 1
                self._runs.update(
                    run.model_copy(
                        update={
                            "status": RunStatus.QUEUED,
                            "retry_count": run.retry_count + 1,
                            "next_retry_at": moment + timedelta(seconds=self._retry_delay_s),
                        }
                    )
                )
            else:
                failed += 1
                self._runs.update(
                    run.model_copy(update={"status": RunStatus.FAILED, "completed_at": moment})
                )
        return RecoveryResult(retryable, failed, already_retryable)


def main() -> None:
    from seleric_swarm.bootstrap import build_runtime

    parser = argparse.ArgumentParser(description="Recover expired Seleric run attempts")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    runtime = build_runtime()
    if runtime.conversations is None:
        raise RuntimeError("conversation repositories are not configured")
    result = RunRecoveryService(
        runtime.conversations.runs,
        retry_delay_s=runtime.settings.run_retry_delay_s,
    ).recover_expired(limit=args.limit)
    print(
        f"retryable={result.retryable} failed={result.failed} "
        f"already_retryable={result.already_retryable}"
    )


if __name__ == "__main__":
    main()
