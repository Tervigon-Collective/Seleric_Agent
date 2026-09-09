import pytest

from seleric_swarm.services.numeric_audit import allowed_numbers, unaudited_numbers


def test_numeric_audit_allows_evidence_values():
    evidence = [{"value": 125000.5, "time_range": {"start": "2026-08-01", "end": "2026-08-01"}}]
    text = "Net sales were 125000.5 INR on 2026-08-01 (EV-abc123)."
    assert unaudited_numbers(text, evidence) == []


def test_numeric_audit_detects_hallucinated_amount():
    evidence = [{"value": 125000.5, "time_range": {"start": "2026-08-01"}}]
    leaked = unaudited_numbers("Net sales were 999999 INR", evidence)
    assert "999999" in leaked


# ---------------------------------------------------------------------------
# query_text parameter — numbers from the user question are exempt
# ---------------------------------------------------------------------------

def test_query_number_is_allowed_when_query_text_provided():
    """'5' in 'last 5 months' must NOT be flagged when query_text is provided."""
    evidence = [
        {"value": 36.0, "unit": "units", "time_range": {"start": "2026-04-03", "end": "2026-09-03"}},
    ]
    query = "What is the best selling product in the last 5 months"
    prose = "Pawveralls Suspender Boots sold the most units (36) in the last 5 months."
    leaked = unaudited_numbers(prose, evidence, query_text=query)
    assert leaked == [], (
        f"'5' from the query should be exempt from audit, but got leaked={leaked}"
    )


def test_query_number_still_blocks_fabricated_values():
    """Providing query_text must NOT exempt metric values that weren't in evidence."""
    evidence = [{"value": 36.0, "time_range": {"start": "2026-04-03", "end": "2026-09-03"}}]
    query = "What is the best selling product in the last 5 months"
    # 999 is NOT in evidence and NOT in the query — must still be caught
    prose = "Product X sold 999 units in the last 5 months."
    leaked = unaudited_numbers(prose, evidence, query_text=query)
    assert "999" in leaked


def test_multiple_query_numbers_all_allowed():
    """All numbers appearing in the query string should be whitelisted."""
    evidence = [{"value": 100.0, "time_range": {"start": "2026-01-01", "end": "2026-03-31"}}]
    query = "Top 10 products in the last 3 months"
    # Both 10 and 3 come from the query
    prose = "Here are the top 10 products over the last 3 months with 100.0 units each."
    leaked = unaudited_numbers(prose, evidence, query_text=query)
    assert leaked == [], (
        f"query numbers 10 and 3 should both be exempt, leaked={leaked}"
    )


def test_allowed_numbers_without_query_text_unchanged():
    """When no query_text is passed, behaviour is identical to before the change."""
    evidence = [{"value": 42.0, "time_range": {"start": "2026-08-01", "end": "2026-08-01"}}]
    allowed = allowed_numbers(evidence)
    assert "42" in allowed
    # No query numbers injected
    assert "5" not in allowed
    assert "10" not in allowed


@pytest.mark.parametrize(
    "query,prose,should_be_clean",
    [
        # N in "last N months" is a query param — clean
        ("Best sellers last 6 months", "Top sellers in the last 6 months.", True),
        # N in "top N" is a query param — clean
        ("Top 5 channels yesterday", "These are the top 5 channels.", True),
        # A metric value NOT in query or evidence — still caught
        ("Top 5 channels yesterday", "Channel X earned 77777 INR.", False),
    ],
)
def test_query_text_parametric_cases(query, prose, should_be_clean):
    evidence = [{"value": 1000.0, "time_range": {"start": "2026-09-02", "end": "2026-09-02"}}]
    leaked = unaudited_numbers(prose, evidence, query_text=query)
    if should_be_clean:
        assert leaked == [], f"expected no leaks for query={query!r}, prose={prose!r}, got {leaked}"
    else:
        assert leaked, f"expected at least one leaked number for prose={prose!r}"
