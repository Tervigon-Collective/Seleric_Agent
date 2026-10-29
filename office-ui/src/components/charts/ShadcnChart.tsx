import {
  Area, Bar, CartesianGrid, Cell, ComposedChart, Funnel, FunnelChart, LabelList, Line, Pie, PieChart,
  PolarAngleAxis, PolarGrid, Radar, RadarChart, ReferenceLine, Scatter, ScatterChart, Treemap, XAxis, YAxis,
} from "recharts";
import {
  ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent, type ChartConfig,
} from "@/components/ui/chart";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatAxisTick, type ChartPlan, type ChartSpec, type Plot } from "./chartPlan";

type Row = Record<string, any>;
type Of<K extends ChartPlan["kind"]> = Extract<ChartPlan, { kind: K }>;

const color = (index: number) => `var(--chart-${(index % 7) + 1})`;
const percent = (share: number | null | undefined) => `${share && share < 0 ? "−" : share ? "+" : ""}${Math.round(Math.abs(share ?? 0) * 100)}%`;

function plotsConfig(plots: Plot[]): ChartConfig {
  return Object.fromEntries(plots.map((p, i) => [p.key, { label: p.name, color: color(i) }]));
}

/** Series marks over one shared category axis. */
function marks(plots: Plot[], rows: Row[], selected: number | null, horizontal: boolean) {
  const dim = (index: number) => (selected === null || selected === index ? 1 : 0.35);
  return plots.map((plot, i) => {
    const common = { dataKey: plot.key, ...(horizontal ? {} : { yAxisId: plot.axis }) };
    if (plot.kind === "line") return <Line key={plot.key} {...common} type="monotone" stroke={color(i)} strokeWidth={2} dot={false} connectNulls />;
    if (plot.kind === "area") {
      return <Area key={plot.key} {...common} type="monotone" stackId={plot.stack} stroke={color(i)} fill={color(i)} fillOpacity={plot.fillOpacity ?? 0.12} connectNulls />;
    }
    return (
      <Bar key={plot.key} {...common} fill={color(i)} stackId={plot.stack} radius={plot.stack ? 0 : horizontal ? [0, 3, 3, 0] : [3, 3, 0, 0]}>
        {rows.map((_, index) => <Cell key={index} fillOpacity={dim(index)} />)}
      </Bar>
    );
  });
}

function Cartesian({ plan, rows, selected, onSelect, height }: { plan: Of<"cartesian">; rows: Row[]; selected: number | null; onSelect: (i: number) => void; height: number | string }) {
  const { plots, axes, xKey, horizontal } = plan;
  return (
    <ChartContainer config={plotsConfig(plots)} className="w-full" style={{ height }}>
      <ComposedChart
        data={rows}
        layout={horizontal ? "vertical" : "horizontal"}
        onClick={(state) => { if (typeof state?.activeTooltipIndex === "number") onSelect(state.activeTooltipIndex); }}
      >
        <CartesianGrid vertical={horizontal} horizontal={!horizontal} />
        {horizontal ? (
          <>
            <XAxis type="number" tickLine={false} axisLine={false} tickFormatter={formatAxisTick} />
            <YAxis dataKey={xKey} type="category" tickLine={false} axisLine={false} width={110} />
          </>
        ) : (
          <>
            <XAxis dataKey={xKey} tickLine={false} axisLine={false} minTickGap={24} />
            {axes.map((axis) => (
              <YAxis key={axis.index} yAxisId={axis.index} orientation={axis.index > 0 ? "right" : "left"} tickLine={false} axisLine={false} width={48} tickFormatter={formatAxisTick} />
            ))}
          </>
        )}
        <ChartTooltip content={<ChartTooltipContent />} />
        {plots.length > 1 && <ChartLegend content={<ChartLegendContent />} />}
        {marks(plots, rows, selected, horizontal)}
      </ComposedChart>
    </ChartContainer>
  );
}

/** One small chart per panel sharing the x axis: a count is never read against a rupee scale. */
function Panels({ plan, rows }: { plan: Of<"panels">; rows: Row[] }) {
  return (
    <div className="flex w-full flex-col gap-2">
      {plan.panels.map((panel, index) => (
        <div key={panel.title}>
          <p className="text-xs font-medium text-muted-foreground">{panel.title}{panel.unit ? ` (${panel.unit})` : ""}</p>
          <ChartContainer config={plotsConfig(panel.plots)} className="w-full" style={{ height: 112 }}>
            <ComposedChart data={rows}>
              <CartesianGrid vertical={false} />
              <XAxis dataKey={plan.xKey} tickLine={false} axisLine={false} minTickGap={24} hide={index < plan.panels.length - 1} />
              <YAxis yAxisId={0} tickLine={false} axisLine={false} width={48} tickFormatter={formatAxisTick} />
              <ChartTooltip content={<ChartTooltipContent />} />
              {marks(panel.plots, rows, null, false)}
            </ComposedChart>
          </ChartContainer>
        </div>
      ))}
    </div>
  );
}

function Waterfall({ plan, selected, onSelect, height }: { plan: Of<"waterfall">; selected: number | null; onSelect: (i: number) => void; height: number | string }) {
  const steps = plan.steps.map((s) => ({ ...s, range: [s.from, s.to] }));
  const tone = { up: "var(--success)", down: "var(--danger)", total: "var(--brand)" } as const;
  return (
    <ChartContainer config={{ range: { label: "Change" } }} className="w-full" style={{ height }}>
      <ComposedChart data={steps} onClick={(state) => { if (typeof state?.activeTooltipIndex === "number") onSelect(state.activeTooltipIndex); }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="label" tickLine={false} axisLine={false} interval={0} minTickGap={8} />
        <YAxis tickLine={false} axisLine={false} width={48} tickFormatter={formatAxisTick} />
        <ChartTooltip content={<ChartTooltipContent hideLabel formatter={(_v, _n, item) => <span>{item.payload.label}: {item.payload.value.toLocaleString("en-IN")}</span>} />} />
        <Bar dataKey="range" radius={3}>
          {steps.map((step, i) => <Cell key={i} fill={tone[step.tone]} fillOpacity={selected === null || selected === i ? 1 : 0.35} />)}
        </Bar>
      </ComposedChart>
    </ChartContainer>
  );
}

function TreemapNode({ x, y, width, height, name, fill }: any) {
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={fill} stroke="var(--background)" strokeWidth={2} />
      {width > 60 && height > 24 && <text x={x + 6} y={y + 16} fill="var(--on-accent)" fontSize={11}>{name}</text>}
    </g>
  );
}

function Slices({ plan, selected, onSelect, height }: { plan: Of<"slices">; selected: number | null; onSelect: (i: number) => void; height: number | string }) {
  const { slices, form, radius } = plan;
  const config: ChartConfig = Object.fromEntries(slices.map((s, i) => [s.name, { label: s.name, color: color(i) }]));
  const dim = (i: number) => (selected === null || selected === i ? 1 : 0.35);
  if (form === "treemap") {
    return (
      <ChartContainer config={config} className="w-full" style={{ height }}>
        <Treemap data={slices.map((s, i) => ({ name: s.name, size: s.value, fill: color(i) }))} dataKey="size" nameKey="name" content={<TreemapNode />} isAnimationActive={false}>
          <ChartTooltip content={<ChartTooltipContent hideLabel />} />
        </Treemap>
      </ChartContainer>
    );
  }
  if (form === "funnel") {
    return (
      <ChartContainer config={config} className="w-full" style={{ height }}>
        <FunnelChart>
          <ChartTooltip content={<ChartTooltipContent hideLabel nameKey="name" />} />
          <Funnel data={slices.map((s, i) => ({ ...s, fill: color(i), fillOpacity: dim(i) }))} dataKey="value" nameKey="name" onClick={(_, i) => onSelect(i)}>
            <LabelList dataKey="name" position="right" className="fill-foreground" stroke="none" />
          </Funnel>
        </FunnelChart>
      </ChartContainer>
    );
  }
  return (
    <ChartContainer config={config} className="w-full" style={{ height }}>
      <PieChart>
        <ChartTooltip content={<ChartTooltipContent hideLabel nameKey="name" />} />
        <Pie data={slices} dataKey="value" nameKey="name" innerRadius={radius[0]} outerRadius={radius[1]} strokeWidth={2} onClick={(_, i) => onSelect(i)}>
          {slices.map((_, i) => <Cell key={i} fill={color(i)} fillOpacity={dim(i)} />)}
        </Pie>
        <ChartLegend content={<ChartLegendContent nameKey="name" />} />
      </PieChart>
    </ChartContainer>
  );
}

function RadarForm({ plan, rows, height }: { plan: Of<"radar">; rows: Row[]; height: number | string }) {
  // Indicators have different maxima, so every axis is drawn as a share of its own max.
  const data = plan.indicators.map((ind) => {
    const row = rows.find((r) => r.indicator === ind.name);
    return { indicator: ind.name, ...Object.fromEntries(plan.series.map((s) => [s.key, ind.max ? ((Number(row?.[s.key]) || 0) / ind.max) * 100 : 0])) };
  });
  const config: ChartConfig = Object.fromEntries(plan.series.map((s, i) => [s.key, { label: s.name, color: color(i) }]));
  return (
    <ChartContainer config={config} className="mx-auto w-full" style={{ height }}>
      <RadarChart data={data}>
        <ChartTooltip content={<ChartTooltipContent />} />
        <PolarAngleAxis dataKey="indicator" />
        <PolarGrid />
        {plan.series.map((s, i) => <Radar key={s.key} dataKey={s.key} stroke={color(i)} fill={color(i)} fillOpacity={0.2} />)}
        {plan.series.length > 1 && <ChartLegend content={<ChartLegendContent />} />}
      </RadarChart>
    </ChartContainer>
  );
}

function ScatterForm({ plan, height }: { plan: Of<"scatter">; height: number | string }) {
  return (
    <ChartContainer config={{ y: { label: plan.name, color: color(0) } }} className="w-full" style={{ height }}>
      <ScatterChart>
        <CartesianGrid />
        <XAxis type="number" dataKey="x" tickLine={false} axisLine={false} tickFormatter={formatAxisTick} />
        <YAxis type="number" dataKey="y" tickLine={false} axisLine={false} width={48} tickFormatter={formatAxisTick} />
        <ChartTooltip content={<ChartTooltipContent hideLabel />} />
        <Scatter data={plan.points} fill={color(0)} />
      </ScatterChart>
    </ChartContainer>
  );
}

/** Recharts has no heatmap: a stock Table whose cells are tinted from the first chart colour. */
function Heatmap({ plan }: { plan: Of<"heatmap"> }) {
  const grid = new Map(plan.cells.map(([x, y, v]) => [`${x}:${y}`, v]));
  const span = plan.max - plan.min || 1;
  return (
    <div className="rounded-md border">
      <Table aria-label="Heatmap">
        <TableHeader>
          <TableRow>
            <TableHead />
            {plan.xs.map((x) => <TableHead key={x} scope="col" className="text-right">{x}</TableHead>)}
          </TableRow>
        </TableHeader>
        <TableBody>
          {plan.ys.map((y, yi) => (
            <TableRow key={y}>
              <TableHead scope="row">{y}</TableHead>
              {plan.xs.map((x, xi) => {
                const value = grid.get(`${xi}:${yi}`);
                const tint = value === undefined ? 0 : Math.round(((value - plan.min) / span) * 70);
                return (
                  <TableCell key={x} className="text-right tabular-nums" style={{ backgroundColor: `color-mix(in oklab, var(--chart-1) ${tint}%, transparent)` }}>
                    {value === undefined ? "—" : value.toLocaleString("en-IN")}
                  </TableCell>
                );
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/** Drivers of the change: signed share-of-change, with each classification written out (never colour-only). */
function Causal({ plan, spec }: { plan: Of<"causal">; spec: ChartSpec }) {
  const nameOf = new Map((spec.nodes ?? []).map((n) => [n.id, n.name || n.id]));
  const data = plan.drivers.map((d) => ({ name: d.name || d.id, share: Math.round((d.share_of_change ?? 0) * 100) }));
  const feeds = (id: string) => plan.links.filter((l) => l.source === id).map((l) => nameOf.get(l.target) ?? l.target).join(", ") || "—";
  if (plan.drivers.length === 0) return <p className="text-sm text-muted-foreground">No drivers were identified for this change.</p>;
  return (
    <div className="flex flex-col gap-3">
      <ChartContainer config={{ share: { label: "Share of change", color: color(0) } }} className="w-full" style={{ height: 40 + data.length * 32 }}>
        <ComposedChart data={data} layout="vertical">
          <CartesianGrid horizontal={false} />
          <XAxis type="number" tickLine={false} axisLine={false} unit="%" />
          <YAxis dataKey="name" type="category" tickLine={false} axisLine={false} width={110} />
          <ReferenceLine x={0} stroke="var(--border)" />
          <ChartTooltip content={<ChartTooltipContent hideLabel />} />
          <Bar dataKey="share" radius={3}>
            {data.map((d, i) => <Cell key={i} fill={d.share >= 0 ? "var(--success)" : "var(--danger)"} />)}
          </Bar>
        </ComposedChart>
      </ChartContainer>
      <div className="rounded-md border">
        <Table aria-label="Drivers of the change">
          <TableHeader>
            <TableRow>
              <TableHead scope="col">Driver</TableHead>
              <TableHead scope="col">Classification</TableHead>
              <TableHead scope="col" className="text-right">Share of change</TableHead>
              <TableHead scope="col">Feeds</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {plan.drivers.map((d) => (
              <TableRow key={d.id}>
                <TableCell>{d.name || d.id}</TableCell>
                <TableCell>{d.status === "ruled_out" ? "ruled out" : (d.classification ?? d.role ?? "—").replaceAll("_", " ")}</TableCell>
                <TableCell className="text-right tabular-nums">{percent(d.share_of_change)}</TableCell>
                <TableCell>{feeds(d.id)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

export function ShadcnChart({ plan, spec, selected, onSelect, height }: {
  plan: ChartPlan;
  spec: ChartSpec;
  selected: number | null;
  onSelect: (index: number) => void;
  height: number | string;
}) {
  const rows = (Array.isArray(spec.data) ? spec.data : []) as Row[];
  switch (plan.kind) {
    case "cartesian": return <Cartesian plan={plan} rows={rows} selected={selected} onSelect={onSelect} height={height} />;
    case "panels": return <Panels plan={plan} rows={rows} />;
    case "waterfall": return <Waterfall plan={plan} selected={selected} onSelect={onSelect} height={height} />;
    case "slices": return <Slices plan={plan} selected={selected} onSelect={onSelect} height={height} />;
    case "radar": return <RadarForm plan={plan} rows={rows} height={height} />;
    case "scatter": return <ScatterForm plan={plan} height={height} />;
    case "heatmap": return <Heatmap plan={plan} />;
    case "causal": return <Causal plan={plan} spec={spec} />;
  }
}
