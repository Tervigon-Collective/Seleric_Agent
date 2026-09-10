# Agent interactions (A2A / collaboration)

Agents communicate **in person** — like a real office. There is no cross-room
"message" line. When a comms event lands, the people involved actually walk to each
other and meet.

## Movement model

`office-ui/src/office/navmesh.ts` is a coarse walkable grid + A* built from the room
walls in `layout.ts`. Characters follow string-pulled paths at a walking pace: down
the 64px carpeted hallways, **through the doorways**, never across a wall
(`src/__tests__/navmesh.test.ts` asserts every desk→desk path stays wall-clear).
`prefers-reduced-motion` snaps them straight to the destination.

## In-person interactions

`OfficeCanvas` turns a comms event into a short choreography (`buildInteraction`):

| Event | Choreography |
| --- | --- |
| `leadership_transferred` | outgoing + incoming lead both walk to the **Handoff / Meeting Room** table, sit across from each other, exchange a folder, linger ~3.4s, then disperse (incoming keeps the LEAD ring) |
| `evidence_requested` | the requesting agent walks to the **Evidence Archive** and back |

While two agents are actually together, talk dots + a soft ground shadow link them —
that is the only "connection" ever drawn, and only for the seconds they're co-located.
At their desks, working agents render **seated** (chair back, tucked legs, typing).

## Backing events

The gateway maps these Seleric events to interaction visuals:

| Seleric | UI | Visual |
| --- | --- | --- |
| `leadership_transfer` | `leadership_transferred` | folder travels from → to, dashed link |
| `remediation_activated` | `task_assigned` | targeted agent re-enters `working` |
| `task_specialists_activated` | `agent_started` | specialists light up in their labs |

## Future (documented, not yet built)

A2A evidence request/response (`evidence_requested` / `evidence_received`) would render
as a small document icon travelling desk→data-room→desk, with the request key on hover
(`technical.mobile_latency`). The event types are reserved in `SwarmUIEventType`; the
backend does not yet surface them as discrete control-plane events, so the office does
not fabricate them.
