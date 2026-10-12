from seleric_swarm.evals._case import EvalCase
from seleric_swarm.evals._runner import ObservedCase, ObservedEvidence
from seleric_swarm.evals._scorer import (
    aggregate,
    figures_grounded,
    percentile,
    response_figures,
    score_case,
)


def _obs(text, values=(), **kw):
    ev = tuple(ObservedEvidence(metric_id="m", value=v) for v in values)
    return ObservedCase(id="c", status="completed", response_text=text, evidence=ev, **kw)


def _case(**expect):
    return EvalCase.model_validate({"id": "c", "turns": ["q"], "expect": expect})


def test_exact_and_rounded_figures_are_grounded():
    g, bad = figures_grounded(_obs("Net sales were 1,234,567 and AOV 812.46.", [1234567, 812.456]))
    assert g == 1.0 and bad == []


def test_invented_figure_is_ungrounded():
    g, bad = figures_grounded(_obs("Net sales were 1,234,567; orders 98,765.", [1234567]))
    assert g == 0.5 and bad == ["98,765"]


def test_percent_and_suffix_forms_match_the_same_value():
    assert figures_grounded(_obs("CTR was 2.38%.", [0.0238]))[0] == 1.0
    assert figures_grounded(_obs("Spend was 1.2 Cr.", [12_000_000.0]))[0] == 1.0
    assert figures_grounded(_obs("Spend was 12.5K.", [12_500]))[0] == 1.0


def test_years_small_ints_and_user_typed_numbers_are_not_claims():
    assert response_figures("In 2026 the top 5 rose.") == []
    assert response_figures("Compare 5000 vs 6200", ignore_text="compare 5000 now") != []
    g, _ = figures_grounded(_obs("You asked about 5000 units."), question="how many of 5000 units")
    assert g is None


def test_narrative_without_figures_is_unscored_not_a_pass():
    assert figures_grounded(_obs("Sales fell because of mix shift."))[0] is None


def test_mentions_forbid_and_cost_are_scored():
    case = _case(must_mention=["mix", "returns"], forbid=["unavailable"])
    s = score_case(case, _obs("The mix shifted. Data unavailable.", cost_usd=0.02), [])
    assert s.metrics["mentions_ok"] == 0.5
    assert s.metrics["forbid_clean"] == 0.0
    assert s.metrics["cost_usd"] == 0.02
    assert s.details["missing_mentions"] == ["returns"]
    assert score_case(_case(), _obs("x"), []).metrics["mentions_ok"] is None


def test_latency_percentiles_and_cost_in_aggregate():
    assert percentile([], 0.5) is None
    assert percentile([10, 20, 30, 40], 0.5) == 20
    assert percentile([10, 20, 30, 40], 0.95) == 40
    cases = [_case(), _case()]
    scores = [score_case(c, _obs("x", latency_ms=ms, cost_usd=c_), []) for c, ms, c_ in zip(cases, (100.0, 300.0), (0.1, 0.3))]
    agg = aggregate(cases, scores).overall
    assert agg["latency_p50_ms"] == 100.0 and agg["latency_p95_ms"] == 300.0
    assert abs(agg["cost_usd"] - 0.2) < 1e-9


def test_derived_figures_and_negatives_are_grounded():
    # relative change, absolute change and share derived from two evidence values
    text = "Sales rose 12.0% from 1,000,000 to 1,120,000, up by 120,000."
    assert figures_grounded(_obs(text, [1_120_000, 1_000_000]))[0] == 1.0
    # a negative evidence value printed as a magnitude
    assert figures_grounded(_obs("Profit fell by 1,234 versus plan.", [-1234]))[0] == 1.0
    # derived-looking but invented stays ungrounded
    assert figures_grounded(_obs("Sales rose 45.0%.", [1_120_000, 1_000_000]))[0] == 0.0


def test_cost_is_reported_not_gated():
    from seleric_swarm.evals._scorer import HIGHER_IS_BETTER, LOWER_IS_BETTER, REPORT_ONLY

    assert "cost_usd" in REPORT_ONLY and "cost_usd" not in HIGHER_IS_BETTER + LOWER_IS_BETTER
