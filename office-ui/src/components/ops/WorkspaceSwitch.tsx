import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { Workspace } from "../../stores/shell";

const TABS: { id: Workspace; label: string }[] = [
  { id: "conversation", label: "Chat" },
  { id: "ops", label: "Ops" },
  { id: "office", label: "Office" },
];

export function WorkspaceSwitch({ current, onChange }: { current: Workspace; onChange: (w: Workspace) => void }) {
  return (
    <Tabs value={current} onValueChange={(value) => onChange(value as Workspace)}>
      <TabsList aria-label="Workspace">
        {TABS.map((t) => <TabsTrigger key={t.id} value={t.id}>{t.label}</TabsTrigger>)}
      </TabsList>
    </Tabs>
  );
}
