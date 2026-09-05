import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, CheckCircle2, CloudSun, Radio, X } from "lucide-react";
import type { AnomalyEvent } from "../../api/types";
import { formatEventTime, formatValue, isEnvironmentalEvent, titleCase } from "../../utils/format";
import { EmptyState, PanelHeading, StatusPill, stateTone } from "./StatusUi";

export type EventFilter = "all" | "critical" | "warnings" | "recovery" | "environmental";

function matchesFilter(event: AnomalyEvent, filter: EventFilter) {
  if (filter === "all") return true;
  if (filter === "critical") return ["critical", "high"].includes(event.severity);
  if (filter === "warnings") return ["warning", "observe"].includes(event.severity);
  if (filter === "recovery") return event.event_type === "recovery" || event.anomaly_type.includes("recover");
  return isEnvironmentalEvent(event);
}

export function EventTimeline({ events, onSelect, initialFilter = "all", limit }: { events: AnomalyEvent[]; onSelect: (event: AnomalyEvent) => void; initialFilter?: EventFilter; limit?: number }) {
  const [filter, setFilter] = useState<EventFilter>(initialFilter);
  const filtered = useMemo(() => events.filter((event) => matchesFilter(event, filter)).slice(0, limit), [events, filter, limit]);

  return (
    <section className="glass-card event-panel">
      <PanelHeading
        eyebrow="Detector output"
        title="Event timeline"
        action={(
          <select value={filter} onChange={(event) => setFilter(event.target.value as EventFilter)} aria-label="Filter anomaly events">
            <option value="all">All</option>
            <option value="critical">Critical</option>
            <option value="warnings">Warnings</option>
            <option value="recovery">Recovery</option>
            <option value="environmental">Environmental</option>
          </select>
        )}
      />
      {filtered.length ? (
        <div className="event-list">
          {filtered.map((event) => {
            const environmental = isEnvironmentalEvent(event);
            const recovering = event.event_type === "recovery";
            const tone = environmental ? stateTone("info") : stateTone(recovering ? "healthy" : event.severity);
            const Icon = environmental ? CloudSun : recovering ? CheckCircle2 : AlertTriangle;
            return (
              <button key={`${event.timestamp}-${event.node_id}-${event.anomaly_type}-${event.parameter}`} onClick={() => onSelect(event)} className="event-row">
                <span className="event-icon" style={{ color: tone.color, background: tone.bg }}><Icon size={15} /></span>
                <span className="event-copy">
                  <strong>{event.message || titleCase(event.anomaly_type)}</strong>
                  <small>{event.node_id.replace("_", "-")} · {titleCase(event.parameter)}</small>
                </span>
                <span className="event-meta"><time>{formatEventTime(event.timestamp)}</time><em style={{ color: tone.color }}>{event.severity}</em></span>
              </button>
            );
          })}
        </div>
      ) : <EmptyState type="event" title="No matching events" message="Events matching this filter will appear here." />}
    </section>
  );
}

export function AnomalyDrawer({ event, onClose }: { event: AnomalyEvent | null; onClose: () => void }) {
  useEffect(() => {
    if (!event) return;
    const closeOnEscape = (keyboardEvent: KeyboardEvent) => {
      if (keyboardEvent.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [event, onClose]);

  if (!event) return null;
  const contributions = Object.entries(event.factor_contributions ?? {});

  return (
    <div className="drawer-layer" role="dialog" aria-modal="true" aria-labelledby="anomaly-drawer-title">
      <button className="drawer-backdrop" onClick={onClose} aria-label="Close anomaly analysis" />
      <aside className="anomaly-drawer">
        <header>
          <div>
            <p className="panel-eyebrow">Explainability</p>
            <h2 id="anomaly-drawer-title">{titleCase(event.anomaly_type)}</h2>
            <span>{event.node_id.replace("_", "-")} · {formatEventTime(event.timestamp)}</span>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close drawer" title="Close"><X size={18} /></button>
        </header>

        <div className="drawer-body">
          <div className="drawer-metrics">
            <div><span>Classification</span><strong>{titleCase(event.anomaly_type)}</strong></div>
            <div><span>Affected sensor</span><strong>{event.suspected_sensor ?? event.affected_sensor ?? titleCase(event.parameter)}</strong></div>
            <div><span>Confidence</span><strong>{event.confidence}%</strong></div>
            <div><span>Severity</span><strong><StatusPill state={event.severity} /></strong></div>
            <div><span>Observed</span><strong>{formatValue(event.observed_value, "")}</strong></div>
            <div><span>Expected</span><strong>{formatValue(event.expected_value, "")}</strong></div>
            <div><span>Trusted / corrected</span><strong>{formatValue(event.corrected_value, "")}</strong></div>
            <div><span>Detector</span><strong>{event.detector_mode}</strong></div>
            <div><span>Model version</span><strong>{event.model_version}</strong></div>
            {event.ml_prediction && <div><span>RF assessment</span><strong>{titleCase(event.ml_prediction)}</strong></div>}
            {typeof event.ml_confidence === "number" && <div><span>RF confidence</span><strong>{Math.round(event.ml_confidence * 100)}%</strong></div>}
          </div>

          <section>
            <h3>Evidence</h3>
            {event.reasons?.length ? (
              <ul className="reason-list">{event.reasons.map((reason) => <li key={reason}><Check size={14} />{reason}</li>)}</ul>
            ) : <p className="muted-copy">No additional reasons were supplied.</p>}
          </section>

          <section>
            <h3>Factor contributions</h3>
            {contributions.length ? contributions.map(([label, value]) => (
              <div className="factor-row" key={label}>
                <div><span>{titleCase(label)}</span><strong>{value}%</strong></div>
                <div className="factor-track"><span style={{ width: `${Math.min(100, Math.max(0, value))}%` }} /></div>
              </div>
            )) : <p className="muted-copy">No factor contributions were supplied.</p>}
          </section>

          <section className="recommended-action">
            <h3>Recommended action</h3>
            <p>{event.recommended_action || "No recommendation supplied."}</p>
          </section>
        </div>
      </aside>
    </div>
  );
}

export function EnvironmentalEventCard({ events }: { events: AnomalyEvent[] }) {
  const event = events.find(isEnvironmentalEvent);

  return (
    <section className="glass-card environmental-card">
      <div className="environmental-head"><CloudSun size={20} /><div><p className="panel-eyebrow">Environmental classification</p><h3>{event ? "Likely Genuine Weather Change" : "No active environmental change"}</h3></div></div>
      {event ? (
        <>
          <p>{event.message}</p>
          <div className="environmental-facts"><span>{event.node_id.replace("_", "-")}</span><span>Sensor health unaffected</span><strong>{event.confidence}% confidence</strong></div>
        </>
      ) : <p>Coherent multi-node changes will appear here without being styled as sensor faults.</p>}
    </section>
  );
}

export function RecoveryPanel({ events }: { events: AnomalyEvent[] }) {
  const recovery = events.find((event) => event.event_type === "recovery" || event.anomaly_type.includes("recover"));
  const samples = Math.min(5, recovery?.healthy_samples ?? Number(recovery?.factor_contributions?.healthy_streak ?? 0));
  return (
    <section className="glass-card recovery-panel">
      <PanelHeading eyebrow="Backend-controlled state" title="Recovery" />
      <div className="recovery-flow">
        <span className={recovery ? "done" : ""}><AlertTriangle size={14} />Anomalous</span>
        <i>→</i>
        <span className={recovery && samples < 5 ? "active" : recovery ? "done" : ""}><Radio size={14} />Recovering</span>
        <i>→</i>
        <span className={recovery && samples >= 5 ? "done" : ""}><CheckCircle2 size={14} />Healthy</span>
      </div>
      {recovery ? (
        <>
          <div className="recovery-progress"><div><span>Healthy readings</span><strong>{samples} / 5</strong></div><div><span style={{ width: `${samples * 20}%` }} /></div></div>
          <p>{recovery.message}</p>
        </>
      ) : <p>No recovery is active. Fault release alone does not mark a sensor healthy.</p>}
    </section>
  );
}
