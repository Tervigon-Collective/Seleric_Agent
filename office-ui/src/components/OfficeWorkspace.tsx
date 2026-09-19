import { useEffect, useMemo, useRef, useState } from "react";
import { useOffice } from "../store";
import type { MissionRef } from "../types";
import { SelericEventProvider } from "../providers/seleric";
import type { ProviderHandlers, SwarmEventProvider } from "../providers/types";
import { OfficeCanvas } from "../render/OfficeCanvas";
import { TopBar } from "./TopBar";
import { MissionHeader } from "./MissionHeader";
import { MissionBoard } from "./MissionBoard";
import { MissionMinimap } from "./MissionMinimap";
import { MissionTimeline } from "./MissionTimeline";
import { AgentHoverCard } from "./AgentHoverCard";
import { AgentInspector } from "./AgentInspector";
import { DebugPanel } from "./DebugPanel";
import { readRoute, writeRoute } from "../routing";

export function OfficeWorkspace({
  dark,
  onToggleTheme,
  onOpenConversations,
}: {
  dark: boolean;
  onToggleTheme: () => void;
  onOpenConversations: () => void;
}) {
  const hydrate = useOffice((s) => s.hydrate);
  const ingestEvent = useOffice((s) => s.ingestEvent);
  const setConn = useOffice((s) => s.setConn);
  const reset = useOffice((s) => s.reset);
  const [missions, setMissions] = useState<MissionRef[]>([]);
  const [missionId, setMissionId] = useState<string | null>(null);
  const [pointer, setPointer] = useState({ x: 0, y: 0 });
  const unsubRef = useRef<null | (() => void)>(null);

  const provider: SwarmEventProvider = useMemo(() => new SelericEventProvider(), []);
  useEffect(() => {
    let alive = true;
    const load = () => provider.listMissions().then((items) => {
      if (!alive) return;
      setMissions(items);
      setMissionId((current) => {
        const fromRoute = readRoute().missionId;
        const chosen = current ?? fromRoute;
        if (chosen && items.some((item) => item.missionId === chosen)) return chosen;
        return items[0]?.missionId ?? (chosen && items.length === 0 ? chosen : null);
      });
    }).catch(() => alive && setMissions([]));
    void load();
    const early = [800, 2500].map((ms) => window.setTimeout(load, ms));
    const timer = setInterval(load, 10_000);
    return () => {
      alive = false;
      early.forEach(clearTimeout);
      clearInterval(timer);
    };
  }, [provider]);
  useEffect(() => {
    let active = true;
    unsubRef.current?.();
    reset();
    if (!missionId) return;
    const handlers: ProviderHandlers = {
      onSnapshot: hydrate, onEvent: ingestEvent,
      onState: (state) => setConn(state === "live" ? "live" : state),
      onDone: () => setConn("closed"),
    };
    setConn("connecting");
    writeRoute({ workspace: "office", missionId });
    void provider.getSnapshot(missionId).then((snapshot) => {
      if (active) hydrate(snapshot);
    }).catch(() => undefined).finally(() => {
      if (active) unsubRef.current = provider.subscribe(missionId, handlers);
    });
    return () => { active = false; unsubRef.current?.(); unsubRef.current = null; };
  }, [provider, missionId, hydrate, ingestEvent, setConn, reset]);
  useEffect(() => {
    const popstate = () => setMissionId(readRoute().missionId);
    window.addEventListener("popstate", popstate);
    return () => window.removeEventListener("popstate", popstate);
  }, []);

  return (
    <div className="office-workspace">
      <TopBar missions={missions} missionId={missionId} onPickMission={setMissionId} dark={dark} onToggleTheme={onToggleTheme} onOpenConversations={onOpenConversations} />
      <div className="stage" onMouseMove={(event) => setPointer({ x: event.clientX, y: event.clientY })}>
        <OfficeCanvas dark={dark} /><MissionHeader /><MissionBoard />
        <AgentHoverCard x={pointer.x} y={pointer.y} /><AgentInspector />
        <MissionMinimap missions={missions} missionId={missionId} onPick={setMissionId} /><DebugPanel />
      </div>
      <MissionTimeline />
    </div>
  );
}
