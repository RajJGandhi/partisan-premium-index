import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatShortDate, labelize } from "../lib/format";
import type { DailyIndexPoint, FairValueComponent, Snapshot } from "../lib/types";
import { PremiumTooltip, ProbabilityTooltip } from "./ChartTooltip";
import { EmptyState } from "./StateViews";

export function IndexHistoryChart({ history }: { history: DailyIndexPoint[] }) {
  const data = history
    .filter((point) => point.date && point.average_signed_premium != null)
    .map((point) => ({
      date: formatShortDate(point.date),
      premium: point.average_signed_premium,
      absolute: point.average_absolute_premium,
    }));

  if (data.length < 2) {
    return <EmptyState title="Index history is building" description="The chart appears after at least two published index observations." />;
  }

  return (
    <div className="chart-frame chart-frame--tall" aria-label="Partisan premium index history">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 12, right: 8, left: -14, bottom: 0 }}>
          <defs>
            <linearGradient id="indexFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--chart-accent)" stopOpacity={0.28} />
              <stop offset="100%" stopColor="var(--chart-accent)" stopOpacity={0.01} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
          <XAxis dataKey="date" tick={{ fill: "var(--text-muted)", fontSize: 12 }} axisLine={false} tickLine={false} minTickGap={28} />
          <YAxis
            tickFormatter={(value: number) => `${(value * 100).toFixed(0)}`}
            tick={{ fill: "var(--text-muted)", fontSize: 12 }}
            axisLine={false}
            tickLine={false}
            width={34}
          />
          <Tooltip content={<PremiumTooltip />} />
          <ReferenceLine y={0} stroke="var(--border-strong)" />
          <Area type="monotone" dataKey="premium" name="Average signed premium" stroke="var(--chart-accent)" strokeWidth={2.5} fill="url(#indexFill)" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function MarketHistoryChart({ history }: { history: Snapshot[] }) {
  const data = history
    .filter((point) => point.observed_at && point.market_probability != null)
    .map((point) => ({
      date: formatShortDate(point.observed_at),
      market: point.market_probability,
      ppi: point.ppi_fair_value,
    }));

  if (data.length < 2) {
    return <EmptyState title="History is building" description="This chart appears after the market has at least two daily observations." />;
  }

  return (
    <div className="chart-frame chart-frame--market" aria-label="Market and PPI fair-value history">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 10, right: 8, left: -14, bottom: 0 }}>
          <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
          <XAxis dataKey="date" tick={{ fill: "var(--text-muted)", fontSize: 12 }} axisLine={false} tickLine={false} minTickGap={28} />
          <YAxis
            domain={[0, 1]}
            tickFormatter={(value: number) => `${Math.round(value * 100)}%`}
            tick={{ fill: "var(--text-muted)", fontSize: 12 }}
            axisLine={false}
            tickLine={false}
            width={42}
          />
          <Tooltip content={<ProbabilityTooltip />} />
          <Legend wrapperStyle={{ fontSize: 12, color: "var(--text-muted)" }} />
          <Line type="monotone" dataKey="market" name="Market probability" stroke="var(--chart-market)" strokeWidth={2.5} dot={false} activeDot={{ r: 4 }} connectNulls />
          <Line type="monotone" dataKey="ppi" name="PPI fair value" stroke="var(--chart-ppi)" strokeWidth={2.5} dot={false} activeDot={{ r: 4 }} connectNulls />
        </LineChart>
      </ResponsiveContainer>
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
    return <EmptyState title="No published components yet" description="Component values appear after the first human-approved fair-value publication." />;
  }

  return (
    <div className="chart-frame chart-frame--components" aria-label="Fair-value component probabilities">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, left: 22, bottom: 0 }}>
          <CartesianGrid stroke="var(--chart-grid)" horizontal={false} />
          <XAxis type="number" domain={[0, 1]} tickFormatter={(value: number) => `${Math.round(value * 100)}%`} tick={{ fill: "var(--text-muted)", fontSize: 12 }} axisLine={false} tickLine={false} />
          <YAxis type="category" dataKey="component" width={88} tick={{ fill: "var(--text-muted)", fontSize: 12 }} axisLine={false} tickLine={false} />
          <Tooltip content={<ProbabilityTooltip />} />
          <Bar dataKey="probability" name="Component value" fill="var(--chart-ppi)" radius={[0, 7, 7, 0]} barSize={18} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
