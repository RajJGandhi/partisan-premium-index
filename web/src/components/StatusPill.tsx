import { CircleCheckBig, CircleMinus, CircleX, Clock3 } from "lucide-react";
import { labelize } from "../lib/format";

interface StatusPillProps {
  status: string | null | undefined;
  compact?: boolean;
}

export function StatusPill({ status, compact = false }: StatusPillProps) {
  const normalized = (status ?? "UNKNOWN").toUpperCase();
  const tone = ["OK", "SUCCESS", "FRESH", "OPEN", "AUTO_ACCEPTED", "APPROVED"].includes(normalized)
    ? "success"
    : ["FAILED", "ERROR", "STALE", "REJECTED"].includes(normalized)
      ? "danger"
      : ["PARTIAL", "PENDING", "NO_DATA", "NO_RUNS"].includes(normalized)
        ? "warning"
        : "neutral";

  const Icon = tone === "success" ? CircleCheckBig : tone === "danger" ? CircleX : tone === "warning" ? Clock3 : CircleMinus;

  return (
    <span className={`status-pill status-pill--${tone}${compact ? " status-pill--compact" : ""}`}>
      <Icon size={compact ? 12 : 14} aria-hidden="true" />
      {labelize(normalized)}
    </span>
  );
}
