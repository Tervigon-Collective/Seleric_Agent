import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useOffice } from "./store";
import type { MissionRef } from "./types";
import { DemoEventProvider } from "./providers/demo";
import { SelericEventProvider } from "./providers/seleric";
import type { ProviderHandlers, SwarmEventProvider } from "./providers/types";
import { TopBar } from "./components/TopBar";
import { MissionHeader } from "./components/MissionHeader";
import { MissionBoard } from "./components/MissionBoard";
import { MissionMinimap } from "./components/MissionMinimap";
import { MissionTimeline } from "./components/MissionTimeline";
import { AgentHoverCard } from "./components/AgentHoverCard";
import { AgentInspector } from "./components/AgentInspector";
import { DebugPanel } from "./components/DebugPanel";
import { OfficeCanvas } from "./render/OfficeCanvas";

const params = new URLSearchParams(location.search);
const START_DEMO = params.get("demo") === "1" || !params.has("mission");
const URL_MISSION = params.get("mission");

export default function App() {
  const [dark, setDark] = useState(() => {
    const s = localStorage.getItem("seleric.theme");
    if (s) return s === "dark";
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  });
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    localStorage.setItem("seleric.theme", dark ? "dark" : "light");
  }, [dark]);

  const providerMode = useOffice((s) => s.providerMode);
  const setProviderMode = useOffice((s) => s.setProviderMode);
  const hydrate = useOffice((s) => s.hydrate);
  const ingestEvent = useOffice((s) => s.ingestEvent);
  const setConn = useOffice((s) => s.setConn);
  const reset = useOffice((s) => s.reset);

  const [missions, setMissions] = useState<MissionRef[]>([]);
  const [missionId, setMissionId] = useState<string | null>(null);
  const [pointer, setPointer] = useState({ x: 0, y: 0 });
  const unsubRef = useRef<null | (() => void)>(null);

  useEffect(() => {
    setProviderMode(START_DEMO ? "demo" : "seleric");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const provider: SwarmEventProvider = useMemo(
    () => (providerMode === "demo" ? new DemoEventProvider({ speed: 1 }) : new SelericEventProvider()),
    [providerMode],
  );

  // load mission list on provider change, then keep it fresh (multi-mission view)
  useEffect(() => {
    let alive = true;
    const load = () =>
      provider
        .listMissions()
        .then((ms) => {
          if (!alive) return;
          setMissions(ms);
          setMissionId((cur) => cur ?? URL_MISSION ?? ms[0]?.missionId ?? null);
        })
        .catch(() => alive && setMissions([]));
    load();
    const t = setInterval(load, 10_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [provider]);

  // (re)subscribe on mission / provider change
  useEffect(() => {
    unsubRef.current?.();
    reset();
    if (!missionId) return;

    const handlers: ProviderHandlers = {
      onSnapshot: hydrate,
      onEvent: ingestEvent,
      onState: (st) => setConn(st === "live" ? "live" : st),
      onDone: () => setConn("closed"),
    };
    setConn("connecting");
    provider
      .getSnapshot(missionId)
      .then(hydrate)
      .catch(() => void 0)
      .finally(() => {
        unsubRef.current = provider.subscribe(missionId, handlers);
      });

    return () => {
      unsubRef.current?.();
      unsubRef.current = null;
    };
  }, [provider, missionId, hydrate, ingestEvent, setConn, reset]);

  const onPickProvider = useCallback(
    (m: "demo" | "seleric") => {
      setProviderMode(m);
      setMissionId(null);
    },
    [setProviderMode],
  );

  return (
    <div className="app">
      <TopBar
        missions={missions}
        missionId={missionId}
        onPickMission={setMissionId}
        providerMode={providerMode}
        onPickProvider={onPickProvider}
        dark={dark}
        onToggleTheme={() => setDark((d) => !d)}
      />

      <div className="stage" onMouseMove={(e) => setPointer({ x: e.clientX, y: e.clientY })}>
        <OfficeCanvas dark={dark} />
        <MissionHeader />
        <MissionBoard />
        <AgentHoverCard x={pointer.x} y={pointer.y} />
        <AgentInspector />
        <MissionMinimap missions={missions} missionId={missionId} onPick={setMissionId} />
        <DebugPanel />
      </div>

      <MissionTimeline />
    </div>
  );
}
