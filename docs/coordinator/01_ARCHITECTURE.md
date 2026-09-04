# 01 — Architecture

```mermaid
flowchart TB
    USER["User"] --> INTAKE
    subgraph COORD["Coordinator Control Plane"]
        INTAKE["Request Intake"]
        NORM["Query Normalizer"]
        SEM["Semantic Resolver"]
        COMP["Complexity Classifier"]
        DEC["Problem Decomposer"]
        DAG["Task DAG Builder"]
        CAP["Capability Resolver"]
        TEAM["Team Assembler"]
        LEAD["Leadership Manager"]
        EXEC["Execution Engine"]
        ART["Artifact Manager"]
        EVAL["Evidence Evaluator"]
        REFINE["Decomposition Refiner"]
        FRONT["Causal Frontier"]
        GAP["Evidence Gap Manager"]
        CONFLICT["Conflict Manager"]
        REM["Remediation Manager"]
        COMPLETE["Completion Gate"]
        CLAIM["Claim Selector"]
        SYN["Response Synthesizer"]
    end
    INTAKE --> NORM --> SEM --> COMP --> DEC
    DEC --> DAG --> CAP --> TEAM --> LEAD --> EXEC
    EXEC --> ART --> EVAL --> REFINE --> FRONT
    FRONT --> LEAD
    FRONT --> GAP
    GAP --> EXEC
    EVAL --> CONFLICT --> EXEC
    EXEC --> SK["Skeptic Agent"]
    SK -->|"PASS"| COMPLETE
    SK -->|"REVISE"| REM
    SK -->|"REJECT"| REM
    REM --> REFINE
    COMPLETE -->|"Incomplete"| REFINE
    COMPLETE -->|"Complete"| CLAIM --> SYN --> USER
    EXEC <-->|A2A| AGENTS["Domain + Specialist Agents"]
    AGENTS -->|MCP| DATA["Business Data / Tools"]
```

## Boundaries

- **LangGraph** — workflow, state, routing, checkpointing
- **A2A** — agent tasks/messages/artifacts
- **MCP** — agent-to-data (via domain agents, not unrestricted Coordinator MCP)
- **ML / DoWhy** — anomaly, forecast, causal estimation
- **LLM** — interpretation, planning enrichment, synthesis (never numeric fabrication)
