# Agent state machine

`AgentOfficeState` (`office-ui/src/types.ts`, mirrored in `normalize.py`):

```
offline · idle · assigned · planning · thinking · working · retrieving_evidence
tool_running · waiting_for_agent · waiting_for_evidence · collaborating · reviewing
blocked · needs_attention · handoff · completed · failed
```

## Derivation — never random

State is **only** ever set from a backend event. `normalize.py::_event_agent_and_status`
and `stateMachine.ts::applyEventToAgents` implement the same mapping:

| Backend kind (canonical) | UI event | Agent → state |
| --- | --- | --- |
| `mission_created` | `mission_started` | coordinator → planning |
| `decomposition_created` / `_refined` | `decomposition_*` | coordinator → planning / thinking |
| `task_plan_created` | `task_created` | coordinator → working |
| `task_wave_executed` | `task_started` | wave lead → working; observer → retrieving_evidence; anomaly → working |
| `task_specialists_activated` | `agent_started` | diagnostic/prediction/strategy → working (per intents) |
| `leadership_transfer` | `leadership_transferred` | from → handoff; to → working + **lead** |
| `skeptic_gate` | `skeptic_review_started` | skeptic → reviewing |
| `skeptic_pass` | `skeptic_pass` | skeptic → completed |
| `skeptic_revise` / `_reject` | `skeptic_revise` / `_reject` | skeptic → reviewing; coordinator → working (remediation) |
| `remediation_planned` | `remediation_created` | coordinator → working |
| `remediation_activated` | `task_assigned` | named agent → working |
| `specialist_error` | `error` | agent → failed |
| `mission_budget_exhausted` | `mission_blocked` | coordinator → blocked |
| `mission_completed` | `mission_completed` | active agents settle → completed/idle |

Artifact buckets in the raw payload (`evidence`, `anomaly`, `hypothesis`, `causal`,
`prediction`, `strategy`, `skeptic`) additionally mark their owning specialist as
having produced work.

## Animation & colour

`animationFor(state)` → `idle · walk · sit_type · think · retrieve · review · wait ·
blocked · handoff · celebrate`. `STATUS_TOKEN[state]` → one of the design tokens
(`neutral · working · thinking · waiting · failed · done · collab`), defined once in
`index.css` and mirrored in `render/palette.ts`.

Accessibility: status is shown by **ring colour + animation + glyph + hover text +
a `!` badge** for waiting/failed — never colour alone. `prefers-reduced-motion`
disables bobbing, dashed-line motion and the walk cycle (characters snap to position).

## Movement priority

Backend work starts immediately on the event. The character's walk is cosmetic and is
never awaited. A sprite mid-walk does not hold up anything.
