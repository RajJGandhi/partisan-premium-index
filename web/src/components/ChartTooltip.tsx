import type { TooltipProps } from "recharts";
import { formatPremium, formatProbability } from "../lib/format";

interface TooltipDatum {
  name?: string;
  value?: number | string;
  dataKey?: string;
  color?: string;
}

export function ProbabilityTooltip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <strong>{label}</strong>
      {(payload as TooltipDatum[]).map((item) => (
        <div key={item.dataKey ?? item.name}>
          <span className="chart-tooltip__dot" style={{ background: item.color }} />
          <span>{item.name}</span>
          <b>{formatProbability(Number(item.value))}</b>
        </div>
      ))}
    </div>
  );
}

export function PremiumTooltip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const item = payload[0] as TooltipDatum | undefined;
  return (
    <div className="chart-tooltip">
      <strong>{label}</strong>
      <div>
        <span className="chart-tooltip__dot" style={{ background: item?.color }} />
        <span>{item?.name}</span>
        <b>{formatPremium(Number(item?.value))}</b>
      </div>
    </div>
  );
}
