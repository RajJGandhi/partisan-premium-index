import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { formatPremium, premiumTone } from "../lib/format";

interface PremiumBadgeProps {
  value: number | null | undefined;
  size?: "small" | "large";
}

export function PremiumBadge({ value, size = "small" }: PremiumBadgeProps) {
  const tone = premiumTone(value);
  const Icon = tone === "positive" ? ArrowUpRight : tone === "negative" ? ArrowDownRight : Minus;
  const label = value == null ? "Awaiting fair value" : formatPremium(value);

  return (
    <span className={`premium-badge premium-badge--${tone} premium-badge--${size}`}>
      <Icon size={size === "large" ? 18 : 14} aria-hidden="true" />
      {label}
    </span>
  );
}
