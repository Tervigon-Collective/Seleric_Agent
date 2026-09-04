# 03 — Progressive Decomposition

```mermaid
flowchart TB
    U["User Question"] --> N["Normalize"]
    N --> D1["Initial Decomposition"]
    D1 --> T1["Execute Highest-Value Questions"]
    T1 --> E["Evidence"]
    E --> F{"Objective Resolved?"}
    F -->|"No"| CF["Identify Unresolved Frontier"]
    CF --> D2["Refine Problem Decomposition"]
    D2 --> T2["Generate Only Required Tasks"]
    T2 --> E
    F -->|"Yes"| V["Validation / Skeptic"]
    V -->|"REVISE"| D2
    V -->|"PASS"| C["Completion Gate"]
    C --> R["Final Response"]
```

Never: one LLM call → 20 tasks → execute everything.

Versioned `ProblemDecomposition` records (`DEC-…-vN`) with parent links, reasons, evidence refs, questions added/retired.

Templates constrain common intents (lookup, comparison, anomaly, diagnostic, predictive, prescriptive, executive_health, cac_diagnostic).

Ruled-out branches (e.g. stable CPM/CTR/CPC) become `irrelevant`.
