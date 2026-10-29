import { useEffect, useMemo, useRef, useState } from "react";
import type { ActivityEvent, Message } from "../api/contracts";
import { conversationsApi } from "../api/conversations";
import {
  type LoopDebugStats,
  type LoopGrounding,
  type MissionTraceResponse,
  traceApi,
} from "../api/trace";
import { isTraceableMissionId, useOffice } from "../store";
import type { SwarmUIEvent } from "../types";
import { sessionRunsFromThread, truncateQuery, type SessionRun } from "./sessionRuns";

export type DebugLogEntry = {
  key: string;
  head: string;
  cls?: "ok" | "bad" | "";
  body?: string;
};

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function fmtSeconds(ms: number | null | undefined): string {
  if (ms == null) return "—";
  return `${(ms / 1000).toFixed(1)}s`;
}

// The trace keeps whole tool returns; the console shows the head of each.
const RESULT_DISPLAY_CHARS = 2000;
function clipResult(text: string): string {
  return text.length <= RESULT_DISPLAY_CHARS ? text : `${text.slice(0, RESULT_DISPLAY_CHARS)}… (${text.length - RESULT_DISPLAY_CHARS} more chars)`;
}

function fmtJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 1);
  } catch {
    return String(value);
  }
}

/** Provisional counters from the live event stream (filled until /trace arrives). */
export function liveStatsFromTimeline(
  timeline: SwarmUIEvent[],
  opts: { nowMs?: number } = {},
): LoopDebugStats | null {
  const nowMs = opts.nowMs ?? Date.now();
  if (!timeline.length) return null;
  let toolCalls = 0;
  let revisions = 0;
  const errors: string[] = [];
  let firstTs: number | null = null;
  for (const event of timeline) {
    const meta = (event.metadata && typeof event.metadata === "object")
      ? event.metadata as Record<string, unknown>
      : {};
    const type = String(meta.conversationEventType || event.eventType || "");
    const ts = Date.parse(event.timestamp);
    if (Number.isFinite(ts)) {
      firstTs = firstTs == null ? ts : Math.min(firstTs, ts);
    }
    if (type.endsWith("tool_started") || type === "tool_started") toolCalls += 1;
    if (type.includes("revis")) revisions += 1;
    if (/fail|error/.test(type) || meta.success === false) {
      errors.push(event.summary || type);
    }
  }
  const elapsed = firstTs != null ? Math.max(0, nowMs - firstTs) : null;
  return {
    elapsed_ms: elapsed,
    tool_calls: toolCalls || null,
    revisions: revisions || 0,
    errors,
    // Tokens / LLM steps only exist after the turn finishes.
    llm_calls: null,
    prompt_tokens: null,
    completion_tokens: null,
    cached_tokens: null,
  };
}

/** Prefer explicit timing; else recover from per-LLM ``at_ms`` (older traces). */
function firstTokenMs(s: LoopDebugStats): number | null {
  const direct = asNumber(s.ttft_ms);
  if (direct != null) return direct;
  for (const step of asList(s.llm_steps)) {
    if (!step || typeof step !== "object") continue;
    const at = asNumber((step as Record<string, unknown>).at_ms);
    if (at != null) return at;
    break;
  }
  return null;
}

function answerStartMs(s: LoopDebugStats): number | null {
  const direct = asNumber(s.answer_ttft_ms);
  if (direct != null) return direct;
  const steps = asList(s.llm_steps);
  for (let i = steps.length - 1; i >= 0; i -= 1) {
    const step = steps[i];
    if (!step || typeof step !== "object") continue;
    const at = asNumber((step as Record<string, unknown>).at_ms);
    if (at != null) return at;
  }
  // Answer is dumped at end of the turn when streaming is not incremental.
  return asNumber(s.total_ms) ?? asNumber(s.elapsed_ms);
}

/** Stats grid cells matching test_agent's Debug aside. */
export function debugStatCells(stats: LoopDebugStats | null | undefined, grounding: LoopGrounding | null | undefined) {
  const s = stats ?? {};
  const g = grounding ?? {};
  const totalMs = asNumber(s.total_ms) ?? asNumber(s.elapsed_ms);
  const errors = asList(s.errors);
  const measured = asNumber(g.measured) ?? 0;
  const derived = asList(g.derived).length;
  const numbers = asNumber(g.numbers);
  const ungrounded = [
    ...asList(g.unmatched),
    ...asList(g.misbound),
    ...asList(g.placeholders),
  ].map(String).filter(Boolean);
  const cache = s.cached_tokens;
  return [
    ["Total", fmtSeconds(totalMs)],
    ["First token", fmtSeconds(firstTokenMs(s))],
    ["Answer starts", fmtSeconds(answerStartMs(s))],
    ["LLM calls", s.llm_calls ?? "—"],
    ["Tool calls", s.tool_calls ?? "—"],
    ["Revisions", s.revisions ?? "—"],
    ["Prompt tok", s.prompt_tokens ?? "—"],
    ["Output tok", s.completion_tokens ?? "—"],
    ["Cache hits", cache == null ? "not reported" : cache],
    ["Errors", errors.length],
    ["Numbers grounded", numbers == null ? "—" : `${measured + derived}/${numbers}`],
    ["Ungrounded", ungrounded.join(" | ") || "none"],
  ] as Array<[string, string | number]>;
}

/** Build the expandable event log from persisted trace steps + per-LLM steps. */
export function entriesFromTrace(
  steps: Array<Record<string, unknown>> | null | undefined,
  llmSteps: Array<Record<string, unknown>> | null | undefined,
): DebugLogEntry[] {
  const out: DebugLogEntry[] = [];
  let llmIndex = 0;
  for (const [i, step] of (steps ?? []).entries()) {
    const kind = String(step.kind ?? "");
    if (kind === "tool_call") {
      const name = String(step.tool ?? "tool");
      out.push({
        key: `step-${i}-call`,
        head: `→ ${name}`,
        body: step.args != null ? fmtJson(step.args) : undefined,
      });
    } else if (kind === "tool_return") {
      const name = String(step.tool ?? "tool");
      const ok = step.ok !== false;
      const ms = asNumber(step.ms);
      const chars = asNumber(step.chars);
      const qid = step.query_id != null ? String(step.query_id) : "";
      const bits = [
        ok ? "✓" : "✗",
        name,
        ms != null ? `${ms}ms` : null,
        chars != null ? `${chars} chars` : null,
        step.duplicate ? "duplicate" : null,
        qid || null,
      ].filter(Boolean);
      out.push({
        key: `step-${i}-return`,
        head: bits.join(" · "),
        cls: ok ? "ok" : "bad",
        body: step.result != null ? clipResult(String(step.result)) : undefined,
      });
    }
  }
  for (const step of llmSteps ?? []) {
    llmIndex += 1;
    const ms = asNumber(step.ms);
    const prompt = asNumber(step.prompt_tokens);
    const completion = asNumber(step.completion_tokens);
    const tools = asNumber(step.tool_calls) ?? 0;
    const cached = asNumber(step.cached_tokens);
    const model = step.model != null ? String(step.model) : "";
    const finish = step.finish != null ? String(step.finish) : "";
    const head = [
      `◀ LLM #${llmIndex}`,
      model || null,
      ms != null ? `${ms}ms` : null,
      prompt != null && completion != null ? `${prompt}→${completion} tok` : null,
      cached != null ? `${cached} cached` : null,
      finish || null,
      `${tools} tool calls`,
    ].filter(Boolean).join(" · ");
    out.push({ key: `llm-${llmIndex}`, head });
  }
  return out;
}

/** Live progress events while the turn is still running. */
export function entriesFromTimeline(timeline: SwarmUIEvent[]): DebugLogEntry[] {
  return timeline.slice(-80).map((event, index) => {
    const meta = (event.metadata && typeof event.metadata === "object")
      ? event.metadata as Record<string, unknown>
      : {};
    const type = String(meta.conversationEventType || event.eventType || "event");
    const summary = event.summary || type.replaceAll("_", " ").replaceAll(".", " ");
    const bad = /fail|error|revis|reject/i.test(`${type} ${summary}`);
    const ok = /complet|done|answer/i.test(`${type} ${summary}`) && !bad;
    let head = summary;
    if (type === "tool_started" || type === "agent.tool_started" || type.endsWith("tool_started")) {
      head = `→ ${summary}`;
    } else if (type === "tool_completed" || type === "agent.tool_completed" || type.endsWith("tool_completed")) {
      head = meta.success === false ? `✗ ${summary}` : `✓ ${summary}`;
    } else if (type.includes("revis")) {
      head = `↺ ${summary}`;
    }
    const bodyKeys = ["tool", "args", "issues", "reason"];
    const bodyObj: Record<string, unknown> = {};
    for (const key of bodyKeys) {
      if (meta[key] != null) bodyObj[key] = meta[key];
    }
    return {
      key: event.eventId || `live-${index}`,
      head,
      cls: bad ? "bad" as const : ok ? "ok" as const : "",
      body: Object.keys(bodyObj).length ? fmtJson(bodyObj) : undefined,
    };
  });
}

function SingleRunDebug({
  missionId,
  timeline,
  stats: statsProp,
  grounding: groundingProp,
  steps: stepsProp,
  live = false,
  persistToStore = true,
  active = true,
}: {
  missionId: string | null;
  timeline: SwarmUIEvent[];
  stats?: LoopDebugStats | null;
  grounding?: LoopGrounding | null;
  steps?: Array<Record<string, unknown>> | null;
  live?: boolean;
  /** Only the active/latest run should write the shared office loopDebug slice. */
  persistToStore?: boolean;
  /** Lazy: skip /trace until the collapsible section is open. */
  active?: boolean;
}) {
  const [fetched, setFetched] = useState<MissionTraceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const eventsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!live || !active) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [live, active]);

  const setLoopDebug = useOffice((s) => s.setLoopDebug);

  useEffect(() => {
    if (!active) return;
    if (!isTraceableMissionId(missionId)) {
      setFetched(null);
      setError(null);
      return;
    }
    // While the turn is running and we have no streamed stats yet, skip /trace —
    // the mission record is still incomplete. Fetch as soon as the run settles
    // (or when answer.completed already shipped stats and we want llm_steps).
    if (live && !statsProp) return;
    let alive = true;
    setLoading(true);
    setError(null);
    void traceApi.get(missionId)
      .then((response) => {
        if (!alive) return;
        setFetched(response);
        if (!persistToStore) return;
        const stats = response.trace?.stats && typeof response.trace.stats === "object"
          ? response.trace.stats as Record<string, unknown> : null;
        const grounding = response.trace?.grounding && typeof response.trace.grounding === "object"
          ? response.trace.grounding as Record<string, unknown> : null;
        const steps = Array.isArray(response.trace?.steps)
          ? response.trace.steps.filter((row): row is Record<string, unknown> => !!row && typeof row === "object")
          : null;
        if (stats || grounding || steps) {
          setLoopDebug({ stats, grounding, steps });
        }
      })
      .catch((reason: unknown) => {
        if (alive) setError(reason instanceof Error ? reason.message : "Unable to load trace");
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [missionId, live, statsProp, setLoopDebug, persistToStore, active]);

  const finalStats = (fetched?.trace?.stats as LoopDebugStats | undefined) ?? statsProp ?? null;
  const liveStats = useMemo(
    () => (finalStats ? null : liveStatsFromTimeline(timeline, { nowMs })),
    [finalStats, timeline, nowMs],
  );
  const stats = finalStats ?? liveStats;
  const grounding = (fetched?.trace?.grounding as LoopGrounding | undefined) ?? groundingProp ?? null;
  const steps = (fetched?.trace?.steps as Array<Record<string, unknown>> | undefined) ?? stepsProp ?? null;
  const llmSteps = (finalStats?.llm_steps as Array<Record<string, unknown>> | undefined) ?? null;

  const cells = useMemo(() => debugStatCells(stats, grounding), [stats, grounding]);
  const entries = useMemo(() => {
    const fromTrace = entriesFromTrace(steps, llmSteps);
    if (fromTrace.length) return fromTrace;
    return entriesFromTimeline(timeline);
  }, [steps, llmSteps, timeline]);

  useEffect(() => {
    const node = eventsRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [entries.length]);

  if (!active) return null;

  // Mid-run can briefly lack a mission id — still show live counters from the timeline.
  if (!missionId && !live && !timeline.length) {
    return <p className="empty-note">Run a question to see loop timing, tokens, tools and grounding.</p>;
  }

  return (
    <div className="loop-debug">
      <div className="loop-debug-stats" aria-label="Loop metrics">
        {cells.map(([label, value]) => (
          <div className="loop-debug-stat" key={label}>
            <span>{label}</span>
            <b>{String(value)}</b>
          </div>
        ))}
      </div>
      {(loading || error) && (
        <p className="empty-note" role={error ? "alert" : "status"}>
          {error ?? "Loading trace…"}
        </p>
      )}
      <div className="loop-debug-events" ref={eventsRef} aria-label="Loop event log">
        {entries.map((entry) => (
          <div className="loop-debug-ev" key={entry.key}>
            <div className="h">
              <b className={entry.cls || undefined}>{entry.head}</b>
            </div>
            {entry.body && (
              <details>
                <summary>details</summary>
                <pre>{entry.body}</pre>
              </details>
            )}
          </div>
        ))}
        {!entries.length && !loading && (
          <p className="empty-note">Events appear here as tools and LLM steps run.</p>
        )}
      </div>
    </div>
  );
}

function statusLabel(status: SessionRun["status"]): string {
  if (status === "running") return "running";
  if (status === "completed") return "done";
  if (status === "failed") return "failed";
  if (status === "cancelled") return "cancelled";
  return "…";
}

function RunAccordion({
  run,
  index,
  total,
  open,
  onOpenChange,
  live,
  timeline,
  stats,
  grounding,
  steps,
}: {
  run: SessionRun;
  index: number;
  total: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  live: boolean;
  timeline: SwarmUIEvent[];
  stats?: LoopDebugStats | null;
  grounding?: LoopGrounding | null;
  steps?: Array<Record<string, unknown>> | null;
}) {
  const n = total - index;
  return (
    <details
      className={`session-debug-run ${live ? "live" : ""} status-${run.status}`}
      open={open}
      onToggle={(event) => onOpenChange((event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary>
        <span className="session-debug-run-index">Run {n}</span>
        <span className="session-debug-run-query" title={run.query}>{truncateQuery(run.query)}</span>
        <span className={`session-debug-run-status ${run.status}`}>{statusLabel(run.status)}</span>
      </summary>
      <SingleRunDebug
        missionId={run.missionId}
        timeline={live ? timeline : []}
        stats={live ? stats : null}
        grounding={live ? grounding : null}
        steps={live ? steps : null}
        live={live}
        persistToStore={live || index === 0}
        active={open}
      />
    </details>
  );
}

/** Session Debug: every run in the thread as a collapsible card (newest first). */
export function LoopDebugConsole({
  missionId,
  timeline,
  stats: statsProp,
  grounding: groundingProp,
  steps: stepsProp,
  live = false,
  threadId = null,
  messages = [],
  currentRunId = null,
}: {
  missionId: string | null;
  timeline: SwarmUIEvent[];
  stats?: LoopDebugStats | null;
  grounding?: LoopGrounding | null;
  steps?: Array<Record<string, unknown>> | null;
  live?: boolean;
  threadId?: string | null;
  messages?: Message[];
  currentRunId?: string | null;
}) {
  const [threadEvents, setThreadEvents] = useState<ActivityEvent[]>([]);
  const [openIds, setOpenIds] = useState<Set<string>>(() => new Set());
  const seededRef = useRef<string | null>(null);

  useEffect(() => {
    if (!threadId) {
      setThreadEvents([]);
      return;
    }
    let alive = true;
    const load = () => {
      void conversationsApi.listThreadEvents(threadId)
        .then((events) => { if (alive) setThreadEvents(events); })
        .catch(() => { /* keep last good snapshot */ });
    };
    load();
    // Refresh while a turn is live so new runs appear without leaving the tab.
    const id = live ? window.setInterval(load, 2500) : 0;
    return () => {
      alive = false;
      if (id) window.clearInterval(id);
    };
  }, [threadId, live, currentRunId, messages.length]);

  const runs = useMemo(
    () => sessionRunsFromThread(messages, threadEvents, {
      currentRunId,
      currentMissionId: missionId,
      live,
    }),
    [messages, threadEvents, currentRunId, missionId, live],
  );

  // Default: newest run (and any live run) expanded; seed once per thread.
  useEffect(() => {
    if (!threadId || !runs.length) return;
    if (seededRef.current === threadId) return;
    seededRef.current = threadId;
    const initial = new Set<string>();
    initial.add(runs[0].runId);
    if (currentRunId) initial.add(currentRunId);
    setOpenIds(initial);
  }, [threadId, runs, currentRunId]);

  useEffect(() => {
    if (currentRunId && live) {
      setOpenIds((prev) => {
        if (prev.has(currentRunId)) return prev;
        const next = new Set(prev);
        next.add(currentRunId);
        return next;
      });
    }
  }, [currentRunId, live]);

  if (!threadId) {
    return <p className="empty-note">Run a question to see loop timing, tokens, tools and grounding.</p>;
  }
  if (!runs.length) {
    return (
      <SingleRunDebug
        missionId={missionId}
        timeline={timeline}
        stats={statsProp}
        grounding={groundingProp}
        steps={stepsProp}
        live={live}
      />
    );
  }

  return (
    <div className="session-debug" aria-label="Session runs">
      <p className="session-debug-hint">
        {runs.length} run{runs.length === 1 ? "" : "s"} in this conversation — expand any to inspect.
      </p>
      {runs.map((run, index) => {
        const isLive = Boolean(live && currentRunId && run.runId === currentRunId);
        const open = openIds.has(run.runId) || isLive;
        return (
          <RunAccordion
            key={run.runId}
            run={run}
            index={index}
            total={runs.length}
            open={open}
            onOpenChange={(next) => {
              setOpenIds((prev) => {
                const copy = new Set(prev);
                if (next) copy.add(run.runId);
                else if (!isLive) copy.delete(run.runId);
                return copy;
              });
            }}
            live={isLive}
            timeline={timeline}
            stats={statsProp}
            grounding={groundingProp}
            steps={stepsProp}
          />
        );
      })}
    </div>
  );
}
