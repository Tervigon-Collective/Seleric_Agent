import { lazy, Suspense, type ComponentProps } from "react";
import { Skeleton } from "@/components/ui/skeleton";

// recharts is the heaviest dependency; keep it out of the first-paint bundle.
const Chart = lazy(() => import("./ShadcnChart").then((m) => ({ default: m.ShadcnChart })));

export function ShadcnChart(props: ComponentProps<typeof Chart>) {
  return (
    <Suspense fallback={<Skeleton className="h-72 w-full" role="status" aria-busy="true" aria-label="Loading chart" />}>
      <Chart {...props} />
    </Suspense>
  );
}
