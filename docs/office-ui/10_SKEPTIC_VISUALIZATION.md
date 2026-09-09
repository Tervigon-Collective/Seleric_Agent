# Skeptic review & remediation

## Review room

The Skeptic Review Room is a visually distinct zone. On `skeptic_gate`
(→ `skeptic_review_started`) the Skeptic character enters `reviewing` at the skeptic
desk and a review animation plays (rotating ring arc). The inspector process view:

```
✓ Receive claim
● Check evidence coverage
○ Look for contradictions
○ Issue verdict
```

## Verdicts

| Backend | UI event | Visual |
| --- | --- | --- |
| `skeptic_pass` / `claim_validated` | `skeptic_pass` | Skeptic → `completed`, "Verdict: PASS", board `skeptic` step done |
| `skeptic_revise` / `claim_challenged` | `skeptic_revise` | Skeptic stays `reviewing` "Verdict: REVISE"; **Coordinator → `working`, "Planning remediation"** |
| `skeptic_reject` / `claim_rejected` | `skeptic_reject` | Skeptic "Verdict: REJECT"; Coordinator remediation |

## The REVISE → remediation loop (brief §24, §67)

`skeptic_revise` does **not** just flash an error. The sequence, all event-driven:

1. `skeptic_revise` — reason surfaced ("missing traffic-mix control") in bubble + timeline.
2. `remediation_created` — Coordinator picks it up (`Remediation planned: …`).
3. `task_assigned` — a targeted task goes to the named agent (Performance in the demo),
   which returns to `working`.
4. `task_completed` — the remediation round closes.
5. `skeptic_review_started` again → `skeptic_pass`.
6. `mission_completed`.

No fake completion is ever shown while a blocker is open; the mission board only
finishes on `mission_completed`. The error fixture (`?` — demo REVISE branch) exercises
exactly this path.
