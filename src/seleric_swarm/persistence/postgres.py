from __future__ import annotations

from typing import Any

from seleric_swarm.contracts.lookup import MissionResult
from seleric_swarm.persistence.memory import InMemoryMissionStore


class PostgresMissionStore:
    """Optional durable store. V1 tests use InMemoryMissionStore."""

    def __init__(self, database_url: str) -> None:
        from sqlalchemy import create_engine, text

        self._engine = create_engine(database_url)
        self._text = text

    def put(self, result: MissionResult, raw_state: dict[str, Any] | None = None) -> None:
        payload = result.model_dump()
        with self._engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    INSERT INTO missions (mission_id, user_query, normalized_query, status, mission_lead, active_specialist)
                    VALUES (:mission_id, :user_query, :normalized_query, :status, :mission_lead, :active_specialist)
                    ON CONFLICT (mission_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        mission_lead = EXCLUDED.mission_lead,
                        active_specialist = EXCLUDED.active_specialist,
                        updated_at = NOW()
                    """
                ),
                {
                    "mission_id": result.mission_id,
                    "user_query": (raw_state or {}).get("user_query", ""),
                    "normalized_query": result.query_class,
                    "status": result.status,
                    "mission_lead": result.mission_lead,
                    "active_specialist": result.active_specialist,
                },
            )
            for evidence in result.evidence:
                conn.execute(
                    self._text(
                        """
                        INSERT INTO evidence_artifacts (
                            evidence_id, mission_id, source, metric_or_fact, value_json, dimensions, provenance, quality_flags
                        ) VALUES (
                            :evidence_id, :mission_id, :source, :metric_or_fact, CAST(:value_json AS JSONB),
                            CAST(:dimensions AS JSONB), CAST(:provenance AS JSONB), CAST(:quality_flags AS JSONB)
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
                            claim_id, mission_id, claim_type, text, support_refs, contradiction_refs, trust_label, gate_status
                        ) VALUES (
                            :claim_id, :mission_id, :claim_type, :text, CAST(:support_refs AS JSONB),
                            CAST(:contradiction_refs AS JSONB), :trust_label, :gate_status
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
        self._last = payload

    def get(self, mission_id: str) -> MissionResult | None:
        with self._engine.begin() as conn:
            row = conn.execute(
                self._text("SELECT mission_id, status FROM missions WHERE mission_id = :id"),
                {"id": mission_id},
            ).mappings().first()
        if not row:
            return None
        cached = getattr(self, "_results", {}).get(mission_id)
        if cached:
            return cached
        return None


def _json(value: Any) -> str:
    import json

    return json.dumps(value)


def build_store(backend: str, database_url: str) -> InMemoryMissionStore | PostgresMissionStore:
    if backend == "postgres":
        return PostgresMissionStore(database_url)
    return InMemoryMissionStore()
