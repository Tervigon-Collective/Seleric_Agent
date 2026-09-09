# Demo mode

The office runs with **no backend** so it can be developed and demoed anywhere.

## Activation

- `http://localhost:5173/` or `?demo=1` → starts in demo mode.
- `?mission=<id>` (without `demo=1`) → starts in live mode against that mission.
- The top-bar "Demo fixture / Live swarm" selector switches at runtime.

## The fixture

`office-ui/src/providers/demoScenario.ts` — `DEMO_SCRIPT` is a scripted CAC
investigation (brief §68 + §83). Each beat is one real-shaped `SwarmUIEvent` plus an
optional `OfficeSnapshot` patch (board / artifacts / stage / final answer), so the
demo path exercises exactly the same store code as the live path.

It walks: assignment → movement → evidence retrieval → anomaly detection → decomposition
refine → **Performance → Funnel** handoff → Funnel wave → **Funnel → Technical** handoff
→ specialist activation → hypothesis test → causal validation → prediction → strategy →
**Skeptic REVISE** → remediation task → remediation done → **Skeptic PASS** → completion.

`DemoEventProvider` (`providers/demo.ts`) implements the same `SwarmEventProvider`
interface as `SelericEventProvider`; `speed` compresses the timeline for tests
(`{ speed: 100 }`).

## Tested

`office-ui/src/__tests__/demoScenario.test.ts` asserts every required beat is present,
that there are exactly two handoffs P→Funnel→Technical, and that replaying the whole
script through the real store ends at `status: completed`, all board steps done,
`leadAgentId === "technical_agent"`, with the final answer set and unique seqs.
