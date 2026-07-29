import { Clock3 } from "lucide-react";
import { formatDateTime, relativeTime } from "../lib/format";

export function DataStamp({ generatedAt }: { generatedAt: string | null | undefined }) {
  return (
    <div className="data-stamp" title={formatDateTime(generatedAt)}>
      <Clock3 size={14} aria-hidden="true" />
      Updated {relativeTime(generatedAt)}
    </div>
  );
}
