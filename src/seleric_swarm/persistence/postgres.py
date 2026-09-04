"""Optional durable Postgres mission store.

Persists the full ``MissionResult`` JSON plus raw control-plane state (including
structured events) so ``GET /v1/missions/{id}`` and ``.../events`` work after
process restart. Requires migration ``002_mission_payload.sql``.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.contracts.lookup import MissionResult
from seleric_swarm.persistence.memory import (
    InMemoryMissionStore,
    extract_events,
    filter_events,
)


class PostgresMissionStore:
    """Durable store backed by the ``missions`` / ``mission_events`` tables."""

    def __init__(self, database_url: str) -> None:
        from sqlalchemy import create_engine, text

        self._engine = create_engine(database_url)
        self._text = text
        self._results: dict[str, MissionResult] = {}
        self._raw: dict[str, dict[str, Any]] = {}

    def put(self, result: MissionResult, raw_state: dict[str, Any] | None = None) -> None:
        # Refuse to clobber cancelled missions (async cancel vs late job completion).
        existing = self._results.get(result.mission_id) or self.get(result.mission_id)
        existing_raw = self._raw.get(result.mission_id) or self.get_raw(result.mission_id)
        if result.status != "cancelled":
            if existing is not None and existing.status == "cancelled":
                return
            if isinstance(existing_raw, dict) and existing_raw.get("status") == "cancelled":
                return

        payload = result.model_dump(mode="json")
        raw = dict(raw_state or {})
        route = str(raw.get("route") or ("swarm" if "artifacts" in raw else "lookup"))
        events = extract_events(raw) or extract_events({"events": getattr(result, "events", None)})
        # Lookup missions may only have events on raw LangGraph state.
        if not events and isinstance(raw.get("events"), list):
            events = [e for e in raw["events"] if isinstance(e, dict)]

        self._results[result.mission_id] = result
        if raw_state is not None:
            self._raw[result.mission_id] = raw_state

        with self._engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    INSERT INTO missions (
                        mission_id, user_query, normalized_query, status, mission_lead,
                        active_specialist, leadership_epoch, route, result_json, raw_json
                    ) VALUES (
                        :mission_id, :user_query, :normalized_query, :status, :mission_lead,
                        :active_specialist, :leadership_epoch, :route,
                        CAST(:result_json AS JSONB), CAST(:raw_json AS JSONB)
                    )
                    ON CONFLICT (mission_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        mission_lead = EXCLUDED.mission_lead,
                        active_specialist = EXCLUDED.active_specialist,
                        leadership_epoch = EXCLUDED.leadership_epoch,
                        route = EXCLUDED.route,
                        result_json = EXCLUDED.result_json,
                        raw_json = EXCLUDED.raw_json,
                        updated_at = NOW()
                    WHERE missions.status IS DISTINCT FROM 'cancelled'
                       OR EXCLUDED.status = 'cancelled'
                    """
                ),
                {
                    "mission_id": result.mission_id,
                    "user_query": raw.get("user_query") or raw.get("query") or "",
                    "normalized_query": result.query_class,
                    "status": result.status,
                    "mission_lead": result.mission_lead,
                    "active_specialist": result.active_specialist,
                    "leadership_epoch": int(result.leadership_epoch or 0),
                    "route": route,
                    "result_json": _json(payload),
                    "raw_json": _json(raw),
                },
            )

            # Replace event log for this mission (idempotent re-put).
            conn.execute(
                self._text("DELETE FROM mission_events WHERE mission_id = :mission_id"),
                {"mission_id": result.mission_id},
            )
            for event in events:
                conn.execute(
                    self._text(
                        """
                        INSERT INTO mission_events (
                            mission_id, task_id, agent_id, event_type, payload
                        ) VALUES (
                            :mission_id, :task_id, :agent_id, :event_type, CAST(:payload AS JSONB)
                        )
                        """
                    ),
                    {
                        "mission_id": result.mission_id,
                        "task_id": event.get("task_id"),
                        "agent_id": event.get("agent_id") or event.get("by"),
                        "event_type": str(event.get("kind") or "event"),
                        "payload": _json(event),
                    },
                )

            for evidence in result.evidence:
                conn.execute(
                    self._text(
                        """
                        INSERT INTO evidence_artifacts (
                            evidence_id, mission_id, source, metric_or_fact, value_json,
                            dimensions, provenance, quality_flags
                        ) VALUES (
                            :evidence_id, :mission_id, :source, :metric_or_fact,
                            CAST(:value_json AS JSONB), CAST(:dimensions AS JSONB),
                            CAST(:provenance AS JSONB), CAST(:quality_flags AS JSONB)
                        )
                        ON CONFLICT (evidence_id) DO NOTHING
                        """
                    ),
                    {
                        "evidence_id": evidence.evidence_id,
                        "mission_id": result.mission_id,
                        "source": evidence.source,
                        "metric_or_fact": evidence.metric_or_fact,
                        "value_json": _json(evidence.value),
                        "dimensions": _json(evidence.time_range),
                        "provenance": _json(evidence.provenance),
                        "quality_flags": _json([]),
                    },
                )

            for claim in result.claims:
                conn.execute(
                    self._text(
                        """
                        INSERT INTO claims (
                            claim_id, mission_id, claim_type, text, support_refs,
                            contradiction_refs, trust_label, gate_status
                        ) VALUES (
                            :claim_id, :mission_id, :claim_type, :text,
                            CAST(:support_refs AS JSONB), CAST(:contradiction_refs AS JSONB),
                            :trust_label, :gate_status
                        )
                        ON CONFLICT (claim_id) DO UPDATE SET gate_status = EXCLUDED.gate_status
                        """
                    ),
                    {
                        "claim_id": claim.claim_id,
                        "mission_id": result.mission_id,
                        "claim_type": claim.claim_type,
                        "text": claim.text,
                        "support_refs": _json(claim.support_refs),
                        "contradiction_refs": _json([]),
                        "trust_label": claim.trust_label,
                        "gate_status": claim.gate_status,
                    },
                )

            conn.execute(
                self._text("DELETE FROM leadership_transfers WHERE mission_id = :mission_id"),
                {"mission_id": result.mission_id},
            )
            for handoff in result.handoff_history:
                conn.execute(
                    self._text(
                        """
                        INSERT INTO leadership_transfers (
                            mission_id, epoch, from_agent, to_agent, reason, evidence_refs
                        ) VALUES (
                            :mission_id, :epoch, :from_agent, :to_agent, :reason,
                            CAST(:evidence_refs AS JSONB)
                        )
                        """
                    ),
                    {
                        "mission_id": result.mission_id,
                        "epoch": int(handoff.epoch or result.leadership_epoch or 0),
                        "from_agent": handoff.from_agent or "",
                        "to_agent": handoff.to_agent or handoff.requested_target or "",
                        "reason": handoff.reason or "",
                        "evidence_refs": _json(handoff.evidence_refs),
                    },
                )

    def get(self, mission_id: str) -> MissionResult | None:
        if mission_id in self._results:
            return self._results[mission_id]
        with self._engine.begin() as conn:
            row = conn.execute(
                self._text(
                    "SELECT result_json FROM missions WHERE mission_id = :id"
                ),
                {"id": mission_id},
            ).mappings().first()
        if not row or row.get("result_json") is None:
            return None
        data = row["result_json"]
        if isinstance(data, str):
            import json

            data = json.loads(data)
        try:
            result = MissionResult.model_validate(data)
        except Exception:
            return None
        self._results[mission_id] = result
        return result

    def get_raw(self, mission_id: str) -> dict[str, Any] | None:
        if mission_id in self._raw:
            return self._raw[mission_id]
        with self._engine.begin() as conn:
            row = conn.execute(
                self._text("SELECT raw_json, route, result_json FROM missions WHERE mission_id = :id"),
                {"id": mission_id},
            ).mappings().first()
        if not row:
            return None
        raw = row.get("raw_json")
        if isinstance(raw, str):
            import json

            raw = json.loads(raw)
        if isinstance(raw, dict) and raw:
            self._raw[mission_id] = raw
            return raw
        # Fall back to result_json wrapped for lookup missions
        result_json = row.get("result_json")
        if isinstance(result_json, str):
            import json

            result_json = json.loads(result_json)
        if isinstance(result_json, dict):
            wrapped = {"route": row.get("route") or "lookup", **result_json}
            self._raw[mission_id] = wrapped
            return wrapped
        return None

    def list_events(
        self,
        mission_id: str,
        *,
        family: str | None = None,
        after_seq: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        with self._engine.begin() as conn:
            rows = conn.execute(
                self._text(
                    """
                    SELECT payload FROM mission_events
                    WHERE mission_id = :mission_id
                    ORDER BY event_id ASC
                    LIMIT :limit
                    """
                ),
                {"mission_id": mission_id, "limit": max(limit * 2, 500)},
            ).mappings().all()
        events: list[dict[str, Any]] = []
        for row in rows:
            payload = row.get("payload")
            if isinstance(payload, str):
                import json

                payload = json.loads(payload)
            if isinstance(payload, dict):
                events.append(payload)
        if not events:
            events = extract_events(self.get_raw(mission_id))
        return filter_events(events, family=family, after_seq=after_seq, limit=limit)


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)


def build_store(backend: str, database_url: str) -> InMemoryMissionStore | PostgresMissionStore:
    if backend == "postgres":
        return PostgresMissionStore(database_url)
    return InMemoryMissionStore()
