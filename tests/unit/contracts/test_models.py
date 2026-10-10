"""Unit tests for contracts.pipeline Part B models."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from seleric_swarm.contracts.pipeline import (
    AnalysisPlan,
    AnswerDocument,
    AnswerDraft,
    ChartBlock,
    Claim,
    Clarification,
    Derivation,
    DeriveStep,
    EngineStep,
    EntitySpec,
    FilterSpec,
    MeasureSpec,
    Paragraph,
    QueryStep,
    QuestionSpec,
    RankingSpec,
    Ref,
    ResultRow,
    ResultSet,
    StageError,
    TableBlock,
    WindowSpec,
    dump,
    fingerprint,
    load,
)


def _window() -> WindowSpec:
    return WindowSpec(role="event", start=date(2026, 10, 1), end=date(2026, 10, 7))


def _question_spec() -> QuestionSpec:
    return QuestionSpec(
        kind="analysis",
        shape="lookup",
        measures=(MeasureSpec(phrase="net sales", metric_id="zz_net_sales"),),
        filters=(FilterSpec(dimension="zz_brand", operator="in", values=("zz_acme",)),),
        entities=(EntitySpec(dimension="zz_brand", values=("zz_acme",)),),
        breakdowns=("zz_channel",),
        windows=(_window(),),
        grain="day",
        ranking=RankingSpec(by_metric_id="zz_net_sales", order="desc", limit=5),
        catalogue_version="v-test-1",
    )


def _analysis_plan() -> AnalysisPlan:
    return AnalysisPlan(
        template_id="lookup",
        steps=(
            QueryStep(
                step_id="q1",
                metric_id="zz_net_sales",
                breakdowns=("zz_channel",),
                filters=(),
                window_role="event",
                grain="day",
            ),
            EngineStep(step_id="e1", engine="investigate", params={"depth": 2}),
            DeriveStep(step_id="d1", op="sum", inputs=("q1",), params={}),
        ),
    )


def _result_set() -> ResultSet:
    return ResultSet(
        ref=Ref(kind="result_set", id="rs1"),
        step_id="q1",
        metric_id="zz_net_sales",
        unit="INR",
        window=_window(),
        breakdowns=("zz_channel",),
        filters=(),
        rows=(
            ResultRow(
                evidence_id="ev1",
                dimensions={"zz_channel": "zz_web"},
                value=100.0,
            ),
        ),
    )


def _derivation() -> Derivation:
    return Derivation(
        ref=Ref(kind="derivation", id="der1"),
        op="sum",
        inputs=(Ref(kind="result_set", id="rs1"),),
        params={},
        value=100.0,
        unit="INR",
    )


def _answer_document() -> AnswerDocument:
    draft = AnswerDraft(
        status="completed",
        claims=(
            Claim(
                claim_id="c1",
                ref=Ref(kind="evidence", id="ev1"),
                label="Net sales",
                role="headline",
            ),
        ),
        blocks=(
            Paragraph(kind="paragraph", text="Net sales were {c1}."),
            TableBlock(
                kind="table",
                result_set=Ref(kind="result_set", id="rs1"),
                columns=("zz_channel", "value"),
            ),
            ChartBlock(
                kind="chart",
                refs=(Ref(kind="result_set", id="rs1"),),
                chart_type="bar",
                title="By channel",
            ),
        ),
    )
    return AnswerDocument(draft=draft, rendered_markdown="Net sales were 100.", evidence_ids=("ev1",))


@pytest.mark.parametrize(
    "factory",
    [
        _question_spec,
        _analysis_plan,
        _result_set,
        _derivation,
        _answer_document,
        lambda: Clarification(reason="ambiguous", question_to_user="Which brand?"),
        lambda: StageError(stage="derive", code="NON_ADDITIVE", message="not additive"),
        lambda: Ref(kind="finding", id="f1"),
    ],
)
def test_round_trip(factory: object) -> None:
    model = factory()  # type: ignore[operator]
    restored = load(dump(model), type(model))
    assert restored == model
    assert fingerprint(restored) == fingerprint(model)


def test_fingerprint_is_sha256_hex() -> None:
    fp = fingerprint(_question_spec())
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_changes_when_field_changes() -> None:
    a = _question_spec()
    b = a.model_copy(update={"catalogue_version": "v-test-2"})
    assert fingerprint(a) != fingerprint(b)


def test_load_from_dict() -> None:
    model = _question_spec()
    restored = load(model.model_dump(mode="json"), QuestionSpec)
    assert restored == model


def test_load_from_bytes() -> None:
    model = StageError(stage="plan", code="NO_TEMPLATE", message="none")
    restored = load(dump(model).encode("utf-8"), StageError)
    assert restored == model


@pytest.mark.parametrize(
    "factory",
    [
        _question_spec,
        _analysis_plan,
        _result_set,
        _derivation,
        _answer_document,
        lambda: Clarification(reason="x", question_to_user="y"),
        lambda: StageError(stage="a", code="b", message="c"),
        lambda: WindowSpec(role="baseline", start=date(2026, 1, 1), end=date(2026, 1, 31)),
        lambda: FilterSpec(dimension="zz_d", operator="not_in", values=("zz_x",)),
        lambda: EntitySpec(dimension="zz_d", values=("zz_y",)),
        lambda: MeasureSpec(phrase="p", metric_id=None),
        lambda: RankingSpec(by_metric_id="zz_m", order="asc", limit=1),
        lambda: QueryStep(
            step_id="s",
            metric_id="zz_m",
            breakdowns=(),
            filters=(),
            window_role="context",
            grain=None,
        ),
        lambda: EngineStep(step_id="s", engine="explore", params={}),
        lambda: DeriveStep(step_id="s", op="delta", inputs=(Ref(kind="result_set", id="r"),)),
        lambda: ResultRow(evidence_id="e", dimensions={}, value=None),
        lambda: Claim(
            claim_id="c",
            ref=Ref(kind="derivation", id="d"),
            label="l",
            role="change",
        ),
        lambda: Paragraph(kind="paragraph", text="{c}"),
        lambda: TableBlock(
            kind="table",
            result_set=Ref(kind="result_set", id="r"),
            columns=(),
        ),
        lambda: ChartBlock(kind="chart", refs=(), chart_type="line", title="t"),
        lambda: AnswerDraft(status="failed", claims=(), blocks=()),
    ],
)
def test_extra_forbid(factory: object) -> None:
    model = factory()  # type: ignore[operator]
    payload = model.model_dump(mode="json")
    payload["unexpected_field"] = "nope"
    with pytest.raises(ValidationError):
        type(model).model_validate(payload)


@pytest.mark.parametrize(
    "factory",
    [
        _question_spec,
        _analysis_plan,
        _result_set,
        _derivation,
        _answer_document,
        lambda: Clarification(reason="x", question_to_user="y"),
        lambda: StageError(stage="a", code="b", message="c"),
    ],
)
def test_frozen(factory: object) -> None:
    model = factory()  # type: ignore[operator]
    field = next(iter(type(model).model_fields))
    with pytest.raises(ValidationError):
        setattr(model, field, getattr(model, field))
