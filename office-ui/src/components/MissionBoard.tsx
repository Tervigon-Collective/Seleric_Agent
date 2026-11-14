import { Card, CardContent } from "@/components/ui/card";
import { cn } from "cn";
import { useOffice } from "../store";

const MARK: Record<string, string> = {
  done: "border-[var(--st-done)] bg-[var(--st-done)] text-[var(--on-accent)]",
  active: "border-[var(--st-working)]",
};

function Step({ state, mark, children, className }: { state: string; mark: string; children: string; className?: string }) {
  return (
    <div data-slot="board-step" data-state={state} className={cn("flex items-center gap-2 text-xs", state === "done" && "text-muted-foreground", state === "active" && "font-semibold", className)}>
      <span className={cn("grid size-4 shrink-0 place-items-center rounded-full border-2 border-[var(--st-neutral)] text-[11px]", MARK[state])}>{mark}</span>
      <span>{children}</span>
    </div>
  );
}

export function MissionBoard() {
  const board = useOffice((s) => s.board);
  const unresolved = useOffice((s) => s.unresolvedQuestions);
  if (!board.length) return null;
  return (
    <Card size="sm" data-slot="mission-board" className="absolute top-2 right-2 z-[4] w-40 max-lg:hidden">
      <CardContent className="flex flex-col gap-1">
        <h2 className="m-0 text-xs tracking-wide text-muted-foreground uppercase">Mission board</h2>
        {board.map((step, i) => <Step key={step.id} state={step.state} mark={step.state === "done" ? "✓" : String(i + 1)}>{step.label}</Step>)}
        {unresolved.length > 0 && (
          <>
            <h2 className="m-0 mt-3 text-xs tracking-wide text-muted-foreground uppercase">Open questions</h2>
            {unresolved.slice(0, 4).map((q) => <Step key={q} state="active" mark="○" className="font-normal">{q}</Step>)}
          </>
        )}
      </CardContent>
    </Card>
  );
}
