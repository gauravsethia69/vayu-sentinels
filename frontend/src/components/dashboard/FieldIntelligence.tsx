import { useMemo, useState, type FormEvent } from "react";
import {
  CheckCircle2, CloudRain, Eye, Gauge, MapPinned, Plus, RadioTower,
  Send, ShieldQuestion, UserRound, Wind, Wrench, X,
} from "lucide-react";
import type { FieldReport, FieldReportCategory } from "../../api/types";
import {
  buildFieldReportPayload, categoryKind, filterFieldReports, initialFieldReportForm,
  quickReports, stationCategories, verificationLabel, weatherCategories,
  type FieldReportFormState,
} from "../../field-intelligence/model";
import { useSkyGuard } from "../../hooks/useSkyGuard";
import { displayNodeId, formatRelativeTime, titleCase } from "../../utils/format";
import { EmptyState, PanelHeading, StatusPill } from "./StatusUi";

function ReportCard({ report, onResolve }: { report: FieldReport; onResolve: (report: FieldReport) => void }) {
  const kind = categoryKind(report.category);
  return (
    <article className={`field-report-card ${kind} ${report.severity}`}>
      <header>
        <span className="field-report-icon">{kind === "station" ? <Wrench size={17} /> : <CloudRain size={17} />}</span>
        <div><p>{kind === "station" ? "AWS / Hardware Report" : "Weather Observation"}</p><h3>{titleCase(report.category)}</h3></div>
        <StatusPill state={report.status ?? "unavailable"} />
      </header>
      <p className="field-observation">{report.observation}</p>
      <div className="field-report-meta">
        <div><span>Reported by</span><strong>{report.reporter_name || titleCase(report.reporter_type)}</strong></div>
        <div><span>Location</span><strong>{report.location_label || "Prototype station area"}</strong></div>
        <div><span>Reported</span><strong>{formatRelativeTime(report.created_at)}</strong></div>
        <div><span>Severity</span><strong>{titleCase(report.severity)}</strong></div>
        <div><span>Reporter confidence</span><strong>{titleCase(report.reporter_confidence)}</strong></div>
        <div><span>Affected radius</span><strong>{report.radius_km} km</strong></div>
      </div>
      <div className="nearby-station-list">
        {report.nearby_stations.map((station) => (
          <span key={station.node_id}><RadioTower size={12} />{displayNodeId(station.node_id)}{station.distance_km != null ? ` · ${station.distance_km} km` : ""}</span>
        ))}
      </div>
      <div className="corroboration-block">
        <div><span>Sensor corroboration</span><strong>{verificationLabel(report)}</strong><em>{report.corroboration_confidence}%</em></div>
        <div className="corroboration-track"><span style={{ width: `${report.corroboration_confidence}%` }} /></div>
        <p>{report.message}</p>
        {report.evidence.slice(0, 3).map((item) => <small key={item}><CheckCircle2 size={11} />{item}</small>)}
      </div>
      {report.status === "active" && <button className="secondary-command full" onClick={() => onResolve(report)}><CheckCircle2 size={14} />Mark Resolved</button>}
    </article>
  );
}

function SpatialNetwork({ report }: { report: FieldReport | null }) {
  return (
    <section className="glass-card field-spatial-card">
      <PanelHeading eyebrow="Prototype proximity network" title="Nearby AWS response" action={<MapPinned size={18} />} />
      <div className="spatial-network" aria-label="Schematic field report to station proximity view">
        <div className="spatial-report-node"><CloudRain size={18} /><strong>{report ? titleCase(report.category) : "No Active Report"}</strong><span>Field report</span></div>
        <div className="spatial-links"><span /><span /></div>
        <div className="spatial-stations">
          {(["AWS_001", "AWS_002", "AWS_003"] as const).map((nodeId) => {
            const station = report?.nearby_stations.find((item) => item.node_id === nodeId);
            return <div key={nodeId}><RadioTower size={17} /><strong>{displayNodeId(nodeId)}</strong><span>{station?.distance_km != null ? `${station.distance_km} km` : station ? "Cluster match" : "Outside report area"}</span></div>;
          })}
        </div>
      </div>
    </section>
  );
}

export function FieldIntelligenceOverview() {
  const { fieldReports, monitoringContexts } = useSkyGuard();
  const active = fieldReports.find((report) => report.status === "active") ?? null;
  return (
    <section className="glass-card field-overview-card">
      <PanelHeading eyebrow="Controller observations" title="Field Intelligence" action={<StatusPill state={active ? "active" : "clear"} />} />
      {active ? (
        <div className="field-overview-content">
          <span className="field-report-icon"><CloudRain size={18} /></span>
          <div><strong>{titleCase(active.category)}</strong><p>{active.observation}</p><small>{formatRelativeTime(active.created_at)} · {verificationLabel(active)} · {active.corroboration_confidence}% sensor support</small></div>
          <div className="field-context-nodes">{active.nearby_stations.map((station) => <span key={station.node_id}>{displayNodeId(station.node_id)} · {titleCase(monitoringContexts[station.node_id]?.monitoring_mode)}</span>)}</div>
        </div>
      ) : <EmptyState title="No active field reports" message="Controller observations and station issues will appear here." />}
    </section>
  );
}

export default function FieldIntelligence({ onNotify }: { onNotify: (message: string, tone?: "success" | "error") => void }) {
  const { backendStatus, commandPending, commands, fieldReports, monitoringContexts } = useSkyGuard();
  const [filter, setFilter] = useState<"active" | "resolved" | "expired" | "all">("active");
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<FieldReportFormState>(initialFieldReportForm);
  const reports = useMemo(() => filterFieldReports(fieldReports, filter), [fieldReports, filter]);
  const active = fieldReports.find((report) => report.status === "active") ?? null;

  function openQuick(category: FieldReportCategory, observation: string) {
    const reportType = stationCategories.some((item) => item.value === category) ? "station" : "weather";
    setForm({ ...initialFieldReportForm, reportType, category, observation, expiry: reportType === "station" ? "until" : "60" });
    setFormOpen(true);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await commands.createFieldReport(buildFieldReportPayload(form));
      setFormOpen(false);
      setForm(initialFieldReportForm);
      onNotify("Field report submitted. Nearby station context updated.");
    } catch (cause) {
      onNotify(cause instanceof Error ? cause.message : "Field report failed", "error");
    }
  }

  async function resolve(report: FieldReport) {
    try {
      await commands.resolveFieldReport(report.id, "Resolved by controller from Field Intelligence.");
      onNotify("Field report resolved. Monitoring context recalculated.");
    } catch (cause) {
      onNotify(cause instanceof Error ? cause.message : "Resolve failed", "error");
    }
  }

  return (
    <div className="dashboard-view-stack field-intelligence-view">
      <div className="view-toolbar">
        <div><p className="panel-eyebrow">Human context · independent sensor evidence</p><h2>Active Field Intelligence</h2></div>
        <button className="primary-command field-add-command" disabled={backendStatus !== "online"} onClick={() => setFormOpen(true)}><Plus size={16} />Add Field Report</button>
      </div>

      <div className="field-summary-grid">
        <div><CloudRain size={17} /><span>Active reports</span><strong>{fieldReports.filter((item) => item.status === "active").length}</strong></div>
        <div><MapPinned size={17} /><span>Nearby weather</span><strong>{fieldReports.filter((item) => item.status === "active" && categoryKind(item.category) === "weather").length}</strong></div>
        <div><Wrench size={17} /><span>Station issues</span><strong>{fieldReports.filter((item) => item.status === "active" && categoryKind(item.category) === "station").length}</strong></div>
        <div><Gauge size={17} /><span>Monitoring</span><strong>{Object.values(monitoringContexts).some((context) => context.monitoring_mode !== "normal") ? "Heightened" : "Normal"}</strong></div>
      </div>

      <section className="glass-card quick-report-panel">
        <PanelHeading eyebrow="Judge-ready controller actions" title="Quick report" action={<span>Prefill only · confirm before submit</span>} />
        <div className="quick-report-grid">{quickReports.map((item) => <button key={item.category} onClick={() => openQuick(item.category, item.observation)}>{categoryKind(item.category) === "station" ? <Wrench size={15} /> : item.category.includes("wind") ? <Wind size={15} /> : item.category.includes("visibility") || item.category === "fog" ? <Eye size={15} /> : <CloudRain size={15} />}<span>{item.label}</span></button>)}</div>
      </section>

      <div className="field-layout"><SpatialNetwork report={active} /><section className="glass-card monitoring-context-card"><PanelHeading eyebrow="Recommended station behavior" title="Monitoring Context" /><div className="monitoring-context-list">{Object.values(monitoringContexts).map((context) => <div key={context.node_id}><RadioTower size={15} /><span><strong>{displayNodeId(context.node_id)}</strong><small>{context.active_context.length} nearby report{context.active_context.length === 1 ? "" : "s"}</small></span><em>{titleCase(context.monitoring_mode)}</em><b>{context.recommended_sample_interval_seconds}s recommended</b></div>)}</div><p>Recommendations are exposed to station firmware; SkyGuard does not assume the ESP32 changes its interval automatically.</p></section></div>

      <div className="field-history-head"><div className="segmented-control field-filter">{(["active", "resolved", "expired", "all"] as const).map((item) => <button className={filter === item ? "active" : ""} onClick={() => setFilter(item)} key={item}>{titleCase(item)}</button>)}</div></div>
      {reports.length ? <div className="field-report-grid">{reports.map((report) => <ReportCard key={report.id} report={report} onResolve={(item) => void resolve(item)} />)}</div> : <EmptyState title={`No ${filter} reports`} message="Field report history is retained without deleting resolved or expired records." />}

      {formOpen && (
        <div className="drawer-layer field-report-layer">
          <button className="drawer-backdrop" onClick={() => setFormOpen(false)} aria-label="Close field report form" />
          <form className="field-report-drawer" onSubmit={(event) => void submit(event)}>
            <header><div><p className="panel-eyebrow">Controller observation</p><h2>Add Field Report</h2><span>Reported context remains separate from detector evidence.</span></div><button type="button" className="icon-button" onClick={() => setFormOpen(false)} aria-label="Close"><X size={17} /></button></header>
            <div className="field-report-form">
              <fieldset><legend>Report type</legend><div className="segmented-control"><button type="button" className={form.reportType === "weather" ? "active" : ""} onClick={() => setForm((current) => ({ ...current, reportType: "weather", category: "clouds_approaching", expiry: "60" }))}>Weather Observation</button><button type="button" className={form.reportType === "station" ? "active" : ""} onClick={() => setForm((current) => ({ ...current, reportType: "station", category: "sensor_damage", expiry: "until" }))}>AWS / Hardware Issue</button></div></fieldset>
              <label><span>Category</span><select value={form.category} onChange={(event) => setForm((current) => ({ ...current, category: event.target.value as FieldReportCategory }))}>{(form.reportType === "weather" ? weatherCategories : stationCategories).map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
              <label><span>Reporter</span><div className="input-with-icon"><UserRound size={14} /><input value={form.reporterName} onChange={(event) => setForm((current) => ({ ...current, reporterName: event.target.value }))} /></div></label>
              <fieldset><legend>Location near</legend><div className="segmented-control field-targets">{(["AWS_001", "AWS_002", "AWS_003", "both", "custom"] as const).map((target) => <button type="button" className={form.target === target ? "active" : ""} onClick={() => setForm((current) => ({ ...current, target }))} key={target}>{target === "both" ? "All Stations" : target === "custom" ? "Custom" : displayNodeId(target)}</button>)}</div></fieldset>
              <label><span>Location description</span><input value={form.locationLabel} onChange={(event) => setForm((current) => ({ ...current, locationLabel: event.target.value }))} placeholder="North Ridge Sector" /></label>
              {form.target === "custom" && <div className="field-form-row"><label><span>Latitude</span><input required type="number" step="any" value={form.latitude} onChange={(event) => setForm((current) => ({ ...current, latitude: event.target.value }))} /></label><label><span>Longitude</span><input required type="number" step="any" value={form.longitude} onChange={(event) => setForm((current) => ({ ...current, longitude: event.target.value }))} /></label></div>}
              <label><span>Observation</span><textarea required minLength={3} value={form.observation} onChange={(event) => setForm((current) => ({ ...current, observation: event.target.value }))} placeholder="Dark clouds approaching rapidly from the west." /></label>
              <div className="field-form-row"><label><span>Direction</span><select value={form.direction} onChange={(event) => setForm((current) => ({ ...current, direction: event.target.value }))}><option value="">Not specified</option>{["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"].map((item) => <option key={item} value={item}>{titleCase(item)}</option>)}</select></label><label><span>Severity</span><select value={form.severity} onChange={(event) => setForm((current) => ({ ...current, severity: event.target.value as FieldReportFormState["severity"] }))}>{["information", "low", "moderate", "high", "critical"].map((item) => <option key={item}>{item}</option>)}</select></label></div>
              <div className="field-form-row"><label><span>Reporter confidence</span><select value={form.reporterConfidence} onChange={(event) => setForm((current) => ({ ...current, reporterConfidence: event.target.value as FieldReportFormState["reporterConfidence"] }))}>{["low", "medium", "high"].map((item) => <option key={item}>{item}</option>)}</select></label><label><span>Affected radius</span><select value={form.radiusPreset} onChange={(event) => { const radiusPreset = event.target.value as FieldReportFormState["radiusPreset"]; setForm((current) => ({ ...current, radiusPreset, radiusKm: radiusPreset === "custom" ? current.radiusKm : Number(radiusPreset) })); }}>{[1, 5, 10, 25].map((item) => <option key={item} value={item}>{item} km</option>)}<option value="custom">Custom</option></select></label></div>
              {form.radiusPreset === "custom" && <label><span>Custom affected radius (km)</span><input required type="number" min="0.1" max="100" step="0.1" value={form.radiusKm} onChange={(event) => setForm((current) => ({ ...current, radiusKm: Number(event.target.value) }))} /></label>}
              <label><span>Duration / expiry</span><select value={form.expiry} onChange={(event) => setForm((current) => ({ ...current, expiry: event.target.value as FieldReportFormState["expiry"] }))}><option value="30">30 minutes</option><option value="60">1 hour</option><option value="120">2 hours</option><option value="until">Until resolved</option></select></label>
              <div className="field-safety-note"><ShieldQuestion size={16} /><p><strong>Context, not ground truth.</strong> This report cannot overwrite readings or force an anomaly classification.</p></div>
              <button className="primary-command" disabled={commandPending}><Send size={16} />Submit Field Report</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
