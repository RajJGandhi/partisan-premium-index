import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatObservationTick, labelize } from "../lib/format";
import type { DailyIndexPoint, FairValueComponent, Snapshot } from "../lib/types";
import { PremiumTooltip, ProbabilityTooltip } from "./ChartTooltip";
import { EmptyState } from "./StateViews";

const axisTick = { fill: "var(--text-muted)", fontSize: 11, fontFamily: "var(--font-mono)" };

export function IndexHistoryChart({ history }: { history: DailyIndexPoint[] }) {
  const data = history
    .filter((point) => (point.timestamp ?? point.date) && point.average_signed_premium != null)
    .map((point) => ({
      // Two runs on the same day are two points: label them "… AM" / "… PM" so the axis
      // does not collapse them onto one tick.
      date: formatObservationTick(point.timestamp ?? point.date, point.slot),
      premium: point.average_signed_premium,
      absolute: point.average_absolute_premium,
    }));

  if (data.length < 2) {
    return <EmptyState title="Index history is building" description="The chart appears after at least two published index observations." />;
  }

  return (
    <div className="chart-frame chart-frame--tall" aria-label="Partisan premium index history">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 6, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="indexFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--premium-fill)" stopOpacity={0.22} />
              <stop offset="100%" stopColor="var(--premium-fill)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
          <XAxis dataKey="date" tick={axisTick} axisLine={false} tickLine={false} minTickGap={30} />
          <YAxis
            tickFormatter={(value: number) => `${(value * 100).toFixed(1)}`}
            tick={axisTick}
            axisLine={false}
            tickLine={false}
            width={42}
            tickCount={5}
          />
          <Tooltip content={<PremiumTooltip />} cursor={{ stroke: "var(--border-strong)", strokeDasharray: "3 3" }} />
          <ReferenceLine y={0} stroke="var(--border-strong)" />
          <Area type="monotone" dataKey="premium" name="Average signed premium" stroke="var(--chart-model)" strokeWidth={2} fill="url(#indexFill)" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function MarketHistoryChart({ history }: { history: Snapshot[] }) {
  const data = history
    .filter((point) => point.observed_at && point.market_probability != null)
    .map((point) => ({
      date: formatObservationTick(point.observed_at, point.slot),
      market: point.market_probability,
      ppi: point.ppi_fair_value,
    }));

  if (data.length < 2) {
    return <EmptyState title="History is building" description="This chart appears after the market has at least two daily observations." />;
  }

  return (
    <div className="chart-frame chart-frame--market" aria-label="Market and PPI fair-value history">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 6, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
          <XAxis dataKey="date" tick={axisTick} axisLine={false} tickLine={false} minTickGap={30} />
          <YAxis
            domain={[0, 1]}
            tickFormatter={(value: number) => `${Math.round(value * 100)}`}
            tick={axisTick}
            axisLine={false}
            tickLine={false}
            width={34}
          />
          <Tooltip content={<ProbabilityTooltip />} cursor={{ stroke: "var(--border-strong)", strokeDasharray: "3 3" }} />
          <Line type="monotone" dataKey="market" name="Market probability" stroke="var(--chart-market)" strokeWidth={2} dot={false} activeDot={{ r: 3.5 }} connectNulls />
          <Line type="monotone" dataKey="ppi" name="Model fair value" stroke="var(--chart-model)" strokeWidth={2} strokeDasharray="0" dot={false} activeDot={{ r: 3.5 }} connectNulls />
        </LineChart>
      </ResponsiveContainer>
      <div className="chart-legend-inline">
        <span><i className="divergence-rail__dot divergence-rail__dot--market" aria-hidden="true" />Market</span>
        <span><i className="divergence-rail__dot divergence-rail__dot--model" aria-hidden="true" />Model fair value</span>
      </div>
    </div>
  );
}

export function ComponentChart({ components }: { components: FairValueComponent[] }) {
  const data = components
    .filter((component) => component.probability != null)
    .map((component) => ({
      component: labelize(component.type),
      probability: component.probability,
      weight: component.weight,
    }));

  if (!data.length) {
    return <EmptyState title="No published components yet" description="Component values appear after the first published fair-value forecast." />;
  }

  return (
    <div className="chart-frame chart-frame--components" aria-label="Fair-value component probabilities">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 20, left: 18, bottom: 0 }}>
          <CartesianGrid stroke="var(--chart-grid)" horizontal={false} />
          <XAxis type="number" domain={[0, 1]} tickFormatter={(value: number) => `${Math.round(value * 100)}`} tick={axisTick} axisLine={false} tickLine={false} />
          <YAxis type="category" dataKey="component" width={84} tick={{ fill: "var(--text-muted)", fontSize: 11, fontFamily: "var(--font-ui)" }} axisLine={false} tickLine={false} />
          <Tooltip content={<ProbabilityTooltip />} cursor={{ fill: "var(--surface-sunken)" }} />
          <Bar dataKey="probability" name="Component value" fill="var(--chart-model)" radius={[1, 1, 1, 1]} barSize={14} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
