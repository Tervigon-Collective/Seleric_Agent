// Adapter boundary for the spatial runtime. Existing office components can keep
// importing `useOffice`; conversation UI imports the explicit Phase 3 name.
export { useOffice, useOffice as useMissionRuntimeStore } from "../store";
export type { ConnState } from "../store";
