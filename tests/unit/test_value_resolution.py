"""Value resolution in the agent's core loop (no question-specific tool).

The gateway's ``catalogue_resolve_values`` learns every dimension's values from
Cube; the runner calls it before the model runs. Three guarantees:

* confident matches (exact, non-vocabulary) become ``RequiredScope`` value
  filters, and an answer whose evidence ignores them is sent back (REVISE);
* the model sees the matches as a prompt block, exact ones as what the user
  meant and partial ones as suggestions;
* ``query_metrics`` can filter to several values at once, so "any of these
  spellings" is one query, and resolution failures never break a mission.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.agent.output import MissionResult
from seleric_swarm.agent.runner import _resolve_values, _values_block
from seleric_swarm.agent.scope import RequiredScope, ValueFilter, value_filters_from_resolution
from seleric_swarm.agent.validation import EvidenceValidator
from seleric_swarm.agent.validation.signals import check_scope_coverage
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    ContextBundle,
    Principal,
)
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import semantic

_MISSION = "MS3-values"

# Shape returned by the gateway's catalogue_resolve_values.
RESOLUTION: dict[str, Any] = {
    "status": "ok",
    "brand_id": "20",
    "terms": [
        {
            "term": "acme chat",
            "catalogue_vocabulary": False,
            "best_match": "exact",
            "dimensions": [
                {
                    "dimension": "lt_utm_medium",
                    "view": "order_attribution",
                    "values": [
                        {"value": "acme chat", "volume": 33, "match": "exact"},
                        {"value": "ac", "volume": 13, "match": "abbreviation"},
                    ],
                    "metrics": ["attributed_orders"],
                },
                {
                    "dimension": "utm_medium",
                    "view": "session_funnel",
                    "values": [{"value": "acme chat", "volume": 900, "match": "exact"}],
                    "metrics": ["web_sessions"],
                },
                {
                    "dimension": "landing_page_path",
                    "view": "session_funnel",
                    "values": [{"value": "/?utm_medium=acme+chat", "volume": 8, "match": "token"}],
                    "metrics": [],
                },
            ],
        },
        {  # catalogue vocabulary: shown, never enforced
            "term": "google",
            "catalogue_vocabulary": True,
            "best_match": "exact",
            "dimensions": [
                {"dimension": "lt_platform", "view": "order_attribution",
                 "values": [{"value": "google", "volume": 5, "match": "exact"}], "metrics": []},
            ],
        },
        {  # partial only: shown, never enforced
            "term": "month",
            "catalogue_vocabulary": False,
            "best_match": "token",
            "dimensions": [
                {"dimension": "campaign_name", "view": "ad_channel_pnl",
                 "values": [{"value": "CAT MONTH-7SEP", "volume": 3, "match": "token"}], "metrics": []},
            ],
        },
    ],
}


def test_only_exact_non_vocabulary_matches_become_filters():
    filters = value_filters_from_resolution(RESOLUTION)
    assert filters == (
        ValueFilter(
            term="acme chat",
            dimensions=frozenset({"lt_utm_medium", "utm_medium"}),
            values=("acme chat", "ac"),
        ),
    )


def test_values_block_labels_match_types_for_the_model():
    block = _values_block(RESOLUTION)
    assert block.startswith("[values in the data")
    assert 'lt_utm_medium = acme chat (exact), ac (abbreviation)' in block
    assert "attributed_orders" in block
    assert "CAT MONTH-7SEP (token)" in block  # suggestion, labelled as such
    assert _values_block({"status": "ok", "terms": []}) == ""


class _Mcp:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((capability, arguments))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _Settings:
    value_resolve_timeout_s = 1.0


class _Runtime:
    settings = _Settings()


async def test_resolve_values_calls_the_gateway_with_the_question():
    mcp = _Mcp(RESOLUTION)
    out = await _resolve_values(_Runtime(), mcp, "orders from acme chat")  # type: ignore[arg-type]
    assert out is RESOLUTION
    assert mcp.calls == [("seleric.catalogue_resolve_values", {"text": "orders from acme chat"})]


@pytest.mark.parametrize(
    "response",
    [RuntimeError("mcp down"), {"status": "warming", "terms": []}, "not a dict"],
)
async def test_resolve_values_fails_open(response):
    assert await _resolve_values(_Runtime(), _Mcp(response), "q") == {}  # type: ignore[arg-type]


# --- coverage: a named value must constrain the evidence -----------------------


def _evidence(store: InMemoryArtifactStore, *, day: int, dimensions: dict[str, str]) -> None:
    stamp = datetime(2026, 9, day, tzinfo=UTC)
    payload = EvidenceArtifact(
        metric_id="attributed_orders",
        dimensions=dimensions,
        grain="day",
        as_of=stamp,
        period_start=stamp,
        period_end=stamp,
        value=10.0 + day,
        source_query={"measure": "attributed_orders"},
    )
    store.put(
        Artifact(
            workspace_id="ws1",
            artifact_type="evidence",
            payload=payload.model_dump(mode="json"),
            classification="factual",
            evidence_ids=[f"raw:attributed_orders:{day}"],
            provenance=ArtifactProvenance(query_version="q1"),
            mission_id=_MISSION,
        )
    )


_SCOPE = RequiredScope(value_filters=value_filters_from_resolution(RESOLUTION))


def test_unfiltered_total_does_not_cover_a_named_value():
    store = InMemoryArtifactStore()
    for d in range(1, 6):
        _evidence(store, day=d, dimensions={})
    out = check_scope_coverage(store.list_for_mission(_MISSION), _SCOPE)
    assert out.status == "INSUFFICIENT"
    assert out.gaps[0].blocking
    assert "acme chat" in out.gaps[0].description


def test_filter_on_any_dimension_holding_the_value_covers_it():
    store = InMemoryArtifactStore()
    for d in range(1, 6):
        _evidence(store, day=d, dimensions={"utm_medium": "acme chat,ac"})
    out = check_scope_coverage(store.list_for_mission(_MISSION), _SCOPE)
    assert out.status == "OK"


# Semantic v2: the gateway reports a word its catalogue uses as a filter value (meta, google, …) as
# non-vocabulary, so it becomes a required scope like any other named value — nothing listed here.
_META = {
    "terms": [{
        "term": "meta", "catalogue_vocabulary": False, "best_match": "exact",
        "dimensions": [
            {"dimension": d, "view": "commerce", "values": [{"value": "meta", "volume": 588, "match": "exact"}],
             "metrics": []}
            for d in ("ad_platform", "finance_channel", "platform")
        ],
    }],
}


def test_unscoped_drill_does_not_cover_a_named_platform():
    # live 2026-10-04: "which channels drove our Meta orders" drilled ALL orders by channel
    store = InMemoryArtifactStore()
    _evidence(store, day=1, dimensions={"channel": "ig_feed"})
    _evidence(store, day=2, dimensions={"channel": "google_pmax"})
    out = check_scope_coverage(store.list_for_mission(_MISSION), RequiredScope(value_filters=value_filters_from_resolution(_META)))
    assert out.status == "INSUFFICIENT" and out.gaps[0].blocking and "meta" in out.gaps[0].description


def test_hierarchy_drill_within_the_named_platform_covers_it():
    # drilldown(hierarchy="traffic", within={"platform": "meta"}) keys evidence by within + level
    store = InMemoryArtifactStore()
    _evidence(store, day=1, dimensions={"platform": "meta", "channel": "ig_feed"})
    _evidence(store, day=2, dimensions={"platform": "meta", "channel": "fb_feed"})
    out = check_scope_coverage(store.list_for_mission(_MISSION), RequiredScope(value_filters=value_filters_from_resolution(_META)))
    assert out.status == "OK"


def test_ignored_named_value_forces_revise_end_to_end():
    store = InMemoryArtifactStore()
    for d in range(1, 11):
        _evidence(store, day=d, dimensions={})
    deps = SelericDeps(
        mission_id=_MISSION,
        as_of=datetime.now(UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="t1",
        run_id="r1",
        trace_id="tr1",
        context=ContextBundle(),
        mcp_client=None,
        artifact_store=store,
        limits=ExecutionLimits(),
        required_scope=_SCOPE,
    )
    assert EvidenceValidator().score(deps).verdict == "REVISE"


# live thread_e75c2615: "why was 2026-09-24 lower than OTHER days" — "other" is a payment_method value in the
# data, so the hint made it a required filter and six revisions chased payment_method = other.
_OTHER = {
    "terms": [{
        "term": "other", "catalogue_vocabulary": False, "best_match": "exact",
        "dimensions": [{"dimension": "payment_method", "view": "payments",
                        "values": [{"value": "other", "volume": 3, "match": "exact"}], "metrics": []}],
    }],
}


def test_ordinary_word_listed_in_not_values_is_not_required():
    store = InMemoryArtifactStore()
    for d in range(1, 6):
        _evidence(store, day=d, dimensions={})
    scope = RequiredScope(value_filters=value_filters_from_resolution(_OTHER))
    gated = check_scope_coverage(store.list_for_mission(_MISSION), scope)
    assert gated.status == "INSUFFICIENT" and "not_values" in gated.gaps[0].description
    waived = check_scope_coverage(store.list_for_mission(_MISSION), scope, not_values=["Other"])
    assert waived.status != "INSUFFICIENT" and not waived.gaps
    # Waiving one word never waives another named value.
    both = RequiredScope(value_filters=value_filters_from_resolution(_OTHER) + _SCOPE.value_filters)
    out = check_scope_coverage(store.list_for_mission(_MISSION), both, not_values=["other"])
    assert out.status == "INSUFFICIENT" and "acme chat" in out.gaps[0].description


def test_not_values_reaches_the_gate_through_the_answer():
    store = InMemoryArtifactStore()
    for d in range(1, 11):
        _evidence(store, day=d, dimensions={})
    deps = SelericDeps(
        mission_id=_MISSION,
        as_of=datetime.now(UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="t1",
        run_id="r1",
        trace_id="tr1",
        context=ContextBundle(),
        mcp_client=None,
        artifact_store=store,
        limits=ExecutionLimits(),
        required_scope=RequiredScope(value_filters=value_filters_from_resolution(_OTHER)),
    )
    draft = MissionResult(status="completed", final_response="x", not_values="other")
    assert draft.not_values == ["other"]
    assert EvidenceValidator().score(deps).verdict == "REVISE"
    assert EvidenceValidator().score(deps, result=draft).verdict != "REVISE"


# --- query_metrics: "any of these values" in one query ---------------------------


class _Ctx:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


class _MetricsMcp:
    def __init__(self) -> None:
        self.arguments: list[dict[str, Any]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        self.arguments.append(arguments)
        return {"rows": [{"attributed_orders": 46}], "provenance": {"query_id": "q1"}}


async def test_query_metrics_filters_to_any_of_several_values():
    mcp = _MetricsMcp()
    deps = SelericDeps(
        mission_id=_MISSION,
        as_of=datetime(2026, 9, 25, tzinfo=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="t1",
        run_id="r1",
        trace_id="tr1",
        context=ContextBundle(),
        mcp_client=mcp,
        artifact_store=InMemoryArtifactStore(),
        limits=ExecutionLimits(),
    )
    result = await semantic.query_metrics(
        _Ctx(deps),  # type: ignore[arg-type]
        metric_id="attributed_orders",
        dimensions={"lt_utm_medium": ["acme chat", "ac", "acme chat"]},
        pool_listed_values=True,  # spellings of ONE group: pooled (a bare list splits per value)
    )
    assert result.success is True
    assert not mcp.arguments[0].get("dimensions")
    medium = [f for f in mcp.arguments[0]["filters"] if f["dimension"] == "lt_utm_medium"]
    assert medium == [{"dimension": "lt_utm_medium", "operator": "equals", "values": ["acme chat", "ac"]}]
    stored = deps.artifact_store.list_for_mission(_MISSION)
    evidence = [EvidenceArtifact.model_validate(a.payload) for a in stored if a.artifact_type == "evidence"]
    assert evidence[0].dimensions["lt_utm_medium"] == "acme chat,ac"


def test_single_item_list_is_an_ordinary_filter():
    assert semantic._sanitize_dimensions({"lt_utm_medium": ["acme chat"]}) == {"lt_utm_medium": "acme chat"}
    assert semantic._sanitize_dimensions({"lt_utm_medium": []}) == {"lt_utm_medium": ""}


class _NotFoundMcp:
    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        return {
            "rows": [{"total_orders": 0}],
            "provenance": {"query_id": "q1", "cube_view": "orders_all_channels"},
            "value_not_found": [
                {
                    "dimension": "channel",
                    "view": "orders_all_channels",
                    "values": ["acme chat"],
                    "found_in": [{"dimension": "lt_utm_medium", "view": "order_attribution", "values": ["acme chat"]}],
                }
            ],
        }


async def test_a_value_the_dimension_never_holds_is_not_reported_as_zero():
    deps = SelericDeps(
        mission_id=_MISSION,
        as_of=datetime(2026, 9, 25, tzinfo=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="t1",
        run_id="r1",
        trace_id="tr1",
        context=ContextBundle(),
        mcp_client=_NotFoundMcp(),
        artifact_store=InMemoryArtifactStore(),
        limits=ExecutionLimits(),
    )
    result = await semantic.query_metrics(
        _Ctx(deps),  # type: ignore[arg-type]
        metric_id="total_orders",
        dimensions={"channel": "acme chat"},
    )
    assert result.success is False
    assert result.error_code == "VALUE_NOT_FOUND"
    assert "lt_utm_medium = acme chat" in result.summary
    assert not deps.artifact_store.list_for_mission(_MISSION)  # no "0" evidence written


def test_mission_result_accepts_empty_or_null_lists():
    from seleric_swarm.agent.output import MissionResult

    base = {"mission_id": "m", "status": "completed", "query": "q", "as_of": "2026-09-25T00:00:00Z", "final_response": "ok"}
    assert MissionResult(**base, limitations="").limitations == []
    assert MissionResult(**base, limitations=None).limitations == []
    assert MissionResult(**base, limitations="one caveat").limitations == ["one caveat"]


class _WeeklyMcp:
    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        return {
            "rows": [
                {"product_net_revenue.week": "2026-09-14T00:00:00.000", "report_date": "2026-09-14", "product_net_revenue": 2049.18},
                {"product_net_revenue.week": "2026-09-21T00:00:00.000", "report_date": "2026-09-21", "product_net_revenue": 10589},
            ],
            "provenance": {"query_id": "q1"},
        }


async def test_week_buckets_cut_short_by_the_period_are_labelled_partial():
    deps = SelericDeps(
        mission_id=_MISSION,
        as_of=datetime(2026, 9, 25, tzinfo=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="t1",
        run_id="r1",
        trace_id="tr1",
        context=ContextBundle(),
        mcp_client=_WeeklyMcp(),
        artifact_store=InMemoryArtifactStore(),
        limits=ExecutionLimits(),
    )
    result = await semantic.query_metrics(
        _Ctx(deps),  # type: ignore[arg-type]
        metric_id="product_net_revenue",
        grain="week",
        period_start=datetime(2026, 9, 18, tzinfo=UTC),
        period_end=datetime(2026, 9, 24, tzinfo=UTC),
    )
    assert result.success is True
    assert "(PARTIAL week: only 2026-09-18..2026-09-20)" in result.summary
    assert "(PARTIAL week: only 2026-09-21..2026-09-24)" in result.summary


# --- ordinary words: the understand call names them, the runner drops their terms ----------------

from seleric_swarm.agent.runner import _without_terms


def test_ordinary_word_terms_are_dropped_from_the_resolution():
    # "why was meta lower than other days": the understand call lists "other" as an
    # ordinary word; its value terms leave the resolution, "meta" stays.
    assert [t["term"] for t in _without_terms(_OTHER, frozenset({"other"}))["terms"]] == []
    assert _without_terms(_META, frozenset({"other"})) == _META
    assert _without_terms(_META, frozenset()) is _META


def test_a_phrase_inside_values_names_them_on_the_best_dimension():
    # live 2026-10-08 MS3-2390789248: "from which ads did the Suspender boot sale came from" ranked a global top 10
    # by ad and filtered names in prose (39 of 45) — the phrase never became a filter.
    resolution = {"terms": [{
        "term": "suspender boot", "catalogue_vocabulary": False, "best_match": "contains",
        "dimensions": [
            {"dimension": "product_title", "values": [
                {"value": "Pawveralls Suspender Boots", "match": "contains"},
                {"value": "Pawveralls Pro Suspender Boots", "match": "contains"},
                {"value": "Suspender Boot Socks", "match": "fuzzy"}]},
            {"dimension": "ad_name", "values": [{"value": "TH-445-PROSUSPENDERBOOTS", "match": "contains"}]},
        ],
    }, {
        # words scattered through a value name nothing
        "term": "for with", "catalogue_vocabulary": False, "best_match": "token",
        "dimensions": [{"dimension": "product_title", "values": [
            {"value": "ScrubEase Bath Brush With 80ml Shampoo Dispenser For Dogs", "match": "token"}]}],
    }]}
    assert value_filters_from_resolution(resolution) == (
        ValueFilter(term="suspender boot", dimensions=frozenset({"product_title"}),
                    values=("Pawveralls Suspender Boots", "Pawveralls Pro Suspender Boots")),
    )


def test_question_axes_come_from_outside_the_measure_phrases():
    # live 2026-10-08: "product gross sale" made net ROAS and ad spend product-level
    from seleric_swarm.agent.runner import _outside_measures

    q = "Rank campaigns by net ROAS with ad spend, orders and Product Gross Sale on the P&L"
    out = _outside_measures(q, ["net ROAS", "ad spend", "orders", "product gross sale", "landing page views"])
    assert "product" not in out.lower() and "on the P&L" in out and "campaigns" in out


def test_a_value_of_several_unrelated_families_is_a_hint_and_one_family_still_blocks():
    """'other' is a channel, a platform and a payment method in the data: the gate cannot know which the user
    meant (live 2026-10-09: "other available cost components" forced four revisions on an exact P&L bridge).
    'meta' names members of one family only — a real scope, still blocking."""
    from seleric_swarm.agent.scope import ValueFilter
    from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot

    cat = CatalogueSnapshot(dimension_families=(
        ("channel", "channel", 0), ("acquisition_channel", "channel", 1),
        ("platform", "platform", 0), ("ad_platform", "platform", 2),
    ))
    store = InMemoryArtifactStore()
    for d in range(1, 4):
        _evidence(store, day=d, dimensions={})
    other = RequiredScope(value_filters=(ValueFilter(
        term="other", dimensions=frozenset({"acquisition_channel", "ad_platform", "payment_method"}), values=("other",)),))
    out = check_scope_coverage(store.list_for_mission(_MISSION), other, catalogue=cat)
    assert not any(g.blocking for g in out.gaps)
    meta = RequiredScope(value_filters=(ValueFilter(
        term="meta", dimensions=frozenset({"platform", "ad_platform"}), values=("meta",)),))
    out = check_scope_coverage(store.list_for_mission(_MISSION), meta, catalogue=cat)
    assert any(g.blocking for g in out.gaps)


def test_a_value_weighed_against_the_whole_only_has_to_appear():
    """'Is Meta responsible for the drop' needs the total and Meta's part (regression Q28): with the
    understanding's part_of_whole reading, unfiltered totals beside Meta rows are not a scope violation. A plain
    scoped question still blocks."""
    import dataclasses

    from seleric_swarm.agent.scope import ValueFilter

    store = InMemoryArtifactStore()
    _evidence(store, day=1, dimensions={})
    _evidence(store, day=2, dimensions={"lt_utm_medium": "meta"})
    vf = ValueFilter(term="meta", dimensions=frozenset({"lt_utm_medium"}), values=("meta",))
    scoped = RequiredScope(value_filters=(vf,))
    weighed = dataclasses.replace(scoped, values_weighed_against_whole=True)
    assert not any(g.blocking for g in check_scope_coverage(store.list_for_mission(_MISSION), weighed).gaps)
    only_total = InMemoryArtifactStore()
    _evidence(only_total, day=1, dimensions={})
    assert any(g.blocking for g in check_scope_coverage(only_total.list_for_mission(_MISSION), weighed).gaps)
