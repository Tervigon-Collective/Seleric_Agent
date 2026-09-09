# Product concept

## The idea

Seleric already runs a dynamic multi-agent swarm. Its work is invisible: it happens
inside LangGraph nodes and A2A messages. The AI Office makes it **visible and legible
at a glance** using the AI-Town / Parcha "AI Office" spatial metaphor — a 2D walkable
office, characters at desks, rooms for meetings and review.

At first glance the user should know:

- who is working, idle, waiting, blocked, reviewing;
- what mission is active and what stage it has reached;
- **who currently leads** the mission;
- who is collaborating;
- where the investigation is (which department is "hot") and how it is moving.

…without reading a log.

## Progressive disclosure (mandatory)

| Layer | Density | Content |
| --- | --- | --- |
| Office floor | low–medium | positions, status rings, lead marker, active zones, one speech bubble |
| Hover card | medium | mission, sub-question, current action/tool/hypothesis, progress, lead, elapsed |
| Inspector | high | overview, current task, structured process, dependencies, per-agent activity, final answer, debug |

## Non-goals

- **Not** an autonomous NPC simulation. Characters never decide anything; the backend does.
- **Not** a standard observability dashboard. Graphs / tables / traces exist only as
  secondary inspector content, never as the primary surface.
- **Not** a new orchestration layer. The gateway is strictly read-only.
- **Never** exposes private model chain-of-thought — only structured workflow stages.
- Movement is representation only; it never delays real work (see `04_AGENT_STATES.md`).

## Influences

| Reference | What we took |
| --- | --- |
| [Parcha AI Office](https://github.com/Parcha-ai/ai-office) | spatial world / map / character + camera metaphor |
| AgentOffice (esteban-dcp / samwang0723) | event-driven agent-presence architecture, status/task/progress schema |
| belle05/agent-office | hover / task / tool / workflow interaction ideas |
| mkanasani/agent-hq | activity feed / task monitoring ideas |

No code or assets were copied. The world is re-implemented as a light canvas renderer
(no game engine, no Convex/Pinecone/Clerk) driven entirely by Seleric events.
