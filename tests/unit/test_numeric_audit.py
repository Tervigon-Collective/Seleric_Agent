from seleric_swarm.services.numeric_audit import unaudited_numbers


def test_numeric_audit_allows_evidence_values():
    evidence = [{"value": 125000.5, "time_range": {"start": "2026-08-01", "end": "2026-08-01"}}]
    text = "Net sales were 125000.5 INR on 2026-08-01 (EV-abc123)."
    assert unaudited_numbers(text, evidence) == []


def test_numeric_audit_detects_hallucinated_amount():
    evidence = [{"value": 125000.5, "time_range": {"start": "2026-08-01"}}]
    leaked = unaudited_numbers("Net sales were 999999 INR", evidence)
    assert "999999" in leaked
