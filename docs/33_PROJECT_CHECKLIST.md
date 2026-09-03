# 33 - Project Initialization Checklist

## Before coding

- [ ] Approve architecture and ADRs.
- [ ] Select initial three domain agents.
- [ ] Inventory MCP servers and capabilities.
- [ ] Define 15-25 canonical metrics.
- [ ] Define mission/evidence storage.
- [ ] Select LLM provider abstraction.
- [ ] Define authentication/secrets approach.
- [ ] Build historical incident benchmark set.

## Before anomaly phase

- [ ] Metric history quality reviewed.
- [ ] Seasonality requirements classified.
- [ ] Detector evaluation metrics agreed.

## Before causal phase

- [ ] First causal graphs reviewed by domain owners.
- [ ] Confounder assumptions documented.
- [ ] Refutation policy agreed.

## Before prediction phase

- [ ] Feature registry exists.
- [ ] Leakage tests exist.
- [ ] Model registry and drift checks exist.

## Before production

- [ ] Security review.
- [ ] Load/latency tests.
- [ ] Incident runbook tested.
- [ ] Audit/replay verified.
- [ ] Write actions disabled.
