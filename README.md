# Seleric Intelligence Swarm

Production-oriented initialization scaffold for a dynamic multi-agent business intelligence system.

## Architectural idea

The system is not a flat group chat of agents. It is a **mission swarm** with:

- **Swarm Coordinator / Mission Controller** for decomposition, routing, termination and arbitration.
- **Domain leaders** for Performance, Commerce, Funnel, Finance, Inventory, Procurement and Technical domains.
- **Intelligence specialists** for Observation, Anomaly Detection, Diagnosis, Prediction, Strategy and Skeptic review.
- **Deterministic intelligence services** for metrics, statistics, anomaly ML, forecasting ML, causal inference (DoWhy), scenario simulation and business rules.
- **A2A** for agent-to-agent interoperability and handoffs.
- **MCP** for agent-to-tool/data access.
- **LangGraph** for internal stateful orchestration, checkpointing and workflow control.
- **Evidence Ledger + Claim Gate** so unsupported conclusions do not reach the user.

## Golden rule

> LLMs decide what to investigate and how to interpret evidence. Data systems, statistical engines, ML models and causal tools produce or validate the evidence.

## Repository map

```text
seleric-swarm-init/
├── README.md
├── QUICKSTART.md
├── pyproject.toml
├── .env.example
├── docker-compose.yml
├── Makefile
├── config/
├── contracts/
├── diagrams/
├── docs/
├── schemas/
├── src/seleric_swarm/
├── tests/
└── .cursor/rules/
```

## Start here

1. Read `docs/00_PROJECT_CHARTER.md`.
2. Read `docs/01_SYSTEM_ARCHITECTURE.md`.
3. Read `docs/02_AGENT_CATALOG.md` and `docs/04_DYNAMIC_LEADERSHIP.md`.
4. Configure your MCP endpoints in `.env` and `config/mcp_servers.yaml`.
5. Define canonical metrics in `config/metric_registry.yaml`.
6. Run the Phase 1 implementation plan in `docs/22_IMPLEMENTATION_ROADMAP.md`.

## Recommended implementation order

**Coordinator + Observer + Evidence Ledger + three domain agents first.** Add anomaly/diagnostic/ML/DoWhy only after metric correctness and provenance are reliable.

## Non-goals for v0

- Autonomous write actions to ad accounts, finance systems or inventory systems.
- Allowing arbitrary agent-to-agent natural-language conversations without schemas.
- Treating LLM self-reported confidence as calibrated confidence.
- Treating DoWhy as an automatic root-cause oracle without a causal graph and assumptions.

## Current reference baseline

The scaffold assumes Python 3.11+ and is designed around current A2A, MCP, LangGraph and DoWhy concepts. Dependency versions should be locked after a compatibility test in your environment.
