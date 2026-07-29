import { AlertTriangle, DatabaseZap, LoaderCircle, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

export function LoadingState({ label = "Loading public data…" }: { label?: string }) {
  return (
    <div className="state-view" role="status">
      <LoaderCircle className="state-view__spinner" size={28} aria-hidden="true" />
      <strong>{label}</strong>
      <span>The static data bundle is being read from the latest published build.</span>
    </div>
  );
}

export function ErrorState({ error }: { error: Error }) {
  return (
    <div className="state-view state-view--error" role="alert">
      <AlertTriangle size={30} aria-hidden="true" />
      <strong>Public data is temporarily unavailable</strong>
      <span>{error.message}</span>
      <button type="button" className="button button--secondary" onClick={() => window.location.reload()}>
        <RefreshCw size={16} aria-hidden="true" />
        Retry
      </button>
    </div>
  );
}

interface EmptyStateProps {
  title: string;
  description: string;
  action?: ReactNode;
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <DatabaseZap size={30} aria-hidden="true" />
      <strong>{title}</strong>
      <span>{description}</span>
      {action}
    </div>
  );
}
