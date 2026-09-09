# Diagnostic Lab visualization

## On the floor

When `task_specialists_activated` carries the `diagnostic` intent, the Diagnostic
character moves to its Lab desk and enters `working`. Its speech bubble shows the
current structured summary ("Testing HYP-21", "Causal validation…") — never raw
reasoning. Hypothesis / causal artifacts appearing in the raw payload keep it marked
as producing work.

## In the inspector

`AgentInspector::processFor("diagnostic_agent")` renders a **structured workflow**, not
chain-of-thought:

```
✓ Load anomaly
✓ Generate hypotheses
✓ Rank hypotheses
● Test strongest hypothesis
○ Causal validation
○ Contradiction check
○ Report
```

Steps flip to done from artifact counts (`anomaly`, `hypothesis`, `causal`) and skeptic
outcome. The hover card and inspector surface `currentHypothesis` and
`progress {current,total,label}` when the backend provides them (e.g. `4 / 6 tests`).

The demo fixture drives HYP-21 ("frontend regression"), then a causal-validation
artifact (temporal precedence ✓, mobile specificity ✓, desktop control ✓), matching
brief §19.

## Anomaly agent

Anomaly's workstation represents detected anomalies; the demo emits
`3 anomalies: Purchase CVR -24%, Mobile CVR -31%, JS errors +771%` as one
`artifact_created` event with refs, shown in its timeline slice and the bottom strip.
