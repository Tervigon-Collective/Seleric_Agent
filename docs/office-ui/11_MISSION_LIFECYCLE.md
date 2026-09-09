# Mission lifecycle — the primary journey

User asks *"Why has CAC increased?"* → the office plays out:

| # | Event(s) | What you see |
| --- | --- | --- |
| 1 | `mission_started` | mission header appears; Coordinator → planning board |
| 2 | `decomposition_created` | planning board fills with sub-questions |
| 3 | `task_created` | Coordinator → mission room, task plan built |
| 4 | `task_started` (lead=performance) | Performance gets the lead ring; Observer → data terminal; Anomaly → working; Performance zone lights |
| 5 | `artifact_created` (anomaly) | "3 anomalies…" bubble; board `verify` done |
| 6 | `decomposition_refined` | Coordinator → thinking; "Purchase CVR is the frontier" |
| 7 | `leadership_transferred` P→Funnel | folder travels to Handoff Room; Funnel gets the ring; Funnel zone lights |
| 8 | `task_started` (lead=funnel) | Funnel investigation wave |
| 9 | `leadership_transferred` Funnel→Technical | ring + lit zone move to Technical |
| 10 | `agent_started` (intents) | Diagnostic / Prediction / Strategy activate in their labs |
| 11–14 | `artifact_created` ×4 | HYP-21, causal validation, forecast, strategy options; board steps fill |
| 15 | `skeptic_review_started` | Skeptic → Review Room; board `skeptic` active |
| 16 | `skeptic_revise` | "REVISE: missing traffic-mix control"; Coordinator → remediation |
| 17 | `remediation_created` → `task_assigned` | targeted task returns to Performance |
| 18 | `task_completed` | "Traffic mix flat — control satisfied" |
| 19 | `skeptic_review_started` → `skeptic_pass` | "Verdict: PASS" |
| 20 | `mission_completed` | agents settle to idle/completed; board all done; final answer available in Coordinator inspector |

At any moment: **hover** an agent for its live card, **click** for the full inspector,
glance at the floor for who is active, at the ring for who leads, at the lit zone for
where the investigation is.

## Interaction during execution

Select agents, inspect tasks/evidence, read the board, follow the lead or activity
(camera modes), pause auto-follow (drag the camera), open/filter the timeline. Users
cannot drag agents onto arbitrary tasks — the Coordinator owns task assignment.

## Completion

`mission_completed` settles active agents gently (no arcade celebration beyond a brief
"celebrate" bob), the board shows all steps done, and the final synthesized answer is
shown in the Coordinator inspector's "Final answer" panel.
