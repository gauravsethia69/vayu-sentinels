import type { ReactNode } from "react";
import { Activity, CircleAlert, Database, Radio } from "lucide-react";

export function stateTone(state?: string | null) {
  const normalized = (state ?? "").toLowerCase();
  if (["healthy", "online", "connected", "validated", "running", "complete"].includes(normalized)) {
    return { color: "#15803d", bg: "rgba(22,163,74,0.09)", border: "rgba(22,163,74,0.2)" };
  }
  if (["critical", "critical_inspection", "communication_failure", "offline", "disconnected"].includes(normalized)) {
    return { color: "#dc2626", bg: "rgba(239,68,68,0.08)", border: "rgba(239,68,68,0.2)" };
  }
  if (["warning", "observe", "inspect_soon", "delayed", "recovering", "estimated"].includes(normalized)) {
    return { color: "#b45309", bg: "rgba(217,119,6,0.08)", border: "rgba(217,119,6,0.2)" };
  }
  return { color: "#3269AB", bg: "rgba(50,105,171,0.08)", border: "rgba(50,105,171,0.18)" };
}

export function StatusPill({
  label,
  state,
  pulse = false,
}: {
  label?: string;
  state?: string | null;
  pulse?: boolean;
}) {
  const safeState =
    typeof state === "string" && state.trim().length > 0
      ? state
      : "unavailable";

  const tone = stateTone(safeState);

  return (
    <span
      className="status-pill"
      style={{
        color: tone.color,
        background: tone.bg,
        borderColor: tone.border,
      }}
      aria-label={`${label ? `${label}: ` : ""}${safeState}`}
    >
      {pulse && (
        <span className="relative flex h-1.5 w-1.5" aria-hidden="true">
          <span
            className="animate-ping-soft absolute inline-flex h-full w-full rounded-full"
            style={{ background: tone.color }}
          />
          <span
            className="relative inline-flex h-1.5 w-1.5 rounded-full"
            style={{ background: tone.color }}
          />
        </span>
      )}

      {label && <span className="status-pill-label">{label}</span>}

      <strong>
        {safeState.replace(/_/g, " ").toUpperCase()}
      </strong>
    </span>
  );
}

export function PanelHeading({ eyebrow, title, action }: { eyebrow?: string; title: string; action?: ReactNode }) {
  return (
    <div className="panel-heading">
      <div>
        {eyebrow && <p className="panel-eyebrow">{eyebrow}</p>}
        <h2>{title}</h2>
      </div>
      {action}
    </div>
  );
}

export function EmptyState({ type = "data", title, message }: { type?: "data" | "connection" | "event"; title: string; message: string }) {
  const Icon = type === "connection" ? Radio : type === "event" ? CircleAlert : Database;
  return (
    <div className="empty-state" role="status">
      <Icon size={22} aria-hidden="true" />
      <strong>{title}</strong>
      <span>{message}</span>
    </div>
  );
}

export function DashboardSkeleton() {
  return (
    <div className="dashboard-skeleton" aria-label="Loading dashboard data">
      <div className="skeleton-row">
        {Array.from({ length: 5 }, (_, index) => <div className="skeleton-card" key={index} />)}
      </div>
      <div className="skeleton-panel" />
      <div className="skeleton-grid">
        <div className="skeleton-panel short" />
        <div className="skeleton-panel short" />
      </div>
    </div>
  );
}

export function ModeBanner({ online, message }: { online: boolean; message?: string | null }) {
  if (online) return null;
  return (
    <div className="mode-banner" role="status">
      <Activity size={16} aria-hidden="true" />
      <strong>LIVE DATA DEGRADED</strong>
      <span>{message ? `Last known readings remain visible: ${message}` : "Backend offline; waiting to reconnect."}</span>
    </div>
  );
}
