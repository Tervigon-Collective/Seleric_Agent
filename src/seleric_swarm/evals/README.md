# evals — behavioural gate (M0.4)

Purpose: run golden cases against the mission pipeline and fail a build when a
metric moves the wrong way. One behaviour, two modes:

- **replay** (CI): recorded MCP responses, deterministic. `run_suite(cases, "replay")`.
- **live** (Jenkins): real MCP and model, truth values queried from the
  catalogue. `run_suite(cases, "live")`.
- **record** (dev): run live and write the MCP cassette back for the next
  replay run. `run_suite(cases, "record", cassette_dir=..., suite=...)`.

## Interface

- `run_suite(cases, mode, *, suite="golden", runner=None, mcp_client=None,
  cassette_dir="eval/cassettes", baseline_path=None, out_dir=None, ...) -> Report`
- `load_cases(source) -> list[EvalCase]` — a file, directory, or list of either.
- `Report` — overall and per-tag metrics, plus the baseline comparison.
- Cassette artefacts: `Cassette`, `RecordingMcpClient`, `ReplayMcpClient`,
  `CassetteMiss`.
- `render_markdown(report)` / `write_report(report, out_dir)`.

`runner` is the injection seam: an async callable `(EvalCase) -> ObservedCase`.
Tests pass a fake; the default drives `run_v3_mission` (until
`pipeline.run_mission` lands, at which point only `_runner.LegacyMissionRunner`
changes).

## Case schema (`eval/golden/*.yaml`)

```yaml
cases:
  - id: lookup-net-sales-yesterday
    as_of: "2026-09-03"
    turns: ["What were net sales yesterday?"]
    tags: [lookup]
    expect:
      status: completed
      spec: {kind: analysis, measures: [{metric_id: metric.net_sales}]}
      values:
        - label: net sales
          truth: {metric_id: metric.net_sales}
          value: 123456.78        # recorded truth (replay); omitted for live
          tolerance: 0.01
      must_mention: []
      forbid: ["I cannot"]
```

Truth is `metrics_query` arguments, never SQL, so a case follows the catalogue.

`expect.spec` is a *partial* spec compared field-by-field (`_scorer.spec_matches`):
an omitted field is not scored, dicts are recursive subsets, and lists
(`measures`, `entities`, `windows`) match element-wise in order. Once the
pipeline surfaces a real spec (M1.2), `eval/golden/spec_shapes.yaml` exercises
the Phase 1 shapes ported from `tests/fixtures/query_specs/th383_and_regressions.jsonl`
(`entity_comparison`, `period_comparison`, `lookup`, `why_single_metric`,
`breakdown`, and a follow-up that keeps its windows).

## Metrics

Higher is better: `spec_accuracy`, `status_ok`, `value_correctness`.
Lower is better: `revisions`, `latency_ms`, `llm_calls`, `tokens`. A metric is
`n/a` when no case asks about it; `n/a` is never compared to a baseline.

## Failure policy

- A replay MCP miss raises `CassetteMiss` (a case never passes on unrecorded
  traffic).
- A mission error is captured on `ObservedCase.error` and reported, not raised.
- `--compare` exits non-zero when any comparable metric is worse than the
  baseline.
- Eval cases and verified examples must stay separate (M1.3): before running,
  the harness loads example ids from `eval/verified` and raises `ValueError`
  when a case id is also an example id, so a scored case can never double as a
  trusted exemplar. Override the store with `run_suite(..., examples_dir=...)`.

## Flag

None. The harness is additive and is gated by the CI replay job and the Jenkins
live job, not a runtime `off|shadow|enforce` flag.

## Tests

```bash
uv run pytest tests/unit/evals -q
```

## Known gaps (until the producers land)

- `obs.llm_calls` / `obs.tokens` are `None`: the legacy trace does not expose
  them. They fill in with M0.3 / M5.1.
- `obs.spec` falls back to `{}` until the pipeline surfaces a `QuestionSpec`;
  spec-accuracy cases score `n/a` until then (M1.2).
- The 30-question golden set lives outside the repo; `eval/golden/lookup.yaml`
  seeds from the in-repo datasets and `eval/golden/spec_shapes.yaml` covers the
  spec-shape fixtures. Import the full set when the owner provides the harness
  (tracked as an open gap).
