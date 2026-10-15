"""Evidence ports register by their ``kind`` (lever, price, stock, incident)."""

from __future__ import annotations

from seleric_swarm.diagnosis.domain.registry import Registry
from seleric_swarm.diagnosis.ports.evidence import EvidencePort

EVIDENCE: Registry[EvidencePort] = Registry("evidence port")
register_evidence = EVIDENCE.register
