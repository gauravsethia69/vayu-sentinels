import { useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  Gauge,
  HeartPulse,
  RadioTower,
  ShieldCheck,
  Wifi,
  Camera,
  GitMerge,
  Wrench,
} from "lucide-react";
import type { AnomalyEvent, NodeId, Parameter } from "../../api/types";
import { useSkyGuard } from "../../hooks/useSkyGuard";
import { displayNodeId, formatRelativeTime, formatValue, titleCase } from "../../utils/format";
import { EnvironmentalEventCard, EventTimeline, RecoveryPanel } from "./EventsPanels";
import FieldIntelligence, { FieldIntelligenceOverview } from "./FieldIntelligence";
import NodeStatusCard from "./NodeStatusCard";
import { SensorDetailPanel, TrustedDataPanel } from "./SensorPanels";
import { EmptyState, PanelHeading, StatusPill } from "./StatusUi";
import TrendChart from "./TrendChart";

interface ViewProps {
  selectedNode: NodeId;
  onSelectNode: (nodeId: NodeId) => void;
  onSelectEvent: (event: AnomalyEvent) => void;
}

interface NotifyViewProps extends ViewProps {
  onNotify: (message: string, tone?: "success" | "error") => void;
}

function MetricCard({ label, value, detail, icon: Icon, tone = "blue", wide = false }: { label: string; value: string; detail: string; icon: typeof Activity; tone?: "blue" | "green" | "red" | "amber"; wide?: boolean }) {
  return (
    <article className={`metric-card ${tone} ${wide ? "wide" : ""}`}>
      <span className="metric-icon"><Icon size={17} /></span>
      <div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>
    </article>
  );
}

function SummaryGrid() {
  const { summary, trusted } = useSkyGuard();
  const health = summary.nodes.length ? Math.round(summary.nodes.reduce((sum, node) => sum + node.health_score, 0) / summary.nodes.length) : 0;
  const communication = summary.nodes.length ? Math.round(summary.nodes.reduce((sum, node) => sum + node.communication_quality, 0) / summary.nodes.length) : 0;
  const anomalies = summary.nodes.reduce((sum, node) => sum + node.active_anomalies.length, 0);
  const validTrusted = trusted.filter((row) => ["validated", "estimated"].includes(row.trusted.quality ?? "")).length;
  const integrity = trusted.length ? Math.round((validTrusted / trusted.length) * 100) : 0;

  return (
    <div className="summary-grid">
      <MetricCard label="Active Stations" value={`${summary.metrics.active_nodes}`} detail={`${summary.nodes.length} configured`} icon={RadioTower} />
      <MetricCard label="Overall System Health" value={`${health}%`} detail={titleCase(summary.system.status)} icon={HeartPulse} tone="green" wide />
      <MetricCard label="Active Anomalies" value={`${anomalies}`} detail={anomalies ? "Requires attention" : "No active faults"} icon={AlertTriangle} tone={anomalies ? "red" : "green"} />
      <MetricCard label="Communication Quality" value={`${communication}%`} detail="Across all stations" icon={Wifi} />
      <MetricCard label="Trusted Data Integrity" value={`${integrity}%`} detail={`${trusted.length} recent outputs`} icon={ShieldCheck} tone="green" />
      <MetricCard
        label="ML Detector"
        value={summary.system.ml_model?.loaded ? "RF v6 Active" : "Safety Fallback"}
        detail={summary.system.ml_model?.model_version ?? summary.system.detector_mode}
        icon={Gauge}
        tone={summary.system.ml_model?.loaded ? "green" : "amber"}
      />
      <MetricCard label="Vision Systems" value={`${summary.system.vision_systems_online ?? 0} / ${summary.nodes.length}`} detail="Observation feeds online" icon={Camera} />
      <MetricCard label="Peer Failovers" value={`${summary.system.peer_failovers_active ?? 0}`} detail="Trusted peer estimates active" icon={GitMerge} tone={summary.system.peer_failovers_active ? "amber" : "green"} />
      <MetricCard label="Maintenance Warnings" value={`${summary.system.maintenance_warnings ?? 0}`} detail="Datasheet-informed priorities" icon={Wrench} tone={summary.system.maintenance_warnings ? "amber" : "green"} />
    </div>
  );
}

function NodeGrid({ selectedNode, onSelectNode }: Pick<ViewProps, "selectedNode" | "onSelectNode">) {
  const { histories, summary } = useSkyGuard();
  return (
    <div className="node-grid">
      {summary.nodes.map((node) => (
        <NodeStatusCard key={node.node_id} node={node} history={histories[node.node_id]} selected={selectedNode === node.node_id} onSelect={() => onSelectNode(node.node_id)} />
      ))}
    </div>
  );
}

function CommunicationCards() {
  const { summary } = useSkyGuard();
  return (
    <div className="communication-grid">
      {summary.nodes.map((node) => {
        const reading = node.latest;
        return (
          <section className="glass-card communication-card" key={node.node_id}>
            <PanelHeading eyebrow="Device link" title={`${displayNodeId(node.node_id)} communication`} action={<StatusPill state={node.communication_state ?? "unavailable"} />} />
            <div className="communication-values">
              <div><span>Wi-Fi RSSI</span><strong>{formatValue(reading?.device?.wifi_rssi, " dBm", 0)}</strong></div>
              <div><span>Packet interval</span><strong>{formatValue(reading?.inter_arrival_time_seconds, " sec", 2)}</strong></div>
              <div><span>Communication quality</span><strong>{Math.round(node.communication_quality)}%</strong></div>
              <div><span>Sequence</span><strong>{reading?.device?.sequence?.toLocaleString() ?? "—"}</strong></div>
              <div><span>Network delay</span><strong>{formatValue(reading?.network_delay_seconds, " sec", 2)}</strong></div>
              <div><span>Last received</span><strong>{formatRelativeTime(reading?.received_at ?? reading?.timestamp)}</strong></div>
            </div>
          </section>
        );
      })}
    </div>
  );
}

function NodeComparison() {
  const { summary } = useSkyGuard();
  const comparisons = [
    ["Temperature consensus", "temperature_c", "°C"],
    ["Humidity consensus", "humidity_pct", "%"],
    ["Pressure consensus", "pressure_hpa", " hPa"],
  ] as const;
  const temperatures = summary.nodes.map((node) => node.latest?.temperature_c).filter((value): value is number => typeof value === "number");
  const temperatureSpread = temperatures.length > 1 ? Math.max(...temperatures) - Math.min(...temperatures) : null;

  return (
    <section className="glass-card comparison-card">
      <PanelHeading eyebrow="Spatial consistency" title="Three-station consensus" action={<StatusPill state={temperatureSpread != null && temperatureSpread > 2 ? "warning" : "good"} />} />
      <div className="comparison-list">
        {comparisons.map(([label, field, unit]) => {
          const values = summary.nodes.map((node) => node.latest?.[field]).filter((value): value is number => typeof value === "number");
          const consensus = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
          return <div key={label}><span>{label}</span><strong>{formatValue(consensus, unit, 2)}</strong><small>{values.map((value) => formatValue(value, unit, 2)).join(" / ") || "Unavailable"}</small></div>;
        })}
      </div>
    </section>
  );
}

function HealthOverview() {
  const { health } = useSkyGuard();
  if (!health.length) return <EmptyState title="No health data" message="Health data appears after sensor readings are processed." />;
  return (
    <div className="health-node-grid">
      {health.map((node) => (
        <section className="glass-card health-node-card" key={node.node_id}>
          <PanelHeading eyebrow="Sensor health" title={displayNodeId(node.node_id)} action={<StatusPill state={node.status ?? "unavailable"} />} />
          {(["temperature", "humidity", "pressure", "communication"] as const).map((parameter) => {
            const value = node[parameter];
            return (
              <div className="health-meter" key={parameter}>
                <div><span>{titleCase(parameter)}</span><strong>{Math.round(value)}%</strong></div>
                <div><span style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></div>
                <small>{parameter === "communication" ? titleCase(node.communication_state) : titleCase(node.sensor_states[parameter])}</small>
              </div>
            );
          })}
          <div className="maintenance-line"><span>Maintenance state</span><strong>{titleCase(node.status)}</strong></div>
        </section>
      ))}
    </div>
  );
}

export function OverviewView({ selectedNode, onSelectNode, onSelectEvent }: ViewProps) {
  const { events, health, histories, summary, trusted } = useSkyGuard();
  const activeKeys = new Set(
    summary.nodes.flatMap((node) => node.active_anomalies.map((anomaly) => `${node.node_id}:${anomaly}`)),
  );
  const activeEvent = events.find(
    (event) => event.event_type !== "recovery" && Array.from(activeKeys).some((key) => {
      const [nodeId, anomaly] = key.split(":");
      return nodeId === event.node_id && (anomaly === event.anomaly_type || anomaly.includes(event.anomaly_type) || event.anomaly_type.includes(anomaly));
    }),
  );
  return (
    <div className="dashboard-view-stack">
      <SummaryGrid />
      <NodeGrid selectedNode={selectedNode} onSelectNode={onSelectNode} />
      <FieldIntelligenceOverview />
      <div className="detail-trusted-grid">
        <SensorDetailPanel selectedNode={selectedNode} nodes={summary.nodes} health={health} trusted={trusted} />
        <TrustedDataPanel selectedNode={selectedNode} trusted={trusted} events={events} />
      </div>
      <TrendChart histories={histories} trusted={trusted} parameter="temperature" selectedNode={selectedNode} />
      <div className="overview-event-grid">
        <section className="glass-card active-anomaly-card">
          <PanelHeading eyebrow="Current detector state" title="Active anomaly" />
          {activeEvent ? (
            <>
              <StatusPill state={activeEvent.severity ?? "information"} />
              <h3>{titleCase(activeEvent.anomaly_type)}</h3>
              <p>{activeEvent.message}</p>
              <div className="anomaly-values"><span>Observed <strong>{formatValue(activeEvent.observed_value)}</strong></span><span>Expected <strong>{formatValue(activeEvent.expected_value)}</strong></span><span>Confidence <strong>{activeEvent.confidence}%</strong></span></div>
              <button className="secondary-command full" onClick={() => onSelectEvent(activeEvent)}>View full analysis</button>
            </>
          ) : <EmptyState type="event" title="No active anomaly" message="The backend has not reported an active fault." />}
        </section>
        <EventTimeline events={events} onSelect={onSelectEvent} limit={6} />
        <div className="overview-state-stack"><EnvironmentalEventCard events={events} /><RecoveryPanel events={events} /></div>
      </div>
    </div>
  );
}

export function LiveSensorsView({ selectedNode, onSelectNode }: ViewProps) {
  const { events, health, histories, summary, trusted } = useSkyGuard();
  return (
    <div className="dashboard-view-stack">
      <NodeGrid selectedNode={selectedNode} onSelectNode={onSelectNode} />
      <SensorDetailPanel selectedNode={selectedNode} nodes={summary.nodes} health={health} trusted={trusted} />
      <TrendChart histories={histories} trusted={trusted} parameter="temperature" selectedNode={selectedNode} compact />
      <TrustedDataPanel selectedNode={selectedNode} trusted={trusted} events={events} />
      <CommunicationCards />
    </div>
  );
}

export function AnalyticsView({ selectedNode }: ViewProps) {
  const { health, histories, trusted } = useSkyGuard();
  const [parameter, setParameter] = useState<Parameter>("temperature");
  return (
    <div className="dashboard-view-stack">
      <div className="view-toolbar">
        <div><p className="panel-eyebrow">Historical analysis</p><h2>Weather telemetry and node comparison</h2></div>
        <div className="segmented-control three compact">
          {(["temperature", "humidity", "pressure"] as Parameter[]).map((item) => <button className={parameter === item ? "active" : ""} onClick={() => setParameter(item)} key={item}>{titleCase(item)}</button>)}
        </div>
      </div>
      <TrendChart histories={histories} trusted={trusted} parameter={parameter} selectedNode={selectedNode} />
      <div className="analytics-secondary-grid"><NodeComparison /><section className="glass-card analytics-health"><PanelHeading eyebrow="Backend health score" title="Node health comparison" />{health.map((node) => <div className="health-meter" key={node.node_id}><div><span>{displayNodeId(node.node_id)}</span><strong>{Math.round(node.node)}%</strong></div><div><span style={{ width: `${node.node}%` }} /></div></div>)}</section></div>
    </div>
  );
}

export function AnomaliesView({ onSelectEvent }: ViewProps) {
  const { events, summary } = useSkyGuard();
  const active = summary.nodes.flatMap((node) => node.active_anomalies.map((anomaly) => ({ node: node.node_id, anomaly })));
  return (
    <div className="dashboard-view-stack">
      <div className="active-anomaly-strip">
        {active.length ? active.map((item) => <div className="active-anomaly-chip" key={`${item.node}-${item.anomaly}`}><AlertTriangle size={15} /><span>{displayNodeId(item.node)}</span><strong>{titleCase(item.anomaly)}</strong></div>) : <div className="all-clear-strip"><CheckCircle2 size={17} /><strong>No active anomaly groups</strong><span>Historical detector events remain available below.</span></div>}
      </div>
      <div className="anomaly-page-grid"><EventTimeline events={events} onSelect={onSelectEvent} /><div className="overview-state-stack"><EnvironmentalEventCard events={events} /><RecoveryPanel events={events} /></div></div>
    </div>
  );
}

export function TrustedDataView({ selectedNode, onSelectNode }: ViewProps) {
  const { events, histories, summary, trusted } = useSkyGuard();
  return (
    <div className="dashboard-view-stack">
      <div className="view-toolbar"><div><p className="panel-eyebrow">Raw observations preserved</p><h2>Trusted data pipeline</h2></div><div className="segmented-control compact">{summary.nodes.map((node) => <button className={selectedNode === node.node_id ? "active" : ""} onClick={() => onSelectNode(node.node_id)} key={node.node_id}>{displayNodeId(node.node_id)}</button>)}</div></div>
      <TrustedDataPanel selectedNode={selectedNode} trusted={trusted} events={events} expanded />
      <TrendChart histories={histories} trusted={trusted} parameter="temperature" selectedNode={selectedNode} />
    </div>
  );
}


export function FieldIntelligenceView({ onNotify }: NotifyViewProps) {
  return <FieldIntelligence onNotify={onNotify} />;
}

export function SystemHealthView() {
  const { events } = useSkyGuard();
  return (
    <div className="dashboard-view-stack">
      <SummaryGrid />
      <HealthOverview />
      <CommunicationCards />
      <RecoveryPanel events={events} />
    </div>
  );
}
