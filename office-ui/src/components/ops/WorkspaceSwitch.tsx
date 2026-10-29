import type { Workspace } from "../../stores/shell";

const TABS: { id: Workspace; label: string }[] = [
  { id: "conversation", label: "Chat" },
  { id: "ops", label: "Ops" },
  { id: "office", label: "Office" },
];

export function WorkspaceSwitch({ current, onChange }: { current: Workspace; onChange: (w: Workspace) => void }) {
  return (
    <div className="workspace-switch" role="tablist" aria-label="Workspace">
      {TABS.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={current === t.id}
          className={current === t.id ? "active" : ""}
          onClick={() => onChange(t.id)}
        >{t.label}</button>
      ))}
    </div>
  );
}
