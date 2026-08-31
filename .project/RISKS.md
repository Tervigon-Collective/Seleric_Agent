# Risk Register

Mirrors `10_IMPLEMENTATION_PLAN_AND_ACCEPTANCE.md` §11. Update status here
as risks materialize, get mitigated, or resolve — don't edit the source doc,
which is the frozen spec.

| Risk | Probability | Impact | Mitigation | Owner | Status |
|---|---|---|---|---|---|
| Far-field echo / wake reliability fails | Medium | High | USB DSP array, acoustic tuning, push-to-talk fallback | Voice/platform | Open |
| Existing metric can't support an intended insight | High | High | Fail closed; expose as data-gap node; add contract before recommending | Data/ML | Open |
| Goal/owner configuration incomplete | High | High | Seed small approved ontology; admin completeness report | Business owner + data | Open |
| Top-three list noisy/duplicated | Medium | High | Hard eligibility, root key, graph dedupe, golden brief tests | Insight service | Open |
| Forecasting produces unstable alerts | Medium | Medium | Simple baseline, backtest gate, cooldown/hysteresis, per-metric disable | Data/ML | Open |
| Dependency edge mistaken for causal proof | Medium | High | Evidence taxonomy, template wording, `VERIFIED_CAUSAL` requires approval | Ontology steward | Open |
| Rule-based meeting extraction low recall | High | Medium | Start one-to-one corpus, vocabulary/rule updates, optional model later | Meeting team | Open |
| Owner/deadline invented or misresolved | Medium | High | Evidence requirement, null allowed, participant resolution, mandatory review | Meeting team | Open |
| GPU unavailable for local transcription | Medium | Medium | Managed batch STT adapter; CPU smaller model; on-demand GPU later | Platform | Open |
| Vexa/OVOS OSS dependency changes | Medium | Medium | Pin image/version, adapter boundary, SBOM, fallback impl | Platform | Open |
| Appsmith becomes coupled to tables | Medium | Medium | API-only writes; contract tests; no production DB creds | Control plane | Open |
| PostgreSQL task queue can't meet later scale | Low in V1 | Medium | TaskQueue port permits Temporal/broker migration without domain rewrite | Platform | Open |
| Data/model versions not reproducible | Medium | High | Pin config/catalogue/model/feature IDs in every trace; immutable artifacts | All services | Open |
| September integration slips | Medium | High | Build vertical slice first; freeze service contracts; defer optional adapters | Program owner | Open — highest near-term priority |
