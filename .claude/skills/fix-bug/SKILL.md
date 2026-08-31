---
name: fix-bug
description: Required root-cause bug-fix workflow for Seleric Voice Node — reproduce, isolate, fix minimally, verify, regression test. Use for any bug ticket. Never patch the symptom at the call site the bug report happened to name.
---

# Fix a bug

1. **Reproduce** the exact reported symptom before touching any code.
2. **Isolate the root cause.** Grep every caller of the function involved —
   if the bug is reachable from more than one path (e.g. both the Voice
   Orchestrator and a scheduled job call the same state function), the fix
   belongs in the shared function, not a guard added only at the path the
   report named.
3. **Check whether the root cause is a violated architecture rule** —
   e.g. business logic that leaked into `SelericBridgeSkill`, or an
   un-provenanced number bypassing the MCP boundary. If so, the fix is
   moving the logic to the right place, not patching around it.
4. **Implement the smallest correct fix** at the root cause.
5. **Write a regression test** that fails before the fix and passes after,
   when practical (see doc 10 §8 acceptance metrics for which class of bug
   this is — voice/data-decision/meeting — to know which golden set it belongs in).
6. **Verify** the originally reported symptom is gone and no adjacent
   behavior regressed.
7. Record root cause (not just symptom) in the ticket's Work Log.
