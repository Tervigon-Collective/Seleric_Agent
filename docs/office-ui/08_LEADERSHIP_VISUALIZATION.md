# Dynamic leadership visualization

Seleric's dynamic mission leadership is one of its most distinctive behaviours, so the
office makes it unmistakable — without a giant crown.

## The lead marker

The current lead gets a **status-coloured floor ring** (ellipse under the character)
and `missionLead: true` in its view-model. The mission header shows `lead <Name> · e<epoch>`.
Exactly one agent is ever the lead — `applyEventToAgents` clears every other ring when
a transfer lands, and `store.ts` re-points `leadAgentId` on every
`leadership_transferred` event.

## The transfer

On `leadership_transfer` (`graph.py` emits it with the last `handoff_history` entry
spread in, plus `frontier`):

1. the outgoing lead → `handoff` state, walks to the Handoff / Meeting Room;
2. the incoming lead → `working` + lead ring, receives the unresolved sub-question as
   `subquestion`;
3. a short **dashed collaboration link** with a travelling folder is drawn between the
   two desks while the event is the most recent one (it is transient, not a permanent
   line);
4. the timeline records `Leadership <from> → <to>: <reason>`.

`leadership_rejected` → `leadership_transfer_rejected`: no ring change, logged with the
policy reason.

## Causal frontier

Because each agent's home zone lights when it is active, the lit region visibly walks
`Performance → Funnel → Technical` as leadership moves. The `stage` field and the
mission board's active step track the same progression. Handoff history is listed in
the Coordinator inspector ("Leadership / dependencies").
